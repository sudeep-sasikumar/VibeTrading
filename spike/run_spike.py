"""Phase 0 spike: run one toy strategy through Vibe-Trading's crypto, US and India engines.

The strategy is deliberately trivial (long while the 50-day average is above the
200-day average). The point is to prove the engine plumbing, not to find an edge.

    python spike/run_spike.py            # all three markets
    python spike/run_spike.py india      # one market
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pandas as pd

from backtest.engines.crypto import CryptoEngine
from backtest.engines.global_equity import GlobalEquityEngine
from backtest.engines.india_equity import IndiaEquityEngine
from backtest.loaders.registry import resolve_loader

START, END = "2019-01-01", "2026-10-31"
RUNS = Path(__file__).parent / "runs"

MARKETS = {
    "crypto": {
        "loader_market": "crypto",
        "engine": CryptoEngine,
        "bars_per_year": 365,
        "codes": ["BTC-USDT", "ETH-USDT", "SOL-USDT", "BNB-USDT", "XRP-USDT",
                  "ADA-USDT", "DOGE-USDT", "AVAX-USDT", "LINK-USDT", "LTC-USDT"],
    },
    "us": {
        "loader_market": "us_equity",
        "engine": lambda cfg: GlobalEquityEngine(cfg, market="us"),
        "bars_per_year": 252,
        # The yahoo loader returns nothing for bare tickers; it wants the .US suffix.
        "codes": ["AAPL.US", "MSFT.US", "NVDA.US", "AMZN.US", "META.US",
                  "GOOGL.US", "JPM.US", "XOM.US", "UNH.US", "COST.US"],
    },
    "india": {
        "loader_market": "india_equity",
        "engine": IndiaEquityEngine,
        "bars_per_year": 252,
        "codes": ["RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS", "ICICIBANK.NS",
                  "ITC.NS", "LT.NS", "SBIN.NS", "BHARTIARTL.NS", "HINDUNILVR.NS"],
    },
}


class SignalEngine:
    """Long (1) while SMA50 > SMA200, flat (0) otherwise."""

    def generate(self, data_map: dict[str, pd.DataFrame]) -> dict[str, pd.Series]:
        signals = {}
        for code, df in data_map.items():
            close = df["close"]
            fast, slow = close.rolling(50).mean(), close.rolling(200).mean()
            signals[code] = (fast > slow).astype(float)
        return signals


def run(name: str) -> dict:
    spec = MARKETS[name]
    loader = resolve_loader(spec["loader_market"])
    config = {
        "codes": spec["codes"],
        "start_date": START,
        "end_date": END,
        "source": loader.name,
        "interval": "1D",
        "initial_cash": 100_000,
        "warmup_bars": 200,
    }
    run_dir = RUNS / name
    run_dir.mkdir(parents=True, exist_ok=True)
    engine = spec["engine"](config)

    started = time.perf_counter()
    try:
        metrics = engine.run_backtest(config, loader, SignalEngine(), run_dir, spec["bars_per_year"])
    except SystemExit as exc:  # the engine reports some failures by printing JSON and exiting
        return {"market": name, "loader": loader.name, "error": f"engine exited ({exc.code})"}
    return {
        "market": name,
        "loader": loader.name,
        "engine": type(engine).__name__,
        "seconds": round(time.perf_counter() - started, 1),
        "metrics": metrics,
    }


if __name__ == "__main__":
    names = sys.argv[1:] or list(MARKETS)
    results = [run(n) for n in names]
    out = RUNS / "summary.json"
    out.write_text(json.dumps(results, indent=2, default=str))
    for r in results:
        m = r.get("metrics") or {}
        keys = ("total_return", "annual_return", "max_drawdown", "sharpe", "trade_count", "benchmark_return")
        print(r["market"], r.get("loader"), r.get("engine"), r.get("seconds"), r.get("error", ""),
              {k: m.get(k) for k in keys if k in m})
