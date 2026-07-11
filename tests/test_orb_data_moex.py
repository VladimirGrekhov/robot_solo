import pandas as pd

import orb_data_moex as m


def test_resample_m15_aggregates_correctly():
    idx = pd.date_range("2026-03-11 10:00", periods=15, freq="1min")
    df = pd.DataFrame({
        "open": range(100, 115),
        "high": [x + 1 for x in range(100, 115)],
        "low": [x - 1 for x in range(100, 115)],
        "close": range(101, 116),
        "volume": [10] * 15,
    }, index=idx)

    out = m.resample_m15(df)
    assert len(out) == 1
    row = out.iloc[0]
    assert row["open"] == 100
    assert row["close"] == 115
    assert row["high"] == 115
    assert row["low"] == 99
    assert row["volume"] == 150


def test_resample_m15_drops_empty_bins():
    idx = pd.to_datetime(["2026-03-11 10:00", "2026-03-11 10:01",
                          "2026-03-11 10:30", "2026-03-11 10:31"])
    df = pd.DataFrame({"open": [1, 2, 3, 4], "high": [1, 2, 3, 4],
                       "low": [1, 2, 3, 4], "close": [1, 2, 3, 4],
                       "volume": [1, 1, 1, 1]}, index=idx)
    out = m.resample_m15(df)
    # бин 10:15-10:30 без данных должен отсутствовать, а не быть NaN-строкой
    assert list(out.index.time) == [pd.Timestamp("10:00").time(), pd.Timestamp("10:30").time()]


def test_resample_m15_empty_input():
    empty = pd.DataFrame(columns=m.OHLCV_COLUMNS)
    out = m.resample_m15(empty)
    assert out.empty
