import numpy as np
import pandas as pd
import pytest

from vibetrading.fasteval import equity_curve
from vibetrading.strategies import Momentum, Pullback, entry_filter, risk_on
from vibetrading.sweep import choose


def _bars(close, volume=1.0, start="2024-01-01"):
    idx = pd.date_range(start, periods=len(close), freq="D")
    c = pd.Series(close, index=idx, dtype=float)
    return pd.DataFrame({"open": c, "high": c, "low": c, "close": c, "volume": volume})


def _all_true(data_map):
    dates = sorted(set().union(*(df.index for df in data_map.values())))
    return pd.DataFrame(True, index=pd.DatetimeIndex(dates), columns=list(data_map))


def test_momentum_goes_to_cash_while_the_market_is_down():
    data_map = {"A": _bars(np.linspace(10, 30, 40))}
    idx = data_map["A"].index
    regime = pd.Series(True, index=idx)
    regime.iloc[-3:] = False
    w = Momentum(tradeable=_all_true(data_map), regime=regime, lookback=5, trend_ma=10,
                 top_n=1, regime_filter=True).generate(data_map)["A"]
    assert w.iloc[-4] == 1.0 and (w.iloc[-3:] == 0).all()


def test_regime_is_ignored_unless_the_filter_is_on():
    data_map = {"A": _bars(np.linspace(10, 30, 40))}
    regime = pd.Series(False, index=data_map["A"].index)
    w = Momentum(tradeable=_all_true(data_map), regime=regime, lookback=5, trend_ma=10,
                 top_n=1).generate(data_map)["A"]
    assert w.iloc[-1] == 1.0


def test_pullback_takes_no_new_entries_in_a_market_downtrend():
    close = list(np.linspace(50, 100, 60)) + [92, 85, 80] + [90, 100, 105]
    data_map = {"A": _bars(close)}
    regime = pd.Series(False, index=data_map["A"].index)
    w = Pullback(tradeable=_all_true(data_map), regime=regime, trend_ma=50, regime_filter=True,
                 stop_loss=None).generate(data_map)["A"]
    assert (w == 0).all()


def test_pullback_ma_exit_sells_when_close_crosses_its_average():
    close = list(np.linspace(50, 100, 60)) + [92, 85, 80, 84, 95]
    data_map = {"A": _bars(close)}
    w = Pullback(tradeable=_all_true(data_map), trend_ma=50, exit_rule="ma", exit_ma=3,
                 exit_rsi=101, stop_loss=None, max_positions=1).generate(data_map)["A"]
    assert w.iloc[60:63].max() == 1.0 and w.iloc[-1] == 0


def test_entry_filter_keeps_the_most_traded():
    data_map = {"BIG": _bars([10.0] * 30, volume=1_000), "SMALL": _bars([10.0] * 30, volume=1)}
    close = pd.DataFrame({s: df["close"] for s, df in data_map.items()})
    narrowed = entry_filter(close, data_map, _all_true(data_map), liquid_top=1)
    assert narrowed["BIG"].iloc[-1] and not narrowed["SMALL"].iloc[-1]


def test_risk_on_fills_forward_and_is_false_before_data():
    regime = pd.Series([True, False], index=pd.to_datetime(["2024-01-02", "2024-01-04"]))
    flags = risk_on(regime, pd.date_range("2024-01-01", "2024-01-05"))
    assert list(flags) == [False, True, True, False, False]


def test_fast_equity_buys_at_next_open_and_charges_costs():
    idx = pd.date_range("2024-01-01", periods=4, freq="D")
    opens = pd.DataFrame({"A": [100.0, 100.0, 110.0, 121.0]}, index=idx)
    weights = pd.DataFrame({"A": [1.0, 1.0, 1.0, 1.0]}, index=idx)   # decided at day-0 close
    equity, _ = equity_curve(weights, opens, cost_per_side=0.01, start="2024-01-01")
    # Day 0: nothing held. Day 1: bought at open (1% cost), earns 100->110.
    assert equity.iloc[1] == pytest.approx(1 + 0.10 - 0.01)
    assert equity.iloc[2] == pytest.approx((1 + 0.10 - 0.01) * 1.10)


def test_choose_takes_best_in_sample_sharpe_among_profitable():
    rows = [
        {"params": {"a": 1}, "in_sample": {"cagr": 0.10, "sharpe": 0.8}, "out_of_sample": None},
        {"params": {"a": 2}, "in_sample": {"cagr": -0.01, "sharpe": 2.0}, "out_of_sample": None},
        {"params": {"a": 3}, "in_sample": {"cagr": 0.20, "sharpe": 1.1}, "out_of_sample": None},
    ]
    assert choose(rows)["params"] == {"a": 3}
