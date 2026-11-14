"""Ephemeris tests.

Tests marked `needs_data` use the JPL files and are skipped when the data folder does not
hold them (the development machine). They run on the server after scripts/fetch_data.py.
"""

import json
from datetime import datetime, timezone

import numpy as np
import pytest

from core import config, ephemeris
from core.datasets import path_of
from core.ephemeris import Direction, check_times, local_directions, time_grid, utc

HAVE_DATA = all(path_of(k).is_file() for k in ephemeris.KERNEL_KEYS)
needs_data = pytest.mark.skipif(not HAVE_DATA, reason="JPL kernel files are not in the data folder")

FIXTURE = config.REPO_ROOT / "tests" / "fixtures" / "horizons_reference.json"
EARTH_KM = 384_400.0
R_KM = config.MOON_RADIUS_M / 1000.0


class FakeSource:
    """Fixed body vectors, for testing the plumbing without JPL files."""

    def __init__(self, sun, earth):
        self.sun, self.earth = np.asarray(sun, float), np.asarray(earth, float)

    def vectors_km(self, times_utc):
        check_times(times_utc)
        n = len(times_utc)
        return {"sun": np.tile(self.sun, (n, 1)), "earth": np.tile(self.earth, (n, 1))}


# ---------- times ----------

def test_time_grid_includes_both_ends():
    g = time_grid(utc(2026, 11, 14), utc(2026, 11, 15))
    assert len(g) == 25 and g[0] == utc(2026, 11, 14) and g[-1] == utc(2026, 11, 15)


def test_naive_times_are_rejected():
    with pytest.raises(ValueError):
        check_times([datetime(2026, 1, 1)])


@pytest.mark.parametrize("t", [utc(1999, 12, 31, 23), utc(2051, 1, 1)])
def test_dates_outside_2000_2050_are_rejected(t):
    with pytest.raises(ValueError):
        check_times([t])


@pytest.mark.parametrize("t", [utc(2000, 1, 1), utc(2050, 12, 31, 23, 59)])
def test_range_edges_are_accepted(t):
    check_times([t])


# ---------- site geometry ----------

def test_earth_parallax_is_applied():
    # Earth far along +x. From (0 N, 0 E) it is at the zenith; from (0 N, 90 E) it sits
    # on the western horizon, lowered by the parallax angle atan(R / d).
    src = FakeSource(sun=[1.5e8, 0, 0], earth=[EARTH_KM, 0, 0])
    t = [utc(2026, 11, 14)]
    at_zenith = ephemeris.directions(src, t, 0.0, 0.0)["earth"]
    assert at_zenith.alt_deg[0] == pytest.approx(90.0)
    side = ephemeris.directions(src, t, 0.0, 90.0)["earth"]
    assert side.az_deg[0] == pytest.approx(270.0)
    assert side.alt_deg[0] == pytest.approx(-np.degrees(np.arctan2(R_KM, EARTH_KM)), rel=1e-9)
    assert side.alt_deg[0] == pytest.approx(-0.259, abs=0.001)


def test_height_lowers_a_horizon_target():
    src = FakeSource(sun=[1.5e8, 0, 0], earth=[EARTH_KM, 0, 0])
    t = [utc(2026, 11, 14)]
    low = ephemeris.directions(src, t, 0.0, 90.0, height_m=0)["earth"].alt_deg[0]
    high = ephemeris.directions(src, t, 0.0, 90.0, height_m=5000)["earth"].alt_deg[0]
    assert high < low


def test_many_sites_match_single_site_results():
    vec = {"sun": np.array([[1.4e8, 3e7, -2e6], [1.4e8, -3e7, 1e6]]),
           "earth": np.array([[3.5e5, 1e5, -2e4], [3.6e5, -9e4, 1e4]])}
    lats, lons = np.array([-89.5, -86.0]), np.array([0.0, 120.0])
    many = local_directions(vec, lats[:, None], lons[:, None], height_m=np.array([0.0, 300.0])[:, None])
    for i in range(2):
        one = local_directions(vec, lats[i], lons[i], height_m=[0.0, 300.0][i])
        for body in ("sun", "earth"):
            assert np.allclose(many[body].alt_deg[i], one[body].alt_deg)
            assert np.allclose(many[body].az_deg[i], one[body].az_deg)
    assert isinstance(many["sun"], Direction)


# ---------- reference fixture ----------

def test_reference_fixture_is_complete():
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    rows = data["rows"]
    assert len(rows) == 54
    assert {r["body"] for r in rows} == {"sun", "earth"}
    assert len({(r["site"], r["body"], r["time_utc"]) for r in rows}) == 54
    for r in rows:
        assert -90 <= r["el_deg"] <= 90 and 0 <= r["az_deg"] < 360
        t = datetime.strptime(r["time_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        check_times([t])


# ---------- with JPL files (server) ----------

@pytest.fixture(scope="module")
def source():
    return ephemeris.SkyfieldSource()


@needs_data
def test_frame_and_radius_load(source):
    assert source.frame is not None


@needs_data
def test_orientation_segment_covers_the_date_range(source):
    lo = source.ts.from_datetime(config.DATE_MIN_UTC).tdb
    hi = source.ts.from_datetime(config.DATE_END_UTC).tdb
    assert source.segment.initial_jd <= lo and hi <= source.segment.final_jd


@needs_data
@pytest.mark.parametrize("t", [utc(2000, 1, 1), utc(2050, 12, 31, 23, 59)])
def test_directions_work_at_both_range_edges(source, t):
    d = ephemeris.directions(source, [t], -89.5, 0.0)
    for body in ("sun", "earth"):
        assert np.all(np.isfinite(d[body].alt_deg)) and np.all(np.isfinite(d[body].az_deg))


@needs_data
def test_agreement_with_horizons(source):
    from scripts.verify_ephemeris import TOLERANCE_DEG, compare
    rows = compare(source)
    worst = max(r["sep_deg"] for r in rows)
    assert worst <= TOLERANCE_DEG, f"largest difference {worst:.6f} deg"


@needs_data
@pytest.mark.parametrize("lat,lon", [(-89.5, 0.0), (-85.5, 90.0), (-87.0, -120.0)])
def test_own_geometry_matches_skyfield_altaz(source, lat, lon):
    times = time_grid(utc(2026, 11, 14), utc(2026, 11, 15), step_h=6)
    ours = ephemeris.directions(source, times, lat, lon)
    t = source.ts.from_datetimes(times)
    site = source.eph["moon"] + source.constants.build_latlon_degrees(source.frame, lat, lon)
    from core.geometry import angular_separation_deg
    for body in ("sun", "earth"):
        alt, az, _ = site.at(t).observe(source.eph[body]).apparent().altaz()
        sep = angular_separation_deg(ours[body].alt_deg, ours[body].az_deg, alt.degrees, az.degrees)
        assert np.max(sep) < 0.001
