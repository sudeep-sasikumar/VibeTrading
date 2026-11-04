"""Run a strategy on a universe through Vibe-Trading's market engine.

The engine gets prices from our DuckDB store (``CachedLoader``), so reruns are
reproducible and nothing is fetched mid-backtest.
"""

from __future__ import annotations

import contextlib
import io
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd
import yaml

from vibetrading import metrics
from vibetrading.breaks import split_at_breaks
from vibetrading.settings import MIN_PRICE_ROWS, VAR_DIR
from vibetrading.store import PriceStore
from vibetrading.strategies import STRATEGIES
from vibetrading.universes import UNIVERSES, Universe

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"
RUNS_DIR = VAR_DIR / "runs"
# Calendar days of prices loaded before the backtest start, enough for a
# 200-bar average on stock calendars (~290 days) with room to spare.
WARMUP_DAYS = 400
BARS_PER_YEAR = {"crypto": 365, "us_equity": 252, "india_equity": 252}


class CachedLoader:
    """Vibe-Trading loader interface over the price store.

    Once ``prepared`` is set (the cleaned, break-split data), the engine is
    served that instead of the raw store.
    """

    name = "vibetrading_cache"
    requires_auth = False

    def __init__(self, store: PriceStore):
        self.store = store
        self.prepared: dict[str, pd.DataFrame] | None = None

    def is_available(self) -> bool:
        return True

    def fetch(self, codes, start_date, end_date, *, interval="1D", fields=None):
        if interval != "1D":
            raise ValueError("the price store holds daily bars only")
        if self.prepared is not None:
            return {s: self.prepared[s] for s in codes if s in self.prepared}
        data = self.store.prices(list(codes), start_date or None, end_date or None)
        return {s: df[["open", "high", "low", "close", "volume"]] for s, df in data.items()}


def tradeable_mask(members: pd.DataFrame, data_map: dict[str, pd.DataFrame], ends_early: set[str]) -> pd.DataFrame:
    """Dates x symbols: member that day and inside the symbol's price span.

    For a symbol whose prices stop before everyone else's (delisted, merged),
    the last two bars are masked out: the strategy's weight goes to zero on the
    second-to-last close and the engine sells at the last bar's open. Without
    this the position could never be sold. It assumes we see the end coming
    one day early, which flatters a collapse slightly; see docs/phase2-findings.md.
    """
    dates = pd.DatetimeIndex(sorted(set().union(*(df.index for df in data_map.values()))))
    mask = pd.DataFrame(False, index=dates, columns=list(data_map))
    spans = members.copy()
    spans["start_date"] = pd.to_datetime(spans["start_date"])
    spans["end_date"] = pd.to_datetime(spans["end_date"])
    for row in spans.itertuples():
        if row.symbol not in data_map:
            continue
        idx = data_map[row.symbol].index
        if len(idx) < 3:
            continue
        # Weight must be zero at the close of idx[-2], so the last tradeable bar is idx[-3].
        first, last = idx[0], idx[-3] if row.symbol in ends_early else idx[-1]
        lo = max(first, row.start_date) if pd.notna(row.start_date) else first
        hi = min(last, row.end_date - pd.Timedelta(1, unit="D")) if pd.notna(row.end_date) else last
        if lo <= hi:
            mask.loc[lo:hi, row.symbol] = True
    return mask


@dataclass
class RunResult:
    run_dir: Path
    summary: dict


def load_config() -> dict:
    return yaml.safe_load(CONFIG_PATH.read_text())


def run(universe_key: str, strategy_key: str, start: str | None = None, end: str | None = None,
        store: PriceStore | None = None) -> RunResult:
    universe: Universe = UNIVERSES[universe_key]
    cfg = load_config()
    period = cfg["periods"]["crypto" if universe.market == "crypto" else "stocks"]
    start = str(start or period["start"])
    split = str(period["split"])
    params = dict(cfg["strategies"][strategy_key])

    own_store = store is None
    store = store or PriceStore()
    try:
        members = store.members(universe.key)
        spans = store.price_spans(sorted(members["symbol"].unique()))
        usable = spans[spans["rows"] >= MIN_PRICE_ROWS]
        load_from = (pd.Timestamp(start) - pd.Timedelta(WARMUP_DAYS, unit="D")).date().isoformat()
        end = str(end or pd.Timestamp(usable["last_date"].max()).date().isoformat())

        loader = CachedLoader(store)
        raw = loader.fetch(sorted(usable.index), load_from, end)
        data_map, price_breaks = split_at_breaks(raw, crypto=universe.market == "crypto")
        loader.prepared = data_map
        latest = max(df.index[-1] for df in data_map.values())
        # Prices ending more than a week before the latest bar mean the symbol stopped trading.
        ends_early = {s for s, df in data_map.items() if df.index[-1] < latest - pd.Timedelta(7, unit="D")}
        # A piece after a break belongs to the universe whenever the original symbol did.
        pieces = pd.concat([members[members["symbol"] == b.symbol].assign(symbol=b.new_symbol)
                            for b in price_breaks] or [members.iloc[:0]])
        mask = tradeable_mask(pd.concat([members[members["symbol"].isin(raw)], pieces]), data_map, ends_early)
        strategy = STRATEGIES[strategy_key](tradeable=mask, **params)
        codes = sorted(data_map)

        engine_config = {
            "codes": codes, "start_date": load_from, "end_date": end,
            "source": loader.name, "interval": "1D",
            "initial_cash": cfg["initial_cash"][universe.market],
            "position_adjustment": "rebalance",
            "rebalance_tolerance": cfg["engine"]["rebalance_tolerance"],
            "evaluation_start_date": start,
        }
        if universe.market == "crypto":
            engine_config["funding_rate"] = cfg["engine"]["crypto_funding_rate"]

        run_dir = RUNS_DIR / universe.key / strategy_key / datetime.now().strftime("%Y%m%d-%H%M%S")
        run_dir.mkdir(parents=True, exist_ok=True)
        engine = _engine(universe, engine_config)
        bars_per_year = BARS_PER_YEAR[universe.market]

        started = datetime.now()
        engine_output = io.StringIO()
        try:
            # The engine prints its whole metrics JSON, and on a data error prints
            # a message and calls sys.exit; keep both out of our output.
            with contextlib.redirect_stdout(engine_output):
                engine.run_backtest(engine_config, loader, strategy, run_dir, bars_per_year)
        except SystemExit:
            raise RuntimeError(f"engine stopped: {engine_output.getvalue().strip()[-500:]}") from None
        seconds = (datetime.now() - started).total_seconds()

        equity, trades, fills = metrics.load_artifacts(run_dir / "artifacts")
        bench_prices = store.prices([universe.benchmark], start, end).get(universe.benchmark)
        benchmark = bench_prices["close"] if bench_prices is not None else None
        report = metrics.period_report(equity, trades, fills, benchmark, split, bars_per_year)

        summary = metrics.to_jsonable({
            "universe": universe.key, "universe_title": universe.title, "membership": universe.membership,
            "strategy": strategy_key, "params": params,
            "start": start, "split": split, "end": end,
            "benchmark": universe.benchmark,
            "symbols_loaded": len(raw), "symbols_ending_early": len(ends_early),
            "price_breaks": [{"symbol": b.symbol, "date": b.date.date().isoformat(),
                              "ratio": b.ratio, "new_symbol": b.new_symbol} for b in price_breaks],
            "engine": type(engine).__name__, "engine_seconds": round(seconds, 1),
            "engine_config": {k: v for k, v in engine_config.items() if k != "codes"},
            "periods": report,
        })
        (run_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        return RunResult(run_dir, summary)
    finally:
        if own_store:
            store.close()


def _engine(universe: Universe, config: dict):
    if universe.market == "crypto":
        from backtest.engines.crypto import CryptoEngine
        return CryptoEngine(config)
    if universe.market == "india_equity":
        from backtest.engines.india_equity import IndiaEquityEngine
        return IndiaEquityEngine(config)
    from backtest.engines.global_equity import GlobalEquityEngine
    return GlobalEquityEngine(config, market="us")


def render(summary: dict) -> str:
    def pct(v):
        return "   –  " if v is None else f"{v * 100:6.1f}%"

    def num(v, fmt="{:5.2f}"):
        return "  –  " if v is None else fmt.format(v)

    lines = [f"{summary['universe_title']} | {summary['strategy']} | {summary['start']} to {summary['end']} "
             f"(split {summary['split']}, {summary['membership']}, {summary['engine_seconds']} s)"]
    if summary.get("price_breaks"):
        lines.append("  price breaks split: " + ", ".join(
            f"{b['symbol']} {b['date']} (x{b['ratio']:.3g})" for b in summary["price_breaks"]))
    lines.append(f"  {'period':<14}{'CAGR':>8}{'max DD':>8}{'Sharpe':>7}{'bench CAGR':>11}{'bench DD':>9}"
                 f"{'trades':>7}{'win':>8}{'hold d':>7}{'turn/yr':>8}{'fees':>7}")
    late_benchmark = None
    for name, p in summary["periods"].items():
        if not p:
            lines.append(f"  {name:<14} (too short)")
            continue
        b = p.get("benchmark") or {}
        # A benchmark that starts later than the period is measured over a shorter window.
        late = bool(b.get("from")) and pd.Timestamp(b["from"]) > pd.Timestamp(p["from"]) + pd.Timedelta(10, unit="D")
        if late:
            late_benchmark = b["from"]
        lines.append(
            f"  {name:<14}{pct(p.get('cagr')):>8}{pct(p.get('max_drawdown')):>8}{num(p.get('sharpe')):>7}"
            f"{pct(b.get('cagr')) + ('*' if late else ' '):>11}{pct(b.get('max_drawdown')):>9}{p.get('round_trips', 0):>7}"
            f"{pct(p.get('win_rate')):>8}{num(p.get('avg_holding_days'), '{:5.1f}'):>7}"
            f"{num(p.get('turnover_per_year'), '{:6.1f}'):>8}{pct(p.get('fees_pct_of_avg_equity')):>7}"
        )
    if late_benchmark:
        lines.append(f"  * benchmark prices start {late_benchmark}, so it is measured from then")
    return "\n".join(lines)
