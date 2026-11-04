"""Command line: ``python -m vibetrading refresh|coverage [universe ...]``."""

from __future__ import annotations

import argparse
import logging
import sys

from vibetrading import coverage
from vibetrading.store import PriceStore
from vibetrading.universes import UNIVERSES, refresh


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vibetrading")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, help_text in [("refresh", "rebuild membership and fetch prices"),
                            ("coverage", "report how much of each universe has prices")]:
        cmd = sub.add_parser(name, help=help_text)
        cmd.add_argument("universes", nargs="*", metavar="universe",
                         help=f"any of {', '.join(UNIVERSES)} (default: all)")
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
