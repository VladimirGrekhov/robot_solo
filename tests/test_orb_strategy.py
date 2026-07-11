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
