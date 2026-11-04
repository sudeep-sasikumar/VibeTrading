"""How much of each universe actually has prices, year by year.

The headline number is point-in-time: on 1 January of each backtest year, how
many of that day's members had a price that day. A member without one can't be
traded in a backtest, which biases the result toward survivors.
"""

from __future__ import annotations

import json

import pandas as pd

from vibetrading.settings import COVERAGE_DIR, MIN_PRICE_ROWS
from vibetrading.store import PriceStore
from vibetrading.universes import Universe

# A gap this long between where prices should start or end and where they do
# counts as partial history rather than rounding.
SLACK = pd.Timedelta(30, unit="D")


def members_on(members: pd.DataFrame, day: pd.Timestamp) -> pd.Index:
    """Symbols that were members on ``day`` (half-open ``[start, end)`` spans)."""
    start_ok = members["start_date"].isna() | (members["start_date"] <= day)
    end_ok = members["end_date"].isna() | (members["end_date"] > day)
    return pd.Index(members.loc[start_ok & end_ok, "symbol"].unique())


def report(store: PriceStore, universe: Universe, today: pd.Timestamp | None = None) -> dict:
    today = (today or pd.Timestamp.today()).normalize()
    members = store.members(universe.key)
    for col in ("start_date", "end_date"):
        members[col] = pd.to_datetime(members[col])
    symbols = sorted(members["symbol"].unique())
    spans = store.price_spans(symbols + [universe.benchmark])
    for col in ("first_date", "last_date"):
        spans[col] = pd.to_datetime(spans[col])
    errors = store.fetch_errors(symbols)
    # Yahoo keeps a one-day stub for some recently delisted companies; that's no history.
    stubs = spans.index[spans["rows"] < MIN_PRICE_ROWS]
    errors.update({s: f"only {spans.at[s, 'rows']} rows (stub)" for s in stubs})
    spans = spans.drop(stubs)

    window_start = pd.Timestamp(universe.backtest_start)
    by_symbol = members.groupby("symbol").agg(start=("start_date", "min"), end=("end_date", "max"),
                                              still_member=("end_date", lambda s: s.isna().any()))
    missing, after_exit, partial = [], [], []
    for symbol, row in by_symbol.iterrows():
        if symbol not in spans.index:
            missing.append({"symbol": symbol, "error": errors.get(symbol, "no data")})
            continue
        first, last = spans.at[symbol, "first_date"], spans.at[symbol, "last_date"]
        expected_start = max(row.start, window_start) if pd.notna(row.start) else window_start
        expected_end = today if row.still_member else row.end
        if not row.still_member and first >= row.end:
            # Prices begin after the company left the index: the ticker most
            # likely belongs to a different company now.
            after_exit.append({"symbol": symbol, "left": row.end.date().isoformat(),
                               "prices_from": first.date().isoformat()})
        elif first > expected_start + SLACK or last < expected_end - SLACK:
            partial.append({"symbol": symbol, "prices": f"{first.date()} to {last.date()}"})

    by_year = []
    for year in range(window_start.year, today.year + 1):
        day = pd.Timestamp(year=year, month=1, day=1)
        if day < window_start:
            day = window_start
        on_day = members_on(members, day)
        priced = [s for s in on_day if s in spans.index
                  and spans.at[s, "first_date"] <= day + pd.Timedelta(7, unit="D")
                  and spans.at[s, "last_date"] >= day]
        by_year.append({"date": day.date().isoformat(), "members": len(on_day), "priced": len(priced)})

    bench = spans.loc[universe.benchmark] if universe.benchmark in spans.index else None
    result = {
        "universe": universe.key,
        "title": universe.title,
        "membership": universe.membership,
        "symbols": len(symbols),
        "with_prices": len(symbols) - len(missing),
        "missing": missing,
        "prices_after_exit": after_exit,
        "partial_history": partial,
        "members_priced_by_year": by_year,
        "benchmark": {
            "symbol": universe.benchmark,
            "from": bench["first_date"].date().isoformat() if bench is not None else None,
            "to": bench["last_date"].date().isoformat() if bench is not None else None,
        },
    }
    COVERAGE_DIR.mkdir(parents=True, exist_ok=True)
    (COVERAGE_DIR / f"{universe.key}.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def render(result: dict) -> str:
    years = "  ".join(f"{y['date'][:4]}:{y['priced']}/{y['members']}" for y in result["members_priced_by_year"])
    bench = result["benchmark"]
    lines = [
        f"{result['title']} ({result['membership']})",
        f"  symbols {result['symbols']}, with prices {result['with_prices']}, missing {len(result['missing'])}, "
        f"prices only after exit {len(result['prices_after_exit'])}, partial {len(result['partial_history'])}",
        f"  members priced on 1 Jan: {years}",
        f"  benchmark {bench['symbol']}: {bench['from']} to {bench['to']}",
    ]
    return "\n".join(lines)
