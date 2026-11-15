"""Terrain around a site for the 3D view: grid geometry, coverage, and the API response."""

import numpy as np
import pytest

from core import config, patch
from core.datasets import DATASETS, locate
from core.horizon import HorizonProfile
from core.terrain import InsufficientData, Window

from tests.test_api import Server, FixedSource, fake_horizon
from api import main as api_main
from core import mission

HAVE_DATA = all(locate(d) is not None for d in DATASETS)
needs_data = pytest.mark.skipif(not HAVE_DATA, reason="terrain or JPL files are not in the data folder")


class FlatTerrain:
    """Constant height everywhere; raster coordinates are simply (lon, lat) in degrees."""
    STEP = 0.01

    def __init__(self, height_m=250.0, hole=None):
        self.height_m = height_m
        self.hole = hole        # (lon, lat) box with no data

    def latlon_to_xy(self, lat, lon):
        return np.atleast_1d(np.asarray(lon, float)), np.atleast_1d(np.asarray(lat, float))

    def read_window(self, x_min, y_min, x_max, y_max, margin_px=1):
        s = self.STEP
        x0, y0 = x_min - (margin_px + 1) * s, y_max + (margin_px + 1) * s
        cols = int(np.ceil((x_max - x0) / s)) + margin_px + 2
        rows = int(np.ceil((y0 - y_min) / s)) + margin_px + 2
        z = np.full((rows, cols), self.height_m, dtype=np.float32)
        if self.hole:
            (lo0, lo1), (la0, la1) = self.hole
            xs = x0 + np.arange(cols) * s
            ys = y0 - np.arange(rows) * s
            z[np.ix_((ys >= la0) & (ys <= la1), (xs >= lo0) & (xs <= lo1))] = np.nan
        return Window(heights=z, x0=x0, y0=y0, dx=s, dy=-s)

    def height_at(self, lat, lon):
        return self.height_m


def test_flat_terrain_shows_the_drop_of_the_sphere():
    t = FlatTerrain(250.0)
    p = patch.terrain_patch(t, -88.9, 43.2, half_size_m=10_000.0, size=5)
    u = p["up_m"]
    assert u.shape == (5, 5) and p["step_m"] == 5_000.0
    assert u[2, 2] == pytest.approx(0.0, abs=1e-6)
    R = config.MOON_RADIUS_M + 250.0
    corner = -R * (1 - np.cos(10_000.0 * np.sqrt(2) / config.MOON_RADIUS_M))
    assert u[0, 0] == pytest.approx(corner, rel=1e-6)      # about -57.6 m
    edge = -R * (1 - np.cos(10_000.0 / config.MOON_RADIUS_M))
    assert u[0, 2] == pytest.approx(edge, rel=1e-6) and u[2, 4] == pytest.approx(edge, rel=1e-6)


def test_rows_run_north_to_south_and_columns_west_to_east():
    # A site at 89 S, 0 E: north is away from the pole along 0 E, east is toward 90 E.
    t = FlatTerrain()
    seen = {}

    def spy(lat, lon):
        seen["lon"] = np.asarray(lon).reshape(3, 3)
        seen["lat"] = np.asarray(lat).reshape(3, 3)
        return FlatTerrain.latlon_to_xy(t, lat, lon)

    t.latlon_to_xy = spy
    patch.terrain_patch(t, -89.0, 0.0, half_size_m=10_000.0, size=3)
    lon, lat = seen["lon"], seen["lat"]
    assert abs(lon[1, 1]) < 1e-9 and lat[1, 1] == pytest.approx(-89.0)   # the site itself
    assert lon[1, 2] > 0 > lon[1, 0]                                       # east column: toward 90 E
    assert lat[0, 1] > -89.0 > lat[2, 1]                                   # north row away from the pole


def test_a_hole_in_the_terrain_is_insufficient_data():
    t = FlatTerrain(hole=((43.0, 43.4), (-88.95, -88.85)))
    with pytest.raises(InsufficientData):
        patch.terrain_patch(t, -88.9, 43.2, size=9)


# ---------- API ----------

@pytest.fixture
def patch_server(monkeypatch):
    monkeypatch.setattr(patch, "horizon", fake_horizon)
    monkeypatch.setattr(mission, "horizon", fake_horizon)
    flat = FlatTerrain()
    monkeypatch.setattr(patch, "terrain_patch", lambda terrain, lat, lon: {
        "ground_height_m": 100.0, "step_m": 78.125, "up_m": np.zeros((patch.PATCH_SIZE, patch.PATCH_SIZE))})
    with Server(api_main.create_app(flat, FixedSource(), load_data=False)) as s:
        yield s


def test_terrain_patch_endpoint(patch_server):
    status, r = patch_server.call("/api/terrain-patch?lat=-89.5&lon=0&height=2")
    assert status == 200 and r["data_status"] == "ok"
    assert r["site"] == {"lat_deg": -89.5, "lon_deg": 0, "height_m": 100}
    g = r["grid"]
    assert g["size"] == 257 and g["half_size_m"] == 10000 and g["step_m"] == 78.125
    assert len(g["up_m"]) == 257 * 257
    h = r["horizon"]
    assert h["step_deg"] == 0.5 and h["radius_m"] == 150000 and h["observer_height_m"] == 2
    assert len(h["elevation_deg"]) == 720 and h["elevation_deg"][0] == 2


def test_terrain_patch_outside_the_terrain_is_a_normal_response(patch_server):
    status, r = patch_server.call("/api/terrain-patch?lat=-84&lon=0")
    assert status == 200 and r["data_status"] == "insufficient_data"
    assert r["grid"] is None and r["horizon"] is None


def test_terrain_patch_rejects_a_bad_height(patch_server):
    status, r = patch_server.call("/api/terrain-patch?lat=-89.5&lon=0&height=500")
    assert status == 400 and r["error"]["field"] == "observer_height_m"


# ---------- real data ----------

@needs_data
def test_real_patch_is_centred_on_the_site():
    from core.terrain import Terrain
    t = Terrain()
    try:
        p = patch.terrain_patch(t, -88.9, 43.2)
        u = p["up_m"]
        assert u.shape == (257, 257) and u[128, 128] == pytest.approx(0.0, abs=0.05)
        assert p["ground_height_m"] == pytest.approx(t.height_at(-88.9, 43.2))
        assert np.isfinite(u).all()
    finally:
        t.close()
