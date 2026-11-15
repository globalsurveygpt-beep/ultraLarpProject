import re
from pathlib import Path

import pytest

from core import config
from core.datasets import DATASETS, by_key, locate, path_of


def test_manifest_has_five_files():
    assert len(DATASETS) == 5


def test_keys_and_filenames_are_unique():
    assert len({d.key for d in DATASETS}) == len(DATASETS)
    assert len({d.filename for d in DATASETS}) == len(DATASETS)


def test_urls_are_https_and_end_with_filename():
    for d in DATASETS:
        assert d.url.startswith("https://")
        assert d.url.rsplit("/", 1)[1] == d.filename


def test_sizes_match_the_published_total():
    assert all(d.size_bytes > 0 for d in DATASETS)
    assert sum(d.size_bytes for d in DATASETS) == 234_898_600


def test_sha256_is_empty_or_hex():
    for d in DATASETS:
        assert d.sha256 is None or re.fullmatch(r"[0-9a-f]{64}", d.sha256)


def test_by_key_and_unknown_key():
    assert by_key("terrain").filename == "LDEM_80S_80MPP_ADJ.TIF"
    with pytest.raises(KeyError):
        by_key("nope")


def test_data_dir_default(monkeypatch):
    monkeypatch.delenv(config.DATA_DIR_ENV, raising=False)
    assert config.data_dir() == config.REPO_ROOT / "data"


def test_data_dir_from_environment(monkeypatch, tmp_path):
    monkeypatch.setenv(config.DATA_DIR_ENV, str(tmp_path))
    assert config.data_dir() == tmp_path.resolve()
    assert path_of("ephemeris") == tmp_path.resolve() / "de440s.bsp"


def test_path_of_with_explicit_base():
    assert path_of("constants", Path("/x")) == Path("/x") / "pck00011.tpc"


# ---------- file name variants ----------

TERRAIN = by_key("terrain")


@pytest.mark.parametrize("name", ["LDEM_80S_80MPP_ADJ.TIF", "LDEM_80S_80MPP_ADJ.tif",
                                  "LDEM_80S_80MPP_ADJ.tiff", "LDEM_80S_80MPP_ADJ.TIFF",
                                  "ldem_80s_80mpp_adj.Tiff"])
def test_terrain_found_under_any_tiff_name(tmp_path, name):
    (tmp_path / name).write_bytes(b"x")
    assert locate(TERRAIN, tmp_path) == tmp_path / name
    assert path_of("terrain", tmp_path) == tmp_path / name


def test_exact_name_wins_over_a_variant(tmp_path):
    for name in ("LDEM_80S_80MPP_ADJ.tiff", "LDEM_80S_80MPP_ADJ.TIF"):
        (tmp_path / name).write_bytes(b"x")
    assert locate(TERRAIN, tmp_path).name == "LDEM_80S_80MPP_ADJ.TIF"


@pytest.mark.parametrize("name", ["LDEM_80S_80MPP_ADJ.TIF.part", "LDEM_80S_80MPP_ADJ.img",
                                  "LDEM_80S_80MPP_ADJ (1).tiff", "LDEM_80S_20MPP_ADJ.TIF"])
def test_other_files_are_not_the_terrain(tmp_path, name):
    (tmp_path / name).write_bytes(b"x")
    assert locate(TERRAIN, tmp_path) is None
    assert path_of("terrain", tmp_path) == tmp_path / "LDEM_80S_80MPP_ADJ.TIF"


def test_only_tiff_gets_the_extension_variant(tmp_path):
    (tmp_path / "DE440S.BSP").write_bytes(b"x")
    (tmp_path / "pck00011.tpcc").write_bytes(b"x")
    assert locate(by_key("ephemeris"), tmp_path) == tmp_path / "DE440S.BSP"  # case only
    assert locate(by_key("constants"), tmp_path) is None


def test_missing_folder_finds_nothing(tmp_path):
    assert locate(TERRAIN, tmp_path / "nope") is None
