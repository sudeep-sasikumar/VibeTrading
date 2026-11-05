"""Walk-forward test: choose settings each year using only the years before it.

The single in-sample/out-of-sample split (2015-2021 / 2022 on) tests one choice
on one period. Walk-forward tests the *choosing*: at the start of each year
from 2018, pick the setting with the best Sharpe over the years before it
(same rule as the sweep), trade it for that year, then choose again. The
stitched-together years are what following the method would have earned.

Every setting is evaluated once over the whole period with the fast evaluator;
the strategies only look backwards, so a year of a full-period run is what that
setting would have earned that year. What this skips is the cost of switching
settings at a year boundary; ``Scheduled`` replays the chosen schedule through
the engine, switches included, to check.

Baselines over the same years:
- default settings (config.yaml), never re-chosen;
- the single-split tuned pick (tuned.yaml), which was chosen on 2015-2021, so
  its 2018-2021 years are in-sample and flattered;
- the grid average: every setting held in equal parts, i.e. making no choice;
- the benchmark (buy and hold).
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from vibetrading import fasteval
from vibetrading.backtest import BARS_PER_YEAR, load_config, load_tuned, prepare
from vibetrading.settings import VAR_DIR
from vibetrading.store import PriceStore
from vibetrading.strategies import STRATEGIES
from vibetrading.sweep import grid
from vibetrading.universes import UNIVERSES

WALKFORWARD_DIR = VAR_DIR / "walkforward"
# Years of history required before the first choice: 2015-2017, so testing starts in 2018.
MIN_TRAINING_YEARS = 3


def series_stats(returns: pd.Series, bars_per_year: int) -> dict:
    """CAGR, max drawdown and Sharpe of a daily return series."""
    returns = returns.dropna()
    if len(returns) < 2:
        return {"cagr": None, "max_drawdown": None, "sharpe": None, "total_return": None}
    equity = (1 + returns).cumprod()
    total = float(equity.iloc[-1] - 1)
    years = max((returns.index[-1] - returns.index[0]).days / 365.25, 1 / 365.25)
    peak = np.maximum.accumulate(np.concatenate([[1.0], equity.to_numpy()]))
    drawdown = np.concatenate([[1.0], equity.to_numpy()]) / peak - 1
    std = returns.std()
    return {
        "total_return": total,
        "cagr": (1 + total) ** (1 / years) - 1 if total > -1 else -1.0,
        "max_drawdown": float(drawdown.min()),
        "sharpe": float(returns.mean() / std * math.sqrt(bars_per_year)) if std > 0 else None,
    }


def grid_returns(prep, strategy_key: str, cfg: dict) -> tuple[pd.DataFrame, list[dict]]:
    """Daily net returns (dates x setting number) for every setting in the sweep grid."""
    cost = cfg["engine"]["sweep_cost_per_side"][prep.universe.market]
    opens = fasteval.opens_panel(prep)
    settings = grid(strategy_key, cfg)
    columns = {}
    for i, params in enumerate(settings):
        daily, _ = fasteval.net_returns(fasteval.strategy_weights(prep, strategy_key, params), opens, cost)
        columns[i] = daily[daily.index >= pd.Timestamp(prep.start)]
    return pd.DataFrame(columns), settings


def choose_on(train: pd.DataFrame, bars_per_year: int) -> int | None:
    """Best training Sharpe among settings with a positive training CAGR (the sweep's rule)."""
    best, best_sharpe = None, -np.inf
    for col in train.columns:
        st = series_stats(train[col], bars_per_year)
        if st["cagr"] is not None and st["cagr"] > 0 and st["sharpe"] is not None and st["sharpe"] > best_sharpe:
            best, best_sharpe = col, st["sharpe"]
    return best


def walk_forward(returns: pd.DataFrame, start: str, bars_per_year: int,
                 window_years: int | None = None) -> tuple[pd.Series, list[dict]]:
    """Stitched returns of the yearly choices, and the schedule.

    ``window_years`` None trains on everything since ``start`` (expanding);
    a number trains on only that many most recent years (rolling).
    """
    first_year = pd.Timestamp(start).year + MIN_TRAINING_YEARS
    pieces, schedule = [], []
    for year in sorted(y for y in set(returns.index.year) if y >= first_year):
        year_start = pd.Timestamp(year=year, month=1, day=1)
        train_from = pd.Timestamp(start) if window_years is None else max(
            pd.Timestamp(start), pd.Timestamp(year=year - window_years, month=1, day=1))
        train = returns[(returns.index >= train_from) & (returns.index < year_start)]
        test = returns[returns.index.year == year]
        chosen = choose_on(train, bars_per_year)
        year_returns = (1 + test).prod() - 1
        if chosen is None:  # nothing made money in training: stay in cash
            pieces.append(pd.Series(0.0, index=test.index))
        else:
            pieces.append(test[chosen])
        schedule.append({
            "year": year,
            "chosen": None if chosen is None else int(chosen),
            "return": float(year_returns[chosen]) if chosen is not None else 0.0,
            "grid_median": float(year_returns.median()),
            "beats_share_of_grid": float((year_returns < year_returns[chosen]).mean()) if chosen is not None else None,
        })
    return pd.concat(pieces), schedule


def _index_of(settings: list[dict], params: dict) -> int | None:
    for i, s in enumerate(settings):
        if all(s.get(k) == v for k, v in params.items()):
            return i
    return None


def run(universe_key: str, strategy_key: str, windows: list[int | None]) -> list[dict]:
    """One result per training window (None = expanding); the grid is evaluated once."""
    cfg = load_config()
    store = PriceStore(read_only=True)
    try:
        prep = prepare(universe_key, store)
    finally:
        store.close()
    returns, settings = grid_returns(prep, strategy_key, cfg)
    return [_result(prep, strategy_key, cfg, returns, settings, w) for w in windows]


def _result(prep, strategy_key: str, cfg: dict, returns: pd.DataFrame, settings: list[dict],
            window_years: int | None) -> dict:
    universe_key = prep.universe.key
    bars_per_year = BARS_PER_YEAR[prep.universe.market]
    wf, schedule = walk_forward(returns, prep.start, bars_per_year, window_years)
    test_from = wf.index[0]
    swept = list(cfg["sweeps"][strategy_key])
    for row in schedule:
        row["params"] = None if row["chosen"] is None else {k: settings[row["chosen"]][k] for k in swept}

    default_i = _index_of(settings, cfg["strategies"][strategy_key])
    tuned_params = load_tuned().get(strategy_key, {}).get(universe_key)
    tuned_i = _index_of(settings, tuned_params) if tuned_params else None
    bench = prep.benchmark.pct_change() if prep.benchmark is not None else None

    def window(series):
        return series[series.index >= test_from]

    lines = {"walk_forward": wf, "grid_average": window(returns.mean(axis=1))}
    if default_i is not None:
        lines["default"] = window(returns[default_i])
    if tuned_i is not None:
        lines["single_split_tuned"] = window(returns[tuned_i])
    if bench is not None:
        lines["benchmark"] = window(bench)

    def year_table(series):
        return {int(y): float((1 + r).prod() - 1) for y, r in series.groupby(series.index.year)}

    result = {
        "universe": universe_key, "strategy": strategy_key,
        "window": "expanding" if window_years is None else f"{window_years}y rolling",
        "test_from": test_from.date().isoformat(), "test_to": wf.index[-1].date().isoformat(),
        "settings_tried": len(settings),
        "schedule": schedule,
        "stats": {name: series_stats(s, bars_per_year) for name, s in lines.items()},
        "years": {name: year_table(s) for name, s in lines.items()},
        "beats_grid_median_years": sum(1 for r in schedule if r["return"] > r["grid_median"]),
        "beats_benchmark_years": (sum(1 for r in schedule
                                      if bench is not None and r["return"] > year_table(window(bench)).get(r["year"], np.inf))),
        "distinct_settings_chosen": len({r["chosen"] for r in schedule}),
    }
    WALKFORWARD_DIR.mkdir(parents=True, exist_ok=True)
    name = f"{universe_key}_{strategy_key}_{'expanding' if window_years is None else f'{window_years}y'}.json"
    (WALKFORWARD_DIR / name).write_text(json.dumps(result, indent=2, default=float), encoding="utf-8")
    return result


def render(result: dict) -> str:
    title = UNIVERSES[result["universe"]].title
    lines = [f"{title} | {result['strategy']} | walk-forward ({result['window']} training) "
             f"{result['test_from']} to {result['test_to']}, {result['settings_tried']} settings"]
    names = [n for n in ("walk_forward", "default", "single_split_tuned", "grid_average", "benchmark")
             if n in result["stats"]]
    lines.append(f"  {'year':<6}{'chosen setting':<44}" + "".join(f"{n[:12]:>13}" for n in names))
    for row in result["schedule"]:
        y = row["year"]
        params = "cash (nothing made money)" if row["params"] is None else _short(row["params"])
        lines.append(f"  {y:<6}{params[:43]:<44}" + "".join(
            f"{result['years'][n].get(y, float('nan')) * 100:12.1f}%" for n in names))
    lines.append(f"  {'all':<6}{'CAGR / max DD / Sharpe':<44}" + "".join(
        f"{_fmt(result['stats'][n]):>13}" for n in names))
    n = len(result["schedule"])
    lines.append(f"  chosen setting beat the grid median in {result['beats_grid_median_years']} of {n} years, "
                 f"the benchmark in {result['beats_benchmark_years']} of {n}; "
                 f"{result['distinct_settings_chosen']} different settings chosen")
    return "\n".join(lines)


_SHORT_KEYS = {"lookback": "lb", "top_n": "top", "regime_filter": "trend", "liquid_top": "liq",
               "entry_rsi": "rsi<", "exit_rule": "exit", "max_hold_bars": "hold", "stop_loss": "stop"}


def _short(params: dict) -> str:
    """``lb=252 top=5 weekly trend=off liq=-``: one setting on one line."""
    parts = []
    for key, value in params.items():
        shown = {True: "on", False: "off", None: "-"}.get(value, value) if isinstance(value, (bool, type(None))) else value
        if key == "rebalance":
            parts.append(str(shown))
        elif key == "entry_rsi":
            parts.append(f"rsi<{shown}")
        else:
            parts.append(f"{_SHORT_KEYS.get(key, key)}={shown}")
    return " ".join(parts)


def _fmt(st: dict) -> str:
    if st["cagr"] is None:
        return "–"
    return f"{st['cagr'] * 100:.0f}/{st['max_drawdown'] * 100:.0f}/{st['sharpe']:.2f}"


@dataclass
class Scheduled:
    """Replays a walk-forward schedule in the engine: each year trades the
    weights of the setting chosen for it (``None`` = cash that year). Years
    before the first choice use the first setting (they are warm-up only);
    years after the last use the last."""

    strategy_key: str
    schedule: dict[int, dict | None]   # year -> full strategy params, or None for cash
    tradeable: pd.DataFrame
    regime: pd.Series | None = None

    def generate(self, data_map: dict[str, pd.DataFrame]) -> dict[str, pd.Series]:
        years = sorted(self.schedule)
        panels: dict[str, pd.DataFrame] = {}
        index = pd.DatetimeIndex(sorted(set().union(*(df.index for df in data_map.values()))))
        combined = pd.DataFrame(0.0, index=index, columns=list(data_map))
        row_years = index.year
        for year in years:
            params = self.schedule[year]
            use = (row_years == year) | ((year == years[0]) & (row_years < year))                 | ((year == years[-1]) & (row_years > year))
            if params is None:
                combined.loc[use] = 0.0
                continue
            key = json.dumps(params, sort_keys=True)
            if key not in panels:
                strategy = STRATEGIES[self.strategy_key](tradeable=self.tradeable, regime=self.regime, **params)
                panels[key] = pd.DataFrame(strategy.generate(data_map)).reindex(index=index, columns=combined.columns)
            combined.loc[use] = panels[key].loc[use].fillna(0.0)
        return {s: combined[s].reindex(df.index).fillna(0.0) for s, df in data_map.items()}


def run_engine(result: dict):
    """Replay a walk-forward result's schedule through the engine, switches included."""
    from vibetrading import backtest

    cfg = load_config()
    base = dict(cfg["strategies"][result["strategy"]])
    schedule = {row["year"]: (None if row["params"] is None else {**base, **row["params"]})
                for row in result["schedule"]}
    window = "expanding" if result["window"] == "expanding" else result["window"].split()[0]
    return backtest.run(
        result["universe"], result["strategy"], start=result["test_from"],
        strategy_factory=lambda prep: Scheduled(result["strategy"], schedule, prep.mask, prep.regime),
        label=f"{result['strategy']}-walkforward-{window}",
        params={"walk_forward_schedule": {str(y): p for y, p in schedule.items()}},
    )
