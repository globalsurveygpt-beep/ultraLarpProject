"""Elevation raster access: any GDAL-readable raster of heights above a lunar sphere.

The projection, pixel grid and NoData value come from the file itself (project rule 6).
Heights are metres above the reference sphere named in the raster CRS; that radius must
equal config.MOON_RADIUS_M, because the ephemeris and the horizon use the same sphere.

Two ways to turn raster coordinates into directions on the sphere:

- polar stereographic on a sphere (the LOLA polar products): exact closed-form formulas,
  fast enough for the millions of pixels a horizon needs;
- any other projection: GDAL coordinate transformation, correct but about 1 second per
  million points.

Rasters up to IN_MEMORY_LIMIT_BYTES are read into memory once; larger ones are read
window by window.
"""

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from core import config
from core.datasets import path_of

IN_MEMORY_LIMIT_BYTES = 1 << 30


class InsufficientData(Exception):
    """The terrain does not cover what the calculation needs (edge of the raster or NoData)."""


@dataclass(frozen=True)
class Window:
    """Heights of a rectangular block of pixels, NaN where there is no data.

    Pixel (row, col) of `heights` has its centre at raster coordinates
    x = x0 + col * dx, y = y0 + row * dy (dy is usually negative).
    """

    heights: np.ndarray
    x0: float
    y0: float
    dx: float
    dy: float

    def centres(self):
        rows, cols = self.heights.shape
        xs = self.x0 + np.arange(cols) * self.dx
        ys = self.y0 + np.arange(rows) * self.dy
        return xs, ys


def _sphere_radius(crs) -> float:
    params = crs.to_dict()
    if "R" in params:
        return float(params["R"])
    a, b = params.get("a"), params.get("b")
    if a is not None and (b is None or float(a) == float(b)):
        return float(a)
    raise ValueError(f"terrain CRS is not a sphere: {crs.to_proj4()}")


def _polar_stereographic_sign(crs) -> float | None:
    """-1 or +1 for a south or north polar stereographic projection that the closed-form
    formulas cover exactly; None otherwise."""
    p = crs.to_dict()
    if p.get("proj") != "stere":
        return None
    lat0 = float(p.get("lat_0", 0))
    lat_ts = float(p.get("lat_ts", lat0))
    if abs(lat0) != 90 or lat_ts != lat0:
        return None
    if float(p.get("k", p.get("k_0", 1))) != 1 or float(p.get("x_0", 0)) or float(p.get("y_0", 0)):
        return None
    return float(np.sign(lat0))


class Terrain:
    def __init__(self, path: Path | None = None):
        import rasterio
        from rasterio.crs import CRS

        self.path = Path(path) if path else path_of("terrain")
        if not self.path.is_file():
            raise FileNotFoundError(f"terrain file not found: {self.path}. Run: python scripts/fetch_data.py")
        self._src = rasterio.open(self.path)
        src = self._src
        if src.crs is None:
            raise ValueError(f"{self.path.name} has no coordinate reference system")
        t = src.transform
        if t.b or t.d:
            raise ValueError(f"{self.path.name}: rotated rasters are not supported")
        self.crs = src.crs
        self.radius_m = _sphere_radius(src.crs)
        if abs(self.radius_m - config.MOON_RADIUS_M) > 1e-6:
            raise ValueError(f"{self.path.name} uses a {self.radius_m} m sphere, config expects {config.MOON_RADIUS_M} m")
        self.width, self.height = src.width, src.height
        self.dx, self.dy = t.a, t.e
        self.left, self.top = t.c, t.f
        self.nodata = src.nodata
        self.pixel_size_m = max(abs(self.dx), abs(self.dy))
        self._polar = _polar_stereographic_sign(src.crs)
        self._lon0 = float(src.crs.to_dict().get("lon_0", 0.0))
        self._geo = CRS.from_proj4(f"+proj=longlat +R={self.radius_m} +no_defs")
        self._array = None
        if self.width * self.height * 4 <= IN_MEMORY_LIMIT_BYTES:
            self._array = self._clean(src.read(1).astype(np.float32))

    @property
    def fingerprint(self) -> str:
        """Identifies the terrain file for caching: name and size in bytes."""
        return f"{self.path.name}:{self.path.stat().st_size}"

    def _clean(self, z: np.ndarray) -> np.ndarray:
        if self.nodata is not None and not np.isnan(self.nodata):
            z[z == self.nodata] = np.nan
        return z

    # ---------- coordinates ----------

    def latlon_to_xy(self, lat_deg, lon_deg):
        """Planetocentric degrees (east positive) to raster CRS coordinates."""
        from rasterio.warp import transform

        lat, lon = np.atleast_1d(np.asarray(lat_deg, float)), np.atleast_1d(np.asarray(lon_deg, float))
        xs, ys = transform(self._geo, self.crs, lon.tolist(), lat.tolist())
        return np.asarray(xs), np.asarray(ys)

    def unit_vectors(self, xs, ys) -> np.ndarray:
        """Unit vectors in the lunar body-fixed frame for raster CRS coordinates, shape (..., 3)."""
        xs, ys = np.broadcast_arrays(np.asarray(xs, float), np.asarray(ys, float))
        if self._polar is not None:
            return self._stereographic_unit_vectors(xs, ys)
        from rasterio.warp import transform

        lon, lat = transform(self.crs, self._geo, xs.ravel().tolist(), ys.ravel().tolist())
        lat, lon = np.radians(np.reshape(lat, xs.shape)), np.radians(np.reshape(lon, xs.shape))
        return np.stack([np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)], axis=-1)

    def grid_dots(self, xs, ys, vectors) -> list[np.ndarray]:
        """Dot products of the unit vectors of a pixel grid with each of `vectors`.

        `xs` (C,) and `ys` (Rw,) are pixel centre coordinates; returns one (Rw, C) array
        per vector. In the polar stereographic case the unit vector is
        v = f * w + (0, 0, -s) with f = 2 / (1 + q^2) and w linear in (qx, qy), so each
        dot product is f * (column term + row term) - s * b_z: no 3D array is built.
        """
        xs, ys = np.asarray(xs, float), np.asarray(ys, float)
        if self._polar is None:
            X, Y = np.meshgrid(xs, ys)
            v = self.unit_vectors(X, Y)
            return [v @ np.asarray(b, float) for b in vectors]
        s = self._polar
        two_r = 2.0 * self.radius_m
        qx, qy = xs / two_r, -s * ys / two_r
        f = 2.0 / (1.0 + (qx * qx)[None, :] + (qy * qy)[:, None])
        c0, s0 = np.cos(np.radians(self._lon0)), np.sin(np.radians(self._lon0))
        out = []
        for b in vectors:
            bx, by, bz = (float(c) for c in b)
            col = qx * (-s0 * bx + c0 * by)
            row = qy * (c0 * bx + s0 * by) + s * bz
            out.append(f * (col[None, :] + row[:, None]) - s * bz)
        return out

    def _stereographic_unit_vectors(self, xs, ys):
        # Inverse polar stereographic on a sphere, scale 1 at the pole. With
        # q = rho / 2R the angular distance from the pole is c = 2 atan q, so
        # sin c = 2q / (1 + q^2) and cos c = (1 - q^2) / (1 + q^2).
        # South pole: x = rho sin(lon - lon0), y = rho cos(lon - lon0).
        # North pole: x = rho sin(lon - lon0), y = -rho cos(lon - lon0).
        s = self._polar
        two_r = 2.0 * self.radius_m
        qx, qy = xs / two_r, -s * ys / two_r  # qy = (rho / 2R) cos(lon - lon0)
        q2 = qx * qx + qy * qy
        f = 2.0 / (1.0 + q2)
        c0, s0 = np.cos(np.radians(self._lon0)), np.sin(np.radians(self._lon0))
        a, b = f * qy, f * qx  # sin c cos(dlon), sin c sin(dlon)
        x = a * c0 - b * s0
        y = a * s0 + b * c0
        z = s * (1.0 - q2) / (1.0 + q2)
        return np.stack([x, y, z], axis=-1)

    # ---------- reading ----------

    def read_window(self, x_min, y_min, x_max, y_max, margin_px: int = 1) -> Window:
        """Pixels whose centres cover the box, plus a margin; outside the raster is NaN."""
        col0 = int(np.floor((x_min - self.left) / self.dx)) - margin_px
        col1 = int(np.floor((x_max - self.left) / self.dx)) + margin_px
        r_a = (y_min - self.top) / self.dy
        r_b = (y_max - self.top) / self.dy
        row0 = int(np.floor(min(r_a, r_b))) - margin_px
        row1 = int(np.floor(max(r_a, r_b))) + margin_px
        heights = np.full((row1 - row0 + 1, col1 - col0 + 1), np.nan, dtype=np.float32)
        r0, r1 = max(row0, 0), min(row1, self.height - 1)
        c0, c1 = max(col0, 0), min(col1, self.width - 1)
        if r0 <= r1 and c0 <= c1:
            if self._array is not None:
                block = self._array[r0:r1 + 1, c0:c1 + 1]
            else:
                from rasterio.windows import Window as RioWindow
                block = self._clean(self._src.read(
                    1, window=RioWindow(c0, r0, c1 - c0 + 1, r1 - r0 + 1)).astype(np.float32))
            heights[r0 - row0:r1 - row0 + 1, c0 - col0:c1 - col0 + 1] = block
        return Window(heights=heights,
                      x0=self.left + (col0 + 0.5) * self.dx,
                      y0=self.top + (row0 + 0.5) * self.dy,
                      dx=self.dx, dy=self.dy)

    def height_at(self, lat_deg: float, lon_deg: float) -> float:
        """Height above the reference sphere, bilinear between the four nearest pixel centres."""
        x, y = (v[0] for v in self.latlon_to_xy(lat_deg, lon_deg))
        w = self.read_window(x, y, x, y, margin_px=1)
        fc = (x - w.x0) / w.dx
        fr = (y - w.y0) / w.dy
        c, r = int(np.floor(fc)), int(np.floor(fr))
        tc, tr = fc - c, fr - r
        z = w.heights[r:r + 2, c:c + 2].astype(float)
        if z.shape != (2, 2) or np.isnan(z).any():
            raise InsufficientData(f"no terrain at lat {lat_deg}, lon {lon_deg}")
        top = z[0, 0] * (1 - tc) + z[0, 1] * tc
        bottom = z[1, 0] * (1 - tc) + z[1, 1] * tc
        return float(top * (1 - tr) + bottom * tr)

    def close(self):
        self._src.close()
