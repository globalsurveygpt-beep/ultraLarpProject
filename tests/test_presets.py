"""The example scenarios on the site page (web/assets/presets.json) give the outcomes they promise.

Each preset is checked on the real terrain and ephemeris at 1-minute resolution, the same way
the interface computes it, so a change in the data or the core that breaks a scenario fails here.
"""

import json
from datetime import datetime
from pathlib import Path

import pytest

from core import mission, nextwin
from core.datasets import DATASETS, locate

PRESETS = Path(__file__).resolve().parents[1] / "web" / "assets" / "presets.json"
HAVE_DATA = all(locate(d) is not None for d in DATASETS)
needs_data = pytest.mark.skipif(not HAVE_DATA, reason="terrain or JPL files are not in the data folder")

with PRESETS.open(encoding="utf-8") as f:
    DATA = json.load(f)
START = datetime.fromisoformat(DATA["start_utc"].replace("Z", "+00:00"))


def test_four_distinct_scenarios_on_one_profile():
    ids = [s["id"] for s in DATA["scenarios"]]
    assert ids == ["A", "B", "C", "D"]
    assert "not recommendations" in DATA["note"]
    mission.MissionProfile.from_dict(DATA["profile"])          # a valid profile
    assert START.minute == 0 and START.year == 2026


@pytest.fixture(scope="module")
def real():
    from core.ephemeris import SkyfieldSource
    from core.terrain import Terrain
    t = Terrain()
    yield t, SkyfieldSource()
    t.close()


@needs_data
@pytest.mark.parametrize("scenario", DATA["scenarios"], ids=lambda s: s["id"])
def test_scenario_outcome(real, scenario):
    terrain, source = real
    expect = scenario["expect"]
    r = mission.assess_site(terrain, source, scenario["lat_deg"], scenario["lon_deg"], START, DATA["profile"])
    assert r["data_status"] == "ok"
    assert r["status"] == expect["status"] and r["reason_code"] == expect["reason_code"]
    failed = [c["key"] for c in r["criteria"] if not c["met"]]
    assert failed == expect.get("failed", [])
    if "next_window_start_utc" in expect:
        n = nextwin.next_window(terrain, source, scenario["lat_deg"], scenario["lon_deg"], START, DATA["profile"])
        assert n["found"] is True
        assert n["window"]["status"] == "acceptable"
        assert n["window"]["window"]["start_utc"] == expect["next_window_start_utc"]
