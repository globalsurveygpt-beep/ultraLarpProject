"""Terrain around one site for the 3D site view (format approved 2026-11-15).

A square of terrain centred on the site, sampled on a regular grid in the site's local frame
(east, north, up), plus the site's horizon profile so the view can draw the far terrain
(out to the horizon radius) as a silhouette without loading it.

Grid: `size` x `size` points, `step_m` apart, from -half_size_m to +half_size_m along east
and north. Row 0 is the northern edge, column 0 the western edge. Each value is the "up"
coordinate in metres of that terrain point in the local frame of the site, measured from
the ground at the site; it includes the drop of the lunar sphere (about 29 m at 10 km), so
the view shows the surface as an observer would. No vertical exaggeration.

The grid point at offset (e, n) is the point at arc distance hypot(e, n) from the site in
azimuth atan2(e, n) on the reference sphere; its height comes from the terrain raster,
bilinear between the four nearest pixel centres.
"""

import numpy as np

from core import config
from core.datasets import by_key
from core.geometry import enu_basis
from core.horizon import horizon
from core.terrain import InsufficientData, Terrain

PATCH_HALF_SIZE_M = 10_000.0
PATCH_SIZE = 257            # points per side: 78.125 m spacing, close to the 80 m terrain pixel
HEIGHT_DECIMALS = 1


def _bilinear(terrain: Terrain, xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
    """Terrain heights at raster CRS points; NaN where the raster does not cover them."""
    w = terrain.read_window(float(xs.min()), float(ys.min()), float(xs.max()), float(ys.max()), margin_px=1)
    fc = (xs - w.x0) / w.dx
    fr = (ys - w.y0) / w.dy
    c = np.floor(fc).astype(int)
    r = np.floor(fr).astype(int)
    tc, tr = fc - c, fr - r
    rows, cols = w.heights.shape
    ok = (c >= 0) & (r >= 0) & (c + 1 < cols) & (r + 1 < rows)
    c, r = np.clip(c, 0, cols - 2), np.clip(r, 0, rows - 2)
    z = w.heights.astype(float)
    top = z[r, c] * (1 - tc) + z[r, c + 1] * tc
    bottom = z[r + 1, c] * (1 - tc) + z[r + 1, c + 1] * tc
    out = top * (1 - tr) + bottom * tr
    out[~ok] = np.nan
    return out


def terrain_patch(terrain: Terrain, lat_deg: float, lon_deg: float, half_size_m: float = PATCH_HALF_SIZE_M,
                  size: int = PATCH_SIZE) -> dict:
    """Grid of local "up" coordinates around the site. Raises InsufficientData when the
    raster does not cover the whole square."""
    R = config.MOON_RADIUS_M
    east, north, up = (np.asarray(v, float) for v in enu_basis(lat_deg, lon_deg))
    offsets = np.linspace(-half_size_m, half_size_m, size)
    E, N = np.meshgrid(offsets, offsets[::-1])          # row 0 = north edge, column 0 = west edge
    s = np.hypot(E, N)
    with np.errstate(invalid="ignore", divide="ignore"):
        t = (E[..., None] * east + N[..., None] * north) / np.where(s > 0, s, 1.0)[..., None]
    a = s / R
    unit = np.cos(a)[..., None] * up + np.sin(a)[..., None] * t
    lat = np.degrees(np.arcsin(np.clip(unit[..., 2], -1.0, 1.0)))
    lon = np.degrees(np.arctan2(unit[..., 1], unit[..., 0]))
    xs, ys = terrain.latlon_to_xy(lat.ravel(), lon.ravel())
    h = _bilinear(terrain, np.asarray(xs), np.asarray(ys)).reshape(E.shape)
    if np.isnan(h).any():
        raise InsufficientData(f"terrain does not cover {half_size_m / 1000:g} km around lat {lat_deg}, lon {lon_deg}")
    ground = float(terrain.height_at(lat_deg, lon_deg))
    # Position of each terrain point relative to the ground at the site, projected on "up".
    rel = (R + h)[..., None] * unit - (R + ground) * up
    u = rel @ up
    return {"ground_height_m": ground, "step_m": float(offsets[1] - offsets[0]), "up_m": u}


def patch_response(terrain: Terrain, source, lat_deg: float, lon_deg: float, observer_height_m: float = 0.0,
                   cache_folder=None) -> dict:
    """GET /api/terrain-patch: the grid above plus the site's horizon profile."""
    from core.mission import MODEL, _check_height, _num, _site, check_site

    check_site(lat_deg, lon_deg)
    _check_height(observer_height_m)
    response = {"data_status": "insufficient_data", "site": _site(lat_deg, lon_deg, None),
                "grid": None, "horizon": None, "meta": {"model": MODEL}}
    try:
        prof = horizon(terrain, lat_deg, lon_deg, observer_height_m, folder=cache_folder)
        patch = terrain_patch(terrain, lat_deg, lon_deg)
    except InsufficientData:
        return response
    response.update({
        "data_status": "ok",
        "site": _site(lat_deg, lon_deg, prof.ground_height_m),
        "grid": {
            "size": PATCH_SIZE, "half_size_m": _num(PATCH_HALF_SIZE_M), "step_m": _num(patch["step_m"], 3),
            "axes": "east, north, up; row 0 = north edge, column 0 = west edge",
            "up_m": [_num(v, HEIGHT_DECIMALS) for v in patch["up_m"].ravel()],
        },
        "horizon": {
            "step_deg": _num(prof.step_deg), "radius_m": _num(prof.radius_m),
            "observer_height_m": _num(prof.observer_height_m, 1),
            "elevation_deg": [_num(v, 3) for v in prof.elevation_deg],
        },
        "meta": {"model": MODEL, "terrain": by_key("terrain").filename,
                 "note": "No vertical exaggeration. Beyond the grid, terrain is drawn from the horizon profile."},
    })
    return response
