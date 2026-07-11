"""
event_calendar.py — календарь рыночных событий Si (MOEX FORTS) для фильтра входов.

Чистый модуль: только stdlib, без зависимостей от брокерского API. Все расчёты
времени ведутся в таймзоне Europe/Moscow — на вход можно передавать как naive
datetime (считается, что это уже МСК), так и aware (конвертируется в МСК).

Окна и решение блокировать/не блокировать вход зафиксированы по итогам анализа
двух лет дневных данных и двух месяцев M5-данных Si — см. таблицу в README.md,
не менять без нового анализа.

Даты заседаний ЦБ РФ читаются из cbr_dates.csv рядом с этим файлом (колонки
date,note). Экспирация квартального контракта Si (третий четверг марта, июня,
сентября, декабря) считается алгоритмически.
"""

from __future__ import annotations

import csv
from datetime import date, datetime, time, timedelta
from enum import Flag, auto
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

MSK = ZoneInfo("Europe/Moscow")

_HERE = Path(__file__).resolve().parent
_CBR_CSV = _HERE / "cbr_dates.csv"

_EXPIRATION_MONTHS = (3, 6, 9, 12)
_CBR_HOT_START = time(13, 0)
_CBR_HOT_END = time(15, 30)
_CPI_START = time(18, 45)
_CPI_END = time(19, 30)
_CLEARING_WINDOWS = ((time(14, 0), time(14, 5)), (time(18, 50), time(19, 5)))
_TAX_DAYS = (25, 26, 27, 28)
_EXPIRATION_ADJ_TRADING_DAYS = 2


class EventFlag(Flag):
    NONE = 0
    CBR_DAY = auto()        # день заседания ЦБ (весь день)
    CBR_HOT = auto()        # окно релиза ЦБ 13:00-15:30 МСК
    CPI_WINDOW = auto()     # среда 18:45-19:30 МСК (недельная инфляция)
    CLEARING = auto()       # 14:00-14:05 и 18:50-19:05 МСК
    TAX_PERIOD = auto()     # 25-28 число (только информационный флаг)
    EXPIRATION = auto()     # третий четверг мар/июн/сен/дек (квартальный Si)
    EXPIRATION_ADJ = auto()  # +/- 2 торговых дня от экспирации (зона стыка контрактов)


_HARD_BLOCK = EventFlag.CBR_HOT
_SOFT_BLOCK = EventFlag.CBR_HOT | EventFlag.CPI_WINDOW | EventFlag.CLEARING


def _to_msk(dt: datetime) -> datetime:
    """Приводит datetime к aware-времени в Europe/Moscow. Naive считается уже МСК."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=MSK)
    return dt.astimezone(MSK)


@lru_cache(maxsize=1)
def _load_cbr_dates() -> frozenset[date]:
    dates: set[date] = set()
    with open(_CBR_CSV, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            dates.add(date.fromisoformat(row["date"].strip()))
    return frozenset(dates)


def _third_thursday(year: int, month: int) -> date:
    """Третий четверг месяца (день экспирации квартального фьючерса)."""
    first = date(year, month, 1)
    offset = (3 - first.weekday()) % 7  # Thursday.weekday() == 3
    first_thursday = first + timedelta(days=offset)
    return first_thursday + timedelta(days=14)


@lru_cache(maxsize=None)
def _expirations_for_year(year: int) -> tuple[date, ...]:
    return tuple(_third_thursday(year, m) for m in _EXPIRATION_MONTHS)


def _is_expiration(d: date) -> bool:
    return d in _expirations_for_year(d.year)


def expiration_dates(year: int) -> tuple[date, ...]:
    """Публичная обёртка над _expirations_for_year: даты экспирации квартального
    Si (третий четверг мар/июн/сен/дек) за календарный год."""
    return _expirations_for_year(year)


def _add_trading_days(d: date, n: int) -> date:
    """Сдвигает дату на n торговых дней (пн-пт). MOEX-праздники не учитываются
    (только календарь expiration_adj-зоны — см. ограничение в README)."""
    step = 1 if n > 0 else -1
    remaining = abs(n)
    cur = d
    while remaining:
        cur += timedelta(days=step)
        if cur.weekday() < 5:
            remaining -= 1
    return cur


def trading_day_offset(d: date, n: int) -> date:
    """Публичная обёртка над _add_trading_days: дата +/- n торговых дней (пн-пт)."""
    return _add_trading_days(d, n)


@lru_cache(maxsize=None)
def _adj_zone(expiration: date) -> frozenset[date]:
    start = _add_trading_days(expiration, -_EXPIRATION_ADJ_TRADING_DAYS)
    end = _add_trading_days(expiration, _EXPIRATION_ADJ_TRADING_DAYS)
    days = []
    cur = start
    while cur <= end:
        days.append(cur)
        cur += timedelta(days=1)
    return frozenset(days)


def _relevant_expirations(year: int) -> tuple[date, ...]:
    # у декабрьской экспирации +2 торговых дня может уйти в январь следующего года,
    # поэтому берём соседние годы тоже (дёшево — по 4 даты на год).
    return _expirations_for_year(year - 1) + _expirations_for_year(year) + _expirations_for_year(year + 1)


def _is_in_adj_zone(d: date) -> bool:
    for exp in _relevant_expirations(d.year):
        if d in _adj_zone(exp):
            return True
    return False


def event_status(dt: datetime) -> EventFlag:
    """Возвращает комбинацию флагов событий, действующих в момент dt (МСК)."""
    msk = _to_msk(dt)
    d = msk.date()
    t = msk.time()
    flags = EventFlag.NONE

    if d in _load_cbr_dates():
        flags |= EventFlag.CBR_DAY
        if _CBR_HOT_START <= t < _CBR_HOT_END:
            flags |= EventFlag.CBR_HOT

    if msk.weekday() == 2 and _CPI_START <= t < _CPI_END:
        flags |= EventFlag.CPI_WINDOW

    if msk.weekday() < 5:
        for start, end in _CLEARING_WINDOWS:
            if start <= t < end:
                flags |= EventFlag.CLEARING
                break

    if d.day in _TAX_DAYS:
        flags |= EventFlag.TAX_PERIOD

    if _is_expiration(d):
        flags |= EventFlag.EXPIRATION

    if _is_in_adj_zone(d):
        flags |= EventFlag.EXPIRATION_ADJ

    return flags


def is_entry_blocked(dt: datetime, hard_only: bool = False) -> bool:
    """True, если вход в момент dt должен быть заблокирован фильтром.

    hard_only=True  — только жёсткий блок (CBR_HOT).
    hard_only=False — жёсткий + мягкий блок (CBR_HOT, CPI_WINDOW, CLEARING).
    TAX_PERIOD и EXPIRATION/EXPIRATION_ADJ вход не блокируют.
    """
    flags = event_status(dt)
    mask = _HARD_BLOCK if hard_only else _SOFT_BLOCK
    return bool(flags & mask)


def _day_boundaries(d: date) -> list[tuple[time, EventFlag]]:
    """Моменты внутри дня d, где включается (начинается) какой-либо флаг."""
    b: dict[time, EventFlag] = {}

    def add(t: time, flag: EventFlag) -> None:
        b[t] = b.get(t, EventFlag.NONE) | flag

    if d in _load_cbr_dates():
        add(time(0, 0), EventFlag.CBR_DAY)
        add(_CBR_HOT_START, EventFlag.CBR_HOT)

    if d.weekday() == 2:
        add(_CPI_START, EventFlag.CPI_WINDOW)

    if d.weekday() < 5:
        for start, _end in _CLEARING_WINDOWS:
            add(start, EventFlag.CLEARING)

    if d.day in _TAX_DAYS and d.day == _TAX_DAYS[0]:
        add(time(0, 0), EventFlag.TAX_PERIOD)

    if _is_expiration(d):
        add(time(0, 0), EventFlag.EXPIRATION)

    if _is_in_adj_zone(d) and not _is_in_adj_zone(d - timedelta(days=1)):
        add(time(0, 0), EventFlag.EXPIRATION_ADJ)

    return sorted(b.items())


def next_event(dt: datetime) -> tuple[datetime, EventFlag]:
    """Ближайшее после dt наступление события (момент включения флага(ов))."""
    msk = _to_msk(dt)
    d = msk.date()
    day_offset = 0
    while True:
        cur_day = d + timedelta(days=day_offset)
        for t, flag in _day_boundaries(cur_day):
            if flag == EventFlag.NONE:
                continue
            candidate = datetime.combine(cur_day, t).replace(tzinfo=MSK)
            if candidate > msk:
                return candidate, flag
        day_offset += 1
