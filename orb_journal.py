"""
orb_journal.py — журнал сделок и пропущенных сигналов ORB (CSV) + дневная сводка.

Каждая сделка (paper и live одинаково) — строка в trades.csv. Каждый пропуск
сигнала (blocked_cbr/blocked_cpi/blocked_clearing/blocked_expiration_adj/
range_too_wide/already_traded/daily_limit/halted/kill_switch) — строка в skips.csv.
"""

from __future__ import annotations

import csv
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

TRADE_FIELDS = ["datetime_in", "datetime_out", "dir", "qty", "entry", "exit", "stop",
                "pnl_pt", "pnl_rub", "exit_reason", "range_width_pt", "event_flags"]
SKIP_FIELDS = ["datetime", "side", "reason"]


@dataclass(frozen=True)
class TradeRecord:
    datetime_in: datetime
    datetime_out: datetime
    dir: str              # "long" | "short"
    qty: int
    entry: float
    exit: float
    stop: float
    pnl_pt: float
    pnl_rub: float
    exit_reason: str       # "stop" | "eod" | "cbr_flat" | "kill"
    range_width_pt: float
    event_flags: str = ""  # человекочитаемый список сработавших календарных флагов

    def as_row(self) -> dict:
        d = asdict(self)
        d["datetime_in"] = self.datetime_in.isoformat(sep=" ")
        d["datetime_out"] = self.datetime_out.isoformat(sep=" ")
        return d


def _append_row(path: Path, fieldnames: list[str], row: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not path.exists() or path.stat().st_size == 0
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        if is_new:
            w.writeheader()
        w.writerow(row)


def append_trade(path: Path, trade: TradeRecord) -> None:
    _append_row(path, TRADE_FIELDS, trade.as_row())


def append_skip(path: Path, dt: datetime, side: str, reason: str) -> None:
    """reason — один из: blocked_cbr, blocked_cpi, blocked_clearing,
    blocked_expiration_adj, range_too_wide, already_traded, daily_limit, halted, kill_switch."""
    _append_row(path, SKIP_FIELDS, {"datetime": dt.isoformat(sep=" "), "side": side, "reason": reason})


def daily_summary(trades: list[TradeRecord], skip_reasons: list[str]) -> dict:
    """Сводка: число сделок, PnL, winrate, профит-фактор, счётчики пропусков по причине."""
    n = len(trades)
    wins = [t for t in trades if t.pnl_rub > 0]
    losses = [t for t in trades if t.pnl_rub <= 0]
    gross_profit = sum(t.pnl_rub for t in wins)
    gross_loss = abs(sum(t.pnl_rub for t in losses))
    sum_pnl = sum(t.pnl_rub for t in trades)
    return {
        "trades": n,
        "wins": len(wins),
        "losses": len(losses),
        "winrate": (len(wins) / n * 100) if n else 0.0,
        "sum_pnl_rub": sum_pnl,
        "avg_pnl_rub": (sum_pnl / n) if n else 0.0,
        "profit_factor": (gross_profit / gross_loss) if gross_loss > 0 else float("inf") if gross_profit > 0 else float("nan"),
        "skip_counts": dict(Counter(skip_reasons)),
    }


def summary_lines(summary: dict) -> list[str]:
    """Готовые строки сводки для лога/консоли (кириллица)."""
    if summary["trades"] == 0 and not summary["skip_counts"]:
        return ["сделок и пропусков нет"]
    lines = [
        f"сделок: {summary['trades']} · побед {summary['wins']} · убытков {summary['losses']} "
        f"· winrate {summary['winrate']:.0f}%",
        f"PnL: {summary['sum_pnl_rub']:+.0f} руб · среднее на сделку {summary['avg_pnl_rub']:+.0f} руб "
        f"· профит-фактор {summary['profit_factor']:.2f}" if summary["trades"] else "сделок не было",
    ]
    if summary["skip_counts"]:
        parts = ", ".join(f"{reason}={cnt}" for reason, cnt in sorted(summary["skip_counts"].items()))
        lines.append(f"пропуски сигналов: {parts}")
    return lines
