"""A fast, approximate backtest for parameter sweeps.

The engine simulates fills bar by bar (S&P 500 momentum: ~4 minutes), too slow
to try dozens of settings. This evaluates the same strategy weights with array
maths in about a second:

- weights decided at a bar's close are held from the next bar's open, as in
  the engine, so each day earns ``weight x (next open / this open - 1)``;
- every change in weight pays ``cost_per_side`` of the traded amount;
- weights stay exactly on target between changes (the engine lets them drift
  until they are 25% off), and there are no lot sizes or cash rounding.

It is for ranking settings against each other, not for reporting results.
``check`` in the sweep compares it with the engine on the default settings.
"""

from __future__ import annotations

import pandas as pd

from vibetrading import metrics
from vibetrading.strategies import STRATEGIES

# The engine forward-fills a symbol's weight across up to 5 missing bars.
WEIGHT_FFILL_LIMIT = 5


def weights_panel(signal_map: dict[str, pd.Series]) -> pd.DataFrame:
    return pd.DataFrame(signal_map).sort_index().ffill(limit=WEIGHT_FFILL_LIMIT).fillna(0.0)


def net_returns(weights: pd.DataFrame, opens: pd.DataFrame, cost_per_side: float) -> tuple[pd.Series, pd.Series]:
    """Daily return after costs, and the traded fraction of equity, per day."""
    opens = opens.reindex(index=weights.index, columns=weights.columns).ffill(limit=WEIGHT_FFILL_LIMIT)
    open_to_open = (opens.shift(-1) / opens - 1).fillna(0.0)
    held = weights.shift(1).fillna(0.0)          # decided at yesterday's close, bought at today's open
    traded = held.diff().abs().sum(axis=1).fillna(held.abs().sum(axis=1))
    return (held * open_to_open).sum(axis=1) - traded * cost_per_side, traded


def equity_curve(weights: pd.DataFrame, opens: pd.DataFrame, cost_per_side: float,
                 start: str) -> tuple[pd.Series, float]:
    """Daily equity from ``start`` (1.0 = initial) and one-way turnover per year."""
    daily, traded = net_returns(weights, opens, cost_per_side)
    window = daily.index >= pd.Timestamp(start)
    daily, traded = daily[window], traded[window]
    equity = (1 + daily).cumprod()
    years = max((equity.index[-1] - equity.index[0]).days / 365.25, 1 / 365.25)
    return equity, float(traded.sum() / 2 / years)


def strategy_weights(prep, strategy_key: str, params: dict) -> pd.DataFrame:
    strategy = STRATEGIES[strategy_key](tradeable=prep.mask, regime=prep.regime, **params)
    return weights_panel(strategy.generate(prep.data_map))


def opens_panel(prep) -> pd.DataFrame:
    return pd.DataFrame({s: df["open"] for s, df in prep.data_map.items()})


def evaluate(prep, strategy_key: str, params: dict, cost_per_side: float, bars_per_year: int) -> dict:
    """In-sample, out-of-sample and full stats for one setting on prepared data."""
    weights = strategy_weights(prep, strategy_key, params)
    equity, turnover = equity_curve(weights, opens_panel(prep), cost_per_side, prep.start)
    split = pd.Timestamp(prep.split)
    out = {"turnover_per_year": turnover,
           "avg_invested": float(weights.loc[weights.index >= pd.Timestamp(prep.start)].sum(axis=1).mean())}
    for name, eq in {"full": equity, "in_sample": equity[equity.index < split],
                     "out_of_sample": equity[equity.index >= split]}.items():
        out[name] = metrics.equity_stats(eq, bars_per_year) if len(eq) > 1 else None
    return out
