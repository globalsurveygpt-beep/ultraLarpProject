# Project name (TBD)

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
3. **Mission logic:** mission duration profiles and lists of allowed landing windows.
4. **API:** a thin FastAPI layer between the core and the interface.
5. **Interface:** a flat polar map, a calendar, charts and comparison view.

Sun and Earth positions are computed locally with Skyfield, so the demo works without internet access.

## Data

| Dataset | Use | Source |
|---|---|---|
| LOLA lunar elevation, south polar region | Terrain and horizon | _TODO: exact product, resolution and link_ |
| JPL ephemerides DE440 (`de440s.bsp`) | Sun and Earth positions | _TODO: link_ |

Any synthetic or placeholder data is labelled as such in the code and in the interface.

## Limitations

The tool shows allowed landing windows based on illumination and Earth visibility. It does not guarantee that a mission will succeed and does not model other mission constraints.

## Run locally

_TODO: setup, data download and start instructions._

## Tech stack

- Backend: Python, FastAPI, NumPy, rasterio, Skyfield
- Frontend: vanilla JavaScript, Chart.js

## Contributing

Issues are open to everyone. Code contributions come through pull requests from approved contributors. See the repository settings for details.

## License

The source code is released under the [MIT License](LICENSE). The datasets listed above remain under the terms of their original providers.
