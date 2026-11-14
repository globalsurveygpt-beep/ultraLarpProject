"""Local frame geometry on a spherical Moon.

Conventions shared by the whole core (ephemeris and terrain horizon use the same functions):

- body-fixed frame: x toward 0 E on the equator, y toward 90 E, z toward the north pole;
- latitude and longitude are planetocentric degrees, east longitude positive;
- azimuth is measured from local north through east, 0 to 360 degrees;
- altitude is the angle above the plane tangent to the sphere at the site.

At latitude -90 or +90 "north" is not defined by the sphere alone; the basis is then
fixed by the longitude that is passed in, so results stay defined and consistent.

Shapes: latitude and longitude broadcast against each other and against the leading axes
of vector arrays. For one site and many times pass scalars and vectors of shape (T, 3).
For many sites at once pass latitude and longitude of shape (S, 1).
"""

import numpy as np


def _angles(lat_deg, lon_deg):
    lat, lon = np.broadcast_arrays(np.radians(np.asarray(lat_deg, dtype=float)),
                                   np.radians(np.asarray(lon_deg, dtype=float)))
    return lat, lon


def enu_basis(lat_deg, lon_deg):
    """Unit vectors (east, north, up) of the local frame, each of shape (..., 3)."""
    lat, lon = _angles(lat_deg, lon_deg)
    sl, cl = np.sin(lat), np.cos(lat)
    so, co = np.sin(lon), np.cos(lon)
    zero = np.zeros_like(lat)
    east = np.stack([-so, co, zero], axis=-1)
    north = np.stack([-sl * co, -sl * so, cl], axis=-1)
    up = np.stack([cl * co, cl * so, sl], axis=-1)
    return east, north, up


def site_position(lat_deg, lon_deg, radius):
    """Position of a site at the given distance from the Moon centre, in the units of `radius`."""
    _, _, up = enu_basis(lat_deg, lon_deg)
    return up * np.asarray(radius, dtype=float)[..., None]


def vectors_to_altaz(vectors, lat_deg, lon_deg):
    """Altitude and azimuth (degrees) of body-frame vectors seen from a site.

    `vectors` point from the site to the target, shape (..., 3).
    """
    v = np.asarray(vectors, dtype=float)
    east, north, up = enu_basis(lat_deg, lon_deg)
    e = np.sum(v * east, axis=-1)
    n = np.sum(v * north, axis=-1)
    u = np.sum(v * up, axis=-1)
    alt = np.degrees(np.arctan2(u, np.hypot(e, n)))
    az = np.degrees(np.arctan2(e, n)) % 360.0
    return alt, az


def angular_separation_deg(alt1, az1, alt2, az2):
    """Angle between two directions given as altitude and azimuth, degrees."""
    a1, z1, a2, z2 = (np.radians(np.asarray(x, dtype=float)) for x in (alt1, az1, alt2, az2))
    # haversine form, accurate for small angles
    h = np.sin((a2 - a1) / 2) ** 2 + np.cos(a1) * np.cos(a2) * np.sin((z2 - z1) / 2) ** 2
    return np.degrees(2 * np.arcsin(np.sqrt(np.clip(h, 0.0, 1.0))))
