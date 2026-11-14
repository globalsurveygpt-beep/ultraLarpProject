import numpy as np
import pytest

from core.geometry import angular_separation_deg, enu_basis, site_position, vectors_to_altaz

SITES = [(0.0, 0.0), (45.0, 30.0), (-89.5, 0.0), (-85.5, 90.0), (-87.0, -120.0), (-90.0, 0.0), (-90.0, 77.0), (90.0, 10.0)]


@pytest.mark.parametrize("lat,lon", SITES)
def test_basis_is_orthonormal_and_right_handed(lat, lon):
    e, n, u = enu_basis(lat, lon)
    for v in (e, n, u):
        assert np.linalg.norm(v) == pytest.approx(1.0)
    assert np.dot(e, n) == pytest.approx(0.0, abs=1e-12)
    assert np.dot(e, u) == pytest.approx(0.0, abs=1e-12)
    assert np.dot(n, u) == pytest.approx(0.0, abs=1e-12)
    assert np.allclose(np.cross(e, n), u)


@pytest.mark.parametrize("lat,lon", SITES)
def test_up_vector_is_at_zenith(lat, lon):
    _, _, u = enu_basis(lat, lon)
    alt, _ = vectors_to_altaz(u * 1000.0, lat, lon)
    assert alt == pytest.approx(90.0)


def test_cardinal_directions_on_the_equator():
    alt, az = vectors_to_altaz([0.0, 0.0, 1.0], 0.0, 0.0)  # toward the north pole
    assert (alt, az) == pytest.approx((0.0, 0.0), abs=1e-9)
    alt, az = vectors_to_altaz([0.0, 1.0, 0.0], 0.0, 0.0)  # toward 90 E
    assert (alt, az) == pytest.approx((0.0, 90.0), abs=1e-9)
    alt, az = vectors_to_altaz([0.0, -1.0, 0.0], 0.0, 0.0)
    assert az == pytest.approx(270.0)


@pytest.mark.parametrize("lat", [-89.5, -85.5, -30.0, 10.0])
def test_altitude_of_the_north_pole_direction_equals_latitude(lat):
    # classic check: the rotation axis stands at an altitude equal to the latitude
    alt, az = vectors_to_altaz([0.0, 0.0, 1.0], lat, 33.0)
    assert alt == pytest.approx(lat)
    assert az == pytest.approx(0.0, abs=1e-9)


def test_azimuth_is_in_0_360():
    v = np.random.default_rng(1).normal(size=(500, 3))
    _, az = vectors_to_altaz(v, -88.0, 15.0)
    assert np.all((az >= 0.0) & (az < 360.0))


def test_site_position_radius():
    p = site_position(-89.5, 40.0, 1737.4)
    assert np.linalg.norm(p) == pytest.approx(1737.4)
    assert p[2] < 0  # southern hemisphere


def test_many_sites_broadcast_against_times():
    rng = np.random.default_rng(2)
    vectors = rng.normal(size=(7, 3))           # 7 times
    lats = np.array([-89.0, -86.0, -85.0])[:, None]
    lons = np.array([0.0, 120.0, -60.0])[:, None]
    alt, az = vectors_to_altaz(vectors, lats, lons)
    assert alt.shape == (3, 7)
    for i in range(3):
        a1, z1 = vectors_to_altaz(vectors, lats[i, 0], lons[i, 0])
        assert np.allclose(alt[i], a1) and np.allclose(az[i], z1)


def test_angular_separation():
    assert angular_separation_deg(0, 0, 0, 90) == pytest.approx(90.0)
    assert angular_separation_deg(10, 359.999, 10, 0.001) == pytest.approx(0.002 * np.cos(np.radians(10)), rel=1e-6)
    assert angular_separation_deg(1.0, 50.0, 1.003, 50.0) == pytest.approx(0.003, rel=1e-6)
