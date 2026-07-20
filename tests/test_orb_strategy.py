from datetime import datetime

import orb_strategy as s

DAY = dict(year=2026, month=3, day=11)


def bar(h, mi, o, hi, lo, c):
    return s.Bar(datetime(DAY["year"], DAY["month"], DAY["day"], h, mi), o, hi, lo, c)


def build_range(rh=105.0, rl=99.0, max_stop_pt=600):
    """Прогоняет 4 бара диапазона 10:00-10:45 с заданными high/low и возвращает состояние."""
    st = s.OrbState()
    highs = [rh - 3, rh - 1, rh, rh - 2]
    lows = [rl + 3, rl + 1, rl + 2, rl]
    for i, mi in enumerate((0, 15, 30, 45)):
        r = s.process_bar(st, bar(10, mi, 100, highs[i], lows[i], 100), False, False, max_stop_pt)
        st = r.state
    return st


def test_range_boundary_1045_included_1100_excluded():
    st = s.OrbState()
    r = s.process_bar(st, bar(10, 45, 100, 105, 95, 101), False, False, 600)
    assert r.state.range_ready is True
    assert r.state.range_high == 105 and r.state.range_low == 95

    st2 = s.OrbState()
    r2 = s.process_bar(st2, bar(11, 0, 100, 999, 1, 101), False, False, 600)
    # 11:00 — уже окно входа, а не диапазона: range_ready не выставляется этим баром
    assert r2.state.range_ready is False


def test_range_accumulates_over_four_bars():
    st = build_range(rh=105.0, rl=99.0)
    assert st.range_ready is True
    assert st.range_high == 105.0
    assert st.range_low == 99.0
    assert st.range_blocked is False


def test_long_entry_on_first_close_above_range():
    st = build_range()
    r = s.process_bar(st, bar(11, 0, 106, 108, 105, 107), False, False, 600)
    assert r.entry is not None
    assert r.entry.side == "long"
    assert r.entry.stop_price == 99.0
    assert r.skip is None


def test_short_entry_on_first_close_below_range():
    st = build_range()
    r = s.process_bar(st, bar(11, 0, 98, 99, 90, 91), False, False, 600)
    assert r.entry is not None
    assert r.entry.side == "short"
    assert r.entry.stop_price == 105.0


def test_max_one_long_and_one_short_per_day():
    st = build_range()

    r = s.process_bar(st, bar(11, 0, 106, 108, 105, 107), False, False, 600)
    assert r.entry.side == "long"
    st = s.open_position(r.state, r.entry, entry_price=107)

    # стоп-аут
    r = s.process_bar(st, bar(11, 15, 100, 100, 95, 96), False, False, 600)
    assert r.exit.reason == "stop"
    st = r.state

    # цена возвращается внутрь диапазона (сбрасывает edge-trigger)
    r = s.process_bar(st, bar(11, 30, 103, 104, 102, 103), False, False, 600)
    st = r.state

    # повторный пробой вверх — уже использован лонг на сегодня
    r = s.process_bar(st, bar(11, 45, 106, 109, 105, 108), False, False, 600)
    assert r.entry is None
    assert r.skip == s.SkipInfo("long", "already_traded")
    st = r.state

    # шорт в этот же день ещё доступен
    r = s.process_bar(st, bar(12, 0, 97, 98, 90, 91), False, False, 600)
    assert r.entry is not None
    assert r.entry.side == "short"
    st = s.open_position(r.state, r.entry, entry_price=91)

    r = s.process_bar(st, bar(12, 15, 95, 106, 94, 96), False, False, 600)  # стоп шорта = 105
    assert r.exit.reason == "stop"
    st = r.state

    # цена возвращается внутрь диапазона (сбрасывает edge-trigger)
    r = s.process_bar(st, bar(12, 30, 100, 104, 100, 102), False, False, 600)
    st = r.state

    r = s.process_bar(st, bar(12, 45, 97, 98, 90, 91), False, False, 600)  # ещё один пробой вниз
    assert r.entry is None
    assert r.skip == s.SkipInfo("short", "already_traded")


def test_position_never_flips_while_open():
    # диапазон 99-105, стоп лонга — 99 (далеко от 91, чтобы стоп не задело)
    st = build_range(rh=105.0, rl=50.0)
    r = s.process_bar(st, bar(11, 0, 106, 108, 105, 107), False, False, 600)
    assert r.entry.stop_price == 50.0
    st = s.open_position(r.state, r.entry, entry_price=107)

    # цена закрывается ниже нижней границы диапазона (был бы шорт-сигнал, будь
    # позиция свободна) — но лонг уже открыт и стоп (50) не задет, поэтому
    # сигнал полностью игнорируется: ни выхода, ни нового (перевёрнутого) входа
    r = s.process_bar(st, bar(11, 15, 60, 61, 55, 58), False, False, 600)
    assert r.entry is None
    assert r.exit is None
    assert r.state.position is not None
    assert r.state.position.side == "long"


def test_allow_flip_reverses_position_on_opposite_breakout():
    # тот же сценарий, что и test_position_never_flips_while_open, но с allow_flip=True —
    # поведение эталонного Pine-скрипта (strategy.entry реверсирует позицию)
    st = build_range(rh=105.0, rl=50.0)
    r = s.process_bar(st, bar(11, 0, 106, 108, 105, 107), False, False, 600, allow_flip=True)
    st = s.open_position(r.state, r.entry, entry_price=107)
    assert st.position.side == "long"

    # low=51 (>stop=50, стоп НЕ задет), close=45 (<range_low=50 — свежий пробой вниз)
    r = s.process_bar(st, bar(11, 15, 55, 56, 51, 45), False, False, 600, allow_flip=True)
    assert r.exit is not None
    assert r.exit.reason == "flip"
    assert r.entry is not None
    assert r.entry.side == "short"
    st = r.state
    assert st.position is None          # закрыт этим же BarResult, откроется на след. баре
    assert st.short_used is True        # шорт считается использованным на сегодня

    # стоп шорта (105 = range_high) всегда совпадает с порогом для флипа обратно в лонг —
    # стоп-проверка идёт раньше проверки флипа, поэтому она и сработает первой
    st = s.open_position(st, r.entry, entry_price=45)
    r = s.process_bar(st, bar(11, 30, 100, 106, 99, 104), False, False, 600, allow_flip=True)
    assert r.exit is not None
    assert r.exit.reason == "stop"
    assert r.entry is None  # не флип — именно стоп, повторный лонг всё равно не использован бы


def test_cbr_hard_block_forces_flat_and_blocks_new_entries():
    st = build_range()
    r = s.process_bar(st, bar(11, 0, 106, 108, 105, 107), False, False, 600)
    st = s.open_position(r.state, r.entry, entry_price=107)

    # 12:59 — ещё не жёсткий блок, позиция держится
    r = s.process_bar(st, bar(12, 45, 107, 108, 106, 107), False, False, 600)
    assert r.exit is None
    st = r.state

    # 13:00 — жёсткий блок (день ЦБ): принудительное закрытие
    r = s.process_bar(st, bar(13, 0, 107, 108, 106, 107), False, True, 600)
    assert r.exit is not None
    assert r.exit.reason == "cbr_flat"
    st = r.state
    assert st.position is None

    # цена ненадолго возвращается внутрь диапазона...
    r = s.process_bar(st, bar(13, 15, 102, 104, 101, 103), True, True, 600)
    st = r.state

    # ...и пробивает его вниз (шорт ещё не использован сегодня) — но вход
    # заблокирован календарём (мы всё ещё в окне 13:00-15:30)
    r = s.process_bar(st, bar(13, 30, 97, 98, 90, 91), True, True, 600)
    assert r.entry is None
    assert r.skip == s.SkipInfo("short", "blocked")


def test_eod_force_close_at_1840_not_at_1830():
    st = build_range()
    r = s.process_bar(st, bar(11, 0, 106, 108, 105, 107), False, False, 600)
    st = s.open_position(r.state, r.entry, entry_price=107)

    r = s.process_bar(st, bar(18, 30, 107, 108, 106, 107), False, False, 600)
    assert r.exit is None
    st = r.state

    r = s.process_bar(st, bar(18, 45, 107, 108, 106, 107), False, False, 600)
    assert r.exit is not None
    assert r.exit.reason == "eod"


def test_range_too_wide_skips_signals():
    st = build_range(rh=800.0, rl=99.0, max_stop_pt=600)  # ширина 701 > 600
    assert st.range_blocked is True

    r = s.process_bar(st, bar(11, 0, 810, 820, 805, 815), False, False, 600)
    assert r.entry is None
    assert r.skip == s.SkipInfo("long", "range_too_wide")


def test_breakeven_arms_and_exits_at_entry():
    """breakeven_r=1.0: после +1R стоп переносится в вход, откат к входу = безубыток."""
    st = build_range(rh=105.0, rl=99.0)
    r = s.process_bar(st, bar(11, 0, 106, 108, 105, 107), False, False, 600)
    st = s.open_position(r.state, r.entry, entry_price=106.0, breakeven_r=1.0)
    assert st.position.be_level == 113.0            # 106 + (106-99)*1.0
    assert st.position.stop_price == 99.0 and st.position.be_armed is False

    # бар достаёт +1R (high>=113) — безубыток взводится, стоп -> цена входа
    r2 = s.process_bar(st, bar(11, 15, 107, 113, 107, 112), False, False, 600)
    assert r2.exit is None
    assert r2.state.position.be_armed is True
    assert r2.state.position.stop_price == 106.0

    # откат к входу: выход по стопу в безубытке (106), а не по исходному стопу (99)
    r3 = s.process_bar(r2.state, bar(11, 30, 110, 111, 105, 106), False, False, 600)
    assert r3.exit is not None and r3.exit.reason == "stop"
    assert r3.exit.price == 106.0


def test_breakeven_off_by_default():
    """Без breakeven_r (по умолчанию 0) стоп остаётся на границе диапазона."""
    st = build_range(rh=105.0, rl=99.0)
    r = s.process_bar(st, bar(11, 0, 106, 108, 105, 107), False, False, 600)
    st = s.open_position(r.state, r.entry, entry_price=106.0)   # breakeven_r=0
    assert st.position.be_level is None

    # ушли высоко и откатились к 105 — стоп 99 не задет, позиция жива
    r2 = s.process_bar(st, bar(11, 15, 107, 120, 107, 118), False, False, 600)
    assert r2.state.position.be_armed is False and r2.state.position.stop_price == 99.0
    r3 = s.process_bar(r2.state, bar(11, 30, 110, 111, 105, 106), False, False, 600)
    assert r3.exit is None
