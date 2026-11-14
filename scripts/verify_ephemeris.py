"""Compare our Sun and Earth directions with reference values from JPL Horizons.

Usage:
    python scripts/verify_ephemeris.py                      compare with the stored reference
    python scripts/verify_ephemeris.py --refresh-reference  query Horizons again and rewrite it

The comparison runs offline: it reads tests/fixtures/horizons_reference.json and the
JPL files in the data folder. Only --refresh-reference talks to Horizons. That is a
verification step, never part of the application's working path.

Exit code: 0 when every direction agrees within TOLERANCE_DEG, 1 otherwise.
"""

import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.ephemeris import SkyfieldSource, directions  # noqa: E402
from core.geometry import angular_separation_deg  # noqa: E402

FIXTURE = REPO_ROOT / "tests" / "fixtures" / "horizons_reference.json"

# Agreement required between Skyfield and Horizons. It is below the 0.029 degree rotation
# between the lunar PA and ME frames, so a wrong frame or a reversed rotation is caught.
TOLERANCE_DEG = 0.01

HORIZONS_URL = "https://ssd.jpl.nasa.gov/api/horizons.api"
USER_AGENT = "lunar-site-ephemeris-check/1.0"
HORIZONS_BODY = {"sun": "10", "earth": "399"}

# Reference sites (planetocentric degrees, east longitude positive) and dates.
SITES = [
    {"site": "near_pole", "lat_deg": -89.5, "lon_deg": 0.0},
    {"site": "zone_edge", "lat_deg": -85.5, "lon_deg": 90.0},
    {"site": "west", "lat_deg": -87.0, "lon_deg": -120.0},
]
DATES = ["2000-01-01", "2026-11-14", "2050-06-15"]
HOURS = [0, 6, 12]

MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], start=1)}


def _parse_time(text: str) -> datetime:
    """Parse a Horizons time such as '2026-Nov-14 00:00' (locale independent)."""
    date, clock = text.strip().split()
    y, mon, d = date.split("-")
    hh, mm = clock.split(":")[:2]
    return datetime(int(y), MONTHS[mon], int(d), int(hh), int(mm), tzinfo=timezone.utc)


def _is_number(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False


def query_horizons(body: str, lat_deg: float, lon_deg: float, date: str) -> tuple[list[dict], str]:
    """Apparent azimuth and elevation of a body from a lunar site, one day, every 6 hours."""
    start = f"{date} {HOURS[0]:02d}:00"
    stop = f"{date} {HOURS[-1]:02d}:00"
    params = {
        "format": "text",
        "COMMAND": f"'{HORIZONS_BODY[body]}'",
        "OBJ_DATA": "'NO'",
        "MAKE_EPHEM": "'YES'",
        "EPHEM_TYPE": "'OBSERVER'",
        "CENTER": "'coord@301'",
        "COORD_TYPE": "'GEODETIC'",
        "SITE_COORD": f"'{lon_deg % 360:.6f},{lat_deg:.6f},0'",
        "START_TIME": f"'{start}'",
        "STOP_TIME": f"'{stop}'",
        "STEP_SIZE": "'6 h'",
        "QUANTITIES": "'4'",
        "ANG_FORMAT": "'DEG'",
        "CSV_FORMAT": "'YES'",
    }
    url = HORIZONS_URL + "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        text = response.read().decode("utf-8")
    if "$$SOE" not in text:
        raise RuntimeError("unexpected Horizons response:\n" + text[:1500])
    source = re.search(r"Target body name:.*?\{source:\s*([^}]+)\}", text)
    rows = []
    body_text = text.split("$$SOE", 1)[1].split("$$EOE", 1)[0]
    for line in body_text.strip().splitlines():
        fields = [f.strip() for f in line.split(",")]
        numbers = [f for f in fields[1:] if _is_number(f)]
        rows.append({"time_utc": _parse_time(fields[0]).strftime("%Y-%m-%dT%H:%M:%SZ"),
                     "az_deg": float(numbers[-2]), "el_deg": float(numbers[-1])})
    return rows, (source.group(1).strip() if source else "unknown")


def refresh_reference() -> int:
    rows, sources = [], set()
    for site in SITES:
        for body in HORIZONS_BODY:
            for date in DATES:
                got, src = query_horizons(body, site["lat_deg"], site["lon_deg"], date)
                sources.add(src)
                for r in got:
                    rows.append({**site, "body": body, **r})
                print(f"  {site['site']:10s} {body:6s} {date}: {len(got)} rows")
                time.sleep(1.0)  # one request at a time, as JPL asks
    data = {
        "source": "JPL Horizons API, " + HORIZONS_URL,
        "fetched_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "horizons_ephemeris": sorted(sources),
        "settings": {
            "center": "coord@301 (Moon), geodetic site coordinates, altitude 0 km on the 1737.4 km sphere",
            "quantities": "4: apparent azimuth (from north through east) and elevation, airless",
            "time_scale": "UTC",
        },
        "rows": rows,
    }
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    print(f"Wrote {len(rows)} reference rows to {FIXTURE.relative_to(REPO_ROOT)}")
    return 0


def load_reference() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def compare(source=None) -> list[dict]:
    """Separation in degrees between our directions and every reference row."""
    source = source or SkyfieldSource()
    result = []
    for r in load_reference()["rows"]:
        t = datetime.strptime(r["time_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        d = directions(source, [t], r["lat_deg"], r["lon_deg"])[r["body"]]
        sep = float(angular_separation_deg(d.alt_deg[0], d.az_deg[0], r["el_deg"], r["az_deg"]))
        result.append({**r, "our_az_deg": float(d.az_deg[0]), "our_el_deg": float(d.alt_deg[0]), "sep_deg": sep})
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare Skyfield directions with JPL Horizons.")
    parser.add_argument("--refresh-reference", action="store_true", help="query Horizons and rewrite the reference file")
    args = parser.parse_args(argv)
    if args.refresh_reference:
        return refresh_reference()
    try:
        rows = compare()
    except FileNotFoundError as e:
        print(e)
        return 1
    print(f"{'site':10s} {'body':6s} {'time UTC':20s} {'el ref':>10s} {'el ours':>10s} {'diff deg':>10s}")
    for r in rows:
        print(f"{r['site']:10s} {r['body']:6s} {r['time_utc']:20s} {r['el_deg']:10.5f} {r['our_el_deg']:10.5f} {r['sep_deg']:10.6f}")
    worst = max(r["sep_deg"] for r in rows)
    ok = worst <= TOLERANCE_DEG
    print(f"Largest difference {worst:.6f} deg, tolerance {TOLERANCE_DEG} deg: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
