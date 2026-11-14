"""Manifest of the data files the core needs.

Sizes were read from the publishers' servers (HTTP Content-Length) on 2026-11-14.
SHA-256 values are filled in after the first verified download
(`python scripts/fetch_data.py --hash`). Until then only sizes are checked.
"""

from dataclasses import dataclass
from pathlib import Path

from core import config


@dataclass(frozen=True)
class Dataset:
    key: str
    filename: str
    url: str
    size_bytes: int
    sha256: str | None
    purpose: str


DATASETS: tuple[Dataset, ...] = (
    Dataset(
        key="terrain",
        filename="LDEM_80S_80MPP_ADJ.TIF",
        url="https://pgda.gsfc.nasa.gov/data/LOLA_20mpp/LDEM_80S_80MPP_ADJ.TIF",
        size_bytes=189_158_392,
        sha256=None,
        purpose="LOLA south pole elevation model, 80 m per pixel (NASA GSFC PGDA product 90)",
    ),
    Dataset(
        key="ephemeris",
        filename="de440s.bsp",
        url="https://naif.jpl.nasa.gov/pub/naif/generic_kernels/spk/planets/de440s.bsp",
        size_bytes=32_726_016,
        sha256=None,
        purpose="JPL planetary ephemeris DE440: Sun, Earth and Moon positions",
    ),
    Dataset(
        key="moon_orientation",
        filename="moon_pa_de440_200625.bpc",
        url="https://naif.jpl.nasa.gov/pub/naif/generic_kernels/pck/moon_pa_de440_200625.bpc",
        size_bytes=12_863_488,
        sha256=None,
        purpose="Lunar orientation for DE440",
    ),
    Dataset(
        key="moon_frames",
        filename="moon_de440_250416.tf",
        url="https://naif.jpl.nasa.gov/pub/naif/generic_kernels/fk/satellites/moon_de440_250416.tf",
        size_bytes=19_478,
        sha256=None,
        purpose="Lunar frame kernel, defines MOON_ME_DE440_ME421",
    ),
    Dataset(
        key="constants",
        filename="pck00011.tpc",
        url="https://naif.jpl.nasa.gov/pub/naif/generic_kernels/pck/pck00011.tpc",
        size_bytes=131_226,
        sha256=None,
        purpose="NAIF planetary constants: lunar radius",
    ),
)


def by_key(key: str) -> Dataset:
    """Return the manifest entry with this key."""
    for ds in DATASETS:
        if ds.key == key:
            return ds
    raise KeyError(f"unknown dataset key: {key}")


def path_of(key: str, base: Path | None = None) -> Path:
    """Return the local path of a dataset file."""
    return (base or config.data_dir()) / by_key(key).filename
