"""Performance stats from an engine run's artifacts, split into periods.

Computed here rather than taken from the engine's own metrics so every period
(full, in-sample, out-of-sample) and the benchmark are measured the same way.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd


def equity_stats(equity: pd.Series, bars_per_year: int) -> dict:
    equity = equity.dropna()
    if len(equity) < 2:
        return {}
    rets = equity.pct_change().dropna()
    years = (equity.index[-1] - equity.index[0]).days / 365.25
    total = equity.iloc[-1] / equity.iloc[0] - 1
    vol = rets.std() * math.sqrt(bars_per_year)
    drawdown = equity / equity.cummax() - 1
    return {
        "from": equity.index[0].date().isoformat(),
        "to": equity.index[-1].date().isoformat(),
        "total_return": total,
        "cagr": (1 + total) ** (1 / years) - 1 if years > 0 and total > -1 else None,
        "max_drawdown": drawdown.min(),
        "sharpe": rets.mean() / rets.std() * math.sqrt(bars_per_year) if rets.std() > 0 else None,
        "volatility": vol,
    }


def trade_stats(trades: pd.DataFrame, fills: pd.DataFrame, equity: pd.Series) -> dict:
    """Round trips closed in the window, turnover and fees for fills in it."""
    if equity.empty:
        return {}
    start, end = equity.index[0], equity.index[-1]
    exits = trades[(trades["event"] == "exit") & trades["timestamp"].between(start, end)]
    in_window = fills[fills["timestamp"].between(start, end)] if not fills.empty else fills
    years = max((end - start).days / 365.25, 1 / 365.25)
    avg_equity = equity.mean()
    return {
        "round_trips": int(len(exits)),
        "win_rate": float((exits["pnl"] > 0).mean()) if len(exits) else None,
        "avg_return_per_trade": float(exits["return_pct"].mean() / 100) if len(exits) else None,
        "avg_holding_days": float(exits["holding_days"].mean()) if len(exits) else None,
        # One-way turnover: traded value per year as a multiple of average equity.
        "turnover_per_year": float(in_window["notional"].abs().sum() / 2 / avg_equity / years) if len(in_window) else 0.0,
        "fees_pct_of_avg_equity": float(in_window["fee"].sum() / avg_equity) if len(in_window) else 0.0,
    }


def load_artifacts(artifacts: Path) -> tuple[pd.Series, pd.DataFrame, pd.DataFrame]:
    eq = pd.read_csv(artifacts / "equity.csv", parse_dates=["timestamp"]).set_index("timestamp")["equity"]
    eq.index = eq.index.tz_localize(None).normalize() if eq.index.tz is not None else eq.index.normalize()
    trades_path = artifacts / "trades.csv"
    trades = pd.read_csv(trades_path, parse_dates=["timestamp"]) if trades_path.exists() else pd.DataFrame(
        columns=["timestamp", "event", "pnl", "return_pct", "holding_days"])
    fills_path = artifacts / "fills.jsonl"
    rows = [json.loads(line) for line in fills_path.read_text().splitlines() if line.strip()] if fills_path.exists() else []
    fills = pd.DataFrame(rows, columns=["timestamp", "notional", "fee"]) if not rows else pd.DataFrame(rows)
    fills["timestamp"] = pd.to_datetime(fills["timestamp"])
    for frame in (trades, fills):
        if len(frame) and frame["timestamp"].dt.tz is not None:
            frame["timestamp"] = frame["timestamp"].dt.tz_localize(None)
        if len(frame):
            frame["timestamp"] = frame["timestamp"].dt.normalize()
    return eq, trades, fills


def period_report(equity: pd.Series, trades: pd.DataFrame, fills: pd.DataFrame,
                  benchmark: pd.Series | None, split: str, bars_per_year: int) -> dict:
    """Stats for the full run, before ``split`` and from ``split`` on."""
    split_ts = pd.Timestamp(split)
    periods = {"full": equity, "in_sample": equity[equity.index < split_ts], "out_of_sample": equity[equity.index >= split_ts]}
    report = {}
    for name, eq in periods.items():
        if len(eq) < 2:
            report[name] = None
            continue
        entry = {**equity_stats(eq, bars_per_year), **trade_stats(trades, fills, eq)}
        if benchmark is not None:
            bench = benchmark[(benchmark.index >= eq.index[0]) & (benchmark.index <= eq.index[-1])]
            entry["benchmark"] = equity_stats(bench, bars_per_year)
        report[name] = entry
    return report


def to_jsonable(obj):
    if isinstance(obj, dict):
        return {k: to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(v) for v in obj]
    if isinstance(obj, (np.floating, float)):
        return None if not math.isfinite(obj) else float(obj)
    if isinstance(obj, np.integer):
        return int(obj)
    return obj
