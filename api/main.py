"""HTTP API: a thin layer between the core and the interface.

It checks input, calls the core and returns JSON; it has no calculation of its own.
Response formats were approved by the project lead on 2026-11-14 (project rule 5).

Run:  uvicorn api.main:app --host 127.0.0.1 --port 8000

Endpoints:
    GET  /api/meta
    GET  /api/instant?lat=&lon=&time=&height=
    GET  /api/day-series?lat=&lon=&date=&height=
    POST /api/window   {"lat_deg", "lon_deg", "start_utc", "profile"}
    POST /api/compare  {"sites": [{"lat_deg", "lon_deg"}, ...], "start_utc", "profile"}
    POST /api/search   {"from_utc", "to_utc", "region", "profile"}

Errors share one shape: {"error": {"field": ..., "message": ...}}.
400 for bad input, 503 when the data files are not loaded. A site without enough
terrain is a normal 200 response with data_status "insufficient_data".

Terrain and ephemeris load once at startup; the precomputed search horizons are read into
memory in a background thread, so the server answers at once and a search made before that
finishes reads the rest from disk. Heavy calculations run in the thread pool,
at most MAX_PARALLEL at a time; further requests wait for a free slot.
If a `web/` folder exists in the repository, it is served at `/` (same origin, no CORS).
"""

import re
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Body, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from core import config, mission, search
from core.datasets import DATASETS, locate
from core.mission import ProfileError

MAX_PARALLEL = 2
MAX_COMPARE_SITES = 3
WEB_DIR = config.REPO_ROOT / "web"
DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class ApiError(Exception):
    def __init__(self, status: int, field: str | None, message: str):
        super().__init__(message)
        self.status, self.field, self.message = status, field, message


def _error(status: int, field, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"field": field, "message": message}})


def parse_time(text, field: str) -> datetime:
    """ISO 8601 with an explicit offset ('Z' or +hh:mm), or a plain date meaning 00:00 UTC."""
    if not isinstance(text, str) or not text.strip():
        raise ApiError(400, field, "required, ISO 8601 UTC time such as 2026-11-14T06:30:00Z or a date 2026-11-14")
    text = text.strip()
    try:
        if DATE_ONLY.match(text):
            return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)
        t = datetime.fromisoformat(text.replace("z", "Z"))
    except ValueError:
        raise ApiError(400, field, f"not an ISO 8601 time: {text!r}") from None
    if t.tzinfo is None:
        raise ApiError(400, field, "time zone missing; add Z for UTC, e.g. 2026-11-14T06:30:00Z")
    return t.astimezone(timezone.utc)


def _number(body: dict, key: str) -> float:
    value = body.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ApiError(400, key, "required, a number")
    return float(value)


def _object(body, key: str) -> dict:
    value = body.get(key, {}) if isinstance(body, dict) else None
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ApiError(400, key, "must be an object")
    return value


def missing_files() -> list[str]:
    return [d.filename for d in DATASETS if locate(d) is None]


def create_app(terrain=None, source=None, load_data: bool = True, max_parallel: int = MAX_PARALLEL) -> FastAPI:
    """Build the application. Tests pass their own terrain and direction source."""
    state = {"terrain": terrain, "source": source, "load_error": None, "store": None}
    slots = threading.BoundedSemaphore(max_parallel)

    @asynccontextmanager
    async def lifespan(app):
        loaded_here = False
        if load_data and (state["terrain"] is None or state["source"] is None):
            try:
                from core.ephemeris import SkyfieldSource
                from core.terrain import Terrain
                if state["terrain"] is None:
                    state["terrain"], loaded_here = Terrain(), True
                state["source"] = state["source"] or SkyfieldSource()
            except (FileNotFoundError, OSError, ValueError) as e:
                state["load_error"] = str(e)
        if loaded_here:
            state["store"] = search.HorizonStore(state["terrain"])
            threading.Thread(target=state["store"].preload, name="horizon-preload", daemon=True).start()
        yield
        if loaded_here:  # close the raster in the thread that opened it
            state["terrain"].close()
            state["terrain"] = None

    app = FastAPI(title="LUCY API", version="1.0", lifespan=lifespan)

    @app.exception_handler(ApiError)
    async def api_error(request: Request, e: ApiError):
        return _error(e.status, e.field, e.message)

    @app.exception_handler(ProfileError)
    async def profile_error(request: Request, e: ProfileError):
        return _error(400, e.field, str(e).split(": ", 1)[-1])

    @app.exception_handler(ValueError)
    async def value_error(request: Request, e: ValueError):
        return _error(400, None, str(e))

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, e: RequestValidationError):
        first = e.errors()[0] if e.errors() else {}
        loc = [str(x) for x in first.get("loc", ()) if x not in ("query", "body")]
        return _error(400, loc[-1] if loc else None, first.get("msg", "invalid request"))

    def compute(fn, *args):
        """Run a core call with the loaded data, at most `max_parallel` at a time."""
        if state["terrain"] is None or state["source"] is None:
            missing = missing_files()
            detail = f"missing data files: {', '.join(missing)}. " if missing else ""
            raise ApiError(503, None, f"data not loaded. {detail}Run: python scripts/fetch_data.py"
                           + (f" ({state['load_error']})" if state["load_error"] else ""))
        with slots:
            return fn(state["terrain"], state["source"], *args)

    @app.get("/api/meta")
    def meta():
        loaded = state["terrain"] is not None and state["source"] is not None
        return mission.meta_response(loaded, missing_files())

    @app.get("/api/instant")
    def instant(lat: float, lon: float, time: str, height: float = 0.0):
        return compute(mission.instant_response, lat, lon, parse_time(time, "time"), height)

    @app.get("/api/day-series")
    def day_series(lat: float, lon: float, date: str, height: float = 0.0):
        return compute(mission.day_series_response, lat, lon, parse_time(date, "date"), height)

    @app.post("/api/window")
    def assess_window(body: dict = Body(...)):
        lat, lon = _number(body, "lat_deg"), _number(body, "lon_deg")
        start = parse_time(body.get("start_utc"), "start_utc")
        profile = mission.MissionProfile.from_dict(_object(body, "profile"))
        return compute(mission.assess_site, lat, lon, start, profile)

    @app.post("/api/compare")
    def compare(body: dict = Body(...)):
        sites = body.get("sites")
        if not isinstance(sites, list) or not 2 <= len(sites) <= MAX_COMPARE_SITES:
            raise ApiError(400, "sites", f"a list of 2 to {MAX_COMPARE_SITES} sites")
        pairs = []
        for s in sites:
            if not isinstance(s, dict):
                raise ApiError(400, "sites", "each site is an object with lat_deg and lon_deg")
            pairs.append((_number(s, "lat_deg"), _number(s, "lon_deg")))
        start = parse_time(body.get("start_utc"), "start_utc")
        profile = mission.MissionProfile.from_dict(_object(body, "profile"))
        return {"results": compute(mission.assess_sites, pairs, start, profile)}

    @app.post("/api/search")
    def inverse_search(body: dict = Body(...)):
        start = parse_time(body.get("from_utc"), "from_utc")
        end = parse_time(body.get("to_utc"), "to_utc")
        region = body.get("region")
        if region is not None and not isinstance(region, dict):
            raise ApiError(400, "region", "must be an object")
        profile = mission.MissionProfile.from_dict(_object(body, "profile"))
        try:
            return compute(lambda t, src, *a: search.run(t, src, *a, store=state["store"]),
                           start, end, profile, region)
        except search.SearchUnavailable as e:
            raise ApiError(503, None, str(e)) from None

    if WEB_DIR.is_dir():
        from fastapi.staticfiles import StaticFiles
        app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")

    return app


app = create_app()
