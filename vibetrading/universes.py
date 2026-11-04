"""The six universes: who belongs to each, and refreshing their prices.

Membership is point-in-time where a free source exists (S&P 500, NASDAQ-100),
ranked from exchange volume for crypto, and today's list only for the Nifty
indices. ``Universe.membership`` records which, so the portal can warn about it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx
import pandas as pd

from vibetrading import settings
from vibetrading.sources import binance, membership, yahoo
from vibetrading.store import PriceStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Universe:
    key: str
    title: str
    market: str            # Vibe-Trading market: us_equity, india_equity, crypto
    benchmark: str         # symbol in the price store
    membership: str        # point_in_time, current_only or volume_rank
    history_start: str
    backtest_start: str
    source_list: str = ""  # index-constitution index or NSE list name


_STOCKS = dict(history_start=settings.STOCK_HISTORY_START, backtest_start=settings.STOCK_BACKTEST_START)

UNIVERSES: dict[str, Universe] = {
    u.key: u
    for u in [
        Universe("crypto200", "Crypto Top 200", "crypto", "BTC-USDT", "volume_rank",
                 settings.CRYPTO_HISTORY_START, settings.CRYPTO_BACKTEST_START),
        Universe("sp500", "S&P 500", "us_equity", "SPY.US", "point_in_time", **_STOCKS, source_list="sp500"),
        Universe("nasdaq100", "NASDAQ-100", "us_equity", "QQQ.US", "point_in_time", **_STOCKS,
                 source_list="nasdaq100"),
        Universe("nifty50", "Nifty 50", "india_equity", "^NSEI", "current_only", **_STOCKS,
                 source_list="ind_nifty50list"),
        Universe("niftymidcap150", "Nifty Midcap 150", "india_equity", "NIFTYMIDCAP150.NS", "current_only",
                 **_STOCKS, source_list="ind_niftymidcap150list"),
        Universe("niftysmallcap250", "Nifty Smallcap 250", "india_equity", "NIFTYSMLCAP250.NS", "current_only",
                 **_STOCKS, source_list="ind_niftysmallcap250list"),
    ]
}

CRYPTO_TOP_N = 200
CRYPTO_VOLUME_WINDOW_DAYS = 30

# Benchmarks that are not themselves universe members: store symbol -> Yahoo ticker.
_YAHOO_BENCHMARKS = {"SPY.US": "SPY", "QQQ.US": "QQQ", "^NSEI": "^NSEI",
                     "NIFTYMIDCAP150.NS": "NIFTYMIDCAP150.NS", "NIFTYSMLCAP250.NS": "NIFTYSMLCAP250.NS"}


# ── membership ──

def us_members(history: pd.DataFrame, backtest_start: str) -> pd.DataFrame:
    """Members whose time in the index overlaps the backtest window."""
    active = history[history["end_date"].isna() | (history["end_date"] > pd.Timestamp(backtest_start))]
    yahoo_ticker = active["ticker"].str.replace(".", "-", regex=False)  # BRK.B is BRK-B on Yahoo
    return pd.DataFrame({
        "symbol": yahoo_ticker + ".US",
        "source_symbol": yahoo_ticker,
        "name": active["name"],
        "start_date": active["start_date"],
        "end_date": active["end_date"],
    }).reset_index(drop=True)


def nse_members(current: pd.DataFrame) -> pd.DataFrame:
    symbol = current["ticker"] + ".NS"
    return pd.DataFrame({"symbol": symbol, "source_symbol": symbol, "name": current["name"],
                         "start_date": pd.NaT, "end_date": pd.NaT})


def rank_by_volume(panel: pd.DataFrame, top_n: int, window_days: int, start: str,
                   today: pd.Timestamp | None = None) -> pd.DataFrame:
    """Monthly top-N by traded value over the previous ``window_days``.

    On the first of each month, every symbol that traded on at least two thirds
    of the window's days is ranked by its summed quote volume; the top N are
    members for that month. Only data from before the month is used, so the
    ranking never peeks ahead. Consecutive months are merged into one span; the
    current month's span is left open (null end).
    """
    if panel.empty:
        return pd.DataFrame(columns=["symbol", "start_date", "end_date"])
    today = (today or pd.Timestamp.today()).normalize()
    rows = []
    for month in pd.date_range(pd.Timestamp(start), panel.index.max(), freq="MS"):
        window = panel[(panel.index >= month - pd.Timedelta(window_days, unit="D")) & (panel.index < month)]
        if window.empty:
            continue
        days_traded = window.notna().sum()
        traded_value = window.sum(min_count=1)
        eligible = traded_value[(days_traded >= window_days * 2 // 3) & (traded_value > 0)]
        month_end = month + pd.offsets.MonthBegin(1)
        rows += [(symbol, month, month_end) for symbol in eligible.nlargest(top_n).index]
    spans = _merge_spans(pd.DataFrame(rows, columns=["symbol", "start_date", "end_date"]))
    spans.loc[spans["end_date"] > today, "end_date"] = pd.NaT
    return spans


def _merge_spans(df: pd.DataFrame) -> pd.DataFrame:
    merged = []
    for symbol, group in df.sort_values(["symbol", "start_date"]).groupby("symbol"):
        start, end = None, None
        for row in group.itertuples():
            if start is None:
                start, end = row.start_date, row.end_date
            elif row.start_date == end:
                end = row.end_date
            else:
                merged.append((symbol, start, end))
                start, end = row.start_date, row.end_date
        merged.append((symbol, start, end))
    return pd.DataFrame(merged, columns=["symbol", "start_date", "end_date"])


# ── refresh ──

def refresh(store: PriceStore, universe: Universe, already_fetched: set[str]) -> None:
    """Rebuild membership and fetch prices. ``already_fetched`` skips symbols a
    previous universe in the same run fetched (S&P 500 and NASDAQ-100 overlap)."""
    if universe.membership == "volume_rank":
        _refresh_crypto(store, universe)
        return
    if universe.market == "us_equity":
        members = us_members(membership.us_index_history(universe.source_list), universe.backtest_start)
    else:
        members = nse_members(membership.nse_current(universe.source_list))
    store.set_members(universe.key, members)
    logger.info("%s: %d membership rows, %d symbols", universe.key, len(members), members["symbol"].nunique())

    to_fetch = dict(zip(members["source_symbol"], members["symbol"]))
    to_fetch[_YAHOO_BENCHMARKS[universe.benchmark]] = universe.benchmark
    to_fetch = {src: sym for src, sym in to_fetch.items() if sym not in already_fetched}
    _fetch_yahoo(store, to_fetch, universe.history_start, universe.key)
    already_fetched.update(to_fetch.values())


def _fetch_yahoo(store: PriceStore, tickers: dict[str, str], start: str, label: str) -> None:
    done = failed = 0
    for ticker, frame, error in yahoo.download(sorted(tickers), start):
        symbol = tickers[ticker]
        if frame is not None:
            store.write_prices(symbol, frame)
        else:
            failed += 1
        store.log_fetch(symbol, "yahoo", frame, error)
        done += 1
        if done % 100 == 0 or done == len(tickers):
            logger.info("%s: fetched %d/%d (%d without data)", label, done, len(tickers), failed)


def _refresh_crypto(store: PriceStore, universe: Universe) -> None:
    with httpx.Client(timeout=30) as client:
        pairs = binance.usdt_pairs(client)
    logger.info("%s: %d candidate USDT pairs (live and delisted)", universe.key, len(pairs))

    # Raw candles don't change after the fact, so refetch only the last few days.
    last = store.last_dates()
    starts = {}
    for pair in pairs:
        seen = last.get(binance.engine_symbol(pair["base"]))
        if seen is not None:
            starts[pair["symbol"]] = (seen - pd.Timedelta(3, unit="D")).strftime("%Y-%m-%d")

    done = failed = 0
    for symbol, frame, error in binance.download(pairs, universe.history_start, starts):
        has_rows = frame is not None and not frame.empty
        if has_rows:
            store.write_prices(symbol, frame)
        elif symbol not in last:
            error = error or "no data"
        # A delisted pair returns nothing new on an incremental fetch; that's not a failure.
        if error:
            failed += 1
        store.log_fetch(symbol, "binance", frame, error)
        done += 1
        if done % 100 == 0 or done == len(pairs):
            logger.info("%s: fetched %d/%d (%d without data)", universe.key, done, len(pairs), failed)

    symbols = [binance.engine_symbol(p["base"]) for p in pairs]
    by_symbol = {binance.engine_symbol(p["base"]): p["symbol"] for p in pairs}
    spans = rank_by_volume(store.quote_volume_panel(symbols), CRYPTO_TOP_N,
                           CRYPTO_VOLUME_WINDOW_DAYS, universe.backtest_start)
    spans["source_symbol"] = spans["symbol"].map(by_symbol)
    spans["name"] = spans["symbol"].str.removesuffix("-USDT")
    store.set_members(universe.key, spans)
    logger.info("%s: %d coins were in the top %d at some point", universe.key, spans["symbol"].nunique(), CRYPTO_TOP_N)
