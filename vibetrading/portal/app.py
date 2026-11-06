"""The portal: one tab per universe, backed by the result files in ``var/``.

    uvicorn vibetrading.portal.app:app --port 8077

Environment:
    DASHBOARD_PASSWORD  if set, every page asks for it (HTTP Basic, any user name)
    REFRESH_HOUR_UTC    hour of the daily price refresh + picks (default 23; "off" disables)
    BOOTSTRAP           "1" (default) builds everything on first start with an empty var/
"""

from __future__ import annotations

import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel

from vibetrading.portal import results
from vibetrading.portal.jobs import JobQueue
from vibetrading.strategies import STRATEGIES
from vibetrading.universes import UNIVERSES

STATIC = Path(__file__).parent / "static"
PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "")
_basic = HTTPBasic(auto_error=False)
queue: JobQueue | None = None


def require_password(credentials: HTTPBasicCredentials | None = Depends(_basic)) -> None:
    if not PASSWORD:
        return
    if credentials is None or not secrets.compare_digest(credentials.password.encode(), PASSWORD.encode()):
        raise HTTPException(401, "password required", headers={"WWW-Authenticate": 'Basic realm="VibeTrading"'})


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global queue
    queue = JobQueue()
    hour = os.environ.get("REFRESH_HOUR_UTC", "23")
    queue.start(None if hour.lower() == "off" else int(hour))
    if os.environ.get("BOOTSTRAP", "1") == "1":
        queue.bootstrap()
    yield


app = FastAPI(title="VibeTrading", lifespan=lifespan, dependencies=[Depends(require_password)],
              docs_url=None, redoc_url=None)


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/overview")
def overview():
    refresh = queue.last_finished("refresh") if queue else None
    return {
        "universes": [results.universe_overview(k) for k in UNIVERSES],
        "featured": results.FEATURED,
        "featured_picks": (results.picks(results.FEATURED["universe"]) or {}).get("strategies", {}).get(
            results.FEATURED["label"]),
        "data_as_of": results.data_as_of(),
        "last_refresh": refresh["finished_at"] if refresh else None,
        "strategies": list(STRATEGIES),
    }


@app.get("/api/universe/{key}")
def universe(key: str):
    if key not in UNIVERSES:
        raise HTTPException(404, "unknown universe")
    return {
        **results.universe_overview(key),
        "runs": results.runs(key),
        "walkforward": results.walkforward(key),
        "picks": results.picks(key),
        "coverage": results.coverage(key),
    }


def _run(key: str, label: str, stamp: str) -> Path:
    path = results.run_path(key, label, stamp)
    if path is None:
        raise HTTPException(404, "no such run")
    return path


@app.get("/api/universe/{key}/runs/{label}/{stamp}/series")
def run_series(key: str, label: str, stamp: str):
    return results.series(key, _run(key, label, stamp))


@app.get("/api/universe/{key}/runs/{label}/{stamp}/trades")
def run_trades(key: str, label: str, stamp: str, limit: int = Query(100, ge=1, le=1000)):
    return results.trades(_run(key, label, stamp), limit)


class JobRequest(BaseModel):
    kind: str
    universe: str | None = None
    strategy: str | None = None
    tuned: bool = False
    start: str | None = None


@app.get("/api/jobs")
def jobs():
    return queue.list()


@app.get("/api/jobs/{job_id}")
def job(job_id: str):
    found = queue.get(job_id)
    if not found:
        raise HTTPException(404, "no such job")
    return found


@app.post("/api/jobs")
def submit(req: JobRequest):
    try:
        return queue.submit(req.kind, universe=req.universe, strategy=req.strategy,
                            tuned=req.tuned or None, start=req.start)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


@app.post("/api/jobs/rerun-all")
def rerun_all():
    """Refresh, recompute picks, and rerun every saved backtest."""
    submitted = [queue.submit("refresh"), queue.submit("picks")]
    for strategy in STRATEGIES:
        submitted.append(queue.submit("backtest", strategy=strategy))
        submitted.append(queue.submit("backtest", strategy=strategy, tuned=True))
    return submitted
