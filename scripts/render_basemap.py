"""Render the map background of the web interface from the LOLA terrain.

Usage:
    python scripts/render_basemap.py                 web/assets/img/basemap.jpg, +-160 km, 80 m
    python scripts/render_basemap.py --half-km 200 --pixel-m 100

The image is a crop of the terrain raster around the south pole, in the raster's own south
polar stereographic projection: pixel columns grow east along x, rows grow south along y,
0 deg E points up. The interface converts a pixel to latitude and longitude with the same
closed-form formulas as core/terrain.py, so the map and the calculation agree exactly.

Brightness is a hillshade with a fixed light direction, chosen for legibility only. It is
not the real illumination of any date; the interface says so under the map.
A JSON file next to the image records the extent and the sphere radius for the interface.
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core import config  # noqa: E402
from core.terrain import Terrain  # noqa: E402

LIGHT_AZIMUTH_DEG = 315.0   # light from the upper left of the image, the usual cartographic choice
LIGHT_ALTITUDE_DEG = 35.0
ELEVATION_SHARE = 0.25      # share of brightness taken from height, so deep craters read darker


def hillshade(z: np.ndarray, pixel_m: float) -> np.ndarray:
    """Lambertian shading in 0..1 for a grid whose rows go down (south) and columns go right (east)."""
    dz_dy, dz_dx = np.gradient(z, pixel_m)
    dz_dnorth = -dz_dy  # rows grow southwards
    az, alt = np.radians(LIGHT_AZIMUTH_DEG), np.radians(LIGHT_ALTITUDE_DEG)
    # Image azimuth: 0 = up, clockwise. Light vector in (right, up, out of the surface).
    lx, ly, lz = np.sin(az) * np.cos(alt), np.cos(az) * np.cos(alt), np.sin(alt)
    shade = (-dz_dx * lx - dz_dnorth * ly + lz) / np.sqrt(1.0 + dz_dx ** 2 + dz_dnorth ** 2)
    return np.clip(shade, 0.0, 1.0)


def render(terrain: Terrain, half_m: float, pixel_m: float) -> np.ndarray:
    n = int(round(2 * half_m / pixel_m))
    w = terrain.read_window(-half_m, -half_m, half_m, half_m, margin_px=0)
    z = w.heights.astype(np.float64)
    if np.isnan(z).any():
        raise SystemExit("the crop reaches past the terrain; choose a smaller --half-km")
    step = pixel_m / terrain.pixel_size_m
    if abs(step - round(step)) > 1e-9 or step < 1:
        raise SystemExit(f"--pixel-m must be a whole multiple of the terrain pixel ({terrain.pixel_size_m:g} m)")
    step = int(round(step))
    if step > 1:  # block mean keeps the relief honest when shrinking
        rows, cols = (z.shape[0] // step) * step, (z.shape[1] // step) * step
        z = z[:rows, :cols].reshape(rows // step, step, cols // step, step).mean(axis=(1, 3))
    z = z[:n, :n]
    lo, hi = np.percentile(z, [1, 99])
    elev = np.clip((z - lo) / (hi - lo), 0, 1)
    shade = hillshade(z, pixel_m)
    grey = (1 - ELEVATION_SHARE) * shade + ELEVATION_SHARE * elev
    return np.round(255 * grey ** 0.9).astype(np.uint8)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Render the web map background from the LOLA terrain.")
    parser.add_argument("--half-km", type=float, default=160.0)
    parser.add_argument("--pixel-m", type=float, default=80.0)
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "web" / "assets" / "img" / "basemap.jpg")
    parser.add_argument("--quality", type=int, default=82)
    args = parser.parse_args(argv)

    import warnings

    import rasterio
    from rasterio.errors import NotGeoreferencedWarning

    terrain = Terrain()
    half_m = args.half_km * 1000
    image = render(terrain, half_m, args.pixel_m)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    warnings.simplefilter("ignore", NotGeoreferencedWarning)  # a plain picture: the extent goes to the JSON
    with rasterio.open(args.out, "w", driver="JPEG", width=image.shape[1], height=image.shape[0],
                       count=1, dtype="uint8", QUALITY=args.quality) as dst:
        dst.write(image, 1)
    for extra in args.out.parent.glob(args.out.name + ".aux.xml"):
        extra.unlink()
    meta = {
        "image": args.out.name,
        "projection": "south polar stereographic, sphere, true scale at the pole",
        "sphere_radius_m": terrain.radius_m,
        "half_size_m": half_m,
        "pixels": image.shape[0],
        "pixel_m": args.pixel_m,
        "working_zone_m": config.WORKING_ZONE_RADIUS_M,
        "terrain": terrain.path.name,
        "light": {"azimuth_deg": LIGHT_AZIMUTH_DEG, "altitude_deg": LIGHT_ALTITUDE_DEG,
                  "note": "display only, not the illumination of any date"},
    }
    args.out.with_suffix(".json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    terrain.close()
    print(f"{args.out} {image.shape[1]}x{image.shape[0]} px, {args.out.stat().st_size / 1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
