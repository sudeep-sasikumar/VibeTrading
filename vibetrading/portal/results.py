"""Read the result files the CLI writes under ``var/``; never the price store."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from vibetrading.settings import COVERAGE_DIR, DB_PATH, VAR_DIR
from vibetrading.universes import UNIVERSES

RUNS_DIR = VAR_DIR / "runs"
PICKS_DIR = VAR_DIR / "picks"
BENCHMARKS_DIR = VAR_DIR / "benchmarks"
WALKFORWARD_DIR = VAR_DIR / "walkforward"

# The one setting that survived the walk-forward test (docs/phase2c-walkforward-findings.md).
FEATURED = {"universe": "sp500", "label": "momentum-tuned"}

# Where each universe stands after the tests so far, in one sentence each.
VERDICTS = {
    "sp500": "Momentum (3-month return, top 10 of the 50 most traded, monthly) is the one setting that beat SPY "
             "in walk-forward testing: about 26% a year vs 14% in 2018–2026. Pullback did not beat buy and hold.",
    "nasdaq100": "Neither strategy beat simply holding QQQ in walk-forward testing.",
    "nifty50": "Results use today's members back to 2015, so they are too good by an unknown amount; "
               "pullback is eaten by trading costs (STT).",
    "niftymidcap150": "Momentum looks strong, but on today's members only, so it can't be trusted yet; "
                      "pullback is eaten by trading costs.",
    "niftysmallcap250": "Momentum looks strong, but on today's members only, so it can't be trusted yet; "
                        "pullback is eaten by trading costs.",
    "crypto200": "Both strategies lost against simply holding BTC in every test, tuned or not.",
}

LABELS = {
    "momentum": "Momentum, default",
    "momentum-tuned": "Momentum, tuned",
    "momentum-walkforward-expanding": "Momentum, walk-forward",
    "pullback": "Pullback, default",
    "pullback-tuned": "Pullback, tuned",
    "pullback-walkforward-expanding": "Pullback, walk-forward",
}


def _json(path: Path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def run_dirs(universe: str) -> dict[str, Path]:
    """Latest run folder per label (``momentum``, ``momentum-tuned``, ...)."""
    out = {}
    base = RUNS_DIR / universe
    if not base.exists():
        return out
    for label_dir in sorted(p for p in base.iterdir() if p.is_dir()):
        stamps = sorted(p for p in label_dir.iterdir() if (p / "summary.json").exists())
        if stamps:
            out[label_dir.name] = stamps[-1]
    return out


def runs(universe: str) -> list[dict]:
    order = list(LABELS)
    items = []
    for label, path in run_dirs(universe).items():
        summary = _json(path / "summary.json")
        items.append({"label": label, "title": LABELS.get(label, label), "stamp": path.name,
                      "featured": universe == FEATURED["universe"] and label == FEATURED["label"],
                      "summary": summary})
    return sorted(items, key=lambda r: order.index(r["label"]) if r["label"] in order else len(order))


def run_path(universe: str, label: str, stamp: str) -> Path | None:
    """Only folders that exist in the listing: URL parts never become arbitrary paths."""
    if universe not in UNIVERSES:
        return None
    base = RUNS_DIR / universe
    label_dir = next((p for p in base.iterdir() if p.name == label), None) if base.exists() else None
    if label_dir is None:
        return None
    return next((p for p in label_dir.iterdir() if p.name == stamp and (p / "summary.json").exists()), None)


def series(universe: str, path: Path) -> dict:
    """Weekly strategy equity vs the benchmark, both starting at 1, and drawdowns."""
    eq = pd.read_csv(path / "artifacts" / "equity.csv", parse_dates=["timestamp"]).set_index("timestamp")["equity"]
    eq.index = (eq.index.tz_localize(None) if eq.index.tz is not None else eq.index).normalize()
    weekly = eq.resample("W-FRI").last().dropna()
    out = {"dates": [d.date().isoformat() for d in weekly.index],
           "strategy": (weekly / eq.iloc[0]).round(4).tolist(),
           "strategy_drawdown": (weekly / weekly.cummax() - 1).round(4).tolist()}
    bench_file = BENCHMARKS_DIR / f"{universe}.csv"
    if bench_file.exists():
        bench = pd.read_csv(bench_file, parse_dates=["date"]).set_index("date")["close"]
        bench = bench[bench.index >= eq.index[0]]
        if len(bench):
            b = bench.resample("W-FRI").last().reindex(weekly.index).ffill()
            first = bench.iloc[0]
            out["benchmark"] = [None if pd.isna(v) else round(v / first, 4) for v in b]
            out["benchmark_drawdown"] = [None if pd.isna(v) else round(v, 4) for v in (b / b.cummax() - 1)]
    return out


def trades(path: Path, limit: int = 100) -> list[dict]:
    file = path / "artifacts" / "trades.csv"
    if not file.exists():
        return []
    df = pd.read_csv(file)
    # "end_of_backtest" exits are positions still open on the last day, not trades.
    df = df[(df["event"] == "exit") & (df["reason"] != "end_of_backtest")].tail(limit).iloc[::-1]
    cols = ["timestamp", "code", "return_pct", "holding_days", "pnl", "reason"]
    return df[[c for c in cols if c in df]].round(2).to_dict("records")


def walkforward(universe: str) -> list[dict]:
    """Expanding-window results first (the main test), then rolling."""
    found = [r for r in (_json(p) for p in WALKFORWARD_DIR.glob(f"{universe}_*.json")) if r]
    return sorted(found, key=lambda r: (r["strategy"], r["window"] != "expanding"))


def picks(universe: str) -> dict | None:
    return _json(PICKS_DIR / f"{universe}.json")


def coverage(universe: str) -> dict | None:
    return _json(COVERAGE_DIR / f"{universe}.json")


def warnings(universe: str) -> list[str]:
    u = UNIVERSES[universe]
    cov = coverage(universe) or {}
    out = []
    if u.membership == "current_only":
        out.append("Uses today's index members all the way back to 2015 (NSE has no free history of past "
                   "members). Companies that dropped out are missing, so every result here is too good "
                   "by an unknown amount.")
    elif u.membership == "point_in_time":
        years = cov.get("members_priced_by_year") or []
        if years:
            y = years[0]
            share = y["priced"] / y["members"] if y["members"] else 0
            out.append(f"Membership is historical, but Yahoo has no prices for {len(cov.get('missing', []))} past "
                       f"members (mostly acquired or bankrupt companies): on {y['date']} only {y['priced']} of "
                       f"{y['members']} members ({share:.0%}) could be traded, so results are still somewhat flattered.")
    elif u.membership == "volume_rank":
        out.append("Each month's top 200 Binance USDT pairs by trading volume over the previous 30 days, delisted "
                   "coins included. Before 2021 fewer than 100 coins qualified, so backtests start in 2021.")
    bench = cov.get("benchmark") or {}
    if bench.get("from") and bench["from"] > u.backtest_start:
        out.append(f"The benchmark ({bench['symbol']}) only has prices from {bench['from']}; "
                   "comparisons before that use a shorter window.")
    return out


def data_as_of() -> str | None:
    if not DB_PATH.exists():
        return None
    return pd.Timestamp(DB_PATH.stat().st_mtime, unit="s", tz="UTC").isoformat(timespec="minutes")


def universe_overview(universe: str) -> dict:
    u = UNIVERSES[universe]
    cov = coverage(universe) or {}
    return {
        "key": universe, "title": u.title, "market": u.market, "membership": u.membership,
        "benchmark": u.benchmark, "regime": u.regime,
        "verdict": VERDICTS.get(universe), "warnings": warnings(universe),
        "symbols": cov.get("symbols"), "with_prices": cov.get("with_prices"),
        "runs": [{k: r[k] for k in ("label", "title", "stamp", "featured")} | {
            "out_of_sample": (r["summary"] or {}).get("periods", {}).get("out_of_sample"),
            "full": (r["summary"] or {}).get("periods", {}).get("full"),
        } for r in runs(universe)],
    }
