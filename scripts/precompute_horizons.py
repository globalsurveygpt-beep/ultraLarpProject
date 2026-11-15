"""Precompute horizon profiles for the inverse-search grid.

Usage:
    python scripts/precompute_horizons.py                  5 km grid, all CPU cores
    python scripts/precompute_horizons.py --workers 4
    python scripts/precompute_horizons.py --step-km 2      a finer grid (about 6 times more sites)
    python scripts/precompute_horizons.py --limit 20       only the first 20 missing sites (a trial run)

Results go to the horizon cache in the data folder (data/cache/horizons/). Sites already
in the cache are skipped, so an interrupted run continues where it stopped, and the
folder can be copied to another machine with the same terrain file.
Each worker reads the terrain once (about 0.25 GB of memory per worker for LOLA 80 m).
"""

import argparse
import multiprocessing as mp
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core import config, horizon  # noqa: E402
from core.search import grid_sites  # noqa: E402
from core.terrain import InsufficientData, Terrain  # noqa: E402

_terrain = None


def _init():
    global _terrain
    _terrain = Terrain()


def _work(site):
    lat, lon = site
    try:
        horizon.horizon(_terrain, lat, lon, 0.0)
        return "ok"
    except InsufficientData:
        return "insufficient"


def missing_sites(terrain: Terrain, sites):
    out = []
    for lat, lon in sites:
        try:
            if horizon.cached(terrain, lat, lon, 0.0) is None:
                out.append((lat, lon))
        except InsufficientData:
            pass
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Precompute horizons for the search grid.")
    parser.add_argument("--step-km", type=float, default=config.SEARCH_GRID_STEP_M / 1000)
    parser.add_argument("--workers", type=int, default=os.cpu_count() or 1)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args(argv)

    sites = grid_sites(args.step_km * 1000)
    missing = missing_sites(Terrain(), sites)
    todo = missing if args.limit is None else missing[:args.limit]
    print(f"Grid {args.step_km:g} km: {len(sites)} sites, {len(sites) - len(missing)} already cached, "
          f"{len(todo)} to compute now with {args.workers} workers", flush=True)
    if not todo:
        return 0

    counts = {"ok": 0, "insufficient": 0}
    start = time.perf_counter()
    with mp.Pool(args.workers, initializer=_init) as pool:
        for n, status in enumerate(pool.imap_unordered(_work, todo, chunksize=4), start=1):
            counts[status] += 1
            if n % 50 == 0 or n == len(todo):
                elapsed = time.perf_counter() - start
                left = elapsed / n * (len(todo) - n)
                print(f"  {n}/{len(todo)}  {elapsed / 60:.1f} min elapsed, about {left / 60:.1f} min left", flush=True)
    elapsed = time.perf_counter() - start
    print(f"Done in {elapsed / 60:.1f} min: {counts['ok']} horizons, {counts['insufficient']} sites without "
          f"enough terrain; {elapsed / len(todo):.2f} s per site overall", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
