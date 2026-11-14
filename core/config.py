"""Settings shared by the core and the scripts.

Calculation parameters below were approved by the project lead on 2026-11-14
(see the core plan). Change them only with that approval.
"""

import os
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
AZIMUTH_STEP_DEG = 0.5

# Time series.
TIME_STEP_H = 1.0
TRANSITION_PRECISION_MIN = 1.0
