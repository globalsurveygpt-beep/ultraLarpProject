# LUCY

**L**unar **U**ptime & **C**ommunication **Y**ardstick

Pick a point near the lunar south pole and a date, and see whether a lander could operate there: is the Sun above the local horizon for the batteries, and is Earth visible for communication?

> Built for the NASA Space Apps Challenge 2026. Independent project, not affiliated with or endorsed by NASA.

**Status:** working prototype for the hackathon. The calculation core, the API and the interface run on real LOLA terrain and JPL ephemerides.

## Demo

- Live demo: [lucy.guardcontract.app](https://lucy.guardcontract.app)
- Video: _link will be added here_ (YouTube).

### Screenshots

**Site analysis.** One question and one answer: can a lander operate here, on this date. Four example scenarios (A to D) show every kind of outcome.

![Site analysis: the answer card and example scenarios](screenshots/01-site-analysis.jpg)

**Assessment and charts.** The assessment against the mission profile, daily Sun and Earth geometry, the nearest acceptable window and the visibility timeline.

![Assessment, daily charts and timeline](screenshots/02-assessment-and-charts.jpg)

**Window search.** Reverse search over a 5 km grid: where and when a landing is possible for the chosen mission conditions.

![Window search: grid map and the ten best sites](screenshots/03-window-search.jpg)

**Comparison.** Up to three sites side by side, each with its own map or 3D view, updated automatically.

![Comparison: three site cards](screenshots/04-comparison-sites.jpg)

![Comparison: window table and visibility bars](screenshots/05-comparison-table.jpg)

## Problem

Near the lunar south pole the Sun skims the horizon, and terrain casts long shadows. A landing site has to offer both sunlight for power and a line of sight to Earth for communication. These conditions change with location and date, and the trade-offs are hard to see in raw data.

## What it does

1. **Reverse search:** find where and when a landing is possible for a given set of mission conditions (5 km grid over the 154 km working zone).
2. **Side-by-side comparison** of 2-3 candidate sites on one screen, with three slots (A, B, C), each with its own map or 3D view. The comparison updates itself and keeps the chosen sites when you leave the page and come back.
3. **Plain metrics** instead of an arbitrary score: longest night, Sun share, longest and total time without Earth.
4. **Live calculation** for any date from 2000 to 2050.
5. **One-sentence explanation** of every result.
6. **Nearest acceptable window** for a site: the first start within five years that meets the thresholds.
7. **Site view:** the terrain around the site in 3D, with the Sun and Earth at the chosen moment. Overview mode looks at the site from outside; Observer mode looks from the lander.
8. **Example scenarios** (four ready points) so that every kind of outcome can be tried with one click. They are examples, not recommendations.

## How it works

The calculation is deterministic and reproducible. It uses no AI and no estimates by eye.

1. **Terrain and horizon preparation:** a horizon profile is computed once per point from a lunar elevation raster.
2. **Calculation core:** for a point and a moment in time, checks whether the Sun and Earth are above the local horizon.
3. **Mission logic:** one mission profile (duration, longest night, longest comms gap, minimum Sun share, instrument height) checked against each window; lists of acceptable landing windows.
4. **API:** a thin FastAPI layer between the core and the interface.
5. **Interface:** a flat polar map, the visibility timeline, daily charts, the 3D site view, a comparison page and the window search.

The demo is hosted on our own server. Terrain and ephemeris files are stored on the server disk, and Sun and Earth positions are computed there with Skyfield. The calculation does not call any external API. The server setup is described in [deploy/README.md](deploy/README.md).

## Verification against JPL Horizons

The Sun and Earth directions computed with Skyfield are checked against an independent source, the JPL Horizons service. The reference values (54 directions at three sites) are stored in the repository as `tests/fixtures/horizons_reference.json`, so the check needs no network access. The largest difference is 0.000022 degrees, far inside the tolerance of 0.01 degrees. Horizons uses the DE441 ephemeris and our calculation uses DE440, and the two agree to this level.

```bash
python scripts/verify_ephemeris.py
```

The result is also shown at the end of the site analysis page in the interface.

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

- The Sun and Earth are treated as points at the centre of each body. The partial Sun disc is not modelled, so the edges of a sunrise or sunset are approximate.
- Eclipses of the Sun by Earth are not modelled.
- Communication means a direct line of sight to Earth. Relay satellites are not considered.
- The example scenarios show what kinds of outcomes exist. They are not recommendations of landing sites.

## Use of AI

The code, tests and documentation of this project were written with the help of AI, under our direction: the goals, the rules and every decision were set by the team, and we reviewed the results. The calculation itself contains no AI. It is plain, deterministic mathematics on real data, and the same inputs always give the same result.

## Run locally

Python 3.12 or 3.13. From the repository root:

```bash
python -m venv .venv
.venv/Scripts/activate        # Linux and macOS: source .venv/bin/activate
pip install -r requirements.txt
python scripts/fetch_data.py                 # about 235 MB into data/
python scripts/precompute_horizons.py        # horizons for the window search (once)
uvicorn api.main:app --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000>. The server also serves the interface. Tests: `pip install -r requirements-dev.txt`, then `pytest`.
The map background is already in `web/assets/img/`; `python scripts/render_basemap.py` redraws it from the terrain.

## Tech stack

- Backend: Python, FastAPI, NumPy, rasterio, Skyfield
- Frontend: vanilla JavaScript, hand-drawn SVG charts and map, Three.js (vendored, MIT) for the 3D site view

## Contributing

Issues are open to everyone. Code contributions come through pull requests from approved contributors. See the repository settings for details.

## License

The source code is released under the [MIT License](LICENSE). The datasets listed above remain under the terms of their original providers.
