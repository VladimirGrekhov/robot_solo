"""
orb_risk.py — риск-модуль ORB: размер позиции и защитные лимиты.

Чистая логика (stdlib + math), кроме load/save_risk_state (JSON-файл, чтобы
дневной/недельный PnL и halt переживали перезапуск робота — это единственная
причина трогать диск в этом модуле).
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

STOP_FILE_NAME = "STOP"


@dataclass(frozen=True)
class RiskConfig:
    risk_per_trade: float = 0.01        # доля депозита, рискуемая на одну сделку
    go_fraction: float = 0.30           # доля депозита, допустимая на ГО одного контракта
    max_stop_pt: float = 600.0          # макс. ширина диапазона (пунктов) — иначе сигнал пропускается
    daily_loss_limit: float = 0.02      # дневной стоп-лосс (доля депозита)
    weekly_halt_limit: float = 0.05     # недельный аварийный лимит (доля депозита) -> halt


def position_size(deposit: float, stop_points: float, rub_per_point: float,
                   go_per_contract: float, cfg: RiskConfig) -> int:
    """Размер позиции = min(риск-лимит, ГО-лимит), округление вниз, не меньше 0.

    риск-лимит = floor(депозит * risk_per_trade / (стоп_в_пунктах * стоимость_шага))
    ГО-лимит    = floor(депозит * go_fraction / ГО_на_контракт)
    """
    if stop_points <= 0 or rub_per_point <= 0 or deposit <= 0:
        return 0
    risk_rub = deposit * cfg.risk_per_trade
    by_risk = math.floor(risk_rub / (stop_points * rub_per_point))
    by_go = math.floor(deposit * cfg.go_fraction / go_per_contract) if go_per_contract > 0 else 0
    return max(0, min(by_risk, by_go))


def range_too_wide(stop_points: float, cfg: RiskConfig) -> bool:
    """True, если ширина диапазона (= будущий стоп в пунктах) больше допустимой."""
    return stop_points > cfg.max_stop_pt


@dataclass(frozen=True)
class RiskState:
    day: date | None = None
    day_pnl_rub: float = 0.0
    week_key: tuple[int, int] | None = None    # (ISO год, ISO неделя)
    week_pnl_rub: float = 0.0
    halted: bool = False


def _week_key(d: date) -> tuple[int, int]:
    iso = d.isocalendar()
    return (iso[0], iso[1])


def record_trade_pnl(state: RiskState, trade_day: date, pnl_rub: float) -> RiskState:
    """Добавляет PnL закрытой сделки в дневной/недельный накопитель, со сбросом
    при смене дня/недели. halted, если уже выставлен, сбросом дня/недели НЕ снимается —
    сброс halt только вручную (см. reset_halt)."""
    day_pnl = pnl_rub if state.day != trade_day else state.day_pnl_rub + pnl_rub
    wk = _week_key(trade_day)
    week_pnl = pnl_rub if state.week_key != wk else state.week_pnl_rub + pnl_rub
    return RiskState(day=trade_day, day_pnl_rub=day_pnl, week_key=wk, week_pnl_rub=week_pnl,
                      halted=state.halted)


def sync_day(state: RiskState, today: date) -> RiskState:
    """Сбрасывает дневной (и, если нужно, недельный) накопитель при смене дня без сделок
    (например, первый вызов за новый день до первой закрытой сделки)."""
    day_pnl = state.day_pnl_rub if state.day == today else 0.0
    wk = _week_key(today)
    week_pnl = state.week_pnl_rub if state.week_key == wk else 0.0
    return RiskState(day=today, day_pnl_rub=day_pnl, week_key=wk, week_pnl_rub=week_pnl,
                      halted=state.halted)


def daily_limit_hit(state: RiskState, deposit: float, cfg: RiskConfig) -> bool:
    return state.day_pnl_rub <= -abs(deposit * cfg.daily_loss_limit)


def weekly_halt_triggered(state: RiskState, deposit: float, cfg: RiskConfig) -> bool:
    return state.week_pnl_rub <= -abs(deposit * cfg.weekly_halt_limit)


def apply_halt_check(state: RiskState, deposit: float, cfg: RiskConfig) -> RiskState:
    """Выставляет halted=True, если недельный убыток достиг аварийного лимита.
    Идемпотентно; снять halted можно только через reset_halt (ручной сброс)."""
    if not state.halted and weekly_halt_triggered(state, deposit, cfg):
        return RiskState(**{**asdict(state), "halted": True})
    return state


def reset_halt(state: RiskState) -> RiskState:
    """Ручной сброс аварийного halt (требуется явное действие оператора)."""
    return RiskState(**{**asdict(state), "halted": False})


def kill_switch_active(work_dir: Path, filename: str = STOP_FILE_NAME) -> bool:
    """True, если в рабочей директории лежит файл-флаг STOP."""
    return (Path(work_dir) / filename).exists()


def load_risk_state(path: Path) -> RiskState:
    path = Path(path)
    if not path.is_file():
        return RiskState()
    data = json.loads(path.read_text(encoding="utf-8"))
    day = date.fromisoformat(data["day"]) if data.get("day") else None
    week_key = tuple(data["week_key"]) if data.get("week_key") else None
    return RiskState(day=day, day_pnl_rub=data.get("day_pnl_rub", 0.0),
                      week_key=week_key, week_pnl_rub=data.get("week_pnl_rub", 0.0),
                      halted=data.get("halted", False))


def save_risk_state(path: Path, state: RiskState) -> None:
    path = Path(path)
    data = {
        "day": state.day.isoformat() if state.day else None,
        "day_pnl_rub": state.day_pnl_rub,
        "week_key": list(state.week_key) if state.week_key else None,
        "week_pnl_rub": state.week_pnl_rub,
        "halted": state.halted,
    }
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
