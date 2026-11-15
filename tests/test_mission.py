"""Mission profile, thresholds, status and response format tests."""

import itertools
import json
from datetime import datetime, timedelta

import numpy as np
import pytest

from core import config, mission
from core.datasets import DATASETS, locate
from core.ephemeris import utc
from core.mission import MissionProfile, ProfileError
from core.terrain import InsufficientData
from core.visibility import Interval, WindowMetrics, WindowResult

START = utc(2026, 11, 14)
HAVE_DATA = all(locate(d) is not None for d in DATASETS)
needs_data = pytest.mark.skipif(not HAVE_DATA, reason="terrain or JPL files are not in the data folder")


def metrics(night=10.0, sun=80.0, gap=5.0, down=8.0, comms=90.0):
    return WindowMetrics(night, sun, gap, down, comms)


def result(m, days=14):
    end = START + timedelta(days=days)
    return WindowResult(START, end, 1.0, m, [Interval(START, end, True)], [Interval(START, end, True)])


# ---------- profile ----------

def test_defaults_are_the_approved_example():
    p = MissionProfile.from_dict({})
    assert p == MissionProfile(14.0, "", 40.0, 24.0, 50.0, 0.0)
    assert mission.EXAMPLE_NOTE


def test_missing_fields_take_defaults():
    p = MissionProfile.from_dict({"max_night_h": 60, "vehicle_type": "  lander "})
    assert p.max_night_h == 60.0 and p.duration_days == 14.0 and p.vehicle_type == "lander"


@pytest.mark.parametrize("field,value", [
    ("duration_days", 0.5), ("duration_days", 61), ("max_night_h", -1), ("max_night_h", 14 * 24 + 1),
    ("max_comms_gap_h", -0.1), ("max_comms_gap_h", 400), ("min_sun_pct", -1), ("min_sun_pct", 100.5),
    ("observer_height_m", -1), ("observer_height_m", 101), ("min_sun_pct", float("nan")),
    ("max_night_h", float("inf")), ("vehicle_type", "x" * 41),
])
def test_out_of_range_values_name_the_field(field, value):
    with pytest.raises(ProfileError) as e:
        MissionProfile.from_dict({field: value})
    assert e.value.field == field


@pytest.mark.parametrize("data,field", [
    ({"max_night": 40}, "max_night"), ({"max_night_h": "40"}, "max_night_h"),
    ({"min_sun_pct": True}, "min_sun_pct"), ({"vehicle_type": 5}, "vehicle_type"),
])
def test_wrong_types_and_unknown_fields(data, field):
    with pytest.raises(ProfileError) as e:
        MissionProfile.from_dict(data)
    assert e.value.field == field


def test_night_limit_is_checked_against_the_window_length():
    MissionProfile.from_dict({"duration_days": 2, "max_night_h": 48})
    with pytest.raises(ProfileError):
        MissionProfile.from_dict({"duration_days": 2, "max_night_h": 49})


def test_range_edges_are_accepted():
    MissionProfile.from_dict({"duration_days": 1, "max_night_h": 0, "max_comms_gap_h": 24,
                              "min_sun_pct": 100, "observer_height_m": 100})


# ---------- thresholds and status ----------

@pytest.mark.parametrize("night_ok,sun_ok,gap_ok", list(itertools.product([True, False], repeat=3)))
def test_all_combinations_of_met_and_failed(night_ok, sun_ok, gap_ok):
    m = metrics(night=30 if night_ok else 50, sun=60 if sun_ok else 40, gap=20 if gap_ok else 30)
    c = mission.evaluate(m, MissionProfile())
    assert [x.met for x in c] == [night_ok, sun_ok, gap_ok]
    status, reason = mission.status_of(c)
    if night_ok and sun_ok and gap_ok:
        assert (status, reason) == ("acceptable", "all_met")
    else:
        first = ["max_night_h", "min_sun_pct", "max_comms_gap_h"][[night_ok, sun_ok, gap_ok].index(False)]
        assert (status, reason) == ("not_acceptable", first)


def test_value_exactly_at_the_limit_meets_it():
    c = mission.evaluate(metrics(night=40.0, sun=50.0, gap=24.0), MissionProfile())
    assert all(x.met for x in c) and all(x.margin == 0 for x in c)


def test_margin_is_positive_when_met():
    c = {x.key: x for x in mission.evaluate(metrics(night=30, sun=70, gap=30), MissionProfile())}
    assert c["max_night_h"].margin == 10 and c["min_sun_pct"].margin == 20
    assert c["max_comms_gap_h"].margin == -6 and not c["max_comms_gap_h"].met


def test_thresholds_use_unrounded_values():
    # 40.004 h rounds to 40.0 in the response but still exceeds a 40 h limit
    c = mission.evaluate(metrics(night=40.004), MissionProfile())
    assert not c[0].met


# ---------- explanation ----------

def test_explanation_acceptable():
    text = mission.explanation(mission.evaluate(metrics(night=12.5, sun=78.2, gap=20.0), MissionProfile()))
    assert text == ("Acceptable: longest night 12.5 h (limit 40 h), Sun visible 78.2% (minimum 50%), "
                    "longest comms gap 20.0 h (limit 24 h).")


def test_explanation_lists_the_other_failures():
    text = mission.explanation(mission.evaluate(metrics(night=254.93, sun=24.1, gap=107.28), MissionProfile()))
    assert text == "Not acceptable: longest night is 254.9 h, limit 40 h. Also not met: Sun share, comms gap."


@pytest.mark.parametrize("m,expected", [
    (metrics(sun=30.0), "Not acceptable: Sun is visible 30.0% of the time, minimum 50%."),
    (metrics(gap=30.0), "Not acceptable: longest comms gap is 30.0 h, limit 24 h."),
])
def test_explanation_single_failure(m, expected):
    assert mission.explanation(mission.evaluate(m, MissionProfile())) == expected


def test_explanation_insufficient_data():
    assert mission.explanation(None) == ("Not enough terrain data around this site: "
                                         "the 150 km circle is not fully covered.")


# ---------- response ----------

def test_response_format_and_rounding():
    r = mission.build_response(-89.5, 0.0, START, MissionProfile(),
                               result(metrics(night=12.3456, sun=78.249, gap=20.0, down=30.555, comms=81.06)),
                               -720.62, "terrain.tif")
    assert list(r) == ["data_status", "site", "window", "profile", "metrics", "criteria", "status",
                       "reason_code", "explanation", "intervals", "meta"]
    assert r["data_status"] == "ok" and r["status"] == "acceptable" and r["reason_code"] == "all_met"
    assert r["site"] == {"lat_deg": -89.5, "lon_deg": 0, "height_m": -720.6}
    assert r["window"] == {"start_utc": "2026-11-14T00:00:00Z", "end_utc": "2026-11-28T00:00:00Z"}
    assert r["metrics"] == {"longest_night_h": 12.35, "sun_pct": 78.2, "longest_comms_gap_h": 20,
                            "comms_down_h": 30.55, "comms_pct": 81.1}
    assert r["criteria"][0] == {"key": "max_night_h", "kind": "max", "unit": "h", "limit": 40,
                                "value": 12.35, "margin": 27.65, "met": True}
    assert r["profile"]["max_night_h"] == 40 and r["profile"]["vehicle_type"] == ""
    assert r["intervals"]["sun"] == [{"start_utc": "2026-11-14T00:00:00Z", "end_utc": "2026-11-28T00:00:00Z",
                                      "visible": True}]
    assert r["meta"]["horizon_radius_km"] == 150 and r["meta"]["time_step_min"] == 1
    json.dumps(r)  # plain JSON types only


def test_response_with_insufficient_data():
    r = mission.build_response(-84.0, 0.0, START, MissionProfile(), None, None, "terrain.tif")
    assert r["data_status"] == "insufficient_data" and r["reason_code"] == "insufficient_data"
    assert all(r[k] is None for k in ("metrics", "criteria", "status", "intervals"))
    assert r["site"]["height_m"] is None
    assert "Not enough terrain data" in r["explanation"]
    json.dumps(r)


def test_assess_site_turns_insufficient_data_into_a_response(monkeypatch, tmp_path):
    def no_terrain(*a, **k):
        raise InsufficientData("edge")

    monkeypatch.setattr(mission, "horizon", no_terrain)
    terrain = type("T", (), {"path": tmp_path / "t.tif"})()
    r = mission.assess_site(terrain, source=None, lat_deg=-84.0, lon_deg=0.0, start_utc=START)
    assert r["data_status"] == "insufficient_data" and r["meta"]["terrain"] == "t.tif"


def test_assess_site_rejects_bad_input_before_computing(monkeypatch):
    monkeypatch.setattr(mission, "horizon", lambda *a, **k: pytest.fail("computed despite bad input"))
    with pytest.raises(ProfileError):
        mission.assess_site(None, None, -89.5, 0.0, START, {"min_sun_pct": 120})
    with pytest.raises(ValueError):
        mission.assess_site(None, None, -89.5, 0.0, utc(2050, 12, 25), {})
    with pytest.raises(ValueError):
        mission.assess_site(None, None, -89.5, 0.0, datetime(2026, 11, 14), {})


# ---------- real data ----------

@needs_data
def test_real_site_matches_the_approved_example():
    from core.ephemeris import SkyfieldSource
    from core.terrain import Terrain

    r = mission.assess_site(Terrain(), SkyfieldSource(), -89.5, 0.0, START, {})
    assert r["data_status"] == "ok"
    assert r["site"]["height_m"] == -720.6
    assert r["metrics"] == {"longest_night_h": 254.93, "sun_pct": 24.1, "longest_comms_gap_h": 107.28,
                            "comms_down_h": 120.18, "comms_pct": 64.2}
    assert (r["status"], r["reason_code"]) == ("not_acceptable", "max_night_h")
    assert r["explanation"] == ("Not acceptable: longest night is 254.9 h, limit 40 h. "
                                "Also not met: Sun share, comms gap.")
    assert [c["margin"] for c in r["criteria"]] == [-214.93, -25.9, -83.28]


@needs_data
def test_real_site_outside_the_zone_is_insufficient_data():
    from core.ephemeris import SkyfieldSource
    from core.terrain import Terrain

    r = mission.assess_site(Terrain(), SkyfieldSource(), -84.0, 0.0, START, {})
    assert r["data_status"] == "insufficient_data"
