"""Swing strategies, written as Vibe-Trading ``SignalEngine`` classes.

``generate`` returns, per symbol, a target portfolio weight for each bar. The
engine reads a weight decided at a bar's close and trades it at the next bar's
open, and scales weights down only if they add up to more than 1, so a
strategy that fills 6 of its 10 slots holds 40% cash.

Every strategy gets a ``tradeable`` mask (dates x symbols): a symbol may only be
held on days it was a universe member and had a price. Where the mask turns
False the weight drops to 0 that same bar, so the position is sold at the next
open: when a stock leaves the index, a coin drops out of the top 200, or the
day before a symbol's prices end.

Optional filters, all off by default:
- ``regime_filter``: buy only while the market (``regime``, True when the
  universe's index or BTC closes above its 200-day average) is in an uptrend.
- ``liquid_top``: buy only the N most traded tradeable symbols by 20-day
  average traded value. Holding isn't affected, so a position isn't sold just
  because it slipped down the liquidity ranking.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


def close_panel(data_map: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Dates x symbols of closes on the union of all calendars (no filling)."""
    return pd.DataFrame({s: df["close"] for s, df in data_map.items()}).sort_index()


def rsi(close: pd.DataFrame, period: int) -> pd.DataFrame:
    """Wilder RSI, column by column."""
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, min_periods=period).mean()
    loss = (-delta).clip(lower=0).ewm(alpha=1 / period, min_periods=period).mean()
    return 100 - 100 / (1 + gain / loss)


def rebalance_days(index: pd.DatetimeIndex, every: str) -> pd.Series:
    """True on the last bar of each week (``weekly``) or month (``monthly``)."""
    period = {"weekly": "W", "monthly": "M"}[every]
    groups = index.to_period(period)
    last = pd.Series(index, index=index).groupby(groups).transform("max")
    return pd.Series(index == last.values, index=index)


def entry_filter(close: pd.DataFrame, data_map: dict[str, pd.DataFrame], mask: pd.DataFrame,
                 liquid_top: int | None) -> pd.DataFrame:
    """``mask`` narrowed to the ``liquid_top`` most traded symbols each day."""
    if not liquid_top:
        return mask
    traded = pd.DataFrame({s: df["close"] * df["volume"] for s, df in data_map.items()})
    traded = traded.reindex(index=close.index, columns=close.columns).rolling(20, min_periods=10).mean()
    rank = traded.where(mask).rank(axis=1, ascending=False, method="first")
    return mask & (rank <= liquid_top)


def risk_on(regime: pd.Series | None, index: pd.DatetimeIndex) -> pd.Series:
    """The market-trend flag on ``index``; always True without a regime series."""
    if regime is None:
        return pd.Series(True, index=index)
    return regime.astype(float).reindex(index, method="ffill").fillna(0.0).astype(bool)


def _to_signal_map(weights: pd.DataFrame, data_map: dict[str, pd.DataFrame]) -> dict[str, pd.Series]:
    """Back onto each symbol's own calendar, which is what the engine reads."""
    return {s: weights[s].reindex(df.index).fillna(0.0) for s, df in data_map.items()}


@dataclass
class Momentum:
    """Weekly rotation into the strongest uptrends.

    On each rebalance bar, rank tradeable symbols that close above their
    ``trend_ma``-day average by their ``lookback``-bar return, and hold the top
    ``top_n`` with a positive return at ``1 / top_n`` each. With the regime
    filter on, the whole book goes to cash on any day the market trend is down.
    """

    tradeable: pd.DataFrame
    regime: pd.Series | None = None
    lookback: int = 126
    trend_ma: int = 200
    top_n: int = 10
    rebalance: str = "weekly"
    regime_filter: bool = False
    liquid_top: int | None = None

    def generate(self, data_map: dict[str, pd.DataFrame]) -> dict[str, pd.Series]:
        close = close_panel(data_map)
        mask = self.tradeable.reindex(index=close.index, columns=close.columns, fill_value=False)
        score = close / close.shift(self.lookback) - 1
        uptrend = close > close.rolling(self.trend_ma, min_periods=self.trend_ma).mean()
        eligible = entry_filter(close, data_map, mask, self.liquid_top) & uptrend & (score > 0)

        is_rebalance = rebalance_days(close.index, self.rebalance)
        weights = pd.DataFrame(np.nan, index=close.index, columns=close.columns)
        for day in close.index[is_rebalance.values]:
            picks = score.loc[day][eligible.loc[day]].nlargest(self.top_n).index
            weights.loc[day] = 0.0
            weights.loc[day, picks] = 1.0 / self.top_n
        weights = weights.ffill().fillna(0.0).where(mask, 0.0)
        if self.regime_filter:
            weights = weights.mul(risk_on(self.regime, close.index), axis=0)
        return _to_signal_map(weights, data_map)


@dataclass
class Pullback:
    """Buy short, sharp dips in long uptrends; sell the bounce.

    Enter when a tradeable symbol is above its ``trend_ma``-day average and its
    ``rsi_period`` RSI closes below ``entry_rsi``. Exit on the bounce (RSI
    closes above ``exit_rsi``, or with ``exit_rule: ma`` the close crosses above
    its ``exit_ma``-day average), after ``max_hold_bars`` bars, on a close
    ``stop_loss`` below the entry close (``None`` turns the stop off), or when
    the symbol stops being tradeable. The regime filter blocks new entries in a
    market downtrend; open positions still exit by their own rules. Up to
    ``max_positions`` at ``1 / max_positions`` each; when more symbols qualify
    than there are free slots, the strongest ``rank_lookback``-bar return wins.
    """

    tradeable: pd.DataFrame
    regime: pd.Series | None = None
    trend_ma: int = 200
    rsi_period: int = 2
    entry_rsi: float = 10.0
    exit_rsi: float = 70.0
    exit_rule: str = "rsi"
    exit_ma: int = 5
    max_hold_bars: int = 10
    stop_loss: float | None = 0.08
    max_positions: int = 10
    rank_lookback: int = 126
    regime_filter: bool = False
    liquid_top: int | None = None

    def generate(self, data_map: dict[str, pd.DataFrame]) -> dict[str, pd.Series]:
        close = close_panel(data_map)
        mask_df = self.tradeable.reindex(index=close.index, columns=close.columns, fill_value=False)
        mask = mask_df.to_numpy()
        can_enter = entry_filter(close, data_map, mask_df, self.liquid_top).to_numpy()
        market_up = (risk_on(self.regime, close.index) if self.regime_filter
                     else pd.Series(True, index=close.index)).to_numpy()
        px = close.to_numpy()
        trend = (close > close.rolling(self.trend_ma, min_periods=self.trend_ma).mean()).to_numpy()
        r = rsi(close, self.rsi_period).to_numpy()
        strength = (close / close.shift(self.rank_lookback) - 1).to_numpy()
        if self.exit_rule == "ma":
            bounced = (close > close.rolling(self.exit_ma, min_periods=self.exit_ma).mean()).to_numpy()
        elif self.exit_rule == "rsi":
            bounced = r > self.exit_rsi
        else:
            raise ValueError(f"exit_rule must be 'rsi' or 'ma', not {self.exit_rule!r}")
        stop = self.stop_loss if self.stop_loss is not None else np.inf

        n_days, n_syms = px.shape
        held = np.zeros(n_syms, dtype=bool)
        entry_px = np.full(n_syms, np.nan)
        bars_held = np.zeros(n_syms, dtype=int)
        out = np.zeros((n_days, n_syms))
        weight = 1.0 / self.max_positions

        for t in range(n_days):
            priced = ~np.isnan(px[t])
            bars_held[held & priced] += 1
            # A symbol with no bar today keeps its position untouched.
            exit_now = held & priced & (
                bounced[t]
                | (bars_held >= self.max_hold_bars)
                | (px[t] < entry_px * (1 - stop))
            )
            exit_now |= held & ~mask[t]
            held &= ~exit_now
            entry_px[exit_now] = np.nan
            bars_held[exit_now] = 0

            free = self.max_positions - int(held.sum())
            if free > 0 and market_up[t]:
                candidates = can_enter[t] & priced & ~held & trend[t] & (r[t] < self.entry_rsi)
                idx = np.flatnonzero(candidates)
                if idx.size:
                    order = idx[np.argsort(-np.nan_to_num(strength[t, idx], nan=-np.inf))]
                    chosen = order[:free]
                    held[chosen] = True
                    entry_px[chosen] = px[t, chosen]
                    bars_held[chosen] = 0
            out[t, held] = weight

        weights = pd.DataFrame(out, index=close.index, columns=close.columns)
        return _to_signal_map(weights, data_map)


STRATEGIES = {"momentum": Momentum, "pullback": Pullback}
