import numpy as np
import pandas as pd
import pytest

from vibetrading.walkforward import Scheduled, series_stats, walk_forward


def _returns():
    """Setting 0 earns steadily in 2015-2017 then loses; setting 1 the reverse."""
    idx = pd.bdate_range("2015-01-01", "2019-12-31")
    early = idx.year < 2018
    rng = np.random.default_rng(0)
    noise = rng.normal(0, 0.001, size=(len(idx), 2))
    a = np.where(early, 0.001, -0.001) + noise[:, 0]
    b = np.where(early, -0.0005, 0.002) + noise[:, 1]
    return pd.DataFrame({0: a, 1: b}, index=idx)


def test_walk_forward_only_uses_earlier_years():
    wf, schedule = walk_forward(_returns(), "2015-01-01", 252)
    assert [r["year"] for r in schedule] == [2018, 2019]
    # 2018 is chosen on 2015-2017, where setting 0 was best, even though it
    # loses in 2018: the choice can't see the year it is used in.
    assert schedule[0]["chosen"] == 0 and schedule[0]["return"] < 0
    # By 2019 the expanding window (2015-2018) still favours 0 ...
    assert schedule[1]["chosen"] == 0
    assert wf.index[0] == pd.Timestamp("2018-01-01")


def test_rolling_window_forgets_old_years():
    _, schedule = walk_forward(_returns(), "2015-01-01", 252, window_years=1)
    # ... while a one-year window looks only at 2018, where setting 1 was best.
    assert schedule[1]["chosen"] == 1


def test_cash_when_nothing_made_money():
    idx = pd.bdate_range("2015-01-01", "2018-12-31")
    losing = pd.DataFrame({0: -0.001, 1: -0.002}, index=idx)
    wf, schedule = walk_forward(losing, "2015-01-01", 252)
    assert schedule[0]["chosen"] is None and (wf == 0).all()


def test_series_stats():
    r = pd.Series([0.1, -0.5, 0.2], index=pd.to_datetime(["2020-01-01", "2020-01-02", "2020-01-03"]))
    st = series_stats(r, 252)
    assert st["total_return"] == pytest.approx(1.1 * 0.5 * 1.2 - 1)
    assert st["max_drawdown"] == pytest.approx(0.55 / 1.1 - 1)


class _Fixed:
    """Stand-in strategy: weight = its ``level`` everywhere."""

    def __init__(self, tradeable, regime=None, level=0.0):
        self.level = level

    def generate(self, data_map):
        return {s: pd.Series(self.level, index=df.index) for s, df in data_map.items()}


def test_scheduled_trades_each_years_setting(monkeypatch):
    monkeypatch.setitem(__import__("vibetrading.walkforward", fromlist=["STRATEGIES"]).STRATEGIES, "fixed", _Fixed)
    idx = pd.date_range("2017-06-01", "2020-06-30", freq="D")
    data_map = {"A": pd.DataFrame({"close": 1.0}, index=idx)}
    strat = Scheduled("fixed", {2018: {"level": 0.2}, 2019: None, 2020: {"level": 0.7}}, tradeable=None)
    w = strat.generate(data_map)["A"]
    assert w["2017-07-01"] == 0.2          # warm-up before the first choice uses it
    assert w["2018-05-01"] == 0.2
    assert w["2019-05-01"] == 0.0          # cash year
    assert w["2020-05-01"] == 0.7
