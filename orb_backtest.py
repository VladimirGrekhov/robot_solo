"""
orb_backtest.py — движок бэктеста ORB на истории MOEX ISS.

Склейка контрактов — без искусственной непрерывной серии и бэк-аджастмента:
для каждого календарного дня берётся РЕАЛЬНЫЙ OHLC того квартального контракта,
который в этот день по правилу ролловера (orb_calendar.active_contract) считается
активным. Стыки между контрактами не создают фиктивных гэпов (в отличие от
классической спличенной серии), а зона +/-2 торговых дня от экспирации и так
не даёт открывать новые позиции (orb_calendar.entry_gate), плюс позиция всегда
закрывается в тот же день (EOD) — риска «дыры» на стыке нет.

Упрощения бэктеста (осознанные, см. README):
  - размер депозита ФИКСИРОВАН на весь период (без сложного процента на equity);
  - ГО на контракт — постоянное ПРЕДПОЛОЖЕНИЕ из конфига (в реальности плавает
    и зависит от брокера); для paper/live вместо этого читается живое ГО из QUIK;
  - комиссия и проскальзывание — по конфигу, применяются на обе стороны сделки.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import orb_calendar
import orb_data_moex
import orb_journal
import orb_risk
import orb_strategy


@dataclass(frozen=True)
class BacktestConfig:
    date_from: date
    date_till: date
    cache_dir: Path
    deposit_rub: float = 300_000.0
    rub_per_point: float = 1.0          # Si: 1 пункт = 1 ₽ (фикс.)
    go_per_contract_assumed: float = 12_000.0
    commission_per_side_rub: float = 5.0
    slippage_ticks: int = 2
    tick_size: float = 1.0
    risk: orb_risk.RiskConfig = orb_risk.RiskConfig()


@dataclass(frozen=True)
class BacktestResult:
    trades: list[orb_journal.TradeRecord]
    skip_reasons: list[str]
    summary: dict


def contract_segments(date_from: date, date_till: date) -> list[tuple[str, date, date]]:
    """(тикер, начало, конец) — непересекающиеся отрезки активности контрактов."""
    segments: list[tuple[str, date, date]] = []
    cur_ticker = orb_calendar.active_contract(date_from)
    cur_start = date_from
    d = date_from
    while d <= date_till:
        t = orb_calendar.active_contract(d)
        if t != cur_ticker:
            segments.append((cur_ticker, cur_start, d - timedelta(days=1)))
            cur_ticker = t
            cur_start = d
        d += timedelta(days=1)
    segments.append((cur_ticker, cur_start, date_till))
    return segments


def load_bars(cfg: BacktestConfig) -> list[orb_strategy.Bar]:
    """Грузит и хронологически склеивает M15-бары всех активных за период контрактов."""
    bars: list[orb_strategy.Bar] = []
    for ticker, seg_start, seg_end in contract_segments(cfg.date_from, cfg.date_till):
        df = orb_data_moex.load_contract_m15(ticker, seg_start, seg_end, cfg.cache_dir)
        for ts, row in df.iterrows():
            bars.append(orb_strategy.Bar(ts.to_pydatetime(), float(row["open"]),
                                          float(row["high"]), float(row["low"]), float(row["close"])))
    bars.sort(key=lambda b: b.dt)
    return bars


def run(cfg: BacktestConfig, bars: list[orb_strategy.Bar] | None = None) -> BacktestResult:
    """Прогоняет ORB по последовательности баров (загружает их сам, если не переданы)."""
    if bars is None:
        bars = load_bars(cfg)

    slippage_pt = cfg.slippage_ticks * cfg.tick_size
    state = orb_strategy.OrbState()
    risk_state = orb_risk.RiskState()
    trades: list[orb_journal.TradeRecord] = []
    skip_reasons: list[str] = []

    pending_entry: orb_strategy.EntrySignal | None = None
    open_meta: dict | None = None   # {"qty", "range_width"} для текущей открытой позиции

    for bar in bars:
        day = bar.dt.date()
        if risk_state.day != day:
            risk_state = orb_risk.sync_day(risk_state, day)

        # 1. исполнить отложенный вход по open текущего бара (сигнал был на предыдущем)
        if pending_entry is not None:
            entry = pending_entry
            pending_entry = None
            long = entry.side == "long"
            fill_price = bar.open + (slippage_pt if long else -slippage_pt)
            stop_points = abs(fill_price - entry.stop_price)
            qty = orb_risk.position_size(cfg.deposit_rub, stop_points, cfg.rub_per_point,
                                          cfg.go_per_contract_assumed, cfg.risk)
            if qty > 0:
                state = orb_strategy.open_position(state, entry, fill_price)
                open_meta = {
                    "side": entry.side, "entry_time": bar.dt, "entry_price": fill_price,
                    "stop_price": entry.stop_price, "qty": qty,
                    "range_width": entry.range_high - entry.range_low,
                }
            else:
                skip_reasons.append("zero_qty")

        # 2. календарные и риск-блокировки входа для ЭТОГО бара
        blocked, reason = orb_calendar.entry_gate(bar.dt)
        force_flat = orb_calendar.force_flat_gate(bar.dt)
        if not blocked:
            if risk_state.halted:
                blocked, reason = True, "halted"
            elif orb_risk.daily_limit_hit(risk_state, cfg.deposit_rub, cfg.risk):
                blocked, reason = True, "daily_limit"

        result = orb_strategy.process_bar(state, bar, blocked, force_flat, cfg.risk.max_stop_pt)
        state = result.state

        if result.exit is not None and open_meta is not None:
            trade = _close_trade(bar, result.exit, open_meta, cfg, slippage_pt)
            trades.append(trade)
            risk_state = orb_risk.record_trade_pnl(risk_state, day, trade.pnl_rub)
            open_meta = None

        if result.entry is not None:
            pending_entry = result.entry

        if result.skip is not None:
            # "blocked" — обобщённая причина из orb_strategy; конкретика (какой именно
            # календарный/риск-блок сработал) known только здесь, в оркестраторе
            specific = reason if result.skip.reason == "blocked" and reason else result.skip.reason
            skip_reasons.append(specific)

    summary = orb_journal.daily_summary(trades, skip_reasons)
    return BacktestResult(trades=trades, skip_reasons=skip_reasons, summary=summary)


def _close_trade(bar: orb_strategy.Bar, exit_sig: orb_strategy.ExitSignal, open_meta: dict,
                  cfg: BacktestConfig, slippage_pt: float) -> orb_journal.TradeRecord:
    side = open_meta["side"]
    long = side == "long"
    raw_exit = exit_sig.price
    exit_price = raw_exit - slippage_pt if long else raw_exit + slippage_pt
    entry_price = open_meta["entry_price"]
    qty = open_meta["qty"]
    pnl_pt = (exit_price - entry_price) if long else (entry_price - exit_price)
    commission = cfg.commission_per_side_rub * qty * 2
    pnl_rub = pnl_pt * cfg.rub_per_point * qty - commission
    return orb_journal.TradeRecord(
        datetime_in=open_meta["entry_time"], datetime_out=bar.dt, dir=side, qty=qty,
        entry=entry_price, exit=exit_price, stop=open_meta["stop_price"],
        pnl_pt=pnl_pt, pnl_rub=pnl_rub, exit_reason=exit_sig.reason,
        range_width_pt=open_meta["range_width"], event_flags="",
    )
