"""What each strategy holds now, and the benchmark series the portal charts.

For every universe, each strategy is run on the latest prices with its default
and its tuned settings; the weights on the last bar are what it would hold from
the next open. The output is a JSON file per universe in ``var/picks``, plus the
benchmark's closes in ``var/benchmarks``, so the web app never has to open the
price store (which a running refresh may be writing).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pandas as pd

from vibetrading import fasteval
from vibetrading.backtest import load_config, load_tuned, prepare, strategy_params
from vibetrading.settings import VAR_DIR
from vibetrading.store import PriceStore
from vibetrading.universes import UNIVERSES

PICKS_DIR = VAR_DIR / "picks"
BENCHMARKS_DIR = VAR_DIR / "benchmarks"
# Changes this many bars back count as "recent" buys and sells.
RECENT_BARS = 5


def holdings(weights: pd.DataFrame, data_map: dict[str, pd.DataFrame]) -> dict:
    last = weights.index[-1]
    now = weights.iloc[-1]
    before = weights.iloc[-1 - RECENT_BARS] if len(weights) > RECENT_BARS else weights.iloc[0] * 0
    held = now[now > 1e-9].sort_values(ascending=False)
    return {
        "as_of": last.date().isoformat(),
        "invested": float(now.sum()),
        "positions": [{"symbol": s, "weight": float(w), "close": float(data_map[s]["close"].iloc[-1])}
                      for s, w in held.items()],
        "recent_buys": sorted(now.index[(now > 1e-9) & (before <= 1e-9)]),
        "recent_sells": sorted(now.index[(now <= 1e-9) & (before > 1e-9)]),
    }


def run(universe_key: str) -> dict:
    cfg = load_config()
    tuned = load_tuned()
    store = PriceStore(read_only=True)
    try:
        prep = prepare(universe_key, store)
    finally:
        store.close()

    out = {"universe": universe_key, "computed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "strategies": {}}
    for strategy_key in cfg["strategies"]:
        for label, use_tuned in ((strategy_key, False), (f"{strategy_key}-tuned", True)):
            if use_tuned and universe_key not in tuned.get(strategy_key, {}):
                continue
            params = strategy_params(cfg, strategy_key, universe_key, use_tuned)
            weights = fasteval.strategy_weights(prep, strategy_key, params)
            out["strategies"][label] = {"params": params, **holdings(weights, prep.data_map)}

    PICKS_DIR.mkdir(parents=True, exist_ok=True)
    (PICKS_DIR / f"{universe_key}.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    if prep.benchmark is not None:
        BENCHMARKS_DIR.mkdir(parents=True, exist_ok=True)
        bench = prep.benchmark.rename("close")
        bench.index.name = "date"
        bench.to_csv(BENCHMARKS_DIR / f"{universe_key}.csv")
    return out


def render(result: dict) -> str:
    lines = [f"{UNIVERSES[result['universe']].title}"]
    for label, s in result["strategies"].items():
        names = ", ".join(f"{p['symbol']} {p['weight'] * 100:.0f}%" for p in s["positions"]) or "cash"
        lines.append(f"  {label:<15} as of {s['as_of']}, {s['invested'] * 100:.0f}% invested: {names}")
    return "\n".join(lines)
