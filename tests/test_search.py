"""Inverse search tests.

Screening is checked against a plain loop on random visibility. The end-to-end tests use a
stand-in horizon cache (by longitude: a flat horizon east of 0, a wall to the west,
"insufficient data" or "not computed" for chosen sites) and a scripted direction source:
the Sun and the Earth far below the south pole, which polar sites see high in the sky,
unless the script hides the Sun by moving it above the north pole.
"""

from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from core import config, horizon as horizon_mod, mission, search
from core.datasets import DATASETS, locate
from core.ephemeris import check_times, utc
from core.horizon import HorizonProfile
from core.mission import MissionProfile, ProfileError
from core.terrain import InsufficientData

START = utc(2026, 11, 14)
HAVE_DATA = all(locate(d) is not None for d in DATASETS)
needs_data = pytest.mark.skipif(not HAVE_DATA, reason="terrain or JPL files are not in the data folder")


class FakeTerrain:
    path = Path("fake_terrain.tif")


class ScriptedSource:
    """Sun hidden in the minutes where `sun_hidden(minutes)` is True; Earth always visible."""

    def __init__(self, sun_hidden=lambda m: np.zeros(m.shape, bool)):
        self.sun_hidden = sun_hidden
        self.calls = 0

    def vectors_km(self, times_utc):
        check_times(times_utc)
        self.calls += 1
        m = np.array([(t - START) / timedelta(minutes=1) for t in times_utc])
        below = np.array([0.0, 0.0, -1.5e8])
        sun = np.where(self.sun_hidden(m)[:, None], -below, below)
        return {"sun": sun, "earth": np.tile([0.0, 0.0, -3.844e5], (len(m), 1))}


def fake_cache(missing=(), insufficient=()):
    def cached(terrain, lat, lon, observer_height_m=0.0, folder=None, **kw):
        if (lat, lon) in missing:
            return None
        if (lat, lon) in insufficient:
            raise InsufficientData("fake")
        wall = 89.9 if lon < 0 else 0.0
        return HorizonProfile(lat, lon, 0.0, 0.0, 150_000.0, 0.5, np.full(720, wall))
    return cached


COARSE = 50_000.0  # 50 km grid: a few dozen sites keep the tests quick
PERMISSIVE = {"duration_days": 2}


def run(monkeypatch, source=None, profile=PERMISSIVE, region=None, days=3, **cache):
    monkeypatch.setattr(horizon_mod, "cached", fake_cache(**cache))
    return search.run(FakeTerrain(), source or ScriptedSource(), START, START + timedelta(days=days),
                      profile, region, step_m=COARSE)


# ---------- grid and region ----------

def test_five_km_grid():
    sites = search.grid_sites()
    assert len(sites) == 2981
    assert (-90.0, 0.0) in sites
    assert len(set(sites)) == len(sites)
    R = config.MOON_RADIUS_M
    dist = [np.radians(lat + 90) * R for lat, _ in sites]
    assert max(dist) <= config.WORKING_ZONE_RADIUS_M + 1e-3
    assert search.grid_sites() == sites  # deterministic: precompute and search build the same keys


def test_circle_region():
    sites = search.grid_sites()
    region = search.parse_region({"kind": "circle", "lat_deg": -89.5, "lon_deg": 0, "radius_km": 20})
    inside = search.sites_in_region(sites, region)
    assert 30 < len(inside) < 70  # about pi * 20^2 / 25 = 50 sites
    assert search.sites_in_region(sites, {"kind": "zone"}) == sites


@pytest.mark.parametrize("region,field", [
    ({"kind": "square"}, "region"), ({"kind": "circle", "lat_deg": -89, "lon_deg": 0}, "radius_km"),
    ({"kind": "circle", "lat_deg": -89, "lon_deg": 0, "radius_km": 0}, "radius_km"),
    ({"kind": "circle", "lat_deg": -95, "lon_deg": 0, "radius_km": 5}, "lat_deg"),
    ({"kind": "circle", "lat_deg": -89, "lon_deg": 0, "radius_km": 5, "x": 1}, "x"),
    ({"kind": "zone", "radius_km": 5}, "region"),
])
def test_bad_regions(region, field):
    with pytest.raises(ProfileError) as e:
        search.parse_region(region)
    assert e.value.field == field


# ---------- screening ----------

def brute(sun, earth, n_starts, w, profile):
    ok, nights, gaps = [], [], []
    for i in range(n_starts):
        def longest(v):
            best = cur = 0
            for x in v[i:i + w]:
                cur = 0 if x else cur + 1
                best = max(best, cur)
            return best
        night, gap, pct = longest(sun), longest(earth), 100 * sun[i:i + w].mean()
        nights.append(night), gaps.append(gap)
        ok.append(night <= profile.max_night_h and pct >= profile.min_sun_pct and gap <= profile.max_comms_gap_h)
    return np.array(ok), np.array(nights), np.array(gaps)


@pytest.mark.parametrize("seed", range(6))
def test_screen_matches_a_plain_loop(seed):
    rng = np.random.default_rng(seed)
    w, n_starts = 48, 60
    # runs of a few hours, like real visibility
    sun = np.repeat(rng.random(40) < 0.6, rng.integers(1, 12, 40))[:n_starts + w - 1]
    earth = np.repeat(rng.random(40) < 0.7, rng.integers(1, 12, 40))[:n_starts + w - 1]
    if len(sun) < n_starts + w - 1 or len(earth) < n_starts + w - 1:
        pytest.skip("random runs too short")
    profile = MissionProfile(duration_days=2, max_night_h=8, max_comms_gap_h=6, min_sun_pct=50)
    s = search.screen(sun, earth, n_starts, w, 1.0, profile)
    ok, nights, gaps = brute(sun, earth, n_starts, w, profile)
    assert s.acceptable_starts == ok.sum()
    if ok.any():
        idx = np.flatnonzero(ok)
        best = min(idx, key=lambda i: (nights[i], gaps[i], i))
        assert s.best_index == best and s.best_night_h == nights[best] and s.best_gap_h == gaps[best]
    else:
        assert s.best_index is None


def test_best_start_prefers_short_night_then_short_gap_then_earliest():
    # four starts with window length 1: nights come from the Sun, gaps from the Earth
    sun = np.array([False, True, True, True])
    earth = np.array([True, False, True, True])
    profile = MissionProfile(duration_days=2, max_night_h=48, max_comms_gap_h=48, min_sun_pct=0)
    s = search.screen(sun, earth, 4, 1, 1.0, profile)
    assert s.acceptable_starts == 4 and s.best_index == 2  # 0 has a night, 1 a gap, 2 beats 3 by order


def test_rank_orders_by_share_then_night():
    S = search.Screen
    screens = [S(10, 100, 0, 5.0, 0.0), S(30, 100, 0, 9.0, 0.0), None, S(30, 100, 0, 2.0, 0.0), S(0, 100, None, None, None)]
    assert search.rank(screens) == [3, 1, 0]


# ---------- end to end with stand-ins ----------

def test_search_separates_open_and_walled_sites(monkeypatch):
    r = run(monkeypatch)
    by_lon = {(m["lat_deg"], m["lon_deg"]): m["acceptable_pct"] for m in r["map"]}
    assert all(pct == 100 for (lat, lon), pct in by_lon.items() if lon >= 0)
    assert all(pct == 0 for (lat, lon), pct in by_lon.items() if lon < 0)
    assert r["grid"]["sites_checked"] == len(r["map"]) == r["grid"]["sites_total"]
    assert 0 < len(r["top"]) <= config.SEARCH_TOP
    assert [t["rank"] for t in r["top"]] == list(range(1, len(r["top"]) + 1))
    for t in r["top"]:
        assert t["window"]["status"] == "acceptable" and t["window"]["site"]["lon_deg"] >= 0
        assert t["acceptable_starts"] == 73  # every hourly start in 3 days: 72 + 1
    assert r["note"] == search.NOTE
    assert r["query"]["region"] == {"kind": "zone"} and r["query"]["start_step_h"] == 1


def test_refinement_drops_windows_that_fail_at_one_minute(monkeypatch):
    # the Sun is hidden for 59 minutes of every hour: hourly samples never see it set
    source = ScriptedSource(lambda m: (m % 60) != 0)
    r = run(monkeypatch, source, profile={"duration_days": 2, "max_night_h": 0.5, "min_sun_pct": 0})
    assert any(m["acceptable_pct"] == 100 for m in r["map"])
    assert r["top"] == []


def test_minute_vectors_are_shared_between_candidates(monkeypatch):
    source = ScriptedSource()
    run(monkeypatch, source)
    assert source.calls == 2  # one hourly call for screening, one minute call for the shared best start


def test_sites_without_data_are_counted(monkeypatch):
    sites = search.grid_sites(COARSE)
    r = run(monkeypatch, missing={sites[0]}, insufficient={sites[1], sites[2]})
    g = r["grid"]
    assert (g["sites_not_precomputed"], g["sites_insufficient_data"]) == (1, 2)
    assert g["sites_checked"] == g["sites_total"] - 3 == len(r["map"])


def test_nothing_precomputed_is_unavailable(monkeypatch):
    with pytest.raises(search.SearchUnavailable):
        run(monkeypatch, missing=set(search.grid_sites(COARSE)))


def test_region_with_only_insufficient_sites_is_an_empty_result(monkeypatch):
    r = run(monkeypatch, insufficient=set(search.grid_sites(COARSE)))
    assert r["map"] == [] and r["top"] == [] and r["grid"]["sites_checked"] == 0


@pytest.mark.parametrize("profile,days,field", [
    ({"duration_days": 2, "observer_height_m": 2}, 3, "observer_height_m"),
    (PERMISSIVE, 91, "to_utc"),
    (PERMISSIVE, -1, "to_utc"),
])
def test_bad_queries(monkeypatch, profile, days, field):
    with pytest.raises(ProfileError) as e:
        run(monkeypatch, profile=profile, days=days)
    assert e.value.field == field


def test_period_must_end_inside_the_date_range(monkeypatch):
    monkeypatch.setattr(horizon_mod, "cached", fake_cache())
    with pytest.raises(ValueError):
        search.run(FakeTerrain(), ScriptedSource(), utc(2050, 12, 20), utc(2050, 12, 25), {"duration_days": 14})


# ---------- in-memory horizons and shared minute vectors ----------

def test_merged_spans():
    d = timedelta(days=2)
    starts = [START + timedelta(days=5), START, START + timedelta(days=1), START + d]
    spans = search.merged_spans(starts, 2)
    # START..+2d, +1d..+3d and +2d..+4d overlap or touch; +5d stands alone
    assert spans == [(START, 4 * 24 * 60), (START + timedelta(days=5), 2 * 24 * 60)]


class RecordingSource(ScriptedSource):
    def __init__(self):
        super().__init__(lambda m: (m // 97) % 3 == 0)
        self.points = 0

    def vectors_km(self, times_utc):
        self.points += len(times_utc)
        return super().vectors_km(times_utc)


def test_window_vectors_equal_direct_calls():
    from core.visibility import window_times
    starts = [START + timedelta(hours=h) for h in (0, 5, 5, 30, 200)]
    source = RecordingSource()
    shared = search.window_vectors(source, starts, 2)
    assert source.calls == 2  # 0, 5 and 30 h overlap; 200 h does not
    assert source.points == (30 * 60 + 2 * 1440) + 2 * 1440
    for start in starts:
        direct = ScriptedSource(source.sun_hidden).vectors_km(window_times(start, 2))
        assert set(shared[start]) == set(direct)
        for body in direct:
            assert np.array_equal(shared[start][body], direct[body])


def test_store_keeps_found_sites_and_rechecks_missing_ones(monkeypatch):
    reads = []
    inner = fake_cache(missing={(-89.0, 0.0)}, insufficient={(-88.0, 0.0)})

    def counting(terrain, lat, lon, *a, **k):
        reads.append((lat, lon))
        return inner(terrain, lat, lon, *a, **k)

    monkeypatch.setattr(horizon_mod, "cached", counting)
    store = search.HorizonStore(FakeTerrain())
    for _ in range(3):
        assert store.get(-87.0, 0.0).lat_deg == -87.0
        assert store.get(-89.0, 0.0) is None
        with pytest.raises(InsufficientData):
            store.get(-88.0, 0.0)
    assert reads.count((-87.0, 0.0)) == 1 and reads.count((-88.0, 0.0)) == 1
    assert reads.count((-89.0, 0.0)) == 3  # not computed yet: looked up again each time
    assert len(store) == 2


def test_search_with_store_matches_search_without(monkeypatch):
    monkeypatch.setattr(horizon_mod, "cached", fake_cache(insufficient={search.grid_sites(COARSE)[1]}))
    source = ScriptedSource(lambda m: (m // 300) % 4 == 0)
    store = search.HorizonStore(FakeTerrain())
    assert store.preload(COARSE) == len(search.grid_sites(COARSE))
    args = (FakeTerrain(), source, START, START + timedelta(days=3), PERMISSIVE, None)
    plain = search.run(*args, step_m=COARSE)
    held = search.run(*args, step_m=COARSE, store=store)
    assert held == plain and plain["grid"]["sites_insufficient_data"] == 1


# ---------- real data ----------

@needs_data
def test_real_search_agrees_with_single_window_assessment():
    from core.ephemeris import SkyfieldSource
    from core.terrain import Terrain

    terrain, source = Terrain(), SkyfieldSource()
    region = {"kind": "circle", "lat_deg": -89.5, "lon_deg": 0.0, "radius_km": 6}
    for lat, lon in search.sites_in_region(search.grid_sites(), search.parse_region(region)):
        horizon_mod.horizon(terrain, lat, lon, 0.0)  # make sure these few are in the cache
    profile = {"duration_days": 3, "max_night_h": 72, "max_comms_gap_h": 72, "min_sun_pct": 0}
    r = search.run(terrain, source, START, START + timedelta(days=2), profile, region)
    assert r["grid"]["sites_checked"] == r["grid"]["sites_total"] > 0
    assert r["top"]
    for t in r["top"]:
        w = t["window"]
        start = datetime.fromisoformat(w["window"]["start_utc"].replace("Z", "+00:00"))
        single = mission.assess_site(terrain, source, w["site"]["lat_deg"], w["site"]["lon_deg"], start, profile)
        assert single["metrics"] == w["metrics"] and single["status"] == "acceptable"
