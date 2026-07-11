from datetime import date, datetime

import orb_calendar as oc


def test_contract_ticker_format():
    assert oc.contract_ticker(date(2026, 9, 17)) == "SiU6"
    assert oc.contract_ticker(date(2025, 12, 18)) == "SiZ5"
    assert oc.contract_ticker(date(2026, 3, 19)) == "SiH6"
    assert oc.contract_ticker(date(2026, 6, 18)) == "SiM6"


def test_active_contract_rollover_two_trading_days_before_expiration():
    # экспирация сентября 2025 — 18.09.2025 (третий четверг), ролловер 16.09.2025
    assert oc.active_contract(date(2025, 9, 15)) == "SiU5"
    assert oc.active_contract(date(2025, 9, 16)) == "SiZ5"  # ролловер уже произошёл
    assert oc.active_contract(date(2025, 9, 17)) == "SiZ5"
    assert oc.active_contract(date(2025, 9, 18)) == "SiZ5"  # сам день экспирации — уже след. контракт


def test_active_contract_across_year_boundary():
    # экспирация декабря 2025 — 18.12.2025, ролловер 16.12.2025 (уходит в контракт марта 2026)
    assert oc.active_contract(date(2025, 12, 15)) == "SiZ5"
    assert oc.active_contract(date(2025, 12, 16)) == "SiH6"
    assert oc.active_contract(date(2025, 12, 17)) == "SiH6"


def test_entry_gate_cbr_hot_hard_block():
    blocked, reason = oc.entry_gate(datetime(2026, 2, 13, 13, 15))  # день ЦБ из cbr_dates.csv
    assert blocked is True
    assert reason == "blocked_cbr"


def test_entry_gate_cpi_window():
    blocked, reason = oc.entry_gate(datetime(2026, 1, 14, 19, 0))  # обычная среда, 18:45-19:30
    assert blocked is True
    assert reason == "blocked_cpi"


def test_entry_gate_clearing():
    blocked, reason = oc.entry_gate(datetime(2026, 1, 15, 14, 2))  # обычный будний день, клиринг
    assert blocked is True
    assert reason == "blocked_clearing"


def test_entry_gate_expiration_adj_blocks_for_orb():
    # 19.03.2026 — экспирация марта; зона +/-2 торговых дня блокирует вход для ORB
    blocked, reason = oc.entry_gate(datetime(2026, 3, 19, 12, 0))
    assert blocked is True
    assert reason == "blocked_expiration_adj"


def test_entry_gate_clear_day():
    blocked, reason = oc.entry_gate(datetime(2026, 1, 15, 12, 0))  # обычный четверг, полдень
    assert blocked is False
    assert reason is None


def test_force_flat_gate_only_cbr_hot():
    assert oc.force_flat_gate(datetime(2026, 2, 13, 13, 15)) is True
    assert oc.force_flat_gate(datetime(2026, 1, 14, 19, 0)) is False  # CPI — не жёсткий блок
    assert oc.force_flat_gate(datetime(2026, 1, 15, 14, 2)) is False  # клиринг — не жёсткий блок


def test_expiration_zone_mode_trading_days_vs_calendar_days():
    # экспирация 19.03.2026 — четверг; +2 торговых дня перепрыгивает выходные до понедельника 23.03
    monday_after = datetime(2026, 3, 23, 12, 0)
    assert oc.entry_gate(monday_after, "trading_days") == (True, "blocked_expiration_adj")
    # в календарных днях |23-19|=4 > 2 — уже вне зоны, как в эталонном Pine-скрипте
    assert oc.entry_gate(monday_after, "calendar_days") == (False, None)

    # обе зоны совпадают в пределах +/-2 календарных/торговых дней без выходных (вторник 17.03)
    tuesday_before = datetime(2026, 3, 17, 12, 0)
    assert oc.entry_gate(tuesday_before, "trading_days")[0] is True
    assert oc.entry_gate(tuesday_before, "calendar_days")[0] is True
