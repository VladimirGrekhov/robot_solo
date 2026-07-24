"""
orb_calendar.py — календарные правила для стратегии ORB поверх event_calendar.py.

В отличие от event_calendar.is_entry_blocked (общий фильтр для «3+1»), у ORB
свои правила блокировки входа и своя логика ролловера контракта:
  - вход блокируется в день ЦБ 13:00-15:30, в среду 18:45-19:30, в клиринг,
    И дополнительно в зоне вокруг экспирации (для «3+1» эта зона была только
    информационной, для ORB — блокирующей, см. спецификацию ORB);
  - открытая позиция принудительно закрывается ТОЛЬКО жёстким блоком (день ЦБ,
    13:00-15:30) — это единственный жёсткий блок в текущей спецификации;
  - активный квартальный контракт определяется с ролловером за 2 торговых дня
    до экспирации (раньше, чем реальная дата экспирации) — это НЕ зависит от
    expiration_zone_mode ниже (ролловер контракта всегда по торговым дням).

expiration_zone_mode ("trading_days" | "calendar_days") — ширина зоны блокировки
входа вокруг экспирации:
  - "trading_days" (по умолчанию) — честные +/-2 ТОРГОВЫХ дня (event_calendar.
    EventFlag.EXPIRATION_ADJ); поскольку экспирация всегда в четверг, вперёд
    это захватывает и следующий понедельник (выходные не считаются торговыми);
  - "calendar_days" — как в эталонном Pine-скрипте (si_strategy_lab.pine):
    наивные +/-2 КАЛЕНДАРНЫХ дня по номеру дня месяца (abs(d.day-exp.day)<=2),
    без расширения через выходные — зона на 1 торговый день ýже.
"""

from __future__ import annotations

from datetime import date, datetime

from event_calendar import EventFlag, MSK, event_status, expiration_dates, trading_day_offset

_ROLLOVER_TRADING_DAYS = 2
_CALENDAR_ZONE_DAYS = 2

_MONTH_CODE = {3: "H", 6: "M", 9: "U", 12: "Z"}


def _msk_date(dt: datetime) -> date:
    if dt.tzinfo is None:
        return dt.date()
    return dt.astimezone(MSK).date()


def _is_in_calendar_day_zone(d: date, n: int = _CALENDAR_ZONE_DAYS) -> bool:
    """Наивная зона +/- n КАЛЕНДАРНЫХ дней вокруг экспирации — как в эталонном
    Pine-скрипте (abs(d - expDay) по номеру дня месяца, без учёта выходных)."""
    for exp in expiration_dates(d.year):
        if exp.month == d.month and abs(d.day - exp.day) <= n:
            return True
    return False


def entry_gate(dt: datetime, expiration_zone_mode: str = "trading_days") -> tuple[bool, str | None]:
    """(blocked, reason) — блокировка нового входа по календарю (без риск-модуля).

    reason — один из: blocked_cbr, blocked_cpi, blocked_clearing,
    blocked_expiration_adj; None, если вход не блокирован календарём."""
    flags = event_status(dt)
    if flags & EventFlag.CBR_HOT:
        return True, "blocked_cbr"
    if flags & EventFlag.CPI_WINDOW:
        return True, "blocked_cpi"
    if flags & EventFlag.CLEARING:
        return True, "blocked_clearing"

    if expiration_zone_mode in ("off", "none", "always"):
        in_zone = False                       # зона экспирации отключена — торгуем сквозь неё
    elif expiration_zone_mode == "calendar_days":
        in_zone = _is_in_calendar_day_zone(_msk_date(dt))
    else:
        in_zone = bool(flags & EventFlag.EXPIRATION_ADJ)
    if in_zone:
        return True, "blocked_expiration_adj"
    return False, None


def force_flat_gate(dt: datetime) -> bool:
    """True — открытая позиция должна быть немедленно закрыта по рынку.

    Единственный жёсткий блок в спецификации ORB — день ЦБ, окно 13:00-15:30."""
    return bool(event_status(dt) & EventFlag.CBR_HOT)


def contract_ticker(expiration: date) -> str:
    """Тикер квартального Si по дате его экспирации, например SiU6 для 2026-09-17."""
    return f"Si{_MONTH_CODE[expiration.month]}{expiration.year % 10}"


def active_contract(d: date) -> str:
    """Тикер квартального контракта, торгуемого на дату d.

    Ролловер — за _ROLLOVER_TRADING_DAYS торговых дней ДО экспирации (раньше
    реальной даты экспирации): начиная с даты ролловера следующий контракт уже
    считается «активным», текущий больше не торгуется роботом."""
    candidates = sorted({
        exp
        for year in (d.year - 1, d.year, d.year + 1)
        for exp in expiration_dates(year)
    })
    for exp in candidates:
        rollover = trading_day_offset(exp, -_ROLLOVER_TRADING_DAYS)
        if d < rollover:
            return contract_ticker(exp)
    return contract_ticker(candidates[-1])


def rollover_date(expiration: date) -> date:
    """Дата, с которой торговля переходит на СЛЕДУЮЩИЙ контракт (2 торговых дня до exp)."""
    return trading_day_offset(expiration, -_ROLLOVER_TRADING_DAYS)
