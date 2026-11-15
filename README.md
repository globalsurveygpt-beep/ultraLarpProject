# LUCY

**L**unar **U**ptime & **C**ommunication **Y**ardstick

Pick a point near the lunar south pole and a date, and see whether a lander could operate there: is the Sun above the local horizon for the batteries, and is Earth visible for communication?

> Built for the NASA Space Apps Challenge 2026. Independent project, not affiliated with or endorsed by NASA.

**Status:** work in progress. Nothing below is final until marked as done.

## Demo

- Live demo: _TODO_
- Video: _TODO_
- Screenshot: _TODO_

## Problem

Near the lunar south pole the Sun skims the horizon, and terrain casts long shadows. A landing site has to offer both sunlight for power and a line of sight to Earth for communication. These conditions change with location and date, and the trade-offs are hard to see in raw data.

## What it does

1. **Reverse search:** find where and when a landing is possible for a given set of mission conditions.
2. **Side-by-side comparison** of 2-3 candidate sites on one screen.
3. **Plain metrics** instead of an arbitrary score: longest night, hours without communication.
4. **Live calculation** for any date.
5. **One-sentence explanation** of every result.

## How it works

The calculation is deterministic and reproducible. It uses no AI and no estimates by eye.

1. **Terrain and horizon preparation:** a horizon profile is computed once per point from a lunar elevation raster.
2. **Calculation core:** for a point and a moment in time, checks whether the Sun and Earth are above the local horizon.
3. **Mission logic:** one mission profile (duration, longest night, longest comms gap, minimum Sun share, instrument height) checked against each window; lists of acceptable landing windows.
4. **API:** a thin FastAPI layer between the core and the interface.
5. **Interface:** a flat polar map, a calendar, charts and comparison view.

The demo is hosted on our own server. Terrain and ephemeris files are stored on the server disk, and Sun and Earth positions are computed there with Skyfield. The calculation does not call any external API.

## Data

| Dataset | Use | Source |
|---|---|---|
| LOLA south pole elevation model, 80 m per pixel (`LDEM_80S_80MPP_ADJ.TIF`), NASA GSFC PGDA product 90 | Terrain and horizon | [pgda.gsfc.nasa.gov/products/90](https://pgda.gsfc.nasa.gov/products/90) |
| JPL planetary ephemeris DE440 (`de440s.bsp`) | Sun, Earth and Moon positions | [NAIF generic kernels, spk/planets](https://naif.jpl.nasa.gov/pub/naif/generic_kernels/spk/planets/) |
| Lunar orientation for DE440 (`moon_pa_de440_200625.bpc`) | Orientation of the Moon | [NAIF generic kernels, pck](https://naif.jpl.nasa.gov/pub/naif/generic_kernels/pck/) |
| Lunar frame kernel (`moon_de440_250416.tf`) | Reference frame that matches the terrain data | [NAIF generic kernels, fk/satellites](https://naif.jpl.nasa.gov/pub/naif/generic_kernels/fk/satellites/) |
| Planetary constants (`pck00011.tpc`) | Radius of the Moon | [NAIF generic kernels, pck](https://naif.jpl.nasa.gov/pub/naif/generic_kernels/pck/) |

All files go into `data/`. `python scripts/fetch_data.py` downloads and checks them. If you download the terrain by hand, either extension works: a browser may save it as `.tiff`, and letter case does not matter.

The terrain data is described in Barker et al. (2023), [doi:10.3847/PSJ/acf3e1](https://doi.org/10.3847/PSJ/acf3e1).

The data files are not stored in this repository (about 235 MB in total). They are downloaded separately into the `data/` folder.

Any synthetic or placeholder data is labelled as such in the code and in the interface.

## Limitations

The tool shows allowed landing windows based on illumination and Earth visibility. It does not guarantee that a mission will succeed and does not model other mission constraints.

## Run locally

_TODO: setup, data download and start instructions. They will be added together with the data download script._

## Tech stack

- Backend: Python, FastAPI, NumPy, rasterio, Skyfield
- Frontend: vanilla JavaScript, Chart.js

## Contributing

Issues are open to everyone. Code contributions come through pull requests from approved contributors. See the repository settings for details.

## License

The source code is released under the [MIT License](LICENSE). The datasets listed above remain under the terms of their original providers.
