"""Terrain and horizon tests.

Synthetic rasters are written as small GeoTIFFs, so the same loader that reads LOLA is
tested. Expected values come from geometry or from a plain per-pixel reference written
independently with GDAL coordinate transformation and core.geometry.
Tests marked `needs_terrain` use the real LOLA file and are skipped without it.
"""

import time

import numpy as np
import pytest
import rasterio
from rasterio.crs import CRS
from rasterio.transform import from_origin
from rasterio.warp import transform

from core import config, horizon
from core.datasets import path_of
from core.geometry import site_position, vectors_to_altaz
from core.horizon import HorizonProfile, compute
from core.terrain import InsufficientData, Terrain

R = config.MOON_RADIUS_M
SOUTH = "+proj=stere +lat_0=-90 +lat_ts=-90 +lon_0=0 +x_0=0 +y_0=0 +R=1737400 +units=m +no_defs"
NORTH_ROTATED = "+proj=stere +lat_0=90 +lat_ts=90 +lon_0=35 +x_0=0 +y_0=0 +R=1737400 +units=m +no_defs"
GEO = CRS.from_proj4("+proj=longlat +R=1737400 +no_defs")

PIXEL = 100.0
HALF = 20_000.0          # synthetic raster covers +-20 km around the pole
SITE = (-89.9, 30.0)     # about 3 km from the pole

HAVE_TERRAIN = path_of("terrain").is_file()
needs_terrain = pytest.mark.skipif(not HAVE_TERRAIN, reason="LOLA terrain file is not in the data folder")


def write_raster(path, z, crs=SOUTH, pixel=PIXEL, half=HALF, nodata=np.nan):
    with rasterio.open(path, "w", driver="GTiff", width=z.shape[1], height=z.shape[0], count=1,
                       dtype="float32", crs=CRS.from_proj4(crs), nodata=nodata,
                       transform=from_origin(-half, half, pixel, pixel)) as dst:
        dst.write(z.astype(np.float32), 1)
    return path


def grid(pixel=PIXEL, half=HALF):
    n = int(2 * half / pixel)
    c = -half + (np.arange(n) + 0.5) * pixel
    return np.meshgrid(c, -c)  # x grows to the right, y grows upward (rows go down)


def site_xy(lat, lon, crs=SOUTH):
    xs, ys = transform(GEO, CRS.from_proj4(crs), [lon], [lat])
    return xs[0], ys[0]


def flat(tmp_path, height=0.0):
    X, _ = grid()
    return Terrain(write_raster(tmp_path / "flat.tif", np.full(X.shape, height)))


def reference_horizon(path, lat, lon, observer_height_m, radius_m, step_deg, ground):
    """Plain per-pixel horizon: GDAL transform, 3D vectors, core.geometry alt/az."""
    with rasterio.open(path) as src:
        z = src.read(1).astype(float)
        X, Y = grid()
        lon_p, lat_p = transform(src.crs, GEO, X.ravel().tolist(), Y.ravel().tolist())
    lat_p, lon_p = np.asarray(lat_p), np.asarray(lon_p)
    target = site_position(lat_p, lon_p, R + z.ravel())
    observer = site_position(lat, lon, R + ground + observer_height_m)
    up_p = target / np.linalg.norm(target, axis=-1, keepdims=True)
    up_s = observer / np.linalg.norm(observer)
    gamma = np.arccos(np.clip(up_p @ up_s, -1, 1))
    alt, az = vectors_to_altaz(target - observer, lat, lon)
    sx, sy = site_xy(lat, lon)
    d_site = np.hypot(X.ravel() - sx, Y.ravel() - sy)
    inside = (gamma * R <= radius_m) & (d_site > d_site.min())
    n = int(round(360 / step_deg))
    best = np.full(n, -np.inf)
    np.maximum.at(best, (az[inside] // step_deg).astype(int) % n, alt[inside])
    return best


# ---------- terrain loader ----------

def test_radius_and_projection_come_from_the_file(tmp_path):
    t = flat(tmp_path)
    assert t.radius_m == R and t.pixel_size_m == PIXEL and t._polar == -1


def test_other_sphere_radius_is_rejected(tmp_path):
    X, _ = grid()
    p = write_raster(tmp_path / "r.tif", np.zeros(X.shape), crs=SOUTH.replace("1737400", "1738100"))
    with pytest.raises(ValueError):
        Terrain(p)


def test_nodata_value_becomes_nan(tmp_path):
    X, _ = grid()
    z = np.zeros(X.shape)
    z[10, 10] = -9999
    t = Terrain(write_raster(tmp_path / "nd.tif", z, nodata=-9999))
    assert np.isnan(t._array[10, 10]) and t._array[11, 11] == 0


def test_height_is_bilinear(tmp_path):
    X, Y = grid()
    t = Terrain(write_raster(tmp_path / "plane.tif", 0.01 * X - 0.02 * Y + 5))
    for lat, lon in [(-89.9, 30.0), (-89.8, -100.0), (-89.95, 170.0)]:
        x, y = site_xy(lat, lon)
        assert t.height_at(lat, lon) == pytest.approx(0.01 * x - 0.02 * y + 5, abs=1e-3)


def test_height_outside_the_raster_is_insufficient_data(tmp_path):
    with pytest.raises(InsufficientData):
        flat(tmp_path).height_at(-89.0, 0.0)  # 30 km from the pole


@pytest.mark.parametrize("crs", [SOUTH, NORTH_ROTATED])
def test_fast_projection_matches_gdal(tmp_path, crs):
    X, _ = grid()
    t = Terrain(write_raster(tmp_path / "p.tif", np.zeros(X.shape), crs=crs))
    rng = np.random.default_rng(3)
    xs, ys = rng.uniform(-HALF, HALF, 50), rng.uniform(-HALF, HALF, 50)
    fast = t.unit_vectors(xs, ys)
    t._polar = None  # force the GDAL path
    slow = t.unit_vectors(xs, ys)
    assert np.max(np.abs(fast - slow)) < 1e-12


def test_grid_dots_match_unit_vectors(tmp_path):
    t = flat(tmp_path)
    xs, ys = np.linspace(-5000, 7000, 13), np.linspace(3000, -9000, 7)
    basis = [np.array([0.3, -0.5, 0.81]), np.array([1.0, 0.0, 0.0])]
    X, Y = np.meshgrid(xs, ys)
    v = t.unit_vectors(X, Y)
    for got, b in zip(t.grid_dots(xs, ys, basis), basis):
        assert np.max(np.abs(got - v @ b)) < 1e-12


# ---------- horizon on synthetic terrain ----------

def test_flat_ground_observer_on_the_ground(tmp_path):
    # on a smooth sphere every ground point lies below the observer's tangent plane,
    # the nearest ones only just: the horizon is slightly negative in every direction
    p = compute(flat(tmp_path), *SITE, radius_m=10_000)
    assert len(p.elevation_deg) == 720
    assert np.all(p.elevation_deg < 0) and np.all(p.elevation_deg > -0.01)


def test_flat_ground_raised_observer_sees_the_dip_of_the_horizon(tmp_path):
    # geometric horizon of a sphere seen from height h: dip = acos(R / (R + h))
    h = 2.0  # tangent point about 2.6 km away, inside the 10 km circle
    p = compute(flat(tmp_path), *SITE, observer_height_m=h, radius_m=10_000)
    dip = np.degrees(np.arccos(R / (R + h)))
    assert np.allclose(p.elevation_deg, -dip, atol=2e-4)


def test_ring_wall_matches_the_per_pixel_reference(tmp_path):
    X, Y = grid()
    sx, sy = site_xy(*SITE)
    d = np.hypot(X - sx, Y - sy)
    z = np.where((d > 6000) & (d < 8000), 400.0, 0.0)
    path = write_raster(tmp_path / "ring.tif", z)
    t = Terrain(path)
    p = compute(t, *SITE, radius_m=10_000, step_deg=2.0)
    ref = reference_horizon(path, *SITE, 0.0, 10_000, 2.0, p.ground_height_m)
    assert np.max(np.abs(p.elevation_deg - ref)) < 1e-6
    # and roughly what a flat-Earth estimate says: atan(400 / 6000) minus curvature
    assert np.all(np.abs(p.elevation_deg - np.degrees(np.arctan(400 / 6000))) < 0.2)


def test_ridge_raises_the_horizon_only_in_its_direction(tmp_path):
    X, Y = grid()
    sx, sy = site_xy(*SITE)
    p0 = compute(flat(tmp_path), *SITE, radius_m=10_000)
    # from a southern site the south pole lies due south (azimuth 180); put the block there
    toward_pole = np.array([-sx, -sy]) / np.hypot(sx, sy)
    bx, by = sx + 5000 * toward_pole[0], sy + 5000 * toward_pole[1]
    z = np.where((np.abs(X - bx) < 150) & (np.abs(Y - by) < 150), 300.0, 0.0)
    p = compute(Terrain(write_raster(tmp_path / "ridge.tif", z)), *SITE, radius_m=10_000)
    raised = np.nonzero(p.elevation_deg > p0.elevation_deg + 1e-9)[0]
    assert raised.size > 0
    az = (raised + 0.5) * 0.5
    assert np.all(np.abs(az - 180.0) < 6)  # only around south
    assert p.elevation_deg.max() == pytest.approx(np.degrees(np.arctan(300 / 4850)), abs=0.3)


def test_near_block_covers_its_whole_angular_width(tmp_path):
    X, Y = grid()
    sx, sy = site_xy(*SITE)
    # a 3x3 pixel block about 300 m away, several tens of degrees wide seen from the site
    ix = np.argmin(np.abs(X[0] - (sx + 300)))
    iy = np.argmin(np.abs(Y[:, 0] - sy))
    z = np.zeros(X.shape)
    z[iy - 1:iy + 2, ix - 1:ix + 2] = 50.0
    p = compute(Terrain(write_raster(tmp_path / "near.tif", z)), *SITE, radius_m=5_000)
    high = p.elevation_deg > 5.0
    # expected width from the block's outer corners; this close to the pole the projection
    # keeps angles, so a planar estimate is good to a fraction of a degree
    cx = X[0, ix] + np.array([-1.5, 1.5]) * PIXEL
    cy = Y[iy, 0] + np.array([-1.5, 1.5]) * PIXEL
    ang = [np.degrees(np.arctan2(y - sy, x - sx)) for x in cx for y in cy]
    width = max(ang) - min(ang)
    assert 40 < width < 100
    assert abs(high.sum() * 0.5 - width) <= 1.5
    # contiguous block of sectors (allowing for wrap-around)
    k = np.nonzero(high)[0]
    assert (np.diff(k) == 1).all() or (np.diff(np.roll(k, -np.argmax(np.diff(k) > 1) - 1)) % 720 == 1).all()


def test_no_empty_sectors_with_lola_like_pixels(tmp_path):
    X, _ = grid(pixel=80.0, half=12_000.0)
    t = Terrain(write_raster(tmp_path / "f80.tif", np.zeros(X.shape), pixel=80.0, half=12_000.0))
    p = compute(t, -89.95, 10.0, radius_m=8_000)
    assert np.all(np.isfinite(p.elevation_deg))


def test_nodata_inside_the_circle_is_insufficient_data(tmp_path):
    X, Y = grid()
    sx, sy = site_xy(*SITE)
    z = np.zeros(X.shape)
    z[np.hypot(X - sx - 4000, Y - sy) < 200] = np.nan
    t = Terrain(write_raster(tmp_path / "hole.tif", z))
    with pytest.raises(InsufficientData):
        compute(t, *SITE, radius_m=10_000)
    compute(t, *SITE, radius_m=3_000)  # the hole is outside a smaller circle


def test_circle_beyond_the_raster_edge_is_insufficient_data(tmp_path):
    with pytest.raises(InsufficientData):
        compute(flat(tmp_path), -89.75, 0.0, radius_m=15_000)  # 7.6 km + 15 km > 20 km


def test_gdal_path_gives_the_same_horizon(tmp_path):
    X, Y = grid()
    z = 200 * np.sin(X / 3000) * np.cos(Y / 2000)
    t = Terrain(write_raster(tmp_path / "waves.tif", z))
    fast = compute(t, *SITE, radius_m=6_000, step_deg=2.0)
    t._polar = None
    slow = compute(t, *SITE, radius_m=6_000, step_deg=2.0)
    assert np.max(np.abs(fast.elevation_deg - slow.elevation_deg)) < 1e-6


# ---------- profile and cache ----------

def test_sector_lookup_wraps_around():
    p = HorizonProfile(-89.0, 0.0, 0.0, 0.0, 1.0, 0.5, np.arange(720.0))
    assert list(p.at([0.0, 0.49, 0.5, 359.99, 360.0, -0.1, 720.25])) == [0, 0, 1, 719, 0, 719, 0]
    assert p.site_height_m == 0.0


def test_cache_returns_the_stored_profile(tmp_path, monkeypatch):
    t = flat(tmp_path)
    folder = tmp_path / "cache"
    first = horizon.horizon(t, *SITE, radius_m=5_000, folder=folder)
    assert len(list(folder.glob("*.npz"))) == 1

    def fail(*a, **k):
        raise AssertionError("recomputed instead of reading the cache")

    monkeypatch.setattr(horizon, "compute", fail)
    again = horizon.horizon(t, *SITE, radius_m=5_000, folder=folder)
    assert np.array_equal(again.elevation_deg, first.elevation_deg)
    assert again.ground_height_m == first.ground_height_m


def test_cache_key_depends_on_every_input(tmp_path):
    t = flat(tmp_path)
    base = dict(lat_deg=-89.9, lon_deg=30.0, observer_height_m=0.0, radius_m=5000.0, step_deg=0.5)
    keys = {horizon.cache_key(t, **base)}
    for field, value in [("lat_deg", -89.8), ("lon_deg", 31.0), ("observer_height_m", 2.0),
                         ("radius_m", 6000.0), ("step_deg", 1.0)]:
        keys.add(horizon.cache_key(t, **{**base, field: value}))
    assert len(keys) == 6


# ---------- real LOLA terrain ----------

@pytest.fixture(scope="module")
def lola():
    return Terrain()


@needs_terrain
def test_lola_file_is_read_as_expected(lola):
    assert (lola.width, lola.height, lola.pixel_size_m) == (7600, 7600, 80.0)
    assert lola._polar == -1


@needs_terrain
def test_lola_horizon_near_the_pole(lola):
    start = time.perf_counter()
    p = compute(lola, -89.5, 0.0)
    elapsed = time.perf_counter() - start
    assert np.all(np.isfinite(p.elevation_deg))
    assert -1.0 < p.elevation_deg.min() and p.elevation_deg.max() < 30.0
    assert elapsed < 30.0, f"{elapsed:.1f} s"


@needs_terrain
def test_lola_outside_the_working_zone_is_insufficient_data(lola):
    with pytest.raises(InsufficientData):
        compute(lola, -84.0, 0.0)  # 183 km from the pole: the 150 km circle leaves the raster
