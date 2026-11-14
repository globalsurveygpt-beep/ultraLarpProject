"""API tests over real HTTP: uvicorn runs on a free localhost port and the tests call it
with urllib, so no extra test client library is needed.

Plumbing tests use a stand-in horizon (flat, or "insufficient data" north of -85) and a
fixed direction source: the Sun far below the south pole (visible from polar sites),
the Earth far above the north pole (hidden).
"""

import json
import socket
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np
import pytest
import uvicorn

from api import main as api_main
from core import config, mission
from core.datasets import DATASETS
from core.ephemeris import check_times
from core.horizon import HorizonProfile
from core.terrain import InsufficientData

HAVE_DATA = all((config.data_dir() / d.filename).is_file() for d in DATASETS)
needs_data = pytest.mark.skipif(not HAVE_DATA, reason="terrain or JPL files are not in the data folder")


class FixedSource:
    def __init__(self):
        self.calls = 0

    def vectors_km(self, times_utc):
        check_times(times_utc)
        self.calls += 1
        n = len(times_utc)
        return {"sun": np.tile([0.0, 0.0, -1.5e8], (n, 1)), "earth": np.tile([0.0, 0.0, 3.844e5], (n, 1))}


class FakeTerrain:
    path = Path("fake_terrain.tif")


def fake_horizon(terrain, lat, lon, observer_height_m=0.0, folder=None, **kwargs):
    if lat > -85.0:
        raise InsufficientData("outside the fake terrain")
    return HorizonProfile(lat, lon, 100.0, observer_height_m, 150_000.0, 0.5, np.full(720, 2.0))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Server:
    def __init__(self, app):
        self.port = free_port()
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="warning"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self):
        self.thread.start()
        deadline = time.time() + 60
        while not self.server.started:
            if time.time() > deadline or not self.thread.is_alive():
                raise RuntimeError("server did not start")
            time.sleep(0.05)
        return self

    def __exit__(self, *exc):
        self.server.should_exit = True
        self.thread.join(timeout=10)

    def call(self, path, body=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())


@pytest.fixture
def fake_server(monkeypatch):
    monkeypatch.setattr(mission, "horizon", fake_horizon)
    source = FixedSource()
    with Server(api_main.create_app(FakeTerrain(), source, load_data=False)) as s:
        s.source = source
        yield s


WINDOW = {"lat_deg": -89.5, "lon_deg": 0, "start_utc": "2026-11-14T00:00:00Z", "profile": {"duration_days": 2}}


# ---------- endpoints ----------

def test_meta(fake_server):
    status, m = fake_server.call("/api/meta")
    assert status == 200
    assert m["zone"] == {"max_distance_from_pole_km": 154, "min_lat_deg": -84.92}
    assert m["dates"] == {"min_utc": "2000-01-01T00:00:00Z", "max_utc": "2050-12-31T23:59:00Z"}
    assert m["window_days"] == {"min": 1, "max": 60}
    keys = [f["key"] for f in m["profile_fields"]]
    assert keys == ["duration_days", "vehicle_type", "max_night_h", "max_comms_gap_h", "min_sun_pct",
                    "observer_height_m"]
    night = m["profile_fields"][2]
    assert night == {"key": "max_night_h", "unit": "h", "default": 40, "min": 0, "max": "window length"}
    assert m["example_note"] == mission.EXAMPLE_NOTE
    assert m["data"]["present"] is True
    assert m["horizon_radius_km"] == 150 and m["time_step_min"] == 1


def test_window(fake_server):
    status, r = fake_server.call("/api/window", WINDOW)
    assert status == 200
    assert r["data_status"] == "ok" and r["status"] == "not_acceptable"
    assert r["metrics"]["sun_pct"] == 100 and r["metrics"]["comms_pct"] == 0
    assert r["reason_code"] == "max_comms_gap_h"
    assert r["window"] == {"start_utc": "2026-11-14T00:00:00Z", "end_utc": "2026-11-16T00:00:00Z"}
    assert r["profile"]["duration_days"] == 2 and r["site"]["height_m"] == 100


def test_window_outside_the_terrain_is_a_normal_response(fake_server):
    status, r = fake_server.call("/api/window", {**WINDOW, "lat_deg": -84.0})
    assert status == 200 and r["data_status"] == "insufficient_data" and r["status"] is None


def test_instant(fake_server):
    status, r = fake_server.call("/api/instant?lat=-89.5&lon=0&time=2026-11-14T06:30:00Z")
    assert status == 200 and r["data_status"] == "ok"
    assert r["time_utc"] == "2026-11-14T06:30:00Z"
    assert r["sun"]["visible"] is True and r["earth"]["visible"] is False
    assert r["sun"]["horizon_deg"] == 2 and set(r["sun"]) == {"alt_deg", "az_deg", "horizon_deg", "margin_deg", "visible"}
    assert r["meta"]["model"] == mission.MODEL


def test_instant_accepts_an_offset_and_converts_to_utc(fake_server):
    status, r = fake_server.call("/api/instant?lat=-89.5&lon=0&time=2026-11-14T08:30:00%2B02:00")
    assert status == 200 and r["time_utc"] == "2026-11-14T06:30:00Z"


def test_day_series(fake_server):
    status, r = fake_server.call("/api/day-series?lat=-89.5&lon=0&date=2026-11-14&height=2")
    assert status == 200 and r["data_status"] == "ok"
    assert r["start_utc"] == "2026-11-14T00:00:00Z" and r["step_min"] == 10
    assert len(r["times_utc"]) == 145 and r["times_utc"][-1] == "2026-11-15T00:00:00Z"
    for body in ("sun", "earth"):
        assert all(len(r[body][k]) == 145 for k in ("alt_deg", "az_deg", "horizon_deg", "margin_deg", "visible"))
    assert r["site"]["height_m"] == 102  # ground 100 m + instrument 2 m


def test_day_series_outside_the_terrain(fake_server):
    status, r = fake_server.call("/api/day-series?lat=-80&lon=0&date=2026-11-14")
    assert status == 200 and r["data_status"] == "insufficient_data" and r["times_utc"] is None


def test_compare_computes_the_ephemeris_once(fake_server):
    body = {"sites": [{"lat_deg": -89.5, "lon_deg": 0}, {"lat_deg": -84.0, "lon_deg": 10},
                      {"lat_deg": -88.0, "lon_deg": 90}],
            "start_utc": "2026-11-14", "profile": {"duration_days": 2}}
    before = fake_server.source.calls
    status, r = fake_server.call("/api/compare", body)
    assert status == 200
    assert [x["data_status"] for x in r["results"]] == ["ok", "insufficient_data", "ok"]
    assert [x["site"]["lat_deg"] for x in r["results"]] == [-89.5, -84, -88]
    assert fake_server.source.calls - before == 1


# ---------- errors ----------

@pytest.mark.parametrize("path,body,field", [
    ("/api/window", {**WINDOW, "profile": {"min_sun_pct": 120}}, "min_sun_pct"),
    ("/api/window", {**WINDOW, "profile": {"max_night": 4}}, "max_night"),
    ("/api/window", {**WINDOW, "lat_deg": "x"}, "lat_deg"),
    ("/api/window", {**WINDOW, "lat_deg": -95}, "lat_deg"),
    ("/api/window", {**WINDOW, "start_utc": "2026-11-14T00:00:00"}, "start_utc"),
    ("/api/window", {**WINDOW, "start_utc": "yesterday"}, "start_utc"),
    ("/api/window", {k: v for k, v in WINDOW.items() if k != "start_utc"}, "start_utc"),
    ("/api/window", {**WINDOW, "profile": [1]}, "profile"),
    ("/api/compare", {"sites": [{"lat_deg": -89, "lon_deg": 0}], "start_utc": "2026-11-14"}, "sites"),
    ("/api/compare", {"sites": [{"lat_deg": -89}] * 2, "start_utc": "2026-11-14"}, "lon_deg"),
    ("/api/instant?lat=-89.5&lon=0", None, "time"),
    ("/api/instant?lat=abc&lon=0&time=2026-11-14", None, "lat"),
    ("/api/instant?lat=-89.5&lon=0&time=2026-11-14&height=500", None, "observer_height_m"),
    ("/api/instant?lat=-89.5&lon=200&time=2026-11-14", None, "lon_deg"),
])
def test_bad_input_is_400_with_the_field(fake_server, path, body, field):
    status, r = fake_server.call(path, body)
    assert status == 400
    assert r["error"]["field"] == field and r["error"]["message"]


@pytest.mark.parametrize("path,body", [
    ("/api/window", {**WINDOW, "start_utc": "2050-12-31T00:00:00Z"}),
    ("/api/instant?lat=-89.5&lon=0&time=1999-12-31T00:00:00Z", None),
])
def test_dates_outside_the_range_are_400(fake_server, path, body):
    status, r = fake_server.call(path, body)
    assert status == 400 and "2000-01-01" in r["error"]["message"]


def test_without_data_compute_is_503_and_meta_still_works():
    with Server(api_main.create_app(load_data=False)) as s:
        status, r = s.call("/api/window", WINDOW)
        assert status == 503 and "fetch_data.py" in r["error"]["message"]
        status, m = s.call("/api/meta")
        assert status == 200 and m["data"]["present"] is False


def test_parse_time():
    assert api_main.parse_time("2026-11-14", "t").isoformat() == "2026-11-14T00:00:00+00:00"
    assert api_main.parse_time("2026-11-14T06:30:00Z", "t").hour == 6
    with pytest.raises(api_main.ApiError):
        api_main.parse_time("2026-11-14 06:30", "t")


# ---------- real data ----------

@needs_data
def test_real_window_over_http():
    with Server(api_main.create_app()) as s:
        status, r = s.call("/api/window", {"lat_deg": -89.5, "lon_deg": 0, "start_utc": "2026-11-14T00:00:00Z"})
    assert status == 200
    assert r["metrics"] == {"longest_night_h": 254.93, "sun_pct": 24.1, "longest_comms_gap_h": 107.28,
                            "comms_down_h": 120.18, "comms_pct": 64.2}
    assert (r["status"], r["reason_code"]) == ("not_acceptable", "max_night_h")
