"""Settings shared by the core and the scripts.

Calculation parameters below were approved by the project lead on 2026-11-14
(see the core plan). Change them only with that approval.
"""

import os
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Data folder: `data/` in the repository root, or the path in this environment variable.
DATA_DIR_ENV = "LUNAR_DATA_DIR"


def data_dir() -> Path:
    """Return the folder that holds the terrain and ephemeris files."""
    value = os.environ.get(DATA_DIR_ENV)
    if value:
        return Path(value).expanduser().resolve()
    return REPO_ROOT / "data"


# Mean lunar radius in metres. Same value in the LOLA raster CRS (IAU 30100 sphere)
# and in the NAIF planetary constants file pck00011.tpc.
MOON_RADIUS_M = 1_737_400.0

# Lunar body-fixed frame that matches the LOLA terrain (Mean Earth, aligned with DE421).
LUNAR_FRAME = "MOON_ME_DE440_ME421"

# Horizon calculation.
HORIZON_RADIUS_M = 150_000.0
# Working zone: sites up to this distance from the pole. With the LOLA 80 m raster
# (+-304 km) and a 150 km horizon this is the farthest site whose circle fits.
WORKING_ZONE_RADIUS_M = 154_000.0
AZIMUTH_STEP_DEG = 0.5

# Time series. Visibility is evaluated on a continuous 1-minute grid: an hourly grid
# misses events shorter than an hour (measured on LOLA: up to 13 such Sun events in
# 90 days at one site), and those split nights. Approved 2026-11-14.
TIME_STEP_MIN = 1.0
# Sampling of the one-day series used for charts.
DAY_SERIES_STEP_MIN = 10.0

# Allowed mission window length, days.
WINDOW_DAYS_MIN = 1.0
WINDOW_DAYS_MAX = 60.0

# Supported dates: 2000 to 2050 inclusive, UTC. The data files cover more
# (de440s.bsp 1849-2150, lunar orientation 1550-2650); this is the product range.
DATE_MIN_UTC = datetime(2000, 1, 1, tzinfo=timezone.utc)
DATE_END_UTC = datetime(2051, 1, 1, tzinfo=timezone.utc)  # exclusive
