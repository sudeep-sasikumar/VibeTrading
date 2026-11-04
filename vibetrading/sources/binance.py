"""Daily USDT-pair candles from Binance's public market-data API.

``data-api.binance.vision`` serves market data only (no account endpoints) and
needs no key. Binance keeps delisted pairs in ``exchangeInfo`` with status
``BREAK`` and still serves their old candles, which is what lets the crypto
universe include coins that later died.
"""

from __future__ import annotations

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pandas as pd

logger = logging.getLogger(__name__)

BASE_URL = "https://data-api.binance.vision/api/v3"
DAY_MS = 86_400_000
WORKERS = 6

# Base assets that are dollars, other fiat, gold or a wrapped copy of another
# coin. Ranking them by volume would fill the "top 200" with things that are
# not separate crypto bets.
EXCLUDED_BASES = {
    "USDC", "BUSD", "TUSD", "USDP", "PAX", "DAI", "FDUSD", "USDS", "USDSB", "SUSD",
    "UST", "USTC", "BFUSD", "USD1", "XUSD", "USDE", "RLUSD", "AEUR", "EUR", "EURI",
    "GBP", "AUD", "TRY", "BRL", "RUB", "UAH", "NGN", "ZAR", "IDRT", "BIDR", "BKRW",
    "PAXG", "XAUT", "WBTC", "WBETH", "BETH",
}
_LEVERAGED = re.compile(r"^(?P<base>.+?)(UP|DOWN|BULL|BEAR)$")


def engine_symbol(base: str) -> str:
    return f"{base}-USDT"


def usdt_pairs(client: httpx.Client) -> list[dict]:
    """Spot USDT pairs, live and delisted, minus stablecoins and leveraged tokens."""
    info = client.get(f"{BASE_URL}/exchangeInfo", params={"permissions": "SPOT"}).raise_for_status().json()
    pairs = [s for s in info["symbols"] if s["quoteAsset"] == "USDT" and s["status"] in {"TRADING", "BREAK"}]
    bases = {s["baseAsset"] for s in pairs}
    return [
        {"symbol": s["symbol"], "base": s["baseAsset"], "status": s["status"]}
        for s in pairs
        if keep_base(s["baseAsset"], bases)
    ]


def keep_base(base: str, all_bases: set[str]) -> bool:
    if base in EXCLUDED_BASES:
        return False
    # BTCUP, ETHBEAR...: only a leveraged token when the stem is itself a listed
    # coin, so JUP or SUPER are kept.
    match = _LEVERAGED.match(base)
    return not (match and match["base"] in all_bases)


def klines(client: httpx.Client, pair: str, start: str) -> pd.DataFrame:
    """All daily candles for ``pair`` from ``start`` to today."""
    cursor = int(pd.Timestamp(start, tz="UTC").timestamp() * 1000)
    now = int(time.time() * 1000)
    rows: list[list] = []
    while cursor < now:
        resp = _get(client, f"{BASE_URL}/klines",
                    {"symbol": pair, "interval": "1d", "startTime": cursor, "limit": 1000})
        page = resp.json()
        if not page:
            break
        rows.extend(page)
        if len(page) < 1000:
            break
        cursor = page[-1][0] + DAY_MS
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows).iloc[:, [0, 1, 2, 3, 4, 5, 7]]
    df.columns = ["open_time", "open", "high", "low", "close", "volume", "quote_volume"]
    df.index = pd.to_datetime(df.pop("open_time"), unit="ms")
    df = df.astype(float)
    # Drop today's unfinished candle.
    return df[df.index < pd.Timestamp.now(tz="UTC").tz_localize(None).normalize()]


def download(pairs: list[dict], start: str, starts: dict[str, str] | None = None):
    """Yield ``(engine_symbol, bars, error)`` per pair, fetching in parallel.

    ``starts`` maps a pair to a later start date, for incremental refreshes.
    """
    starts = starts or {}
    with httpx.Client(timeout=30) as client, ThreadPoolExecutor(WORKERS) as pool:
        def one(pair: dict):
            try:
                return engine_symbol(pair["base"]), klines(client, pair["symbol"], starts.get(pair["symbol"], start)), None
            except Exception as exc:
                return engine_symbol(pair["base"]), None, str(exc)

        yield from pool.map(one, pairs)


def _get(client: httpx.Client, url: str, params: dict, attempts: int = 5) -> httpx.Response:
    for attempt in range(attempts):
        resp = client.get(url, params=params)
        if resp.status_code in (418, 429):  # rate limited: Binance says how long to wait
            time.sleep(int(resp.headers.get("Retry-After", 2 ** attempt)))
            continue
        return resp.raise_for_status()
    return resp.raise_for_status()
