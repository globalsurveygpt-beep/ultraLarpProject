"""Nearest acceptable window for one site (POST /api/next-window, format approved 2026-11-15).

From a given start time, start times are screened every hour (the same screening as the
inverse search) over a fixed horizon of years. The first start that passes the screen is
recalculated at 1-minute resolution by the same code as a single assessment; when the
minute check disagrees (the hourly screen sits close to a threshold), the next screened
starts are tried. The answer is either that window, fully assessed, or "none found" with
the period that was searched.

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
CHUNK_STARTS = 2000          # starts screened per block: bounds memory, stops early once found
MAX_MINUTE_CHECKS = 25       # screened candidates re-checked at 1 minute before giving up
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
    checked = 0
    for first in range(0, n_starts, CHUNK_STARTS):
        count = min(CHUNK_STARTS, n_starts - first)
        # Hourly samples covering every window that starts in this block.
        t0 = from_utc + first * step
        times = time_grid(t0, t0 + (count - 1 + ws - 1) * step, START_STEP_H)
        s = series_from_vectors(source.vectors_km(times), prof)
        passed = _screen_block(s["sun"].visible, s["earth"].visible, 0, count, ws, profile)
        for i in passed:
            start = t0 + int(i) * step
            result = window(source, prof, start, profile.duration_days)
            full = build_response(lat_deg, lon_deg, start, profile, result, prof.site_height_m, terrain.path.name)
            if full["status"] == "acceptable":
                response.update({"found": True, "window": full})
                return response
            checked += 1
            if checked >= MAX_MINUTE_CHECKS:
                return response
    return response

