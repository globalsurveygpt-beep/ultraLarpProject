import re
from pathlib import Path

import pytest

from core import config
from core.datasets import DATASETS, by_key, path_of


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
