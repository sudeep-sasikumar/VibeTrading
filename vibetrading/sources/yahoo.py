"""Daily bars from Yahoo Finance, adjusted for splits and dividends.

Same adjustment as Vibe-Trading's own yfinance loader (``auto_adjust=True``), so
cached prices and the engine's own fetches agree.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

# Yahoo starts refusing large bursts; batches of this size with a pause between
# them have been reliable.
BATCH_SIZE = 80
PAUSE_SECONDS = 2.0


def download(tickers: list[str], start: str) -> Iterator[tuple[str, pd.DataFrame | None, str | None]]:
    """Yield ``(ticker, bars, error)`` for every ticker, in batches."""
    for i in range(0, len(tickers), BATCH_SIZE):
        batch = tickers[i : i + BATCH_SIZE]
        try:
            data = yf.download(
                batch, start=start, auto_adjust=True, group_by="ticker",
                threads=True, progress=False,
            )
        except Exception as exc:  # network or Yahoo-side failure for the whole batch
            for ticker in batch:
                yield ticker, None, f"batch failed: {exc}"
            continue
        for ticker in batch:
            yield ticker, *_extract(data, ticker)
        if i + BATCH_SIZE < len(tickers):
            time.sleep(PAUSE_SECONDS)


def _extract(data: pd.DataFrame, ticker: str) -> tuple[pd.DataFrame | None, str | None]:
    if isinstance(data.columns, pd.MultiIndex):
        if ticker not in data.columns.get_level_values(0):
            return None, "no data"
        frame = data[ticker]
    else:
        frame = data
    frame = frame.rename(columns=str.lower).dropna(subset=["close"])
    if frame.empty:
        return None, "no data"
    frame = frame[["open", "high", "low", "close", "volume"]].astype(float)
    frame.index = pd.to_datetime(frame.index).tz_localize(None)
    return frame, None
