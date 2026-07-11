"""
backtest_filter_demo.py — A/B/C-прогон бэктеста «3+1» с/без календарного фильтра.

Сравнивает три варианта:
  - none — без календарного фильтра;
  - soft — мягкий фильтр (is_entry_blocked(hard_only=False): CBR_HOT, CPI_WINDOW, CLEARING);
  - hard — только жёсткий фильтр (is_entry_blocked(hard_only=True): CBR_HOT).

Во всех трёх вариантах из результатов исключаются сделки, чей PnL пересекает
гэп склейки контрактов (splice_guard.mark_splice_gaps) — это фиктивное движение,
а не эдж стратегии.

Вход: CSV с колонками datetime,open,high,low,close,volume (M5).
Сигнальная функция signal_func — ЗАГЛУШКА, замени на свою логику «3+1».
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

import pandas as pd

from event_calendar import is_entry_blocked
from splice_guard import mark_splice_gaps, trade_crosses_gap

STOP_POINTS = 200
TAKE_POINTS = 400
MAX_HOLD_BARS = 48  # ~4 часа на M5 — предохранитель от вечно висящих сделок


def signal_func(df: pd.DataFrame) -> pd.Series:
    """ЗАГЛУШКА сигнальной функции — подключи сюда свою логику паттерна «3+1».

    Должна вернуть pd.Series той же длины/индекса, что df, со значениями:
      1  — сигнал на покупку (long),
     -1  — сигнал на продажу (short),
      0  — сигнала нет.

    Ниже — условный плейсхолдер (пробой хая/лоу последних 3 баров), только
    чтобы демо давало не пустой набор сделок. Для реальной стратегии замени
    целиком.
    """
    high3 = df["high"].shift(1).rolling(3).max()
    low3 = df["low"].shift(1).rolling(3).min()
    signal = pd.Series(0, index=df.index, dtype="int8")
    signal[df["close"] > high3] = 1
    signal[df["close"] < low3] = -1
    return signal


@dataclass
class Trade:
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    side: int
    pnl_points: float


def _simulate_trade(df: pd.DataFrame, entry_i: int, side: int) -> Trade | None:
    entry_price = df["close"].iloc[entry_i]
    stop = entry_price - side * STOP_POINTS
    take = entry_price + side * TAKE_POINTS
    last_i = min(entry_i + MAX_HOLD_BARS, len(df) - 1)

    for i in range(entry_i + 1, last_i + 1):
        bar = df.iloc[i]
        hit_stop = bar["low"] <= stop if side == 1 else bar["high"] >= stop
        hit_take = bar["high"] >= take if side == 1 else bar["low"] <= take
        if hit_stop and hit_take:
            # оба уровня внутри одного бара — консервативно считаем стоп первым
            return Trade(df.index[entry_i], df.index[i], side, -STOP_POINTS)
        if hit_stop:
            return Trade(df.index[entry_i], df.index[i], side, -STOP_POINTS)
        if hit_take:
            return Trade(df.index[entry_i], df.index[i], side, TAKE_POINTS)

    exit_i = last_i
    exit_price = df["close"].iloc[exit_i]
    pnl = side * (exit_price - entry_price)
    return Trade(df.index[entry_i], df.index[exit_i], side, pnl)


def run_backtest(df: pd.DataFrame, signal: pd.Series, mode: str) -> list[Trade]:
    """mode: 'none' | 'soft' | 'hard'."""
    trades: list[Trade] = []
    i = 0
    n = len(df)
    while i < n:
        side = signal.iloc[i]
        if side == 0:
            i += 1
            continue

        dt = df.index[i].to_pydatetime()
        blocked = False
        if mode == "soft":
            blocked = is_entry_blocked(dt, hard_only=False)
        elif mode == "hard":
            blocked = is_entry_blocked(dt, hard_only=True)

        if blocked:
            i += 1
            continue

        trade = _simulate_trade(df, i, int(side))
        if trade is None:
            i += 1
            continue

        if not trade_crosses_gap(df, trade.entry_time, trade.exit_time):
            trades.append(trade)

        i = df.index.get_loc(trade.exit_time) + 1

    return trades


def compute_metrics(trades: list[Trade]) -> dict:
    n = len(trades)
    if n == 0:
        return {"trades": 0, "winrate": 0.0, "avg_pnl": 0.0, "profit_factor": float("nan"), "max_dd": 0.0}

    pnls = [t.pnl_points for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    winrate = len(wins) / n * 100
    avg_pnl = sum(pnls) / n
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pnls:
        equity += p
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)

    return {
        "trades": n,
        "winrate": winrate,
        "avg_pnl": avg_pnl,
        "profit_factor": profit_factor,
        "max_dd": max_dd,
    }


def print_comparison(results: dict[str, dict]) -> None:
    headers = ["вариант", "сделок", "winrate %", "avg PnL, пт", "PF", "max DD, пт"]
    rows = []
    for mode, m in results.items():
        rows.append([
            mode,
            str(m["trades"]),
            f"{m['winrate']:.1f}",
            f"{m['avg_pnl']:.1f}",
            f"{m['profit_factor']:.2f}" if m["profit_factor"] not in (float("inf"),) else "inf",
            f"{m['max_dd']:.1f}",
        ])

    widths = [max(len(h), *(len(r[i]) for r in rows)) for i, h in enumerate(headers)]
    line = "  ".join(h.ljust(w) for h, w in zip(headers, widths))
    print(line)
    print("-" * len(line))
    for r in rows:
        print("  ".join(c.ljust(w) for c, w in zip(r, widths)))


def main(csv_path: str) -> None:
    df = pd.read_csv(csv_path, parse_dates=["datetime"], index_col="datetime")
    df = mark_splice_gaps(df)
    signal = signal_func(df)

    results = {}
    for mode in ("none", "soft", "hard"):
        trades = run_backtest(df, signal, mode)
        results[mode] = compute_metrics(trades)

    print_comparison(results)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Использование: python backtest_filter_demo.py <path_to_m5.csv>")
        raise SystemExit(1)
    main(sys.argv[1])
