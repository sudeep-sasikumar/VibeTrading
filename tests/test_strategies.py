import numpy as np
import pandas as pd

from vibetrading.backtest import tradeable_mask
from vibetrading.strategies import Momentum, Pullback, rebalance_days


def _bars(close: list[float], start="2024-01-01") -> pd.DataFrame:
    idx = pd.date_range(start, periods=len(close), freq="D")
    c = pd.Series(close, index=idx, dtype=float)
    return pd.DataFrame({"open": c, "high": c, "low": c, "close": c, "volume": 1.0})


def _all_true(data_map):
    dates = sorted(set().union(*(df.index for df in data_map.values())))
    return pd.DataFrame(True, index=pd.DatetimeIndex(dates), columns=list(data_map))


def test_tradeable_mask_respects_membership_and_early_ends():
    data_map = {"A": _bars([1.0] * 10), "B": _bars([1.0] * 10)}
    members = pd.DataFrame({
        "symbol": ["A", "B"],
        "start_date": pd.to_datetime(["2024-01-03", None]),
        "end_date": pd.to_datetime(["2024-01-06", None]),
    })
    mask = tradeable_mask(members, data_map, ends_early={"B"})
    # A: member 3rd up to (not including) the 6th.
    assert list(mask.index[mask["A"]].day) == [3, 4, 5]
    # B's prices end on the 10th: last tradeable bar is the 8th, so its weight is
    # zero at the 9th's close and the engine sells at the 10th's open.
    assert mask["B"].iloc[-3] and not mask["B"].iloc[-2] and not mask["B"].iloc[-1]


def test_rebalance_days_marks_last_bar_of_each_week():
    idx = pd.date_range("2024-01-01", "2024-01-14", freq="D")  # Mon 1st .. Sun 14th
    days = rebalance_days(idx, "weekly")
    assert list(idx[days.values].day) == [7, 14]


def test_momentum_holds_top_n_uptrends_at_equal_weight():
    n = 40
    data_map = {
        "UP_FAST": _bars(list(np.linspace(10, 30, n))),
        "UP_SLOW": _bars(list(np.linspace(10, 12, n))),
        "DOWN": _bars(list(np.linspace(30, 10, n))),
    }
    strat = Momentum(tradeable=_all_true(data_map), lookback=5, trend_ma=10, top_n=2)
    weights = strat.generate(data_map)
    last = {s: w.iloc[-1] for s, w in weights.items()}
    assert last == {"UP_FAST": 0.5, "UP_SLOW": 0.5, "DOWN": 0.0}


def test_momentum_drops_a_symbol_the_day_it_stops_being_tradeable():
    n = 40
    data_map = {"A": _bars(list(np.linspace(10, 30, n)))}
    mask = _all_true(data_map)
    mask.iloc[-5:, 0] = False
    weights = Momentum(tradeable=mask, lookback=5, trend_ma=10, top_n=1).generate(data_map)["A"]
    assert weights.iloc[-6] == 1.0 and (weights.iloc[-5:] == 0).all()


def test_pullback_enters_on_dip_and_exits_on_bounce():
    # Long uptrend, a sharp three-day dip, then a recovery.
    close = list(np.linspace(50, 100, 60)) + [92, 85, 80] + [90, 100, 105]
    data_map = {"A": _bars(close)}
    strat = Pullback(tradeable=_all_true(data_map), trend_ma=50, rsi_period=2,
                     entry_rsi=10, exit_rsi=70, max_hold_bars=10, stop_loss=0.5, max_positions=2)
    w = strat.generate(data_map)["A"]
    held = w[w > 0]
    assert len(held) > 0 and (held == 0.5).all()
    assert held.index[0] >= w.index[60]          # entered during the dip
    assert w.iloc[-1] == 0                       # sold on the bounce


def test_pullback_stop_loss_and_slot_limit():
    dip = list(np.linspace(50, 100, 60)) + [90, 80, 70, 60, 50]
    data_map = {s: _bars(dip) for s in ("A", "B", "C")}
    strat = Pullback(tradeable=_all_true(data_map), trend_ma=50, rsi_period=2, entry_rsi=10,
                     exit_rsi=70, max_hold_bars=10, stop_loss=0.15, max_positions=2)
    weights = pd.DataFrame(strat.generate(data_map))
    assert weights.iloc[-5].gt(0).sum() == 2        # bought the first dip, two slots filled
    assert (weights.gt(0).sum(axis=1) <= 2).all()   # never more than 2 slots
    assert (weights.iloc[-1] == 0).all()            # the slide hit the 15% stop
