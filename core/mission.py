"""Mission profile, thresholds, status and the window assessment response.

The response format below was approved by the project lead on 2026-11-14 (project
rule 5: change it only with that approval). The tool reports whether a landing window
meets the profile's light and communication thresholds; it does not predict mission
success (project rule 10).

Thresholds, in the fixed order used to pick the reason code (power first, then comms):
    max_night_h      longest night must not exceed the limit
    min_sun_pct      Sun share must be at least the limit
    max_comms_gap_h  longest comms gap must not exceed the limit
A value exactly at the limit meets it. Margin is positive when the threshold is met.
Thresholds compare unrounded values; the response carries rounded ones.
"""

from dataclasses import asdict, dataclass, fields
from datetime import datetime, timedelta
from pathlib import Path

from core import config
from core.datasets import by_key
from core.ephemeris import DirectionSource, check_times
from core.horizon import horizon
from core.terrain import InsufficientData, Terrain
from core.visibility import Interval, WindowResult, window

# Defaults are an example setup, not a recommendation; the interface labels them so.
EXAMPLE_NOTE = "Example setup, not a recommendation."
VEHICLE_TYPE_MAX_LEN = 40
OBSERVER_HEIGHT_MAX_M = 100.0

MODEL = "body centre, no eclipses, direct line of sight to Earth"


class ProfileError(ValueError):
    """A profile field is missing, unknown or out of range."""

    def __init__(self, field: str, message: str):
        super().__init__(f"{field}: {message}")
        self.field = field


@dataclass(frozen=True)
class MissionProfile:
    duration_days: float = 14.0
    vehicle_type: str = ""          # label only, does not affect the calculation
    max_night_h: float = 40.0
    max_comms_gap_h: float = 24.0
    min_sun_pct: float = 50.0
    observer_height_m: float = 0.0

    @classmethod
    def from_dict(cls, data: dict | None) -> "MissionProfile":
        """Build a profile from user input; missing fields take the defaults."""
        data = dict(data or {})
        known = {f.name for f in fields(cls)}
        unknown = sorted(set(data) - known)
        if unknown:
            raise ProfileError(unknown[0], "unknown field")
        values = {}
        for name, value in data.items():
            if name == "vehicle_type":
                if not isinstance(value, str):
                    raise ProfileError(name, "must be text")
                values[name] = value.strip()
            else:
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise ProfileError(name, "must be a number")
                values[name] = float(value)
        profile = cls(**values)
        profile.validate()
        return profile

    def validate(self) -> None:
        def check(name, lo, hi, unit):
            v = getattr(self, name)
            if not (lo <= v <= hi):  # also rejects NaN
                raise ProfileError(name, f"must be between {lo:g} and {hi:g} {unit}, got {v:g}")

        check("duration_days", config.WINDOW_DAYS_MIN, config.WINDOW_DAYS_MAX, "days")
        window_h = self.duration_days * 24.0
        check("max_night_h", 0.0, window_h, "h (the window length)")
        check("max_comms_gap_h", 0.0, window_h, "h (the window length)")
        check("min_sun_pct", 0.0, 100.0, "%")
        check("observer_height_m", 0.0, OBSERVER_HEIGHT_MAX_M, "m")
        if len(self.vehicle_type) > VEHICLE_TYPE_MAX_LEN:
            raise ProfileError("vehicle_type", f"at most {VEHICLE_TYPE_MAX_LEN} characters")

    def to_dict(self) -> dict:
        return {k: (v if isinstance(v, str) else _num(v)) for k, v in asdict(self).items()}


# key, kind, unit, metric name, short name for the explanation
CRITERIA = (
    ("max_night_h", "max", "h", "longest_night_h", "longest night"),
    ("min_sun_pct", "min", "pct", "sun_pct", "Sun share"),
    ("max_comms_gap_h", "max", "h", "longest_comms_gap_h", "comms gap"),
)
UNIT_DECIMALS = {"h": 2, "pct": 1}


@dataclass(frozen=True)
class Criterion:
    key: str
    kind: str        # "max" or "min"
    unit: str        # "h" or "pct"
    limit: float
    value: float
    margin: float    # positive when met
    met: bool


def evaluate(metrics, profile: MissionProfile) -> list[Criterion]:
    out = []
    for key, kind, unit, metric, _ in CRITERIA:
        limit, value = getattr(profile, key), getattr(metrics, metric)
        margin = limit - value if kind == "max" else value - limit
        out.append(Criterion(key, kind, unit, limit, value, margin, margin >= 0.0))
    return out


def status_of(criteria: list[Criterion]) -> tuple[str, str]:
    """('acceptable', 'all_met') or ('not_acceptable', key of the first failed threshold)."""
    for c in criteria:
        if not c.met:
            return "not_acceptable", c.key
    return "acceptable", "all_met"


def _limit(x: float) -> str:
    return f"{x:g}"


def explanation(criteria: list[Criterion] | None) -> str:
    """One sentence built from the reason code and the numbers; None means no data."""
    if criteria is None:
        return (f"Not enough terrain data around this site: the {config.HORIZON_RADIUS_M / 1000:g} km "
                f"circle is not fully covered.")
    c = {x.key: x for x in criteria}
    night, sun, gap = c["max_night_h"], c["min_sun_pct"], c["max_comms_gap_h"]
    status, reason = status_of(criteria)
    if status == "acceptable":
        return (f"Acceptable: longest night {night.value:.1f} h (limit {_limit(night.limit)} h), "
                f"Sun visible {sun.value:.1f}% (minimum {_limit(sun.limit)}%), "
                f"longest comms gap {gap.value:.1f} h (limit {_limit(gap.limit)} h).")
    first = c[reason]
    if reason == "max_night_h":
        text = f"longest night is {first.value:.1f} h, limit {_limit(first.limit)} h"
    elif reason == "min_sun_pct":
        text = f"Sun is visible {first.value:.1f}% of the time, minimum {_limit(first.limit)}%"
    else:
        text = f"longest comms gap is {first.value:.1f} h, limit {_limit(first.limit)} h"
    others = [name for key, _, _, _, name in CRITERIA if key != reason and not c[key].met]
    sentence = f"Not acceptable: {text}."
    if others:
        sentence += f" Also not met: {', '.join(others)}."
    return sentence


# ---------- response ----------

def _num(x: float, decimals: int | None = None) -> float | int:
    x = float(x)
    if decimals is not None:
        x = round(x, decimals)
    return int(x) if x.is_integer() else x


def _iso(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _intervals(ivs: list[Interval]) -> list[dict]:
    return [{"start_utc": _iso(i.start_utc), "end_utc": _iso(i.end_utc), "visible": i.visible} for i in ivs]


def build_response(lat_deg: float, lon_deg: float, start_utc: datetime, profile: MissionProfile,
                   result: WindowResult | None, site_height_m: float | None, terrain_name: str) -> dict:
    """Assessment response in the approved format. `result` None means insufficient data."""
    end_utc = start_utc + timedelta(days=profile.duration_days)
    response = {
        "data_status": "ok" if result is not None else "insufficient_data",
        "site": {"lat_deg": _num(lat_deg), "lon_deg": _num(lon_deg),
                 "height_m": None if site_height_m is None else _num(site_height_m, 1)},
        "window": {"start_utc": _iso(start_utc), "end_utc": _iso(end_utc)},
        "profile": profile.to_dict(),
        "metrics": None, "criteria": None, "status": None,
        "reason_code": "insufficient_data",
        "explanation": explanation(None),
        "intervals": None,
        "meta": {
            "terrain": terrain_name,
            "ephemeris": by_key("ephemeris").filename,
            "model": MODEL,
            "horizon_radius_km": _num(config.HORIZON_RADIUS_M / 1000),
            "time_step_min": _num(config.TIME_STEP_MIN),
        },
    }
    if result is None:
        return response
    m = result.metrics
    criteria = evaluate(m, profile)
    status, reason = status_of(criteria)
    response.update({
        "metrics": {
            "longest_night_h": _num(m.longest_night_h, 2),
            "sun_pct": _num(m.sun_pct, 1),
            "longest_comms_gap_h": _num(m.longest_comms_gap_h, 2),
            "comms_down_h": _num(m.comms_down_h, 2),
            "comms_pct": _num(m.comms_pct, 1),
        },
        "criteria": [
            {"key": c.key, "kind": c.kind, "unit": c.unit, "limit": _num(c.limit),
             "value": _num(c.value, UNIT_DECIMALS[c.unit]),
             "margin": _num(c.margin, UNIT_DECIMALS[c.unit]), "met": c.met}
            for c in criteria
        ],
        "status": status,
        "reason_code": reason,
        "explanation": explanation(criteria),
        "intervals": {"sun": _intervals(result.sun_intervals), "earth": _intervals(result.earth_intervals)},
    })
    return response


def assess_site(terrain: Terrain, source: DirectionSource, lat_deg: float, lon_deg: float,
                start_utc: datetime, profile: MissionProfile | dict | None = None,
                cache_folder: Path | None = None) -> dict:
    """Whole path for one site: horizon (cached), window visibility, thresholds, response.

    Bad input (profile, dates) raises ValueError; missing terrain around the site is a
    normal response with data_status "insufficient_data".
    """
    if not isinstance(profile, MissionProfile):
        profile = MissionProfile.from_dict(profile)
    check_times([start_utc, start_utc + timedelta(days=profile.duration_days) - timedelta(minutes=1)])
    try:
        prof = horizon(terrain, lat_deg, lon_deg, profile.observer_height_m, folder=cache_folder)
    except InsufficientData:
        return build_response(lat_deg, lon_deg, start_utc, profile, None, None, terrain.path.name)
    result = window(source, prof, start_utc, profile.duration_days)
    return build_response(lat_deg, lon_deg, start_utc, profile, result, prof.site_height_m, terrain.path.name)
