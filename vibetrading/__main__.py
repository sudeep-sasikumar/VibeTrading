"""Command line.

    python -m vibetrading refresh  [universe ...]
    python -m vibetrading coverage [universe ...]
    python -m vibetrading backtest --strategy momentum [universe ...] [--start 2016-01-01] [--end ...] [--tuned]
    python -m vibetrading sweep    --strategy momentum [universe ...]
    python -m vibetrading check    [universe ...]
    python -m vibetrading walkforward --strategy momentum [universe ...] [--window 3] [--engine]
    python -m vibetrading picks    [universe ...]
"""

from __future__ import annotations

import argparse
import logging
import sys

from vibetrading import coverage
from vibetrading.store import PriceStore
from vibetrading.strategies import STRATEGIES
from vibetrading.universes import UNIVERSES, refresh


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vibetrading")
    sub = parser.add_subparsers(dest="command", required=True)
    commands = {}
    for name, help_text in [("refresh", "rebuild membership and fetch prices"),
                            ("coverage", "report how much of each universe has prices"),
                            ("backtest", "run a strategy and print its stats"),
                            ("sweep", "choose settings on the in-sample years (fast evaluator)"),
                            ("check", "compare the fast evaluator with saved engine runs"),
                            ("walkforward", "re-choose settings each year from earlier years only"),
                            ("picks", "what each strategy holds now (for the portal)")]:
        cmd = commands[name] = sub.add_parser(name, help=help_text)
        cmd.add_argument("universes", nargs="*", metavar="universe",
                         help=f"any of {', '.join(UNIVERSES)} (default: all)")
    bt = commands["backtest"]
    bt.add_argument("--strategy", required=True, choices=list(STRATEGIES))
    bt.add_argument("--start", help="first evaluated date (default from config.yaml)")
    bt.add_argument("--end", help="last date (default: latest price)")
    bt.add_argument("--tuned", action="store_true", help="use the settings in tuned.yaml")
    commands["sweep"].add_argument("--strategy", required=True, choices=list(STRATEGIES))
    commands["sweep"].add_argument("--workers", type=int, default=1,
                                   help="universes swept in parallel (each needs ~0.5-1 GB of memory)")
    wf = commands["walkforward"]
    wf.add_argument("--strategy", required=True, choices=list(STRATEGIES))
    wf.add_argument("--window", type=int, help="also test training on only the last N years")
    wf.add_argument("--engine", action="store_true", help="also replay the chosen schedule through the engine")
    args = parser.parse_args(argv)
    unknown = [u for u in args.universes if u not in UNIVERSES]
    if unknown:
        parser.error(f"unknown universe {', '.join(unknown)}; choose from {', '.join(UNIVERSES)}")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    # yfinance logs every ticker it can't find; the coverage report lists them instead.
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    keys = args.universes or list(UNIVERSES)
    if args.command == "picks":
        from vibetrading import picks
        for key in keys:
            print(picks.render(picks.run(key)))
        return 0
    if args.command == "walkforward":
        from vibetrading import walkforward
        windows = [None] + ([args.window] if args.window else [])
        for key in keys:
            for i, result in enumerate(walkforward.run(key, args.strategy, windows)):
                print(walkforward.render(result))
                if args.engine and i == 0:  # replay the expanding-window schedule
                    from vibetrading import backtest
                    print(backtest.render(walkforward.run_engine(result).summary))
        return 0
    if args.command in ("sweep", "check"):
        from vibetrading import sweep
        if args.command == "sweep":
            for result in sweep.run(args.strategy, keys, args.workers):
                print(sweep.render(result))
            return 0
        rows = sweep.check(keys)
        print(f"{'universe':<18}{'strategy':<10}{'period':<15}{'engine CAGR':>12}{'fast CAGR':>10}"
              f"{'engine Sharpe':>14}{'fast Sharpe':>12}")
        for r in rows:
            print(f"{r['universe']:<18}{r['strategy']:<10}{r['period']:<15}{r['engine_cagr'] * 100:11.1f}%"
                  f"{r['fast_cagr'] * 100:9.1f}%{r['engine_sharpe']:14.2f}{r['fast_sharpe']:12.2f}")
        return 0
    store = PriceStore()
    try:
        if args.command == "backtest":
            from vibetrading import backtest  # imports the engines, which is slow
            for key in keys:
                result = backtest.run(key, args.strategy, args.start, args.end, store=store, tuned=args.tuned)
                print(backtest.render(result.summary))
                print(f"  saved to {result.run_dir}")
            return 0
        if args.command == "refresh":
            fetched: set[str] = set()
            for key in keys:
                refresh(store, UNIVERSES[key], fetched)
        for key in keys:
            print(coverage.render(coverage.report(store, UNIVERSES[key])))
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
