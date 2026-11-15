"""Nearest acceptable window for one site (POST /api/next-window, format approved 2026-11-15).

From a given start time, start times are screened every hour (the same screening as the
inverse search) over a fixed horizon of years. The first start that passes the screen is
checked at 1-minute resolution; the hourly screen can pass a start that the minute grid
rejects (a night a little over the limit), so the search never gives up on the screen's word
alone: it checks block after block until a start passes or the period ends. A block is the
minute-resolution Sun and Earth series over BLOCK_DAYS of start times; every hourly start in
it is evaluated on that one series, and the first acceptable one is recalculated by the same
code as a single assessment (core.mission), so the window shown is exactly what an assessment
of that start returns. The answer is that window, fully assessed, or "none found" with the
period that was searched.

Unlike the grid search this uses the site's own horizon for the requested instrument height.
"""

from datetime import datetime, timedelta

import numpy as np

from core import config
from core.ephemeris import DirectionSource, check_times, time_grid
from core.horizon import horizon
from core.mission import MODEL, MissionProfile, _iso, _num, build_response, check_site
from core.search import _longest_hidden
from core.terrain import InsufficientData, Terrain
from core.visibility import series_from_vectors, window

NEXT_WINDOW_YEARS = 5
START_STEP_H = config.SEARCH_START_STEP_H
CHUNK_STARTS = 2000          # hourly starts screened per block: bounds memory, stops early once found
BLOCK_DAYS = 7               # start times evaluated on one minute-resolution series
NOTE = ("Start times screened every hour; the window shown is recalculated at 1-minute resolution. "
        "First acceptable start found in the searched period.")


def search_period(from_utc: datetime, duration_days: float, years: float = NEXT_WINDOW_YEARS) -> tuple[datetime, int]:
    """Last start searched and the number of hourly starts; the last window must end inside the supported dates."""
    check_times([from_utc, from_utc + timedelta(days=duration_days) - timedelta(minutes=1)])
    step = timedelta(hours=START_STEP_H)
    latest = config.DATE_END_UTC - timedelta(days=duration_days)          # a start whose window still fits
    last = min(from_utc + timedelta(days=365.25 * years), latest)
    n = int((last - from_utc) / step) + 1
    return from_utc + (n - 1) * step, n


def _screen_block(sun, earth, first: int, count: int, ws: int, profile: MissionProfile) -> np.ndarray:
    """Indices (relative to the sample arrays) of starts first..first+count-1 that pass the hourly screen."""
    starts = np.arange(first, first + count)
    night = _longest_hidden(sun, starts, ws) * START_STEP_H
    gap = _longest_hidden(earth, starts, ws) * START_STEP_H
    cs = np.concatenate(([0], np.cumsum(np.asarray(sun, int))))
    sun_pct = 100.0 * (cs[starts + ws] - cs[starts]) / ws
    ok = (night <= profile.max_night_h) & (sun_pct >= profile.min_sun_pct) & (gap <= profile.max_comms_gap_h)
    return starts[ok]


def next_window(terrain: Terrain, source: DirectionSource, lat_deg: float, lon_deg: float, from_utc: datetime,
                profile: MissionProfile | dict | None = None, cache_folder=None,
                years: float = NEXT_WINDOW_YEARS) -> dict:
    if not isinstance(profile, MissionProfile):
        profile = MissionProfile.from_dict(profile)
    check_site(lat_deg, lon_deg)
    last_start, n_starts = search_period(from_utc, profile.duration_days, years)
    response = {
        "data_status": "insufficient_data",
        "site": {"lat_deg": _num(lat_deg), "lon_deg": _num(lon_deg), "height_m": None},
        "query": {"from_utc": _iso(from_utc), "to_utc": _iso(last_start), "start_step_h": _num(START_STEP_H),
                  "years": _num(years), "profile": profile.to_dict()},
        "found": False, "window": None, "note": NOTE, "meta": {"model": MODEL},
    }
    try:
        prof = horizon(terrain, lat_deg, lon_deg, profile.observer_height_m, folder=cache_folder)
    except InsufficientData:
        return response
    response["data_status"] = "ok"
    response["site"]["height_m"] = _num(prof.site_height_m, 1)

    ws = int(round(profile.duration_days * 24.0 / START_STEP_H))
    step = timedelta(hours=START_STEP_H)
    minutes_per_start = int(round(START_STEP_H * 60.0))
    ws_min = int(round(profile.duration_days * 24 * 60))
    block_starts = int(BLOCK_DAYS * 24 / START_STEP_H)
    skip_until = -1                       # hourly starts below this index were evaluated at 1 minute already
    for first in range(0, n_starts, CHUNK_STARTS):
        count = min(CHUNK_STARTS, n_starts - first)
        # Hourly samples covering every window that starts in this chunk.
        t0 = from_utc + first * step
        times = time_grid(t0, t0 + (count - 1 + ws - 1) * step, START_STEP_H)
        s = series_from_vectors(source.vectors_km(times), prof)
        for i in _screen_block(s["sun"].visible, s["earth"].visible, 0, count, ws, profile):
            index = first + int(i)
            if index < skip_until:
                continue
            found = _check_block(source, prof, profile, from_utc + index * step, min(block_starts, n_starts - index),
                                 ws_min, minutes_per_start)
            skip_until = index + min(block_starts, n_starts - index)
            if found is None:
                continue
            start = from_utc + (index + found) * step
            full = build_response(lat_deg, lon_deg, start, profile, window(source, prof, start, profile.duration_days),
                                  prof.site_height_m, terrain.path.name)
            if full["status"] == "acceptable":
                response.update({"found": True, "window": full})
                return response
    return response


def _check_block(source, prof, profile: MissionProfile, block_start, n_starts: int, ws_min: int, minutes_per_start: int):
    """Offset (in hourly starts) of the first acceptable start among the `n_starts` hourly starts
    from `block_start`, evaluated on one 1-minute series; None when none is acceptable."""
    total = (n_starts - 1) * minutes_per_start + ws_min
    minute = timedelta(minutes=config.TIME_STEP_MIN)
    s = series_from_vectors(source.vectors_km([block_start + k * minute for k in range(total)]), prof)
    starts = np.arange(n_starts) * minutes_per_start
    night = _longest_hidden(s["sun"].visible, starts, ws_min) * config.TIME_STEP_MIN / 60.0
    gap = _longest_hidden(s["earth"].visible, starts, ws_min) * config.TIME_STEP_MIN / 60.0
    cs = np.concatenate(([0], np.cumsum(np.asarray(s["sun"].visible, int))))
    sun_pct = 100.0 * (cs[starts + ws_min] - cs[starts]) / ws_min
    ok = np.flatnonzero((night <= profile.max_night_h) & (sun_pct >= profile.min_sun_pct) & (gap <= profile.max_comms_gap_h))
    return int(ok[0]) if ok.size else None
