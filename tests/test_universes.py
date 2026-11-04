import pandas as pd
import pytest

from vibetrading.coverage import members_on
from vibetrading.sources.binance import keep_base
from vibetrading.store import PriceStore
from vibetrading.universes import rank_by_volume, us_members


def _panel(volumes: dict[str, float], start="2024-12-01", end="2025-02-28"):
    dates = pd.date_range(start, end, freq="D")
    return pd.DataFrame({s: v for s, v in volumes.items()}, index=dates)


def test_rank_by_volume_takes_top_n_from_the_previous_window():
    panel = _panel({"A-USDT": 300.0, "B-USDT": 200.0, "C-USDT": 100.0})
    spans = rank_by_volume(panel, top_n=2, window_days=30, start="2025-01-01",
                           today=pd.Timestamp("2025-06-01"))
    assert set(spans["symbol"]) == {"A-USDT", "B-USDT"}
    a = spans[spans["symbol"] == "A-USDT"].iloc[0]
    # January and February merge into one span ending on 1 March.
    assert a.start_date == pd.Timestamp("2025-01-01")
    assert a.end_date == pd.Timestamp("2025-03-01")


def test_rank_by_volume_ignores_the_month_being_ranked():
    # C only spikes during January itself, so it can't make January's list.
    panel = _panel({"A-USDT": 300.0, "B-USDT": 200.0, "C-USDT": 1.0})
    panel.loc["2025-01-01":"2025-01-31", "C-USDT"] = 1e9
    spans = rank_by_volume(panel, top_n=2, window_days=30, start="2025-01-01",
                           today=pd.Timestamp("2025-06-01"))
    c = spans[spans["symbol"] == "C-USDT"]
    assert list(c["start_date"]) == [pd.Timestamp("2025-02-01")]


def test_rank_by_volume_skips_coins_that_traded_too_few_days():
    panel = _panel({"A-USDT": 300.0, "NEW-USDT": 1e9})
    panel.loc[:"2024-12-25", "NEW-USDT"] = float("nan")  # listed a week before January
    spans = rank_by_volume(panel, top_n=2, window_days=30, start="2025-01-01",
                           today=pd.Timestamp("2025-06-01"))
    jan = spans[spans["start_date"] == pd.Timestamp("2025-01-01")]
    assert list(jan["symbol"]) == ["A-USDT"]


def test_rank_by_volume_leaves_the_current_month_open():
    panel = _panel({"A-USDT": 300.0})
    spans = rank_by_volume(panel, top_n=1, window_days=30, start="2025-01-01",
                           today=pd.Timestamp("2025-02-15"))
    assert spans["end_date"].isna().all()


@pytest.mark.parametrize("base, kept", [
    ("BTC", True), ("USDC", False), ("FDUSD", False), ("PAXG", False),
    ("BTCUP", False), ("ETHBEAR", False), ("JUP", True), ("SUPER", True),
])
def test_keep_base(base, kept):
    assert keep_base(base, {"BTC", "ETH", "USDC", "JUP", "SUPER", "BTCUP", "ETHBEAR"}) is kept


def test_us_members_maps_tickers_and_drops_members_who_left_before_the_window():
    history = pd.DataFrame({
        "ticker": ["BRK.B", "AAPL", "GONE"],
        "name": ["Berkshire", "Apple", "Gone Inc"],
        "start_date": pd.to_datetime(["2010-02-16", "2006-01-01", "2001-01-01"]),
        "end_date": pd.to_datetime([None, None, "2012-05-01"]),
    })
    members = us_members(history, "2015-01-01")
    assert list(members["symbol"]) == ["BRK-B.US", "AAPL.US"]
    assert list(members["source_symbol"]) == ["BRK-B", "AAPL"]


def test_members_on_uses_half_open_spans():
    members = pd.DataFrame({
        "symbol": ["IN", "LEFT", "LATER", "OPEN"],
        "start_date": pd.to_datetime(["2015-01-01", "2010-01-01", "2020-06-01", None]),
        "end_date": pd.to_datetime([None, "2020-01-01", None, None]),
    })
    assert set(members_on(members, pd.Timestamp("2020-01-01"))) == {"IN", "OPEN"}


def test_store_round_trip_replaces_from_first_new_date(tmp_path):
    store = PriceStore(tmp_path / "t.duckdb")
    dates = pd.date_range("2025-01-01", periods=5, freq="D")
    bars = pd.DataFrame({"open": 1.0, "high": 2.0, "low": 0.5, "close": [1, 2, 3, 4, 5],
                         "volume": 10.0}, index=dates)
    store.write_prices("X.US", bars)
    # An incremental fetch overlapping the last two days replaces only those.
    update = bars.iloc[3:].assign(close=[40.0, 50.0])
    store.write_prices("X.US", update)
    got = store.prices(["X.US"])["X.US"]
    assert list(got["close"]) == [1, 2, 3, 40, 50]
    assert got.index.name == "trade_date"
    store.close()
