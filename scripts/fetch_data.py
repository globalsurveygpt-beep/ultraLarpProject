"""Download and verify the data files listed in core/datasets.py.

Usage:
    python scripts/fetch_data.py           download missing or invalid files, verify all
    python scripts/fetch_data.py --check   verify only, download nothing
    python scripts/fetch_data.py --hash    print SHA-256 of the files that are present

The data folder is `data/` in the repository root, or the path in LUNAR_DATA_DIR.
Exit code: 0 when every file is present and valid, 1 otherwise.

Safety: a file is downloaded to `<name>.part`, checked against the manifest and only
then renamed to its final name, so an interrupted or broken download never leaves a
bad file under the working name. Files that are already valid are not downloaded again.

Resume: when the connection drops, the download continues from the end of `<name>.part`
(HTTP Range request), up to ATTEMPTS times with a pause between them. A `.part` left by
a failed run is resumed by the next run. A `.part` larger than the manifest size, or a
complete one whose SHA-256 does not match, is deleted.
"""

import argparse
import hashlib
import http.client
import sys
import time
import urllib.request
from pathlib import Path
from typing import Callable, Iterable

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core import config  # noqa: E402
from core.datasets import DATASETS, Dataset  # noqa: E402

USER_AGENT = "lunar-site-data-fetch/1.0"
CHUNK_BYTES = 1 << 20
TIMEOUT_S = 60
ATTEMPTS = 20
RETRY_PAUSE_S = 10.0

Progress = Callable[[int, int], None]
Download = Callable[[str, Path, Progress | None], None]
Log = Callable[[str], None]


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(CHUNK_BYTES), b""):
            h.update(block)
    return h.hexdigest()


def verify(path: Path, ds: Dataset) -> str | None:
    """Return None if the file matches the manifest, otherwise the reason it does not."""
    if not path.is_file():
        return "missing"
    size = path.stat().st_size
    if size != ds.size_bytes:
        return f"size {size} bytes, expected {ds.size_bytes}"
    if ds.sha256 and sha256_of(path) != ds.sha256:
        return "SHA-256 does not match the manifest"
    return None


def http_download(url: str, dest: Path, progress: Progress | None = None) -> None:
    """Stream a URL into a local file, continuing from the end of `dest` if it exists.

    Sends a Range request when `dest` already holds bytes. If the server answers 206 the
    new bytes are appended; if it ignores the range (200) the file is written from scratch.
    """
    have = dest.stat().st_size if dest.is_file() else 0
    headers = {"User-Agent": USER_AGENT}
    if have:
        headers["Range"] = f"bytes={have}-"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
        resumed = have and response.status == 206
        done = have if resumed else 0
        total = int(response.headers.get("Content-Length") or 0)
        total = total + done if total else 0
        with open(dest, "ab" if resumed else "wb") as out:
            _copy(response, out, done, total, progress)


def _copy(response, out, done: int, total: int, progress: Progress | None) -> None:
    while True:
        block = response.read(CHUNK_BYTES)
        if not block:
            break
        out.write(block)
        done += len(block)
        if progress:
            progress(done, total)


def _progress_printer(name: str) -> Progress:
    last = [-1]

    def show(done: int, total: int) -> None:
        if not total:
            return
        pct = done * 100 // total
        if pct != last[0] and pct % 5 == 0:
            last[0] = pct
            end = "\n" if pct >= 100 else ""
            print(f"\r  {name}: {pct}%", end=end, file=sys.stderr, flush=True)

    return show


def _size(path: Path) -> int:
    return path.stat().st_size if path.is_file() else 0


def fetch_one(ds: Dataset, data_dir: Path, download: Download = http_download, log: Log = print) -> bool:
    """Make sure one file is present and valid. Return True on success."""
    target = data_dir / ds.filename
    if verify(target, ds) is None:
        log(f"  ok       {ds.filename} (already present)")
        return True
    data_dir.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")
    last_error = "no attempt made"
    for attempt in range(1, ATTEMPTS + 1):
        if _size(part) > ds.size_bytes:
            part.unlink()  # cannot be a prefix of the right file
        if _size(part) < ds.size_bytes:
            if attempt > 1:
                log(f"  retry    {ds.filename}: attempt {attempt} of {ATTEMPTS}, "
                    f"continuing from {_size(part)} bytes")
                time.sleep(RETRY_PAUSE_S)
            try:
                download(ds.url, part, _progress_printer(ds.filename))
            # urllib network errors are OSError; a body cut short raises IncompleteRead
            except (OSError, http.client.HTTPException) as e:
                last_error = str(e)
                continue
        if _size(part) < ds.size_bytes:
            last_error = f"connection closed at {_size(part)} of {ds.size_bytes} bytes"
            continue
        reason = verify(part, ds)
        if reason:
            part.unlink(missing_ok=True)
            log(f"  FAILED   {ds.filename}: {reason}")
            return False
        part.replace(target)  # atomic rename within one folder
        log(f"  fetched  {ds.filename} ({ds.size_bytes} bytes)")
        return True
    log(f"  FAILED   {ds.filename}: {last_error}. "
        f"{_size(part)} bytes kept in {part.name}; run again to continue")
    return False


def fetch_all(data_dir: Path, datasets: Iterable[Dataset] = DATASETS,
              download: Download = http_download, log: Log = print) -> bool:
    results = [fetch_one(ds, data_dir, download, log) for ds in datasets]
    return all(results)


def check_all(data_dir: Path, datasets: Iterable[Dataset] = DATASETS, log: Log = print) -> bool:
    ok = True
    for ds in datasets:
        reason = verify(data_dir / ds.filename, ds)
        if reason is None:
            log(f"  ok       {ds.filename}")
        else:
            ok = False
            log(f"  BAD      {ds.filename}: {reason}")
    return ok


def hash_all(data_dir: Path, datasets: Iterable[Dataset] = DATASETS, log: Log = print) -> bool:
    ok = True
    for ds in datasets:
        path = data_dir / ds.filename
        if path.is_file():
            log(f"  {ds.key}: {sha256_of(path)}  {ds.filename}")
        else:
            ok = False
            log(f"  {ds.key}: missing  {ds.filename}")
    return ok


def main(argv: list[str] | None = None, datasets: Iterable[Dataset] = DATASETS,
         download: Download = http_download) -> int:
    parser = argparse.ArgumentParser(description="Download and verify the project data files.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="verify only, download nothing")
    mode.add_argument("--hash", action="store_true", help="print SHA-256 of present files")
    args = parser.parse_args(argv)

    datasets = tuple(datasets)
    data_dir = config.data_dir()
    print(f"Data folder: {data_dir}")

    if args.hash:
        ok = hash_all(data_dir, datasets)
    elif args.check:
        ok = check_all(data_dir, datasets)
    else:
        ok = fetch_all(data_dir, datasets, download)

    print("All files present and valid." if ok else "Some files are missing or invalid.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
