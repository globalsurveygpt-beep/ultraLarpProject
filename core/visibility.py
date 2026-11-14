"""Sun and Earth visibility from a site: series, intervals and window metrics.

Margin = altitude of the body centre minus the terrain horizon in the body's azimuth.
The body is visible when the margin is above zero. Model: body centre, no eclipses;
"Earth visible" means a direct line of sight to the Earth centre (no relay satellites).

Time handling: a window [start, start + duration) is sampled every config.TIME_STEP_MIN
minutes from its start; the end is not included. Each sample stands for the step that
begins at it, so durations are counts of samples times the step. An interval that began
before the window is counted only from the window start, and likewise at the end.
Short events are not filtered: one visible sample is a visible interval.

The observer is the same for both parts of the margin: the horizon profile is computed
for a site height (terrain plus instrument), and that height is passed to the ephemeris.

Body vectors do not depend on the site, so the `*_from_vectors` functions let one
ephemeris call serve many sites.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Sequence

import numpy as np

from core import config
from core.ephemeris import BODIES, DirectionSource, check_times, local_directions
from core.horizon import HorizonProfile


@dataclass(frozen=True)
class BodySeries:
    alt_deg: np.ndarray
    az_deg: np.ndarray
    horizon_deg: np.ndarray   # terrain horizon in the body's azimuth
    margin_deg: np.ndarray    # alt - horizon
    visible: np.ndarray       # margin > 0


@dataclass(frozen=True)
class BodyState:
    alt_deg: float
    az_deg: float
    horizon_deg: float
    margin_deg: float
    visible: bool


@dataclass(frozen=True)
class Interval:
    start_utc: datetime
    end_utc: datetime
    visible: bool

    @property
    def duration_h(self) -> float:
        return (self.end_utc - self.start_utc).total_seconds() / 3600.0


@dataclass(frozen=True)
class WindowMetrics:
    longest_night_h: float       # longest continuous time without the Sun
    sun_pct: float               # share of the window with the Sun visible
    longest_comms_gap_h: float   # longest continuous time without the Earth
    comms_down_h: float          # total time without the Earth
    comms_pct: float             # share of the window with the Earth visible


@dataclass(frozen=True)
class WindowResult:
    start_utc: datetime
    end_utc: datetime            # not included
    step_min: float
    metrics: WindowMetrics
    sun_intervals: list[Interval]
    earth_intervals: list[Interval]


@dataclass(frozen=True)
class DaySeries:
    times_utc: list[datetime]
    sun: BodySeries
    earth: BodySeries


# ---------- series ----------

def series_from_vectors(vectors_km: dict[str, np.ndarray], profile: HorizonProfile) -> dict[str, BodySeries]:
    d = local_directions(vectors_km, profile.lat_deg, profile.lon_deg, profile.site_height_m)
    out = {}
    for body in BODIES:
        alt, az = d[body].alt_deg, d[body].az_deg
        hor = profile.at(az)
        margin = alt - hor
        out[body] = BodySeries(alt_deg=alt, az_deg=az, horizon_deg=hor, margin_deg=margin, visible=margin > 0.0)
    return out


def series(source: DirectionSource, profile: HorizonProfile, times_utc: Sequence[datetime]) -> dict[str, BodySeries]:
    return series_from_vectors(source.vectors_km(times_utc), profile)


def instant(source: DirectionSource, profile: HorizonProfile, time_utc: datetime) -> dict[str, BodyState]:
    """State of the Sun and the Earth at one moment."""
    s = series(source, profile, [time_utc])
    return {body: BodyState(alt_deg=float(b.alt_deg[0]), az_deg=float(b.az_deg[0]),
                            horizon_deg=float(b.horizon_deg[0]), margin_deg=float(b.margin_deg[0]),
                            visible=bool(b.visible[0]))
            for body, b in s.items()}


def day_series(source: DirectionSource, profile: HorizonProfile, day_start_utc: datetime,
               step_min: float = config.DAY_SERIES_STEP_MIN) -> DaySeries:
    """Series over 24 hours from `day_start_utc`, both ends included (for charts)."""
    n = int(round(24 * 60 / step_min))
    times = [day_start_utc + timedelta(minutes=i * step_min) for i in range(n + 1)]
    s = series(source, profile, times)
    return DaySeries(times_utc=times, sun=s["sun"], earth=s["earth"])


# ---------- window ----------

def window_times(start_utc: datetime, duration_days: float,
                 step_min: float = config.TIME_STEP_MIN) -> list[datetime]:
    """Sample times of the window [start, start + duration), end not included."""
    if not config.WINDOW_DAYS_MIN <= duration_days <= config.WINDOW_DAYS_MAX:
        raise ValueError(f"window length must be {config.WINDOW_DAYS_MIN:g} to "
                         f"{config.WINDOW_DAYS_MAX:g} days, got {duration_days:g}")
    n = int(round(duration_days * 24 * 60 / step_min))
    step = timedelta(minutes=step_min)
    times = [start_utc + i * step for i in range(n)]
    check_times([times[0], times[-1]])
    return times


def _runs(visible: np.ndarray):
    """Start index, length and state of each run of equal values."""
    v = np.asarray(visible, dtype=bool)
    starts = np.concatenate(([0], np.flatnonzero(v[1:] != v[:-1]) + 1))
    lengths = np.diff(np.concatenate((starts, [len(v)])))
    return starts, lengths, v[starts]


def intervals(visible: np.ndarray, start_utc: datetime, step_min: float) -> list[Interval]:
    """Consecutive intervals of constant visibility that cover the sampled span exactly."""
    starts, lengths, states = _runs(visible)
    step = timedelta(minutes=step_min)
    return [Interval(start_utc=start_utc + int(s) * step, end_utc=start_utc + int(s + n) * step, visible=bool(st))
            for s, n, st in zip(starts, lengths, states)]


def _longest_hidden_h(visible: np.ndarray, step_min: float) -> float:
    _, lengths, states = _runs(visible)
    hidden = lengths[~states]
    return float(hidden.max()) * step_min / 60.0 if hidden.size else 0.0


def metrics(sun_visible: np.ndarray, earth_visible: np.ndarray, step_min: float) -> WindowMetrics:
    sun, earth = np.asarray(sun_visible, bool), np.asarray(earth_visible, bool)
    return WindowMetrics(
        longest_night_h=_longest_hidden_h(sun, step_min),
        sun_pct=100.0 * float(sun.mean()),
        longest_comms_gap_h=_longest_hidden_h(earth, step_min),
        comms_down_h=float((~earth).sum()) * step_min / 60.0,
        comms_pct=100.0 * float(earth.mean()),
    )


def window_from_vectors(vectors_km: dict[str, np.ndarray], profile: HorizonProfile, start_utc: datetime,
                        step_min: float = config.TIME_STEP_MIN) -> WindowResult:
    """Window result from body vectors already computed for the window's sample times."""
    s = series_from_vectors(vectors_km, profile)
    n = len(s["sun"].visible)
    return WindowResult(
        start_utc=start_utc,
        end_utc=start_utc + n * timedelta(minutes=step_min),
        step_min=step_min,
        metrics=metrics(s["sun"].visible, s["earth"].visible, step_min),
        sun_intervals=intervals(s["sun"].visible, start_utc, step_min),
        earth_intervals=intervals(s["earth"].visible, start_utc, step_min),
    )


def window(source: DirectionSource, profile: HorizonProfile, start_utc: datetime,
           duration_days: float) -> WindowResult:
    """Sun and Earth visibility metrics for a mission window at the profile's site."""
    times = window_times(start_utc, duration_days)
    return window_from_vectors(source.vectors_km(times), profile, start_utc)
