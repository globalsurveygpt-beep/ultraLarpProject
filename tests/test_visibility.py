"""Visibility tests.

Most tests use a scripted direction source and a hand-made horizon, so the expected
answer is known exactly. The site is (0 N, 0 E): there the local frame is up = +x,
east = +y, north = +z, and a body placed at site + distance * direction is seen at
exactly the scripted altitude and azimuth.
Tests marked `needs_data` use the real LOLA terrain and JPL files.
"""

from datetime import datetime, timedelta

import numpy as np
import pytest

from core import config, ephemeris, visibility
from core.datasets import DATASETS, locate
from core.ephemeris import check_times, utc
from core.horizon import HorizonProfile

R_KM = config.MOON_RADIUS_M / 1000.0
START = utc(2026, 11, 14)
MIN = timedelta(minutes=1)

HAVE_DATA = all(locate(d) is not None for d in DATASETS)
needs_data = pytest.mark.skipif(not HAVE_DATA, reason="terrain or JPL files are not in the data folder")


def flat_profile(elevation=0.0, step=0.5):
    n = int(360 / step)
    return HorizonProfile(0.0, 0.0, 0.0, 0.0, 150_000.0, step, np.full(n, float(elevation)))


class ScriptedSource:
    """Sun and Earth follow altitude/azimuth scripts given per minute from START."""

    def __init__(self, sun, earth):
        self.scripts = {"sun": sun, "earth": earth}

    def vectors_km(self, times_utc):
        check_times(times_utc)
        minutes = np.array([(t - START) / MIN for t in times_utc])
        site = np.array([R_KM, 0.0, 0.0])
        out = {}
        for body, dist in (("sun", 1.5e8), ("earth", 384_400.0)):
            alt, az = self.scripts[body](minutes)
            alt, az = np.radians(np.broadcast_to(alt, minutes.shape)), np.radians(np.broadcast_to(az, minutes.shape))
            d = np.stack([np.sin(alt), np.cos(alt) * np.sin(az), np.cos(alt) * np.cos(az)], axis=-1)
            out[body] = site + dist * d
        return out


def const(alt, az=90.0):
    return lambda m: (np.full(m.shape, float(alt)), np.full(m.shape, float(az)))


def up_except(hidden_ranges, az=90.0):
    """+1 degree, except -1 degree inside the given [from, to) minute ranges."""
    def script(m):
        alt = np.ones(m.shape)
        for a, b in hidden_ranges:
            alt[(m >= a) & (m < b)] = -1.0
        return alt, np.full(m.shape, az)
    return script


def run(sun, earth, days=1.0, profile=None):
    return visibility.window(ScriptedSource(sun, earth), profile or flat_profile(), START, days)


# ---------- metrics ----------

def test_always_visible():
    r = run(const(5), const(5))
    assert r.metrics == visibility.WindowMetrics(0.0, 100.0, 0.0, 0.0, 100.0)
    assert len(r.sun_intervals) == 1 and r.sun_intervals[0].visible
    assert r.sun_intervals[0].duration_h == 24.0


def test_never_visible():
    r = run(const(-5), const(-5), days=2)
    assert r.metrics == visibility.WindowMetrics(48.0, 0.0, 48.0, 48.0, 0.0)


def test_one_night_of_known_length():
    r = run(up_except([(100, 400)]), const(5))
    assert r.metrics.longest_night_h == 5.0
    assert r.metrics.sun_pct == pytest.approx(100 * (1440 - 300) / 1440)
    night = [i for i in r.sun_intervals if not i.visible]
    assert len(night) == 1
    assert night[0].start_utc == START + 100 * MIN and night[0].end_utc == START + 400 * MIN


def test_night_that_began_before_the_window_counts_from_its_start():
    r = run(up_except([(-500, 120)]), const(5))
    assert r.metrics.longest_night_h == 2.0
    assert r.sun_intervals[0].start_utc == START and not r.sun_intervals[0].visible


def test_night_running_past_the_window_end_is_cut_there():
    r = run(up_except([(1440 - 90, 5000)]), const(5))
    assert r.metrics.longest_night_h == 1.5
    assert r.sun_intervals[-1].end_utc == r.end_utc == START + timedelta(days=1)


def test_short_glimpse_of_the_sun_splits_the_night():
    # dark for minutes 100-700, except 3 minutes of light at 300-303
    r = run(up_except([(100, 300), (303, 700)]), const(5))
    assert r.metrics.longest_night_h == pytest.approx(397 / 60)
    glimpse = [i for i in r.sun_intervals if i.visible and i.duration_h < 1]
    assert len(glimpse) == 1 and glimpse[0].duration_h == pytest.approx(3 / 60)


def test_comms_metrics_use_the_earth():
    r = run(const(5), up_except([(0, 60), (600, 780)]))
    m = r.metrics
    assert (m.longest_night_h, m.sun_pct) == (0.0, 100.0)
    assert m.longest_comms_gap_h == 3.0
    assert m.comms_down_h == 4.0
    assert m.comms_pct == pytest.approx(100 * (1440 - 240) / 1440)


def test_intervals_cover_the_window_and_alternate():
    r = run(up_except([(10, 20), (500, 900), (1300, 1440)]), up_except([(0, 5)]))
    for ivs in (r.sun_intervals, r.earth_intervals):
        assert ivs[0].start_utc == r.start_utc and ivs[-1].end_utc == r.end_utc
        for a, b in zip(ivs, ivs[1:]):
            assert a.end_utc == b.start_utc and a.visible != b.visible
        assert sum(i.duration_h for i in ivs) == pytest.approx(24.0)


# ---------- margin and horizon ----------

def test_margin_is_altitude_minus_horizon():
    r = visibility.instant(ScriptedSource(const(2.0, az=45.0), const(1.0, az=200.0)), flat_profile(1.5), START)
    assert r["sun"].margin_deg == pytest.approx(0.5) and r["sun"].visible
    assert r["sun"].az_deg == pytest.approx(45.0) and r["sun"].horizon_deg == 1.5
    assert r["earth"].margin_deg == pytest.approx(-0.5) and not r["earth"].visible


def test_body_exactly_on_the_horizon_is_not_visible():
    s = visibility.series_from_vectors({"sun": np.array([[R_KM, 1.5e8, 0.0]]), "earth": np.array([[R_KM, 4e5, 0.0]])},
                                       flat_profile(0.0))
    assert s["sun"].margin_deg[0] == 0.0 and not s["sun"].visible[0]


def test_horizon_step_at_a_sector_boundary():
    # Sun at 2 degrees drifting east from azimuth 10.2 to 10.8 over an hour; the horizon
    # is 1 degree in sector [10.0, 10.5) and 3 degrees in [10.5, 11.0)
    profile = flat_profile(0.0)
    profile.elevation_deg[20], profile.elevation_deg[21] = 1.0, 3.0
    sun = lambda m: (np.full(m.shape, 2.0), 10.2 + 0.01 * np.clip(m, 0, 60))
    r = run(sun, const(5), profile=profile)
    assert not r.sun_intervals[-1].visible
    crossing = r.sun_intervals[0].end_utc  # azimuth reaches 10.5 at minute 30
    assert abs((crossing - (START + 30 * MIN)).total_seconds()) <= 60


def test_site_height_reaches_the_ephemeris():
    # the Earth on the western horizon of the sphere is lifted into view for a raised observer
    earth = {"sun": np.array([[1.5e8, 0.0, 0.0]]), "earth": np.array([[R_KM, -384_400.0, 0.0]])}
    low = visibility.series_from_vectors(earth, flat_profile(0.0))
    high_profile = HorizonProfile(0.0, 0.0, 800.0, 200.0, 150_000.0, 0.5, np.zeros(720))
    high = visibility.series_from_vectors(earth, high_profile)
    assert low["earth"].alt_deg[0] == pytest.approx(0.0, abs=1e-9)
    assert high["earth"].alt_deg[0] < 0  # the observer moved up by 1 km, the target sinks


# ---------- window and day series ----------

def test_window_times_exclude_the_end():
    t = visibility.window_times(START, 1.0)
    assert len(t) == 1440 and t[0] == START and t[-1] == START + 1439 * MIN


@pytest.mark.parametrize("days", [0.5, 60.5, -1])
def test_window_length_limits(days):
    with pytest.raises(ValueError):
        visibility.window_times(START, days)


def test_window_must_stay_inside_the_date_range():
    with pytest.raises(ValueError):
        visibility.window_times(utc(2050, 12, 25), 14)
    with pytest.raises(ValueError):
        visibility.window_times(datetime(2026, 11, 14), 1)  # naive datetime


def test_day_series_has_145_points_with_both_ends():
    d = visibility.day_series(ScriptedSource(const(3), const(-3)), flat_profile(1.0), START)
    assert len(d.times_utc) == 145
    assert d.times_utc[0] == START and d.times_utc[-1] == START + timedelta(days=1)
    assert d.times_utc[1] - d.times_utc[0] == timedelta(minutes=10)
    assert np.allclose(d.sun.margin_deg, 2.0) and d.sun.visible.all()
    assert np.allclose(d.earth.margin_deg, -4.0) and not d.earth.visible.any()


# ---------- real data ----------

@needs_data
def test_real_window_is_self_consistent():
    from core.horizon import horizon
    from core.terrain import Terrain

    source = ephemeris.SkyfieldSource()
    profile = horizon(Terrain(), -89.5, 0.0)
    r = visibility.window(source, profile, START, 14)
    m = r.metrics
    total_h = 14 * 24.0
    for ivs, pct in ((r.sun_intervals, m.sun_pct), (r.earth_intervals, m.comms_pct)):
        assert sum(i.duration_h for i in ivs) == pytest.approx(total_h)
        assert sum(i.duration_h for i in ivs if i.visible) == pytest.approx(pct / 100 * total_h)
    assert m.comms_down_h == pytest.approx((100 - m.comms_pct) / 100 * total_h)
    assert 0 <= m.longest_night_h <= total_h and m.longest_comms_gap_h <= m.comms_down_h + 1e-9
    # a single moment agrees with the window series
    moment = START + timedelta(days=3, hours=7)
    state = visibility.instant(source, profile, moment)
    in_light = any(i.visible and i.start_utc <= moment < i.end_utc for i in r.sun_intervals)
    assert state["sun"].visible == in_light
