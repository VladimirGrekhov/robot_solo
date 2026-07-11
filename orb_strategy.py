"""
orb_strategy.py — чистая логика стратегии ORB (Opening Range Breakout) для Si M15.

Никаких зависимостей от QuikPy/pandas/файлов — только stdlib. Календарные блокировки
(ЦБ/CPI/клиринг/экспирация) и риск-лимиты сюда НЕ зашиты: вызывающий код (бэктест
или live-оркестратор) сам решает blocked_entry/force_flat на баре и передаёт их
готовыми булевыми флагами — так стратегия тестируется без единой реальной даты.

Спецификация (зафиксирована бэктестом, не менять без нового анализа):
  - диапазон 10:00:00-10:59:59 МСК (4 бара M15: 10:00,10:15,10:30,10:45);
  - вход 11:00-18:40: лонг на первом закрытии выше high диапазона, шорт — ниже low;
    не больше одного лонга и одного шорта в день; позиция всегда одна (нетто);
  - стоп — противоположная граница диапазона, без трейлинга и тейка;
  - в 18:40 позиция закрывается по рынку безусловно;
  - если ширина диапазона (в пунктах) больше max_stop_pt — сигналы дня не берутся.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, time

RANGE_START = time(10, 0)
RANGE_END = time(11, 0)      # исключая — последний бар диапазона стартует в 10:45
RANGE_LAST_BAR = time(10, 45)
ENTRY_START = time(11, 0)
ENTRY_END = time(18, 40)     # исключая; не выровнено по сетке M15 — сравнение по времени


@dataclass(frozen=True)
class Bar:
    """Один закрытый бар M15. dt — время ОТКРЫТИЯ бара, МСК (naive)."""
    dt: datetime
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class Position:
    side: str            # "long" | "short"
    entry_time: datetime
    entry_price: float
    stop_price: float
    range_high: float
    range_low: float


@dataclass(frozen=True)
class EntrySignal:
    side: str
    signal_bar_dt: datetime
    range_high: float
    range_low: float
    stop_price: float


@dataclass(frozen=True)
class ExitSignal:
    reason: str           # "stop" | "eod" | "cbr_flat"
    price: float


@dataclass(frozen=True)
class SkipInfo:
    side: str
    reason: str           # "already_traded" | "range_too_wide" | "blocked"


@dataclass(frozen=True)
class OrbState:
    current_day: date | None = None
    range_high: float | None = None
    range_low: float | None = None
    range_ready: bool = False
    range_blocked: bool = False
    long_used: bool = False
    short_used: bool = False
    was_above_range: bool = False
    was_below_range: bool = False
    position: Position | None = None


@dataclass(frozen=True)
class BarResult:
    state: OrbState
    entry: EntrySignal | None = None
    exit: ExitSignal | None = None
    skip: SkipInfo | None = None


def _fresh_day(day: date) -> OrbState:
    return OrbState(current_day=day)


def process_bar(state: OrbState, bar: Bar, blocked_entry: bool, force_flat: bool,
                 max_stop_pt: float, allow_flip: bool = False) -> BarResult:
    """Обрабатывает один закрытый бар M15 и возвращает новое состояние + события.

    blocked_entry — вход запрещён календарём (ЦБ/CPI/клиринг/зона экспирации);
    force_flat    — открытая позиция должна быть немедленно закрыта (день ЦБ 13:00-15:30);
    max_stop_pt   — максимально допустимая ширина диапазона в пунктах;
    allow_flip    — разрешить разворот позиции противоположным свежим пробоем без
                    ожидания стопа/EOD (поведение эталонного Pine-скрипта). По
                    умолчанию False — противоположный сигнал при открытой позиции
                    игнорируется (исходная спецификация ORB: «позиция всегда одна»).

    Позиция за смену дня НЕ переносится (робот дневной, закрывается в 18:40) —
    если на новый день пришёл незакрытый Position, это ошибка вызывающего кода,
    здесь состояние просто сбрасывается «с чистого листа».
    """
    t = bar.dt.time()
    d = bar.dt.date()

    if state.current_day != d:
        state = _fresh_day(d)

    # 1. накопление диапазона
    if RANGE_START <= t < RANGE_END:
        rh = bar.high if state.range_high is None else max(state.range_high, bar.high)
        rl = bar.low if state.range_low is None else min(state.range_low, bar.low)
        state = replace(state, range_high=rh, range_low=rl)
        if t == RANGE_LAST_BAR:
            width = rh - rl
            state = replace(state, range_ready=True, range_blocked=(width > max_stop_pt))
        return BarResult(state)

    # 2. трекинг пробоя диапазона — обновляем КАЖДЫЙ бар после готовности диапазона,
    # независимо от того, есть ли открытая позиция. Иначе после стоп-аута внутри
    # удержания позиции флаг "уже были выше диапазона" не сбросится, и настоящий
    # повторный пробой не будет распознан как новое событие (edge-triggered вход).
    new_break_long = new_break_short = False
    if state.range_ready:
        above = bar.close > state.range_high
        below = bar.close < state.range_low
        new_break_long = above and not state.was_above_range
        new_break_short = below and not state.was_below_range
        state = replace(state, was_above_range=above, was_below_range=below)

    # 3. выходы по открытой позиции — приоритет выше новых входов
    if state.position is not None:
        pos = state.position
        hit_stop = (bar.low <= pos.stop_price) if pos.side == "long" else (bar.high >= pos.stop_price)
        exit_sig = None
        flip_entry = None
        if hit_stop:
            exit_sig = ExitSignal("stop", pos.stop_price)
        elif force_flat:
            exit_sig = ExitSignal("cbr_flat", bar.open)
        elif t >= ENTRY_END:
            exit_sig = ExitSignal("eod", bar.open)
        elif allow_flip and ENTRY_START <= t < ENTRY_END:
            # переворот позиции противоположным свежим пробоем — поведение
            # эталонного Pine-скрипта (strategy.entry реверсирует позицию);
            # по умолчанию выключено (спецификация ORB требует игнорировать
            # противоположный сигнал, пока позиция открыта — см. allow_flip=False)
            opp_side = "short" if pos.side == "long" else "long"
            opp_break = new_break_short if pos.side == "long" else new_break_long
            opp_used = state.short_used if opp_side == "short" else state.long_used
            if opp_break and not state.range_blocked and not opp_used and not blocked_entry:
                exit_sig = ExitSignal("flip", bar.close)
                stop = state.range_low if opp_side == "long" else state.range_high
                flip_entry = EntrySignal(opp_side, bar.dt, state.range_high, state.range_low, stop)
                if opp_side == "long":
                    state = replace(state, long_used=True)
                else:
                    state = replace(state, short_used=True)
        if exit_sig is not None:
            state = replace(state, position=None)
        return BarResult(state, entry=flip_entry, exit=exit_sig)

    # 4. входы — только по свежему пробою (edge-triggered), позиции нет
    entry_sig = None
    skip_info = None
    if state.range_ready and ENTRY_START <= t < ENTRY_END and (new_break_long or new_break_short):
        side = "long" if new_break_long else "short"
        used = state.long_used if side == "long" else state.short_used
        if state.range_blocked:
            skip_info = SkipInfo(side, "range_too_wide")
        elif used:
            skip_info = SkipInfo(side, "already_traded")
        elif blocked_entry:
            skip_info = SkipInfo(side, "blocked")
        else:
            stop = state.range_low if side == "long" else state.range_high
            entry_sig = EntrySignal(side, bar.dt, state.range_high, state.range_low, stop)
            if side == "long":
                state = replace(state, long_used=True)
            else:
                state = replace(state, short_used=True)

    return BarResult(state, entry=entry_sig, skip=skip_info)


def open_position(state: OrbState, entry: EntrySignal, entry_price: float) -> OrbState:
    """Фиксирует в состоянии открытую позицию после того, как вызывающий код
    (риск-модуль/брокер) подтвердил размер и фактически выставил вход."""
    pos = Position(side=entry.side, entry_time=entry.signal_bar_dt, entry_price=entry_price,
                   stop_price=entry.stop_price, range_high=entry.range_high, range_low=entry.range_low)
    return replace(state, position=pos)
