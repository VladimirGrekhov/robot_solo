from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from event_calendar import EventFlag, event_status, is_entry_blocked, next_event

MSK = ZoneInfo("Europe/Moscow")

CBR_DAY = datetime(2026, 2, 13)          # день заседания ЦБ (из cbr_dates.csv)
PLAIN_WEDNESDAY = datetime(2026, 1, 14)  # обычная среда, не день ЦБ
PLAIN_THURSDAY = datetime(2026, 1, 15)   # обычный четверг, не день ЦБ
PLAIN_WEEKDAY = datetime(2026, 1, 15)    # обычный будний день для теста клиринга


# --- границы окон ---

def test_cbr_hot_boundary():
    before = CBR_DAY.replace(hour=12, minute=59)
    at_start = CBR_DAY.replace(hour=13, minute=0)
    assert not is_entry_blocked(before)
    assert is_entry_blocked(at_start)
    assert is_entry_blocked(at_start, hard_only=True)
    assert not is_entry_blocked(before, hard_only=True)


def test_cpi_window_boundary():
    before = PLAIN_WEDNESDAY.replace(hour=18, minute=44)
    at_start = PLAIN_WEDNESDAY.replace(hour=18, minute=45)
    assert not is_entry_blocked(before)
    assert is_entry_blocked(at_start)
    # мягкий блок, жёсткий фильтр не должен его учитывать
    assert not is_entry_blocked(at_start, hard_only=True)


def test_clearing_window_boundary():
    inside = PLAIN_WEEKDAY.replace(hour=14, minute=4, second=59)
    after_end = PLAIN_WEEKDAY.replace(hour=14, minute=5, second=0)
    assert is_entry_blocked(inside)
    assert not is_entry_blocked(after_end)
    assert not is_entry_blocked(inside, hard_only=True)


# --- CPI: среда блокируется, четверг нет ---

def test_cpi_only_on_wednesday():
    wed = PLAIN_WEDNESDAY.replace(hour=19, minute=15)  # вне окна клиринга 18:50-19:05
    thu = PLAIN_THURSDAY.replace(hour=19, minute=15)
    assert is_entry_blocked(wed)
    assert not is_entry_blocked(thu)
    assert bool(event_status(wed) & EventFlag.CPI_WINDOW)
    assert not bool(event_status(thu) & EventFlag.CPI_WINDOW)


# --- экспирации ---

@pytest.mark.parametrize("d", [datetime(2026, 3, 19), datetime(2026, 6, 18)])
def test_quarterly_expiration_dates(d):
    assert bool(event_status(d.replace(hour=12)) & EventFlag.EXPIRATION)


def test_non_expiration_thursday():
    # соседний четверг не должен считаться экспирацией
    assert not bool(event_status(datetime(2026, 3, 12, 12, 0)) & EventFlag.EXPIRATION)


# --- naive и aware дают одинаковый результат ---

def test_naive_and_aware_equivalent():
    naive = CBR_DAY.replace(hour=13, minute=15)
    aware = naive.replace(tzinfo=MSK)
    assert event_status(naive) == event_status(aware)
    assert is_entry_blocked(naive) == is_entry_blocked(aware)


def test_aware_other_timezone_converted_to_msk():
    utc = ZoneInfo("UTC")
    # 13:15 МСК зимой = 10:15 UTC (МСК = UTC+3 круглый год)
    aware_utc = datetime(2026, 2, 13, 10, 15, tzinfo=utc)
    naive_msk = datetime(2026, 2, 13, 13, 15)
    assert event_status(aware_utc) == event_status(naive_msk)


# --- next_event через границы ---

def test_next_event_across_weekend():
    friday_evening = datetime(2026, 1, 16, 20, 0)  # пятница, после клиринга
    dt, flag = next_event(friday_evening)
    assert dt == datetime(2026, 1, 19, 14, 0, tzinfo=MSK)  # понедельник, клиринг
    assert flag == EventFlag.CLEARING


def test_next_event_across_year_boundary():
    new_year_eve = datetime(2025, 12, 31, 20, 0)
    dt, flag = next_event(new_year_eve)
    assert dt == datetime(2026, 1, 1, 14, 0, tzinfo=MSK)
    assert flag == EventFlag.CLEARING


# --- TAX_PERIOD информационный, не блокирует ---

def test_tax_period_does_not_block():
    dt = datetime(2026, 1, 26, 12, 0)  # 26 число, не среда/не ЦБ/не клиринг
    assert bool(event_status(dt) & EventFlag.TAX_PERIOD)
    assert not is_entry_blocked(dt)
