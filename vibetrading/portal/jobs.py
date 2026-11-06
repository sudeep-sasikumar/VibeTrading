"""Background jobs for the portal: one at a time, each in its own process.

Refreshing prices, backtests and picks all open the DuckDB price store, and
DuckDB allows one writer *or* several readers, never both. Running jobs one
at a time in a separate process keeps them out of each other's way, keeps the
engine's ``sys.exit`` and memory use out of the web server, and suits a small
VPS. The web app itself only reads result files.

Jobs are kept in ``var/jobs.json`` so the list survives a restart; a job that
was running when the server stopped is marked failed on the next start.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from vibetrading.settings import DB_PATH, VAR_DIR
from vibetrading.strategies import STRATEGIES
from vibetrading.universes import UNIVERSES

REPO_ROOT = Path(__file__).resolve().parents[2]
JOBS_FILE = VAR_DIR / "jobs.json"
LOGS_DIR = VAR_DIR / "jobs"
KEEP_JOBS = 200
LOG_TAIL_CHARS = 4000


def command_for(job: dict) -> list[str]:
    """The ``python -m vibetrading`` arguments for a job; validates its fields."""
    kind = job["kind"]
    universe = job.get("universe")
    if universe is not None and universe not in UNIVERSES:
        raise ValueError(f"unknown universe {universe!r}")
    strategy = job.get("strategy")
    if strategy is not None and strategy not in STRATEGIES:
        raise ValueError(f"unknown strategy {strategy!r}")
    universes = [universe] if universe else []
    if kind == "refresh":
        return ["refresh", *universes]
    if kind == "picks":
        return ["picks", *universes]
    if kind == "backtest":
        if not strategy:
            raise ValueError("a backtest needs a strategy")
        args = ["backtest", "--strategy", strategy, *universes]
        if job.get("tuned"):
            args.append("--tuned")
        if job.get("start"):
            args += ["--start", _date(job["start"])]
        return args
    if kind == "walkforward":
        if not strategy:
            raise ValueError("a walk-forward needs a strategy")
        return ["walkforward", "--strategy", strategy, *universes, "--window", "3", "--engine"]
    raise ValueError(f"unknown job kind {kind!r}")


def _date(value: str) -> str:
    return datetime.strptime(value, "%Y-%m-%d").date().isoformat()


class JobQueue:
    def __init__(self, path: Path | None = None):
        self.path = path or JOBS_FILE
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.jobs: list[dict] = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else []
        for job in self.jobs:  # queued jobs simply run after the restart
            if job["status"] == "running":
                job.update(status="failed", error="server stopped while it was running",
                           finished_at=_now())
        self._save()

    # ── public ──

    def submit(self, kind: str, **fields) -> dict:
        job = {"id": uuid.uuid4().hex[:10], "kind": kind, **{k: v for k, v in fields.items() if v not in (None, "")}}
        job["args"] = command_for(job)
        with self.lock:
            duplicate = next((j for j in self.jobs if j["status"] in ("queued", "running")
                              and j["args"] == job["args"]), None)
            if duplicate:
                return duplicate
            job.update(status="queued", queued_at=_now())
            self.jobs.append(job)
            self._save()
        self.wake.set()
        return job

    def list(self, limit: int = 50) -> list[dict]:
        with self.lock:
            return [dict(j) for j in reversed(self.jobs[-limit:])]

    def get(self, job_id: str) -> dict | None:
        with self.lock:
            job = next((dict(j) for j in self.jobs if j["id"] == job_id), None)
        if job:
            log = LOGS_DIR / f"{job_id}.log"
            job["log_tail"] = log.read_text(encoding="utf-8", errors="replace")[-LOG_TAIL_CHARS:] if log.exists() else ""
        return job

    def last_finished(self, kind: str) -> dict | None:
        with self.lock:
            return next((dict(j) for j in reversed(self.jobs) if j["kind"] == kind and j["status"] == "done"), None)

    def start(self, refresh_hour_utc: int | None) -> None:
        threading.Thread(target=self._worker, daemon=True, name="jobs").start()
        if refresh_hour_utc is not None:
            threading.Thread(target=self._scheduler, args=(refresh_hour_utc,), daemon=True, name="schedule").start()

    def bootstrap(self) -> None:
        """First start on an empty volume: build everything the portal shows."""
        if DB_PATH.exists():
            return
        self.submit("refresh")
        self.submit("picks")
        for strategy in STRATEGIES:
            self.submit("backtest", strategy=strategy)
            self.submit("backtest", strategy=strategy, tuned=True)
        for universe in ("sp500", "nasdaq100"):
            for strategy in STRATEGIES:
                self.submit("walkforward", universe=universe, strategy=strategy)

    # ── internals ──

    def _worker(self) -> None:
        while True:
            job = self._next()
            if job is None:
                self.wake.wait(timeout=30)
                self.wake.clear()
                continue
            self._run(job)

    def _next(self) -> dict | None:
        with self.lock:
            return next((j for j in self.jobs if j["status"] == "queued"), None)

    def _run(self, job: dict) -> None:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        with self.lock:
            job.update(status="running", started_at=_now())
            self._save()
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
        with open(LOGS_DIR / f"{job['id']}.log", "w", encoding="utf-8") as log:
            code = subprocess.call([sys.executable, "-m", "vibetrading", *job["args"]],
                                   cwd=REPO_ROOT, stdout=log, stderr=subprocess.STDOUT, env=env)
        with self.lock:
            job.update(status="done" if code == 0 else "failed", finished_at=_now(), exit_code=code)
            if code != 0:
                job["error"] = f"exited with code {code}; see the log"
            self._save()

    def _scheduler(self, hour_utc: int) -> None:
        """Every day at ``hour_utc``: refresh prices, then recompute picks."""
        while True:
            now = datetime.now(timezone.utc)
            run_at = now.replace(hour=hour_utc, minute=0, second=0, microsecond=0)
            if run_at <= now:
                run_at += timedelta(days=1)
            time.sleep((run_at - now).total_seconds())
            self.submit("refresh", scheduled=True)
            self.submit("picks", scheduled=True)

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.jobs = self.jobs[-KEEP_JOBS:]
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.jobs, indent=1), encoding="utf-8")
        tmp.replace(self.path)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
