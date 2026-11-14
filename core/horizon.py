"""Terrain horizon profile seen from a site.

For every terrain pixel within `radius_m` (great-circle distance on the reference sphere)
the elevation angle from the observer is computed in 3D: the pixel is placed on the
sphere at its height and seen from the observer standing at the site's height. The Moon's
curvature is therefore part of the geometry, nothing is approximated.

The profile has one value per azimuth sector: sector k covers azimuths
[k * step, (k + 1) * step) degrees from north through east, and holds the highest
elevation angle of the terrain in that sector.

Near pixels cover several sectors at once. A pixel whose angular width seen from the site
exceeds one sector is written into every sector between the azimuths of its corners, so a
close slope leaves no gaps. The pixel that contains the site itself is left out.

Elevation for one pixel, with v its unit vector, u the site's up vector, r_t and r_o the
distances of target and observer from the Moon centre:
    tan(elevation) = (v.u - r_o / r_t) / |v - (v.u) u|
Only the tangent is compared per pixel; the arctangent is taken once per sector.
"""

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from core import config
from core.geometry import enu_basis
from core.terrain import InsufficientData, Terrain

# Bump when the result of compute() changes for the same inputs; old cache entries are then ignored.
ALGORITHM_VERSION = 1

# Pixels per processing block. Small blocks stay in the CPU cache: measured 2.2 s per
# 150 km horizon with 100 000 against 4.3 s with 1 500 000 (dev machine, 2026-11-14).
CHUNK_PIXELS = 100_000


@dataclass(frozen=True)
class HorizonProfile:
    lat_deg: float
    lon_deg: float
    ground_height_m: float      # terrain height at the site above the reference sphere
    observer_height_m: float    # instrument height above the ground
    radius_m: float
    step_deg: float
    elevation_deg: np.ndarray   # one value per sector

    @property
    def site_height_m(self) -> float:
        """Observer height above the reference sphere; pass it to the ephemeris."""
        return self.ground_height_m + self.observer_height_m

    def sector_of(self, az_deg):
        n = len(self.elevation_deg)
        return (np.floor(np.mod(np.asarray(az_deg, float), 360.0) / self.step_deg).astype(int)) % n

    def at(self, az_deg):
        """Horizon elevation (degrees) in the given azimuths."""
        return self.elevation_deg[self.sector_of(az_deg)]


def _circle_bbox(terrain: Terrain, up, east, north, gamma):
    """Bounding box in raster coordinates of the circle at angular distance gamma."""
    az = np.radians(np.arange(0.0, 360.0, 5.0))
    pts = (np.cos(gamma) * up[None, :]
           + np.sin(gamma) * (np.cos(az)[:, None] * north[None, :] + np.sin(az)[:, None] * east[None, :]))
    lat = np.degrees(np.arcsin(np.clip(pts[:, 2], -1, 1)))
    lon = np.degrees(np.arctan2(pts[:, 1], pts[:, 0]))
    lat = np.append(lat, np.degrees(np.arcsin(np.clip(up[2], -1, 1))))
    lon = np.append(lon, np.degrees(np.arctan2(up[1], up[0])))
    xs, ys = terrain.latlon_to_xy(lat, lon)
    return xs.min(), ys.min(), xs.max(), ys.max()


def compute(terrain: Terrain, lat_deg: float, lon_deg: float, observer_height_m: float = 0.0,
            radius_m: float = config.HORIZON_RADIUS_M,
            step_deg: float = config.AZIMUTH_STEP_DEG) -> HorizonProfile:
    """Horizon profile at a site. Raises InsufficientData when the circle is not fully
    covered by terrain (raster edge or NoData)."""
    n_sectors = int(round(360.0 / step_deg))
    if abs(n_sectors * step_deg - 360.0) > 1e-9:
        raise ValueError("the azimuth step must divide 360 degrees")
    R = terrain.radius_m
    ground = terrain.height_at(lat_deg, lon_deg)
    r_obs = R + ground + observer_height_m
    east, north, up = (np.asarray(v, float) for v in enu_basis(lat_deg, lon_deg))
    gamma_max = radius_m / R
    cos_max = np.cos(gamma_max)

    win = terrain.read_window(*_circle_bbox(terrain, up, east, north, gamma_max), margin_px=2)
    xs, ys = win.centres()
    sx, sy = (v[0] for v in terrain.latlon_to_xy(lat_deg, lon_deg))
    site_col = int(np.floor((sx - win.x0) / win.dx + 0.5))
    site_row = int(np.floor((sy - win.y0) / win.dy + 0.5))

    # pixels seen wider than one sector are spread over the sectors they cover
    near_sin = terrain.pixel_size_m * np.sqrt(2.0) / np.radians(step_deg) / R

    best = np.full(n_sectors, -np.inf)
    rows_per_chunk = max(1, CHUNK_PIXELS // len(xs))
    for r0 in range(0, len(ys), rows_per_chunk):
        r1 = min(r0 + rows_per_chunk, len(ys))
        vu, ve, vn = terrain.grid_dots(xs, ys[r0:r1], (up, east, north))
        inside = vu >= cos_max
        if r0 <= site_row < r1 and 0 <= site_col < len(xs):
            inside[site_row - r0, site_col] = False
        if not inside.any():
            continue
        z = win.heights[r0:r1].astype(float)
        if np.isnan(z[inside]).any():
            raise InsufficientData(
                f"terrain does not cover the {radius_m / 1000:g} km circle around lat {lat_deg}, lon {lon_deg}")
        s = np.hypot(ve, vn)
        s[~inside] = 1.0  # keep the division finite outside the circle
        t = (vu - r_obs / (R + z)) / s
        # Azimuth in float32 only selects the sector: its error (~1e-7 rad) matters
        # only for a pixel within that distance of a sector boundary.
        ang = np.arctan2(ve.astype(np.float32), vn.astype(np.float32))
        sector = np.floor(ang * np.float32(n_sectors / (2 * np.pi))).astype(np.int32)
        sector[sector < 0] += n_sectors
        sector[sector >= n_sectors] -= n_sectors

        far = inside & (s >= near_sin)
        tf, kf = t[far], sector[far]
        # a sparse first pass gives a lower bound per sector; only pixels above it can win
        np.maximum.at(best, kf[::16], tf[::16])
        keep = tf > best[kf]
        np.maximum.at(best, kf[keep], tf[keep])
        near = inside & (s < near_sin)
        if near.any():
            rr, cc = np.nonzero(near)
            az = np.degrees(np.arctan2(ve[near], vn[near])) % 360.0
            _spread_near(terrain, best, xs[cc], ys[r0:r1][rr], az, t[near],
                         east, north, step_deg, n_sectors)

    if np.isneginf(best).any():
        raise InsufficientData(f"{int(np.isneginf(best).sum())} azimuth sectors have no terrain")
    return HorizonProfile(lat_deg=float(lat_deg), lon_deg=float(lon_deg), ground_height_m=ground,
                          observer_height_m=float(observer_height_m), radius_m=float(radius_m),
                          step_deg=float(step_deg), elevation_deg=np.degrees(np.arctan(best)))


def _spread_near(terrain, best, x, y, az, t, east, north, step_deg, n_sectors):
    hx, hy = terrain.dx / 2.0, terrain.dy / 2.0
    lo = np.zeros_like(az)
    hi = np.zeros_like(az)
    for cx, cy in ((-hx, -hy), (-hx, hy), (hx, -hy), (hx, hy)):
        c = terrain.unit_vectors(x + cx, y + cy)
        caz = np.degrees(np.arctan2(c @ east, c @ north))
        d = (caz - az + 180.0) % 360.0 - 180.0
        lo, hi = np.minimum(lo, d), np.maximum(hi, d)
    k_lo = np.floor((az + lo) / step_deg).astype(int)
    k_hi = np.floor((az + hi) / step_deg).astype(int)
    count = k_hi - k_lo + 1
    idx = np.repeat(k_lo, count) + (np.arange(count.sum()) - np.repeat(np.cumsum(count) - count, count))
    np.maximum.at(best, idx % n_sectors, np.repeat(t, count))


# ---------- disk cache ----------

def cache_dir() -> Path:
    return config.data_dir() / "cache" / "horizons"


def cache_key(terrain: Terrain, lat_deg, lon_deg, observer_height_m, radius_m, step_deg) -> str:
    fields = {
        "terrain": terrain.fingerprint,
        "lat": f"{lat_deg:.7f}", "lon": f"{lon_deg:.7f}",
        "observer_height_m": f"{observer_height_m:.3f}",
        "radius_m": f"{radius_m:.1f}", "step_deg": f"{step_deg:.6f}",
        "algorithm": ALGORITHM_VERSION,
    }
    return hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()[:32]


def horizon(terrain: Terrain, lat_deg: float, lon_deg: float, observer_height_m: float = 0.0,
            radius_m: float = config.HORIZON_RADIUS_M, step_deg: float = config.AZIMUTH_STEP_DEG,
            folder: Path | None = None) -> HorizonProfile:
    """compute() with a disk cache. Results with insufficient data are not cached."""
    folder = folder or cache_dir()
    path = folder / (cache_key(terrain, lat_deg, lon_deg, observer_height_m, radius_m, step_deg) + ".npz")
    if path.is_file():
        try:
            with np.load(path) as f:
                return HorizonProfile(lat_deg=float(lat_deg), lon_deg=float(lon_deg),
                                      ground_height_m=float(f["ground_height_m"]),
                                      observer_height_m=float(observer_height_m), radius_m=float(radius_m),
                                      step_deg=float(step_deg), elevation_deg=f["elevation_deg"])
        except (OSError, KeyError, ValueError):
            pass  # unreadable entry: recompute and overwrite
    profile = compute(terrain, lat_deg, lon_deg, observer_height_m, radius_m, step_deg)
    folder.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.stem}.{os.getpid()}.tmp.npz")
    np.savez(tmp, elevation_deg=profile.elevation_deg, ground_height_m=profile.ground_height_m)
    tmp.replace(path)
    return profile
