"""DuckDB store for daily prices and universe membership.

Symbols are stored as the Vibe-Trading engines expect them: ``AAPL.US``,
``RELIANCE.NS``, ``BTC-USDT``. Benchmarks that are indices keep their Yahoo
symbol (``^NSEI``).

Membership rows are half-open intervals ``[start_date, end_date)``: a stock
removed from the index on ``end_date`` is not a member that day. A null
``start_date`` means "member since before our data starts" and a null
``end_date`` means "still a member".
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pandas as pd

from vibetrading.settings import DB_PATH

PRICE_COLUMNS = ["open", "high", "low", "close", "volume", "quote_volume"]
MEMBER_COLUMNS = ["symbol", "source_symbol", "name", "start_date", "end_date"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS prices (
    symbol VARCHAR NOT NULL,
    date DATE NOT NULL,
    open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
    volume DOUBLE,
    quote_volume DOUBLE,          -- traded value in the quote currency; crypto only
    PRIMARY KEY (symbol, date)
);
CREATE TABLE IF NOT EXISTS members (
    universe VARCHAR NOT NULL,
    symbol VARCHAR NOT NULL,
    source_symbol VARCHAR,        -- the symbol the data source knows it by
    name VARCHAR,
    start_date DATE,
    end_date DATE
);
-- One row per symbol describing its most recent fetch (an incremental fetch
-- covers only the last few days, so use prices for the full span).
CREATE TABLE IF NOT EXISTS fetch_log (
    symbol VARCHAR PRIMARY KEY,
    source VARCHAR,
    fetched_at TIMESTAMP,
    rows INTEGER,
    first_date DATE,
    last_date DATE,
    error VARCHAR
);
"""


class PriceStore:
    def __init__(self, path: Path = DB_PATH, read_only: bool = False):
        """``read_only`` lets several processes read at once (parallel sweeps)."""
        if read_only:
            # Each sweep worker opens its own connection; by default DuckDB lets
            # every one of them claim most of the machine's memory.
            self.con = duckdb.connect(str(path), read_only=True,
                                      config={"memory_limit": "512MB", "threads": 2})
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        self.con = duckdb.connect(str(path))
        self.con.execute(_SCHEMA)

    def close(self) -> None:
        self.con.close()

    # ── prices ──

    def write_prices(self, symbol: str, frame: pd.DataFrame) -> None:
        """Replace this symbol's rows from the frame's first date onward.

        A full refetch therefore replaces the whole history (Yahoo's adjusted
        prices change when a dividend is paid), and an incremental fetch
        replaces only the overlap.
        """
        if frame.empty:
            return
        df = frame.reindex(columns=PRICE_COLUMNS).copy()
        df.index = pd.to_datetime(df.index).normalize()
        df = df[~df.index.duplicated(keep="last")].sort_index()
        df.insert(0, "date", df.index.date)
        df.insert(0, "symbol", symbol)
        self.con.execute("BEGIN")
        self.con.execute("DELETE FROM prices WHERE symbol = ? AND date >= ?", [symbol, df["date"].iloc[0]])
        self.con.register("incoming", df.reset_index(drop=True))
        self.con.execute(f"INSERT INTO prices SELECT symbol, date, {', '.join(PRICE_COLUMNS)} FROM incoming")
        self.con.unregister("incoming")
        self.con.execute("COMMIT")

    def prices(self, symbols: list[str], start: str | None = None, end: str | None = None) -> dict[str, pd.DataFrame]:
        """Daily bars per symbol, in the shape Vibe-Trading loaders return."""
        out = {}
        # In chunks: one query for 750 symbols briefly holds several copies of
        # the whole result, which a machine short of memory can't spare.
        for i in range(0, len(symbols), 100):
            df = self.con.execute(
                f"""SELECT symbol, date, {', '.join(PRICE_COLUMNS)} FROM prices
                    WHERE symbol IN (SELECT unnest(?)) AND date >= COALESCE(?::DATE, DATE '1900-01-01')
                      AND date <= COALESCE(?::DATE, DATE '2999-12-31')
                    ORDER BY symbol, date""",
                [symbols[i : i + 100], start, end],
            ).df()
            for symbol, group in df.groupby("symbol", sort=False):
                frame = group.drop(columns="symbol").set_index("date")
                frame.index = pd.to_datetime(frame.index)
                frame.index.name = "trade_date"
                out[symbol] = frame
        return out

    def last_dates(self) -> dict[str, pd.Timestamp]:
        rows = self.con.execute("SELECT symbol, max(date) FROM prices GROUP BY symbol").fetchall()
        return {s: pd.Timestamp(d) for s, d in rows}

    def price_spans(self, symbols: list[str]) -> pd.DataFrame:
        """First date, last date and row count per symbol that has prices."""
        return self.con.execute(
            """SELECT symbol, min(date) AS first_date, max(date) AS last_date, count(*) AS rows
               FROM prices WHERE symbol IN (SELECT unnest(?)) GROUP BY symbol""",
            [symbols],
        ).df().set_index("symbol")

    def quote_volume_panel(self, symbols: list[str]) -> pd.DataFrame:
        """Dates x symbols of daily traded value, for ranking crypto by volume."""
        df = self.con.execute(
            "SELECT date, symbol, quote_volume FROM prices WHERE symbol IN (SELECT unnest(?))",
            [symbols],
        ).df()
        if df.empty:
            return pd.DataFrame()
        panel = df.pivot(index="date", columns="symbol", values="quote_volume")
        panel.index = pd.to_datetime(panel.index)
        return panel.sort_index()

    def log_fetch(self, symbol: str, source: str, frame: pd.DataFrame | None, error: str | None = None) -> None:
        has = frame is not None and not frame.empty
        self.con.execute(
            "INSERT OR REPLACE INTO fetch_log VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                symbol, source, datetime.now(timezone.utc).replace(tzinfo=None),
                len(frame) if has else 0,
                frame.index.min().date() if has else None,
                frame.index.max().date() if has else None,
                error,
            ],
        )

    def fetch_errors(self, symbols: list[str]) -> dict[str, str]:
        rows = self.con.execute(
            "SELECT symbol, error FROM fetch_log WHERE error IS NOT NULL AND symbol IN (SELECT unnest(?))",
            [symbols],
        ).fetchall()
        return dict(rows)

    # ── membership ──

    def set_members(self, universe: str, members: pd.DataFrame) -> None:
        df = members.reindex(columns=MEMBER_COLUMNS).copy()
        df.insert(0, "universe", universe)
        self.con.execute("BEGIN")
        self.con.execute("DELETE FROM members WHERE universe = ?", [universe])
        self.con.register("incoming", df)
        self.con.execute(
            "INSERT INTO members SELECT universe, symbol, source_symbol, name, "
            "CAST(start_date AS DATE), CAST(end_date AS DATE) FROM incoming"
        )
        self.con.unregister("incoming")
        self.con.execute("COMMIT")

    def members(self, universe: str) -> pd.DataFrame:
        return self.con.execute(
            f"SELECT {', '.join(MEMBER_COLUMNS)} FROM members WHERE universe = ? ORDER BY symbol, start_date",
            [universe],
        ).df()
