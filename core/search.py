"""Inverse search: where and when the profile's thresholds are met.

Two passes keep a request within seconds while the reported windows stay exact:

1. Screening. Every grid site with a precomputed horizon, every start time from
   `from_utc` to `to_utc` every SEARCH_START_STEP_H hours, Sun and Earth visibility
   sampled hourly. Gives each site the share of acceptable start times (an estimate:
   hourly sampling can miss events shorter than an hour) and its best start.
2. Refinement. The best start of the leading sites is recomputed on the 1-minute grid
   with the same code as a single window assessment. Only windows that stay acceptable
   go into the top list.

Ranking uses the thresholds' own quantities, no combined score:
- best start of a site: shortest longest night, then shortest comms gap, then earliest;
- sites: larger share of acceptable starts first, then shorter longest night of the
  best start, then grid order.

Horizons come from the cache only (see scripts/precompute_horizons.py), computed for an
instrument height of 0 m, so the search accepts only observer_height_m = 0. A HorizonStore
keeps them in memory, so a server reads the cache files once instead of on every request.
The 1-minute Sun and Earth vectors of all candidate windows are computed in one pass over
the merged time spans the windows cover, then sliced per window.
The grid is pure geometry: points every step on the south polar stereographic plane of
the reference sphere, kept when their great-circle distance from the pole is within the
working zone. The same function feeds the precompute script, so cache keys match.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

from core import config, horizon as horizon_mod
from core.ephemeris import DirectionSource, check_times, time_grid
from core.mission import MODEL, MissionProfile, ProfileError, build_response, _iso, _num
from core.terrain import InsufficientData, Terrain
from core.visibility import series_from_vectors, window_from_vectors

NOTE = ("Map shares come from hourly sampling of start times; "
        "top results are recomputed at 1-minute resolution.")


class SearchUnavailable(Exception):
    """No precomputed horizons for the requested region."""


class HorizonStore:
    """Cached horizons held in memory.

    A site found on disk (a profile, or "insufficient data") is kept; a site not computed
    yet is looked up on disk again next time, so a precompute that runs later is picked up.
    Reads and writes of a dict are safe across request threads.
    """

    _INSUFFICIENT = "insufficient"

    def __init__(self, terrain: Terrain, folder: Path | None = None):
        self.terrain, self.folder = terrain, folder
        self._sites: dict = {}

    def get(self, lat_deg: float, lon_deg: float):
        key = (lat_deg, lon_deg)
        hit = self._sites.get(key)
        if hit is None:
            try:
                hit = horizon_mod.cached(self.terrain, lat_deg, lon_deg, 0.0, folder=self.folder)
            except InsufficientData:
                hit = self._INSUFFICIENT
            if hit is None:
                return None
            self._sites[key] = hit
        if hit is self._INSUFFICIENT:
            raise InsufficientData("terrain does not cover this site (cached)")
        return hit

    def preload(self, step_m: float = config.SEARCH_GRID_STEP_M) -> int:
        """Read every grid site from the cache; returns how many are now held."""
        for lat, lon in grid_sites(step_m):
            try:
                self.get(lat, lon)
            except InsufficientData:
                pass
        return len(self._sites)

    def __len__(self):
        return len(self._sites)


# ---------- grid and region ----------

def grid_sites(step_m: float = config.SEARCH_GRID_STEP_M,
               zone_m: float = config.WORKING_ZONE_RADIUS_M) -> list[tuple[float, float]]:
    R = config.MOON_RADIUS_M
    n = int(zone_m // step_m)
    k = np.arange(-n, n + 1) * step_m
    X, Y = np.meshgrid(k, -k)
    c = 2.0 * np.arctan(np.hypot(X, Y) / (2.0 * R))  # angular distance from the pole
    keep = R * c <= zone_m + 1e-6
    lat = np.round(np.degrees(c[keep]) - 90.0, 6)
    lon = np.round(np.degrees(np.arctan2(X[keep], Y[keep])), 6)
    lon = np.where(lat <= -90.0, 0.0, lon) + 0.0  # + 0.0 turns -0.0 into 0.0
    return [(float(a), float(b)) for a, b in zip(lat, lon)]


def _unit(lat, lon):
    lat, lon = np.radians(np.asarray(lat, float)), np.radians(np.asarray(lon, float))
    return np.stack([np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)], axis=-1)


def parse_region(region: dict | None) -> dict:
    region = dict(region or {"kind": "zone"})
    kind = region.get("kind", "zone")
    if kind == "zone":
        if set(region) - {"kind"}:
            raise ProfileError("region", "a zone region has no other fields")
        return {"kind": "zone"}
    if kind != "circle":
        raise ProfileError("region", "kind must be 'zone' or 'circle'")
    out = {"kind": "circle"}
    for key, lo, hi in (("lat_deg", -90.0, 90.0), ("lon_deg", -180.0, 180.0),
                        ("radius_km", 0.0, config.SEARCH_REGION_MAX_RADIUS_KM)):
        v = region.get(key)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi or (key == "radius_km" and v == 0):
            raise ProfileError(key, f"required for a circle region, between {lo:g} and {hi:g}")
        out[key] = float(v)
    unknown = set(region) - {"kind", "lat_deg", "lon_deg", "radius_km"}
    if unknown:
        raise ProfileError(sorted(unknown)[0], "unknown field")
    return out


def sites_in_region(sites: list[tuple[float, float]], region: dict) -> list[tuple[float, float]]:
    if region["kind"] == "zone":
        return list(sites)
    centre = _unit(region["lat_deg"], region["lon_deg"])
    pts = _unit([s[0] for s in sites], [s[1] for s in sites])
    dist_km = np.arccos(np.clip(pts @ centre, -1, 1)) * config.MOON_RADIUS_M / 1000.0
    return [s for s, d in zip(sites, dist_km) if d <= region["radius_km"] + 1e-9]


# ---------- screening ----------

@dataclass(frozen=True)
class Screen:
    acceptable_starts: int
    n_starts: int
    best_index: int | None
    best_night_h: float | None
    best_gap_h: float | None

    @property
    def acceptable_pct(self) -> float:
        return 100.0 * self.acceptable_starts / self.n_starts


def _longest_hidden(visible: np.ndarray, starts: np.ndarray, length: int) -> np.ndarray:
    """Longest run of hidden samples inside each window [start, start + length)."""
    v = np.asarray(visible, bool)
    edges = np.flatnonzero(np.diff(np.concatenate(([True], v, [True])).astype(np.int8)))
    s, e = edges[0::2], edges[1::2]  # hidden runs [s, e)
    if s.size == 0:
        return np.zeros(len(starts))
    clipped = (np.minimum(e[None, :], (starts + length)[:, None]) - np.maximum(s[None, :], starts[:, None]))
    return np.clip(clipped, 0, None).max(axis=1).astype(float)


def screen(sun_visible: np.ndarray, earth_visible: np.ndarray, n_starts: int, window_samples: int,
           sample_h: float, profile: MissionProfile) -> Screen:
    """Evaluate every start index 0..n_starts-1 on sampled visibility."""
    starts = np.arange(n_starts)
    night = _longest_hidden(sun_visible, starts, window_samples) * sample_h
    gap = _longest_hidden(earth_visible, starts, window_samples) * sample_h
    cs = np.concatenate(([0], np.cumsum(np.asarray(sun_visible, int))))
    sun_pct = 100.0 * (cs[starts + window_samples] - cs[starts]) / window_samples
    ok = (night <= profile.max_night_h) & (sun_pct >= profile.min_sun_pct) & (gap <= profile.max_comms_gap_h)
    if not ok.any():
        return Screen(0, n_starts, None, None, None)
    idx = np.flatnonzero(ok)
    best = idx[np.lexsort((idx, gap[idx], night[idx]))[0]]
    return Screen(int(ok.sum()), n_starts, int(best), float(night[best]), float(gap[best]))


def rank(screens: list[Screen | None]) -> list[int]:
    """Indices of sites with at least one acceptable start, best first."""
    order = [i for i, s in enumerate(screens) if s is not None and s.acceptable_starts > 0]
    return sorted(order, key=lambda i: (-screens[i].acceptable_pct, screens[i].best_night_h, i))


# ---------- the search ----------

MINUTE = timedelta(minutes=config.TIME_STEP_MIN)


def merged_spans(starts: list[datetime], duration_days: float) -> list[tuple[datetime, int]]:
    """Windows [start, start + duration) merged where they overlap or touch:
    (span start, number of 1-minute samples)."""
    n = int(round(duration_days * 24 * 60 / config.TIME_STEP_MIN))
    spans = []
    for start in sorted(set(starts)):
        end = start + n * MINUTE
        if spans and start <= spans[-1][1]:
            spans[-1][1] = max(spans[-1][1], end)
        else:
            spans.append([start, end])
    return [(a, int(round((b - a) / MINUTE))) for a, b in spans]


def window_vectors(source: DirectionSource, starts: list[datetime], duration_days: float) -> dict:
    """1-minute body vectors for each window start, from one source call per merged span.
    Each value equals source.vectors_km(visibility.window_times(start, duration_days))."""
    n = int(round(duration_days * 24 * 60 / config.TIME_STEP_MIN))
    out = {}
    for span_start, count in merged_spans(starts, duration_days):
        vectors = source.vectors_km([span_start + i * MINUTE for i in range(count)])
        span_end = span_start + count * MINUTE
        for start in set(starts):
            if span_start <= start < span_end:
                i = int(round((start - span_start) / MINUTE))
                out[start] = {body: v[i:i + n] for body, v in vectors.items()}
    return out


def parse_period(from_utc: datetime, to_utc: datetime, duration_days: float) -> int:
    """Validate the start period; return the number of start times."""
    if to_utc < from_utc:
        raise ProfileError("to_utc", "must not be before from_utc")
    if to_utc - from_utc > timedelta(days=config.SEARCH_MAX_PERIOD_DAYS):
        raise ProfileError("to_utc", f"the start period is at most {config.SEARCH_MAX_PERIOD_DAYS:g} days")
    check_times([from_utc, to_utc + timedelta(days=duration_days) - timedelta(minutes=1)])
    return int((to_utc - from_utc) / timedelta(hours=config.SEARCH_START_STEP_H)) + 1


def run(terrain: Terrain, source: DirectionSource, from_utc: datetime, to_utc: datetime,
        profile: MissionProfile | dict | None = None, region: dict | None = None,
        cache_folder: Path | None = None, step_m: float = config.SEARCH_GRID_STEP_M,
        store: HorizonStore | None = None) -> dict:
    if not isinstance(profile, MissionProfile):
        profile = MissionProfile.from_dict(profile)
    if profile.observer_height_m != 0.0:
        raise ProfileError("observer_height_m", "the search uses horizons precomputed for 0 m; set 0")
    region = parse_region(region)
    n_starts = parse_period(from_utc, to_utc, profile.duration_days)
    sample_h = config.SEARCH_START_STEP_H
    window_samples = int(round(profile.duration_days * 24.0 / sample_h))

    if store is None:
        def lookup(lat, lon):
            return horizon_mod.cached(terrain, lat, lon, 0.0, folder=cache_folder)
    else:
        lookup = store.get
    sites = sites_in_region(grid_sites(step_m), region)
    profiles, insufficient = [], 0
    for lat, lon in sites:
        try:
            p = lookup(lat, lon)
        except InsufficientData:
            insufficient += 1
            continue
        if p is not None:
            profiles.append(p)
    not_precomputed = len(sites) - len(profiles) - insufficient
    if not profiles and not_precomputed:
        raise SearchUnavailable(
            f"no precomputed horizons for this region ({not_precomputed} grid sites missing). "
            f"Run: python scripts/precompute_horizons.py")

    times = time_grid(from_utc, from_utc + timedelta(hours=sample_h * (n_starts - 1 + window_samples - 1)), sample_h)
    vectors = source.vectors_km(times) if profiles else None
    screens = []
    for p in profiles:
        s = series_from_vectors(vectors, p)
        screens.append(screen(s["sun"].visible, s["earth"].visible, n_starts, window_samples, sample_h, profile))

    candidates = rank(screens)[:config.SEARCH_CANDIDATES]
    starts = {i: from_utc + timedelta(hours=sample_h * screens[i].best_index) for i in candidates}
    minute_vectors = window_vectors(source, list(starts.values()), profile.duration_days)
    top = []
    for i in candidates:
        if len(top) >= config.SEARCH_TOP:
            break
        start = starts[i]
        p = profiles[i]
        result = window_from_vectors(minute_vectors[start], p, start)
        response = build_response(p.lat_deg, p.lon_deg, start, profile, result, p.site_height_m, terrain.path.name)
        if response["status"] == "acceptable":
            top.append({"rank": len(top) + 1, "acceptable_pct": _num(screens[i].acceptable_pct, 1),
                        "acceptable_starts": screens[i].acceptable_starts, "window": response})

    return {
        "data_status": "ok",
        "query": {"from_utc": _iso(from_utc), "to_utc": _iso(to_utc), "start_step_h": _num(sample_h),
                  "region": region, "profile": profile.to_dict()},
        "grid": {"step_km": _num(step_m / 1000), "sites_total": len(sites), "sites_checked": len(profiles),
                 "sites_insufficient_data": insufficient, "sites_not_precomputed": not_precomputed},
        "map": [{"lat_deg": _num(p.lat_deg), "lon_deg": _num(p.lon_deg), "acceptable_pct": _num(s.acceptable_pct, 1)}
                for p, s in zip(profiles, screens)],
        "top": top,
        "note": NOTE,
        "meta": {"model": MODEL, "time_step_min": _num(config.TIME_STEP_MIN)},
    }
