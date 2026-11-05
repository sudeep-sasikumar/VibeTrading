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
# Settings chosen by `python -m vibetrading sweep`, per strategy and universe.
TUNED_PATH = CONFIG_PATH.with_name("tuned.yaml")
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


@dataclass
class Prepared:
    """Everything a strategy needs for one universe and window, loaded once."""

    universe: Universe
    start: str
    split: str
    end: str
    load_from: str
    raw: dict[str, pd.DataFrame]          # store prices, before break splitting
    data_map: dict[str, pd.DataFrame]     # what the engine and strategies see
    mask: pd.DataFrame
    regime: pd.Series                     # True while the market index closes above its average
    benchmark: pd.Series | None
    ends_early: set[str]
    price_breaks: list


def load_config() -> dict:
    return yaml.safe_load(CONFIG_PATH.read_text())


def load_tuned() -> dict:
    return yaml.safe_load(TUNED_PATH.read_text()) if TUNED_PATH.exists() else {}


def strategy_params(cfg: dict, strategy_key: str, universe_key: str, tuned: bool) -> dict:
    """Defaults from config.yaml, overlaid with tuned.yaml's choice for this universe."""
    params = dict(cfg["strategies"][strategy_key])
    if tuned:
        chosen = load_tuned().get(strategy_key, {}).get(universe_key)
        if chosen is None:
            raise ValueError(f"tuned.yaml has no {strategy_key} settings for {universe_key}; run a sweep first")
        params.update(chosen)
    return params


def prepare(universe_key: str, store: PriceStore, start: str | None = None, end: str | None = None) -> Prepared:
    universe: Universe = UNIVERSES[universe_key]
    cfg = load_config()
    period = cfg["periods"]["crypto" if universe.market == "crypto" else "stocks"]
    start = str(start or period["start"])
    split = str(period["split"])

    members = store.members(universe.key)
    spans = store.price_spans(sorted(members["symbol"].unique()))
    usable = spans[spans["rows"] >= MIN_PRICE_ROWS]
    load_from = (pd.Timestamp(start) - pd.Timedelta(WARMUP_DAYS, unit="D")).date().isoformat()
    end = str(end or pd.Timestamp(usable["last_date"].max()).date().isoformat())

    raw = CachedLoader(store).fetch(sorted(usable.index), load_from, end)
    data_map, price_breaks = split_at_breaks(raw, crypto=universe.market == "crypto")
    latest = max(df.index[-1] for df in data_map.values())
    # Prices ending more than a week before the latest bar mean the symbol stopped trading.
    ends_early = {s for s, df in data_map.items() if df.index[-1] < latest - pd.Timedelta(7, unit="D")}
    # A piece after a break belongs to the universe whenever the original symbol did.
    pieces = pd.concat([members[members["symbol"] == b.symbol].assign(symbol=b.new_symbol)
                        for b in price_breaks] or [members.iloc[:0]])
    mask = tradeable_mask(pd.concat([members[members["symbol"].isin(raw)], pieces]), data_map, ends_early)

    regime_close = store.prices([universe.regime], None, end)[universe.regime]["close"]
    regime_ma = cfg["engine"]["regime_ma"]
    regime = regime_close > regime_close.rolling(regime_ma, min_periods=regime_ma).mean()
    bench = store.prices([universe.benchmark], start, end).get(universe.benchmark)
    return Prepared(universe, start, split, end, load_from, raw, data_map, mask, regime,
                    bench["close"] if bench is not None else None, ends_early, price_breaks)


def run(universe_key: str, strategy_key: str, start: str | None = None, end: str | None = None,
        store: PriceStore | None = None, tuned: bool = False,
        strategy_factory=None, label: str | None = None, params: dict | None = None) -> RunResult:
    """Run one strategy through the engine.

    ``strategy_factory(prep)`` replaces the configured strategy (the walk-forward
    uses it to run a schedule of settings); ``label`` names its run folder and
    ``params`` is what its summary records.
    """
    cfg = load_config()
    if strategy_factory is None:
        params = strategy_params(cfg, strategy_key, universe_key, tuned)

    own_store = store is None
    store = store or PriceStore()
    try:
        prep = prepare(universe_key, store, start, end)
        universe = prep.universe
        loader = CachedLoader(store)
        loader.prepared = prep.data_map
        strategy = (strategy_factory(prep) if strategy_factory is not None
                    else STRATEGIES[strategy_key](tradeable=prep.mask, regime=prep.regime, **params))

        engine_config = {
            "codes": sorted(prep.data_map), "start_date": prep.load_from, "end_date": prep.end,
            "source": loader.name, "interval": "1D",
            "initial_cash": cfg["initial_cash"][universe.market],
            "position_adjustment": "rebalance",
            "rebalance_tolerance": cfg["engine"]["rebalance_tolerance"],
            "evaluation_start_date": prep.start,
        }
        if universe.market == "crypto":
            engine_config["funding_rate"] = cfg["engine"]["crypto_funding_rate"]

        label = label or (f"{strategy_key}-tuned" if tuned else strategy_key)
        run_dir = RUNS_DIR / universe.key / label / datetime.now().strftime("%Y%m%d-%H%M%S")
        run_dir.mkdir(parents=True, exist_ok=True)
        engine = _engine(universe, engine_config)
        # The engine also writes a CSV of every symbol's prices (750 files for the
        # S&P 500). They duplicate the price store and, on a machine short of
        # memory, writing them was what failed; pass it no prices to copy.
        write_artifacts = engine._write_artifacts
        engine._write_artifacts = lambda run_dir, _data_map, *args, **kw: write_artifacts(run_dir, {}, *args, **kw)
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
        report = metrics.period_report(equity, trades, fills, prep.benchmark, prep.split, bars_per_year)

        summary = metrics.to_jsonable({
            "universe": universe.key, "universe_title": universe.title, "membership": universe.membership,
            "strategy": strategy_key, "tuned": tuned, "params": params,
            "start": prep.start, "split": prep.split, "end": prep.end,
            "benchmark": universe.benchmark, "regime_symbol": universe.regime,
            "symbols_loaded": len(prep.raw), "symbols_ending_early": len(prep.ends_early),
            "price_breaks": [{"symbol": b.symbol, "date": b.date.date().isoformat(),
                              "ratio": b.ratio, "new_symbol": b.new_symbol} for b in prep.price_breaks],
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

    tuned = " (tuned)" if summary.get("tuned") else ""
    lines = [f"{summary['universe_title']} | {summary['strategy']}{tuned} | {summary['start']} to {summary['end']} "
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
