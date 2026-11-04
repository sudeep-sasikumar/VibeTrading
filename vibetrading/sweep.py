"""Choose strategy settings on the in-sample years, without peeking.

For each universe, every combination in config.yaml's ``sweeps`` grid is run
through the fast evaluator. The rule, fixed before any result is seen: pick the
best in-sample Sharpe among settings that made money in-sample. Out-of-sample
numbers are computed for every setting but only reported, so you can see
whether in-sample rank carried over at all (the rank correlation) and how the
chosen setting compares with the grid's typical out-of-sample result.
"""

from __future__ import annotations

import itertools
import json
import logging
from concurrent.futures import ProcessPoolExecutor

import pandas as pd
import yaml

from vibetrading import fasteval
from vibetrading.backtest import BARS_PER_YEAR, TUNED_PATH, load_config, load_tuned, prepare
from vibetrading.settings import VAR_DIR
from vibetrading.store import PriceStore
from vibetrading.universes import UNIVERSES

logger = logging.getLogger(__name__)

SWEEPS_DIR = VAR_DIR / "sweeps"


def grid(strategy_key: str, cfg: dict) -> list[dict]:
    """Every combination of the sweep values over the strategy's defaults."""
    space = cfg["sweeps"][strategy_key]
    keys = list(space)
    base = dict(cfg["strategies"][strategy_key])
    return [{**base, **dict(zip(keys, values))} for values in itertools.product(*space.values())]


def sweep_universe(universe_key: str, strategy_key: str) -> dict:
    """Runs in a worker process: evaluate the whole grid on one universe."""
    cfg = load_config()
    universe = UNIVERSES[universe_key]
    cost = cfg["sweep_cost_per_side"][universe.market] if "sweep_cost_per_side" in cfg else \
        cfg["engine"]["sweep_cost_per_side"][universe.market]
    store = PriceStore(read_only=True)
    try:
        prep = prepare(universe_key, store)
    finally:
        store.close()
    rows = []
    swept = list(cfg["sweeps"][strategy_key])
    for params in grid(strategy_key, cfg):
        result = fasteval.evaluate(prep, strategy_key, params, cost, BARS_PER_YEAR[universe.market])
        rows.append({"params": {k: params[k] for k in swept}, **result})
    return {"universe": universe_key, "strategy": strategy_key, "split": prep.split,
            "start": prep.start, "end": prep.end, "rows": rows}


def choose(rows: list[dict]) -> dict | None:
    """Best in-sample Sharpe among settings with a positive in-sample CAGR."""
    candidates = [r for r in rows if r["in_sample"] and (r["in_sample"].get("cagr") or -1) > 0
                  and r["in_sample"].get("sharpe") is not None]
    return max(candidates, key=lambda r: r["in_sample"]["sharpe"]) if candidates else None


def summarize(result: dict) -> dict:
    rows = result["rows"]
    table = pd.DataFrame([{
        "is_sharpe": r["in_sample"]["sharpe"] if r["in_sample"] else None,
        "oos_sharpe": r["out_of_sample"]["sharpe"] if r["out_of_sample"] else None,
        "oos_cagr": r["out_of_sample"]["cagr"] if r["out_of_sample"] else None,
    } for r in rows]).astype(float)
    chosen = choose(rows)
    return {
        "configs": len(rows),
        "chosen": chosen,
        # Does a better in-sample Sharpe tend to mean a better out-of-sample one?
        "rank_correlation_is_oos": float(table["is_sharpe"].corr(table["oos_sharpe"], method="spearman")),
        "oos_cagr_median": float(table["oos_cagr"].median()),
        "oos_sharpe_median": float(table["oos_sharpe"].median()),
        "chosen_oos_sharpe_percentile": (float((table["oos_sharpe"] < chosen["out_of_sample"]["sharpe"]).mean())
                                         if chosen and chosen["out_of_sample"] else None),
    }


def run(strategy_key: str, universe_keys: list[str], workers: int = 1) -> list[dict]:
    """``workers`` > 1 sweeps universes in parallel; each worker needs ~0.5-1 GB."""
    if workers <= 1:
        results = [sweep_universe(key, strategy_key) for key in universe_keys]
    else:
        with ProcessPoolExecutor(min(workers, len(universe_keys))) as pool:
            results = list(pool.map(sweep_universe, universe_keys, [strategy_key] * len(universe_keys)))
    SWEEPS_DIR.mkdir(parents=True, exist_ok=True)
    tuned = load_tuned()
    for result in results:
        result["summary"] = summarize(result)
        path = SWEEPS_DIR / f"{result['universe']}_{strategy_key}.json"
        path.write_text(json.dumps(result, indent=2, default=float), encoding="utf-8")
        chosen = result["summary"]["chosen"]
        if chosen:
            tuned.setdefault(strategy_key, {})[result["universe"]] = chosen["params"]
    TUNED_PATH.write_text(
        "# Written by `python -m vibetrading sweep`: per strategy and universe, the\n"
        "# setting with the best in-sample Sharpe. Used by `backtest --tuned`.\n"
        + yaml.safe_dump(tuned, sort_keys=True), encoding="utf-8")
    return results


def render(result: dict) -> str:
    s = result["summary"]
    title = UNIVERSES[result["universe"]].title
    if not s["chosen"]:
        return f"{title}: no setting made money in-sample"
    c = s["chosen"]

    def fmt(p):
        return "–" if not p else f"CAGR {p['cagr'] * 100:5.1f}%  DD {p['max_drawdown'] * 100:6.1f}%  Sharpe {p['sharpe']:.2f}"

    return "\n".join([
        f"{title} ({s['configs']} settings; in-sample to {result['split']})",
        f"  chosen: {c['params']}",
        f"    in-sample      {fmt(c['in_sample'])}",
        f"    out-of-sample  {fmt(c['out_of_sample'])}   turnover {c['turnover_per_year']:.1f}/yr",
        f"  grid out-of-sample median: CAGR {s['oos_cagr_median'] * 100:.1f}%, Sharpe {s['oos_sharpe_median']:.2f}; "
        f"chosen beats {s['chosen_oos_sharpe_percentile'] * 100:.0f}% of the grid out-of-sample; "
        f"in-sample vs out-of-sample rank correlation {s['rank_correlation_is_oos']:+.2f}",
    ])


def check(universe_keys: list[str]) -> list[dict]:
    """Fast evaluator vs the engine on default settings (latest saved engine runs)."""
    cfg = load_config()
    out = []
    store = PriceStore(read_only=True)
    try:
        for key in universe_keys:
            prep = prepare(key, store)
            market = UNIVERSES[key].market
            for strategy_key in cfg["strategies"]:
                runs = sorted((VAR_DIR / "runs" / key / strategy_key).glob("*/summary.json"))
                if not runs:
                    continue
                engine = json.loads(runs[-1].read_text(encoding="utf-8"))["periods"]
                fast = fasteval.evaluate(prep, strategy_key, dict(cfg["strategies"][strategy_key]),
                                         cfg["engine"]["sweep_cost_per_side"][market], BARS_PER_YEAR[market])
                for period in ("in_sample", "out_of_sample"):
                    out.append({"universe": key, "strategy": strategy_key, "period": period,
                                "engine_cagr": engine[period]["cagr"], "fast_cagr": fast[period]["cagr"],
                                "engine_sharpe": engine[period]["sharpe"], "fast_sharpe": fast[period]["sharpe"]})
    finally:
        store.close()
    return out
