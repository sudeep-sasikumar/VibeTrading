import pandas as pd

from vibetrading.breaks import split_at_breaks


def _bars(close, volume=None):
    idx = pd.date_range("2024-01-01", periods=len(close), freq="D")
    return pd.DataFrame({"close": close, "volume": volume or [100.0] * len(close)}, index=idx)


def test_crypto_ticker_reuse_is_split_into_two_instruments():
    data = {"LUNA-USDT": _bars([1.0, 0.9, 0.00005, 9.0, 8.5])}
    out, breaks = split_at_breaks(data, crypto=True)
    assert [b.new_symbol for b in breaks] == ["LUNAX20240104-USDT"]
    assert list(out["LUNA-USDT"]["close"]) == [1.0, 0.9, 0.00005]
    assert list(out["LUNAX20240104-USDT"]["close"]) == [9.0, 8.5]


def test_crypto_crash_without_a_volume_swap_is_kept():
    # A real collapse: price down 3,000x but coin volume only ~100x.
    data = {"LUNA-USDT": _bars([17.0, 1.0, 0.0003], volume=[100.0, 1_000.0, 100_000.0])}
    out, breaks = split_at_breaks(data, crypto=True)
    assert breaks == [] and list(out) == ["LUNA-USDT"]


def test_crypto_redenomination_is_split():
    # Price / 1000, coin volume x 1000: a token swap, not a loss.
    data = {"QUICK-USDT": _bars([60.0, 62.0, 0.061, 0.063], volume=[10.0, 10.0, 12_000.0, 11_000.0])}
    out, breaks = split_at_breaks(data, crypto=True)
    assert len(breaks) == 1 and breaks[0].date == pd.Timestamp("2024-01-03")
    assert set(out) == {"QUICK-USDT", "QUICKX20240103-USDT"}


def test_stock_unadjusted_split_is_split():
    data = {"GPIL.NS": _bars([1000.0, 1010.0, 205.0, 207.0]), "OK.NS": _bars([10.0, 11.0, 10.5, 10.8])}
    out, breaks = split_at_breaks(data, crypto=False)
    assert [b.new_symbol for b in breaks] == ["GPILX20240103.NS"]
    assert out["OK.NS"].equals(data["OK.NS"])
