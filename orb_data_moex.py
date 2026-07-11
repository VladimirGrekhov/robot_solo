"""
orb_data_moex.py — загрузка исторических свечей Si с MOEX ISS для бэктеста ORB.

MOEX ISS не отдаёт готовый M15-интервал (доступны только 1, 10, 60, 24, 7, 31, 4),
поэтому качаем 1-минутные свечи постранично (по 500 строк) и агрегируем в M15
сами через pandas.resample. Результат кэшируется в parquet рядом с датой запроса,
повторный запуск бэктеста на том же периоде не ходит в сеть.
"""

from __future__ import annotations

import time
from datetime import date
from pathlib import Path

import pandas as pd
import requests

ISS_CANDLES_URL = ("https://iss.moex.com/iss/engines/futures/markets/forts/"
                    "securities/{sec}/candles.json")
_PAGE_SIZE = 500
_MINUTE_INTERVAL = 1
_REQUEST_TIMEOUT = 30
_BETWEEN_REQUESTS_SEC = 0.05

OHLCV_COLUMNS = ["open", "high", "low", "close", "volume"]


def fetch_minute_candles(sec: str, date_from: date, date_till: date,
                          session: requests.Session | None = None) -> pd.DataFrame:
    """Качает 1-минутные свечи sec за [date_from, date_till] постранично с ISS.

    Возвращает DataFrame с DatetimeIndex (начало бара, МСК naive) и колонками
    OHLCV; пустой DataFrame, если контракт в этот период не торговался."""
    sess = session or requests
    rows: list[list] = []
    start = 0
    url = ISS_CANDLES_URL.format(sec=sec)
    while True:
        params = {"from": date_from.isoformat(), "till": date_till.isoformat(),
                   "interval": _MINUTE_INTERVAL, "start": start}
        resp = sess.get(url, params=params, timeout=_REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()["candles"]["data"]
        if not data:
            break
        rows.extend(data)
        if len(data) < _PAGE_SIZE:
            break
        start += _PAGE_SIZE
        time.sleep(_BETWEEN_REQUESTS_SEC)

    if not rows:
        return pd.DataFrame(columns=OHLCV_COLUMNS)

    cols = ["open", "close", "high", "low", "value", "volume", "begin", "end"]
    df = pd.DataFrame(rows, columns=cols)
    df["datetime"] = pd.to_datetime(df["begin"])
    df = df.set_index("datetime")[OHLCV_COLUMNS].sort_index()
    return df


def resample_m15(df_1min: pd.DataFrame) -> pd.DataFrame:
    """Агрегирует 1-минутные свечи в M15 (open=первый, high=max, low=min, close=последний,
    volume=сумма). Бары без данных (нет торгов) отбрасываются."""
    if df_1min.empty:
        return pd.DataFrame(columns=OHLCV_COLUMNS)
    agg = df_1min.resample("15min", label="left", closed="left").agg({
        "open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum",
    })
    return agg.dropna(subset=["open"])


def _cache_path(cache_dir: Path, sec: str, date_from: date, date_till: date) -> Path:
    return Path(cache_dir) / f"{sec}_{date_from.isoformat()}_{date_till.isoformat()}_m15.parquet"


def load_contract_m15(sec: str, date_from: date, date_till: date, cache_dir: Path,
                       session: requests.Session | None = None) -> pd.DataFrame:
    """M15 OHLCV контракта sec за период — из parquet-кэша, если есть, иначе с ISS."""
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = _cache_path(cache_dir, sec, date_from, date_till)
    if cache_file.exists():
        return pd.read_parquet(cache_file)
    raw = fetch_minute_candles(sec, date_from, date_till, session=session)
    m15 = resample_m15(raw)
    m15.to_parquet(cache_file)
    return m15
