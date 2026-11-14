"""Sun and Earth directions from a site on the Moon, computed locally with Skyfield.

How it works:

1. Skyfield gives the apparent position (light time and aberration included) of the Sun
   and of the Earth centre relative to the Moon centre, once per time grid.
2. The vectors are rotated into the lunar body-fixed frame MOON_ME_DE440_ME421, which
   matches the LOLA terrain frame.
3. For each site the site position is subtracted (this matters for the Earth: up to about
   0.26 degrees of parallax) and the result is converted to local altitude and azimuth
   with core.geometry, the same functions the terrain horizon uses.

Step 1 is the only expensive one, so one time grid serves any number of sites.
No file is downloaded: kernels are opened by path and the time scale uses Skyfield's
built-in tables.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol, Sequence

import numpy as np

from core import config
from core.datasets import path_of
from core.geometry import site_position, vectors_to_altaz

BODIES = ("sun", "earth")
KERNEL_KEYS = ("ephemeris", "moon_orientation", "moon_frames", "constants")


@dataclass(frozen=True)
class Direction:
    alt_deg: np.ndarray
    az_deg: np.ndarray


class DirectionSource(Protocol):
    def vectors_km(self, times_utc: Sequence[datetime]) -> dict[str, np.ndarray]:
        """Apparent position of each body in BODIES relative to the Moon centre,
        lunar body-fixed frame, kilometres, shape (N, 3)."""


def check_times(times_utc: Sequence[datetime]) -> None:
    """Reject naive datetimes and dates outside the supported range."""
    if not len(times_utc):
        raise ValueError("no times given")
    for t in times_utc:
        if t.tzinfo is None:
            raise ValueError("times must be timezone-aware (UTC)")
    lo, hi = min(times_utc), max(times_utc)
    if lo < config.DATE_MIN_UTC or hi >= config.DATE_END_UTC:
        raise ValueError(
            f"dates must be within {config.DATE_MIN_UTC:%Y-%m-%d} .. "
            f"{config.DATE_END_UTC - timedelta(seconds=1):%Y-%m-%d} UTC"
        )


def time_grid(start_utc: datetime, end_utc: datetime, step_h: float = config.TIME_STEP_H) -> list[datetime]:
    """Times from start to end inclusive, `step_h` hours apart."""
    if end_utc < start_utc:
        raise ValueError("end is before start")
    step = timedelta(hours=step_h)
    n = int((end_utc - start_utc) / step)
    return [start_utc + i * step for i in range(n + 1)]


def orientation_segment(pc, ts, frame_name: str):
    """The binary orientation segment that covers the whole supported date range.

    moon_pa_de440_200625.bpc holds two segments for the same body (1549-2426 and
    2426-2650). Skyfield keeps only the last one it reads per body, so the frame would
    reject our dates; we pick the covering segment explicitly instead.
    Uses Skyfield internals (_segment_list, build_frame(_segment=...)); the Skyfield
    version is pinned in requirements.txt.
    """
    integer = pc.variables[f"FRAME_{frame_name}"]
    relative = pc.variables.get(f"TKFRAME_{integer}_RELATIVE")
    base = pc.variables[f"FRAME_{relative}"] if relative else integer
    lo = ts.from_datetime(config.DATE_MIN_UTC).tdb
    hi = ts.from_datetime(config.DATE_END_UTC).tdb
    for segment in pc._segment_list:
        if segment.body == base and segment.initial_jd <= lo and hi <= segment.final_jd:
            return segment
    raise LookupError(
        f"no orientation segment for frame {base} covers "
        f"{config.DATE_MIN_UTC:%Y-%m-%d} .. {config.DATE_END_UTC:%Y-%m-%d}"
    )


class SkyfieldSource:
    """DirectionSource backed by the four JPL/NAIF files in the data folder."""

    def __init__(self, data_dir: Path | None = None):
        from skyfield.api import load, load_file
        from skyfield.planetarylib import PlanetaryConstants

        base = data_dir or config.data_dir()
        paths = {k: path_of(k, base) for k in KERNEL_KEYS}
        missing = [p.name for p in paths.values() if not p.is_file()]
        if missing:
            raise FileNotFoundError(
                f"missing data files in {base}: {', '.join(missing)}. Run: python scripts/fetch_data.py"
            )

        self.ts = load.timescale(builtin=True)
        self.eph = load_file(str(paths["ephemeris"]))

        pc = PlanetaryConstants()
        pc.read_text(open(paths["moon_frames"], "rb"))
        pc.read_text(open(paths["constants"], "rb"))
        pc.read_binary(open(paths["moon_orientation"], "rb"))
        self.constants = pc
        # Build the ME frame by its full name; the MOON_ME alias is a second-level
        # frame that Skyfield cannot resolve.
        self.segment = orientation_segment(pc, self.ts, config.LUNAR_FRAME)
        self.frame = pc.build_frame(pc.variables[f"FRAME_{config.LUNAR_FRAME}"], _segment=self.segment)

        radii = pc.variables.get("BODY301_RADII")
        if radii is None or any(abs(float(r) * 1000.0 - config.MOON_RADIUS_M) > 1e-6 for r in radii):
            raise ValueError(f"lunar radius in {paths['constants'].name} is {radii} km, "
                             f"config expects {config.MOON_RADIUS_M / 1000} km")

    def vectors_km(self, times_utc: Sequence[datetime]) -> dict[str, np.ndarray]:
        check_times(times_utc)
        t = self.ts.from_datetimes(list(times_utc))
        observer = self.eph["moon"].at(t)
        rotation = self.frame.rotation_at(t)  # ICRF -> body frame, shape (3, 3, N)
        out = {}
        for body in BODIES:
            icrf = observer.observe(self.eph[body]).apparent().position.km  # (3, N)
            out[body] = np.einsum("ij...,j...->...i", rotation, icrf)
        return out


def local_directions(vectors_km: dict[str, np.ndarray], lat_deg, lon_deg, height_m=0.0) -> dict[str, Direction]:
    """Altitude and azimuth of each body seen from a site.

    `height_m` is the height above the reference sphere: terrain elevation plus the
    height of the instrument above the ground. Latitude and longitude broadcast against
    the time axis; for many sites at once pass arrays of shape (S, 1).
    """
    radius_km = (config.MOON_RADIUS_M + np.asarray(height_m, dtype=float)) / 1000.0
    site_km = site_position(lat_deg, lon_deg, radius_km)
    out = {}
    for body, v in vectors_km.items():
        alt, az = vectors_to_altaz(np.asarray(v, dtype=float) - site_km, lat_deg, lon_deg)
        out[body] = Direction(alt_deg=alt, az_deg=az)
    return out


def directions(source: DirectionSource, times_utc: Sequence[datetime], lat_deg, lon_deg,
               height_m=0.0) -> dict[str, Direction]:
    """Sun and Earth altitude and azimuth from one site at the given times."""
    return local_directions(source.vectors_km(times_utc), lat_deg, lon_deg, height_m)


def utc(year, month, day, hour=0, minute=0) -> datetime:
    """Shorthand for a timezone-aware UTC datetime."""
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)
