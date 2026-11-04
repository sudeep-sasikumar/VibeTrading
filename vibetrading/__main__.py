"""Command line.

    python -m vibetrading refresh  [universe ...]
    python -m vibetrading coverage [universe ...]
    python -m vibetrading backtest --strategy momentum [universe ...] [--start 2016-01-01] [--end ...]
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
                            ("backtest", "run a strategy and print its stats")]:
        cmd = commands[name] = sub.add_parser(name, help=help_text)
        cmd.add_argument("universes", nargs="*", metavar="universe",
                         help=f"any of {', '.join(UNIVERSES)} (default: all)")
    bt = commands["backtest"]
    bt.add_argument("--strategy", required=True, choices=list(STRATEGIES))
    bt.add_argument("--start", help="first evaluated date (default from config.yaml)")
    bt.add_argument("--end", help="last date (default: latest price)")
    args = parser.parse_args(argv)
    unknown = [u for u in args.universes if u not in UNIVERSES]
    if unknown:
        parser.error(f"unknown universe {', '.join(unknown)}; choose from {', '.join(UNIVERSES)}")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    # yfinance logs every ticker it can't find; the coverage report lists them instead.
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    keys = args.universes or list(UNIVERSES)
    store = PriceStore()
    try:
        if args.command == "backtest":
            from vibetrading import backtest  # imports the engines, which is slow
            for key in keys:
                result = backtest.run(key, args.strategy, args.start, args.end, store=store)
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
