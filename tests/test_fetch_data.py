"""Tests for scripts/fetch_data.py. No internet: downloads are replaced by a fake, and
http_download is tested against a small server on localhost."""

import hashlib
import http.server
import threading
import urllib.error
from pathlib import Path

import pytest

from core import config
from core.datasets import Dataset
from scripts import fetch_data as fd

PAYLOAD_A = b"terrain bytes " * 10
PAYLOAD_B = b"kernel"


@pytest.fixture(autouse=True)
def no_retry_pause(monkeypatch):
    monkeypatch.setattr(fd, "RETRY_PAUSE_S", 0.0)


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
    """Behaves like http_download with a server that honours Range requests.

    Each call continues from the end of `dest` and appends at most `per_call` bytes,
    then the "connection closes". URLs in `fail` append a few bytes and then raise.
    `calls` records the URLs, `offsets` the byte offset each call started from.
    """

    def __init__(self, payloads, fail=(), per_call=None):
        self.payloads = payloads
        self.fail = set(fail)
        self.per_call = per_call
        self.calls, self.offsets = [], []

    def __call__(self, url, dest, progress=None):
        dest = Path(dest)
        have = dest.stat().st_size if dest.is_file() else 0
        self.calls.append(url)
        self.offsets.append(have)
        if url in self.fail:
            with open(dest, "ab") as f:
                f.write(b"par")
            raise urllib.error.URLError("connection reset")
        rest = self.payloads[url][have:]
        if self.per_call is not None:
            rest = rest[:self.per_call]
        with open(dest, "ab") as f:
            f.write(rest)


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


def test_short_download_is_never_used(tmp_path):
    # the server keeps closing the connection 3 bytes before the end
    ds = make_ds("a", PAYLOAD_A)
    dl = FakeDownload({ds.url: PAYLOAD_A[:-3]})
    assert not fd.fetch_one(ds, tmp_path, dl, log=lambda m: None)
    assert not (tmp_path / ds.filename).exists()
    assert len(dl.calls) == fd.ATTEMPTS


def test_dropped_connection_is_resumed(tmp_path):
    ds = make_ds("a", PAYLOAD_A, sha=True)
    dl = FakeDownload({ds.url: PAYLOAD_A}, per_call=50)  # 140 bytes in three pieces
    assert fd.fetch_one(ds, tmp_path, dl, log=lambda m: None)
    assert (tmp_path / ds.filename).read_bytes() == PAYLOAD_A
    assert dl.offsets == [0, 50, 100]
    assert no_part_files(tmp_path)


def test_part_left_by_a_failed_run_is_resumed_by_the_next(tmp_path, monkeypatch):
    monkeypatch.setattr(fd, "ATTEMPTS", 2)
    ds = make_ds("a", PAYLOAD_A)
    dl = FakeDownload({ds.url: PAYLOAD_A}, per_call=30)
    assert not fd.fetch_one(ds, tmp_path, dl, log=lambda m: None)
    part = tmp_path / (ds.filename + ".part")
    assert part.stat().st_size == 60 and not (tmp_path / ds.filename).exists()
    monkeypatch.setattr(fd, "ATTEMPTS", 5)
    assert fd.fetch_one(ds, tmp_path, dl, log=lambda m: None)
    assert dl.offsets[2] == 60
    assert (tmp_path / ds.filename).read_bytes() == PAYLOAD_A


def test_oversized_part_is_discarded(tmp_path):
    ds = make_ds("a", PAYLOAD_A)
    (tmp_path / (ds.filename + ".part")).write_bytes(b"x" * (len(PAYLOAD_A) + 1))
    dl = FakeDownload({ds.url: PAYLOAD_A})
    assert fd.fetch_one(ds, tmp_path, dl, log=lambda m: None)
    assert dl.offsets == [0]
    assert (tmp_path / ds.filename).read_bytes() == PAYLOAD_A


def test_wrong_sha256_is_rejected(tmp_path):
    ds = make_ds("a", PAYLOAD_A, sha=True)
    tampered = b"X" + PAYLOAD_A[1:]  # same size, different content
    dl = FakeDownload({ds.url: tampered})
    assert not fd.fetch_one(ds, tmp_path, dl, log=lambda m: None)
    assert not (tmp_path / ds.filename).exists()
    assert no_part_files(tmp_path)


def test_network_errors_are_retried_and_never_produce_the_working_file(tmp_path):
    ds = make_ds("a", PAYLOAD_A)
    dl = FakeDownload({}, fail=[ds.url])
    assert not fd.fetch_one(ds, tmp_path, dl, log=lambda m: None)
    assert len(dl.calls) == fd.ATTEMPTS
    assert not (tmp_path / ds.filename).exists()


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


class _RangeHandler(http.server.BaseHTTPRequestHandler):
    """Serves PAYLOAD_A. Honours Range when `honour_range` is set; the first full
    response is cut after `cut` bytes, as a dropped connection would be."""

    honour_range = True
    cut = None
    seen_ranges: list = []

    def do_GET(self):
        rng = self.headers.get("Range")
        type(self).seen_ranges.append(rng)
        start = int(rng.split("=")[1].rstrip("-")) if rng and self.honour_range else 0
        body = PAYLOAD_A[start:]
        self.send_response(206 if start else 200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.cut is not None and not start:
            body = body[:self.cut]
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def local_server():
    def start(honour_range, cut):
        handler = type("H", (_RangeHandler,), {"honour_range": honour_range, "cut": cut, "seen_ranges": []})
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return f"http://127.0.0.1:{server.server_port}/a.bin", handler

    servers = []
    yield start
    for s in servers:
        s.shutdown()
        s.server_close()


@pytest.mark.parametrize("honour_range", [True, False])
def test_http_download_resumes_with_range(tmp_path, local_server, honour_range):
    url, handler = local_server(honour_range, cut=None)
    dest = tmp_path / "a.bin.part"
    dest.write_bytes(PAYLOAD_A[:40])
    fd.http_download(url, dest)
    assert handler.seen_ranges == ["bytes=40-"]
    assert dest.read_bytes() == PAYLOAD_A  # appended on 206, rewritten on 200


def test_fetch_one_over_http_survives_a_dropped_connection(tmp_path, local_server):
    url, handler = local_server(True, cut=25)
    ds = Dataset("a", "a.bin", url, len(PAYLOAD_A), hashlib.sha256(PAYLOAD_A).hexdigest(), "test")
    assert fd.fetch_one(ds, tmp_path, log=lambda m: None)
    assert handler.seen_ranges == [None, "bytes=25-"]
    assert (tmp_path / "a.bin").read_bytes() == PAYLOAD_A


def test_main_hash_prints_sha256(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(config.DATA_DIR_ENV, str(tmp_path))
    ds = make_ds("a", PAYLOAD_A)
    (tmp_path / ds.filename).write_bytes(PAYLOAD_A)
    assert fd.main(["--hash"], [ds]) == 0
    assert hashlib.sha256(PAYLOAD_A).hexdigest() in capsys.readouterr().out


def make_tif(payload):
    return Dataset(key="terrain", filename="T.TIF", url="https://example.invalid/T.TIF",
                   size_bytes=len(payload), sha256=None, purpose="test")


def test_tiff_saved_by_a_browser_counts_as_present(tmp_path):
    ds = make_tif(PAYLOAD_A)
    (tmp_path / "T.tiff").write_bytes(PAYLOAD_A)
    dl = FakeDownload({ds.url: PAYLOAD_A})
    assert fd.fetch_one(ds, tmp_path, dl, log=lambda *a: None)
    assert dl.calls == [] and not (tmp_path / "T.TIF").exists()
    assert fd.check_all(tmp_path, [ds], log=lambda *a: None)


def test_wrong_variant_is_replaced_by_a_download(tmp_path):
    ds = make_tif(PAYLOAD_A)
    (tmp_path / "T.tiff").write_bytes(b"short")
    dl = FakeDownload({ds.url: PAYLOAD_A})
    assert fd.fetch_one(ds, tmp_path, dl, log=lambda *a: None)
    assert (tmp_path / "T.TIF").read_bytes() == PAYLOAD_A
    assert fd.check_all(tmp_path, [ds], log=lambda *a: None)  # the exact name wins
