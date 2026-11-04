"""Find one-day price breaks that aren't trading, and split the series there.

Raw exchange data has events no real holder experienced as a price move:
- Crypto redenominations (QUICK 1:1000, BNX, SUN...): price divides by N on one
  day while coin volume multiplies by roughly N.
- Ticker reuse: Binance's LUNA-USDT is the old LUNA until May 2022, then the
  new LUNA, 177,000x higher the next day.
- Unadjusted stock splits and relistings on Yahoo (GPIL, PATANJALI).

Holding through one books a fake 99% loss or a fake 100,000% gain. The fix
treats each break as the end of one instrument and the start of another: the
part before the break keeps the symbol (and, like a delisting, is sold the bar
before the break), the part after gets a new symbol. That is neutral for a
redenomination (holders were swapped, not wiped out) but would dodge a real
crash, which is why crypto drops count only when volume shows a swap.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

# Crypto: no top-200 coin has really risen 20x in a day; drops count only when
# coin volume also jumps 500x (LUNA's real crash was ~100x).
CRYPTO_UP, CRYPTO_DOWN, CRYPTO_SWAP_VOLUME = 20.0, 1 / 20, 500.0
# Stocks: Yahoo adjusts splits, so a 3x one-day move is a data problem.
STOCK_UP, STOCK_DOWN = 3.0, 1 / 4


@dataclass(frozen=True)
class Break:
    symbol: str
    date: pd.Timestamp
    ratio: float
    new_symbol: str


def find_breaks(symbol: str, bars: pd.DataFrame, crypto: bool) -> list[pd.Timestamp]:
    ratio = bars["close"] / bars["close"].shift(1)
    if crypto:
        swap = bars["volume"] / bars["volume"].shift(1) > CRYPTO_SWAP_VOLUME
        hit = (ratio > CRYPTO_UP) | ((ratio < CRYPTO_DOWN) & swap)
    else:
        hit = (ratio > STOCK_UP) | (ratio < STOCK_DOWN)
    return list(bars.index[hit.fillna(False)])


def split_at_breaks(data_map: dict[str, pd.DataFrame], crypto: bool) -> tuple[dict[str, pd.DataFrame], list[Break]]:
    """Return the data with each broken series cut into pieces, and the breaks.

    Pieces after a break are named ``<BASE>X<yyyymmdd>-USDT`` (crypto) or
    ``<TICKER>X<yyyymmdd>.<SUFFIX>`` (stocks), which the engine still reads as
    the same market.
    """
    out: dict[str, pd.DataFrame] = {}
    found: list[Break] = []
    for symbol, bars in data_map.items():
        cuts = find_breaks(symbol, bars, crypto)
        if not cuts:
            out[symbol] = bars
            continue
        pieces = [bars[bars.index < cuts[0]]]
        names = [symbol]
        for i, cut in enumerate(cuts):
            nxt = cuts[i + 1] if i + 1 < len(cuts) else None
            pieces.append(bars[(bars.index >= cut) & ((bars.index < nxt) if nxt is not None else True)])
            names.append(_renamed(symbol, cut))
            ratio = float(bars["close"].loc[cut] / bars["close"].shift(1).loc[cut])
            found.append(Break(symbol, cut, ratio, names[-1]))
        for name, piece in zip(names, pieces):
            if len(piece):
                out[name] = piece
    return out, found


def _renamed(symbol: str, day: pd.Timestamp) -> str:
    stem, sep, suffix = symbol.rpartition("-" if symbol.endswith("-USDT") else ".")
    return f"{stem}X{day:%Y%m%d}{sep}{suffix}"
