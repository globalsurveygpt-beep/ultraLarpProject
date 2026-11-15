"""Nearest acceptable window for one site: screening, minute check, search period, API."""

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from api import main as api_main
from core import mission, nextwin
from core.ephemeris import check_times
from tests.test_api import FakeTerrain, Server, fake_horizon

T0 = datetime(2026, 12, 1, tzinfo=timezone.utc)
PROFILE = {"duration_days": 2, "max_night_h": 40, "max_comms_gap_h": 24, "min_sun_pct": 50}


class EarthAfter:
    """Sun always overhead of a south-polar site; the Earth overhead only from `t_on` on
    (before that it is far above the north pole, hidden)."""

    def __init__(self, t_on):
        self.t_on = t_on

    def vectors_km(self, times_utc):
        check_times(times_utc)
        n = len(times_utc)
        on = np.array([t >= self.t_on for t in times_utc]) if self.t_on else np.zeros(n, bool)
        earth = np.where(on[:, None], [0.0, 0.0, -3.844e5], [0.0, 0.0, 3.844e5])
        return {"sun": np.tile([0.0, 0.0, -1.5e8], (n, 1)), "earth": earth}


@pytest.fixture(autouse=True)
def flat_horizon(monkeypatch):
    monkeypatch.setattr(nextwin, "horizon", fake_horizon)
    monkeypatch.setattr(mission, "horizon", fake_horizon)


def test_first_acceptable_start_is_found_and_checked_at_one_minute():
    t_on = T0 + timedelta(days=10)
    r = nextwin.next_window(FakeTerrain(), EarthAfter(t_on), -89.5, 0.0, T0, PROFILE)
    assert r["data_status"] == "ok" and r["found"] is True
    w = r["window"]
    # The comms gap may be 24 h: the first acceptable start is 24 h before the Earth appears.
    assert w["window"]["start_utc"] == mission._iso(t_on - timedelta(hours=24))
    assert w["status"] == "acceptable" and w["metrics"]["longest_comms_gap_h"] == 24
    assert r["query"]["from_utc"] == "2026-12-01T00:00:00Z" and r["query"]["start_step_h"] == 1


def test_an_acceptable_first_window_is_returned_as_is():
    r = nextwin.next_window(FakeTerrain(), EarthAfter(T0 - timedelta(days=1)), -89.5, 0.0, T0, PROFILE)
    assert r["found"] and r["window"]["window"]["start_utc"] == "2026-12-01T00:00:00Z"


def test_no_window_reports_the_searched_period():
    r = nextwin.next_window(FakeTerrain(), EarthAfter(None), -89.5, 0.0, T0, PROFILE)
    assert r["data_status"] == "ok" and r["found"] is False and r["window"] is None
    last = datetime.fromisoformat(r["query"]["to_utc"].replace("Z", "+00:00"))
    assert last - T0 == timedelta(hours=int(365.25 * 5 * 24))
    assert r["query"]["years"] == 5


def test_the_period_stops_where_the_last_window_still_fits_the_supported_dates():
    start = datetime(2049, 6, 1, tzinfo=timezone.utc)
    last, n = nextwin.search_period(start, 14.0)
    assert last + timedelta(days=14) <= datetime(2051, 1, 1, tzinfo=timezone.utc)
    assert n == int((last - start) / timedelta(hours=1)) + 1


def test_outside_the_terrain_is_a_normal_response():
    r = nextwin.next_window(FakeTerrain(), EarthAfter(None), -84.0, 0.0, T0, PROFILE)
    assert r["data_status"] == "insufficient_data" and r["found"] is False


def test_bad_input_is_rejected():
    with pytest.raises(ValueError):
        nextwin.next_window(FakeTerrain(), EarthAfter(None), -95.0, 0.0, T0, PROFILE)
    with pytest.raises(ValueError):
        nextwin.next_window(FakeTerrain(), EarthAfter(None), -89.5, 0.0, datetime(1999, 1, 1, tzinfo=timezone.utc), PROFILE)


def test_next_window_endpoint():
    source = EarthAfter(T0 + timedelta(days=3))
    with Server(api_main.create_app(FakeTerrain(), source, load_data=False)) as s:
        status, r = s.call("/api/next-window", {"lat_deg": -89.5, "lon_deg": 0, "from_utc": "2026-12-01T00:00:00Z",
                                                "profile": PROFILE})
        assert status == 200 and r["found"] is True
        assert set(r) == {"data_status", "site", "query", "found", "window", "note", "meta"}
        assert r["window"]["window"]["start_utc"] == "2026-12-03T00:00:00Z"
        status, r = s.call("/api/next-window", {"lat_deg": -89.5, "lon_deg": 0, "from_utc": "2026-12-01T00:00:00Z",
                                                "profile": {"max_night_h": -1}})
        assert status == 400 and r["error"]["field"] == "max_night_h"
