"""Tests for scripts/fetch_data.py. No network: downloads are replaced by a fake."""

import hashlib
import urllib.error
from pathlib import Path

from core import config
from core.datasets import Dataset
from scripts import fetch_data as fd

PAYLOAD_A = b"terrain bytes " * 10
PAYLOAD_B = b"kernel"


def make_ds(key, payload, sha=False, size=None):
    return Dataset(
        key=key,
        filename=f"{key}.bin",
        url=f"https://example.invalid/{key}.bin",
        size_bytes=len(payload) if size is None else size,
        sha256=hashlib.sha256(payload).hexdigest() if sha else None,
        purpose="test",
    )


class FakeDownload:
    """Writes preset bytes for each URL and records which URLs were requested."""

    def __init__(self, payloads, fail=()):
        self.payloads = payloads
        self.fail = set(fail)
        self.calls = []

    def __call__(self, url, dest, progress=None):
        self.calls.append(url)
        if url in self.fail:
            Path(dest).write_bytes(b"partial")
            raise urllib.error.URLError("connection reset")
        Path(dest).write_bytes(self.payloads[url])


def no_part_files(folder):
    return not list(Path(folder).glob("*.part"))


def test_missing_file_is_downloaded(tmp_path):
    ds = make_ds("a", PAYLOAD_A)
    dl = FakeDownload({ds.url: PAYLOAD_A})
    assert fd.fetch_one(ds, tmp_path, dl, log=lambda m: None)
    assert (tmp_path / ds.filename).read_bytes() == PAYLOAD_A
    assert dl.calls == [ds.url]
    assert no_part_files(tmp_path)


def test_valid_file_is_not_downloaded_again(tmp_path):
    ds = make_ds("a", PAYLOAD_A, sha=True)
    (tmp_path / ds.filename).write_bytes(PAYLOAD_A)
    dl = FakeDownload({})
    assert fd.fetch_one(ds, tmp_path, dl, log=lambda m: None)
    assert dl.calls == []


def test_second_run_downloads_nothing(tmp_path):
    datasets = [make_ds("a", PAYLOAD_A), make_ds("b", PAYLOAD_B)]
    dl = FakeDownload({d.url: p for d, p in zip(datasets, [PAYLOAD_A, PAYLOAD_B])})
    assert fd.fetch_all(tmp_path, datasets, dl, log=lambda m: None)
    assert len(dl.calls) == 2
    assert fd.fetch_all(tmp_path, datasets, dl, log=lambda m: None)
    assert len(dl.calls) == 2


def test_short_download_is_rejected(tmp_path):
    ds = make_ds("a", PAYLOAD_A)
    dl = FakeDownload({ds.url: PAYLOAD_A[:-3]})
    assert not fd.fetch_one(ds, tmp_path, dl, log=lambda m: None)
    assert not (tmp_path / ds.filename).exists()
    assert no_part_files(tmp_path)


def test_wrong_sha256_is_rejected(tmp_path):
    ds = make_ds("a", PAYLOAD_A, sha=True)
    tampered = b"X" + PAYLOAD_A[1:]  # same size, different content
    dl = FakeDownload({ds.url: tampered})
    assert not fd.fetch_one(ds, tmp_path, dl, log=lambda m: None)
    assert not (tmp_path / ds.filename).exists()
    assert no_part_files(tmp_path)


def test_network_error_leaves_no_partial_file(tmp_path):
    ds = make_ds("a", PAYLOAD_A)
    dl = FakeDownload({}, fail=[ds.url])
    assert not fd.fetch_one(ds, tmp_path, dl, log=lambda m: None)
    assert not (tmp_path / ds.filename).exists()
    assert no_part_files(tmp_path)


def test_corrupt_existing_file_is_replaced(tmp_path):
    ds = make_ds("a", PAYLOAD_A)
    (tmp_path / ds.filename).write_bytes(b"old and wrong")
    dl = FakeDownload({ds.url: PAYLOAD_A})
    assert fd.fetch_one(ds, tmp_path, dl, log=lambda m: None)
    assert (tmp_path / ds.filename).read_bytes() == PAYLOAD_A


def test_failed_file_does_not_stop_the_others(tmp_path):
    a, b = make_ds("a", PAYLOAD_A), make_ds("b", PAYLOAD_B)
    dl = FakeDownload({b.url: PAYLOAD_B}, fail=[a.url])
    assert not fd.fetch_all(tmp_path, [a, b], dl, log=lambda m: None)
    assert (tmp_path / b.filename).exists()


def test_main_check_and_fetch(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(config.DATA_DIR_ENV, str(tmp_path / "data"))
    datasets = [make_ds("a", PAYLOAD_A), make_ds("b", PAYLOAD_B)]
    dl = FakeDownload({d.url: p for d, p in zip(datasets, [PAYLOAD_A, PAYLOAD_B])})

    assert fd.main(["--check"], datasets, dl) == 1
    assert "missing" in capsys.readouterr().out
    assert dl.calls == []

    assert fd.main([], datasets, dl) == 0
    assert fd.main(["--check"], datasets, dl) == 0


def test_main_hash_prints_sha256(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(config.DATA_DIR_ENV, str(tmp_path))
    ds = make_ds("a", PAYLOAD_A)
    (tmp_path / ds.filename).write_bytes(PAYLOAD_A)
    assert fd.main(["--hash"], [ds]) == 0
    assert hashlib.sha256(PAYLOAD_A).hexdigest() in capsys.readouterr().out
