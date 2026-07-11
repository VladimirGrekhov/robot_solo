from datetime import date, datetime

import orb_backtest as bt
import orb_strategy as s


def bar(day, h, mi, o, hi, lo, c):
    return s.Bar(datetime(2026, 3, day, h, mi), o, hi, lo, c)


def range_bars(day, rh=105.0, rl=99.0):
    return [
        bar(day, 10, 0, 100, rh - 3, rl + 3, 101),
        bar(day, 10, 15, 101, rh - 1, rl + 1, 102),
        bar(day, 10, 30, 102, rh, rl + 2, 103),
        bar(day, 10, 45, 103, rh - 2, rl, 104),
    ]


def test_contract_segments_cover_period_without_gaps():
    segs = bt.contract_segments(date(2025, 7, 11), date(2026, 7, 10))
    assert segs[0][1] == date(2025, 7, 11)
    assert segs[-1][2] == date(2026, 7, 10)
    for (_, _, end), (_, start2, _) in zip(segs, segs[1:]):
        assert (start2 - end).days == 1


def test_backtest_trade_fill_slippage_and_commission():
    bars = range_bars(11) + [
        bar(11, 11, 0, 106, 108, 105, 107),   # пробой вверх, сигнал
        bar(11, 11, 15, 100, 100, 95, 96),    # исполнение по open=100 (+slip), стоп задет в этом же баре
        bar(11, 18, 45, 96, 97, 95, 96),
    ]
    cfg = bt.BacktestConfig(date_from=None, date_till=None, cache_dir=None)
    res = bt.run(cfg, bars=bars)

    assert len(res.trades) == 1
    t = res.trades[0]
    assert t.dir == "long"
    assert t.entry == 100 + cfg.slippage_ticks * cfg.tick_size          # 102.0
    assert t.exit == t.stop - cfg.slippage_ticks * cfg.tick_size        # 99 - 2 = 97
    expected_qty = t.qty  # проверяем внутреннюю согласованность, не пересчитываем risk здесь
    expected_pnl_pt = t.exit - t.entry
    expected_commission = cfg.commission_per_side_rub * expected_qty * 2
    expected_pnl_rub = expected_pnl_pt * cfg.rub_per_point * expected_qty - expected_commission
    assert abs(t.pnl_rub - expected_pnl_rub) < 1e-9


def test_backtest_eod_exit_uses_bar_open_after_1840():
    bars = range_bars(11) + [
        bar(11, 11, 0, 106, 108, 105, 107),
        bar(11, 11, 15, 107, 120, 106, 118),   # держим позицию (стоп 99 не задет), далеко от стопа
        bar(11, 18, 30, 118, 119, 117, 118),
        bar(11, 18, 45, 115, 116, 114, 115),   # первый бар >= 18:40 -> принудительный выход по open=115
    ]
    cfg = bt.BacktestConfig(date_from=None, date_till=None, cache_dir=None)
    res = bt.run(cfg, bars=bars)
    assert len(res.trades) == 1
    assert res.trades[0].exit_reason == "eod"


def test_backtest_daily_loss_limit_blocks_further_entries_same_day():
    # порог дневного убытка ниже любой возможной сделки — гарантированно выбивается первой же
    small_risk = bt.orb_risk.RiskConfig(daily_loss_limit=0.00001)
    cfg = bt.BacktestConfig(date_from=None, date_till=None, cache_dir=None,
                             deposit_rub=300_000.0, risk=small_risk)

    bars = range_bars(11) + [
        bar(11, 11, 0, 106, 108, 105, 107),    # long
        bar(11, 11, 15, 100, 100, 95, 96),     # стоп -> убыток, дневной лимит будет выбит
        bar(11, 12, 0, 100, 104, 100, 102),    # возврат внутрь диапазона
        bar(11, 12, 15, 97, 98, 90, 91),       # новый пробой вниз в тот же день
        bar(11, 18, 45, 91, 92, 90, 91),
    ]
    res = bt.run(cfg, bars=bars)
    assert len(res.trades) == 1  # вторая сделка не открылась
    assert "daily_limit" in res.skip_reasons
