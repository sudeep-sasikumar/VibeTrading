import json

import pandas as pd
import pytest

from vibetrading.portal import jobs, results


def test_job_commands_are_built_only_from_known_values():
    assert jobs.command_for({"kind": "backtest", "strategy": "momentum", "universe": "sp500", "tuned": True,
                             "start": "2018-01-01"}) == [
        "backtest", "--strategy", "momentum", "sp500", "--tuned", "--start", "2018-01-01"]
    assert jobs.command_for({"kind": "refresh"}) == ["refresh"]
    for bad in ({"kind": "rm"}, {"kind": "backtest", "strategy": "x; rm -rf /"},
                {"kind": "picks", "universe": "../../etc"}, {"kind": "backtest", "strategy": "momentum",
                                                             "start": "2018-01-01 --oops"}):
        with pytest.raises(ValueError):
            jobs.command_for(bad)


def test_job_queue_dedupes_and_survives_restart(tmp_path):
    q = jobs.JobQueue(tmp_path / "jobs.json")
    a = q.submit("picks", universe="nifty50")
    assert q.submit("picks", universe="nifty50")["id"] == a["id"]   # same work already queued
    q.jobs[0]["status"] = "running"
    q._save()
    restarted = jobs.JobQueue(tmp_path / "jobs.json")
    assert restarted.jobs[0]["status"] == "failed"


@pytest.fixture
def fake_runs(tmp_path, monkeypatch):
    run = tmp_path / "runs" / "sp500" / "momentum" / "20260101-000000"
    (run / "artifacts").mkdir(parents=True)
    (run / "summary.json").write_text(json.dumps({"periods": {}}))
    pd.DataFrame({"timestamp": ["2026-01-02", "2026-01-05"], "code": ["A.US", "B.US"], "event": ["exit", "exit"],
                  "reason": ["signal", "end_of_backtest"], "return_pct": [1.0, 2.0], "holding_days": [3, 4],
                  "pnl": [10.0, 20.0]}).to_csv(run / "artifacts" / "trades.csv", index=False)
    monkeypatch.setattr(results, "RUNS_DIR", tmp_path / "runs")
    return run


def test_run_path_only_resolves_listed_folders(fake_runs):
    assert results.run_path("sp500", "momentum", "20260101-000000") == fake_runs
    assert results.run_path("sp500", "..", "momentum") is None
    assert results.run_path("sp500", "momentum", "../../x") is None
    assert results.run_path("nope", "momentum", "20260101-000000") is None


def test_trades_leave_out_positions_closed_by_the_end_of_the_backtest(fake_runs):
    assert [t["code"] for t in results.trades(fake_runs)] == ["A.US"]


def test_password_is_required_when_set(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from vibetrading.portal import app as portal

    monkeypatch.setattr(portal, "PASSWORD", "s3cret")
    monkeypatch.setattr(jobs, "JOBS_FILE", tmp_path / "jobs.json")
    monkeypatch.setenv("REFRESH_HOUR_UTC", "off")
    monkeypatch.setenv("BOOTSTRAP", "0")
    with TestClient(portal.app) as client:
        assert client.get("/api/jobs").status_code == 401
        assert client.get("/api/jobs", auth=("any", "wrong")).status_code == 401
        assert client.get("/api/jobs", auth=("any", "s3cret")).status_code == 200
