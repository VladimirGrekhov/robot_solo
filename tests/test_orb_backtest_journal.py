import csv
from datetime import date, datetime

import orb_backtest as bt
import orb_journal as j


def _trade(pnl=100.0):
    return j.TradeRecord(datetime(2026, 3, 11, 11, 0), datetime(2026, 3, 11, 12, 0),
                          "long", 2, 100.0, 150.0, 99.0, 50.0, pnl, "eod", 6.0, "")


def test_write_backtest_trades_overwrites(tmp_path):
    path = tmp_path / "bt_trades.csv"
    j.write_backtest_trades(path, [_trade(), _trade(), _trade()])
    with open(path, encoding="utf-8") as f:
        assert len(list(csv.DictReader(f))) == 3

    # повторная запись — файл содержит ТОЛЬКО последний прогон
    j.write_backtest_trades(path, [_trade()])
    with open(path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    assert rows[0]["dir"] == "long"


def test_append_backtest_run_accumulates(tmp_path):
    path = tmp_path / "bt_runs.csv"
    summary = j.daily_summary([_trade(100), _trade(-50)], ["range_too_wide", "blocked_cbr"])
    params = {"deposit_rub": 300000.0, "commission_per_side_rub": 5.0, "slippage_ticks": 2,
              "risk_per_trade": 0.01, "go_fraction": 0.3, "max_stop_pt": 600.0,
              "daily_loss_limit": 0.02, "weekly_halt_limit": 0.05,
              "allow_position_flip": False, "expiration_zone_mode": "trading_days"}
    j.append_backtest_run(path, "moex_iss", params, summary, "2025-07-11", "2026-07-10", 15462)
    j.append_backtest_run(path, "quik_chart", params, summary, "2026-06-01", "2026-07-10", 900)

    with open(path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 2
    assert rows[0]["source"] == "moex_iss"
    assert rows[1]["source"] == "quik_chart"
    assert rows[0]["trades"] == "2"
    assert rows[0]["winrate"] == "50.0"
    assert "range_too_wide=1" in rows[0]["skip_counts"]
    assert rows[1]["bars"] == "900"


def test_daily_summary_exposes_gross_sums():
    s = j.daily_summary([_trade(100), _trade(200), _trade(-50)], [])
    assert s["gross_profit_rub"] == 300
    assert s["gross_loss_rub"] == 50
    assert abs(s["profit_factor"] - 6.0) < 1e-9


def test_period_summary_monthly_and_averages():
    trades = [
        j.TradeRecord(datetime(2026, 1, 10, 11, 0), datetime(2026, 1, 10, 12, 0),
                       "long", 1, 100, 200, 90, 100, 1000.0, "eod", 5.0, ""),
        j.TradeRecord(datetime(2026, 1, 20, 11, 0), datetime(2026, 1, 20, 12, 0),
                       "long", 1, 100, 150, 90, 50, 500.0, "eod", 5.0, ""),
        j.TradeRecord(datetime(2026, 3, 5, 11, 0), datetime(2026, 3, 5, 12, 0),
                       "short", 1, 100, 130, 110, -30, -300.0, "stop", 5.0, ""),
    ]
    ps = j.period_summary(trades, deposit_rub=100_000.0,
                           date_from=date(2026, 1, 1), date_till=date(2026, 3, 31))
    assert ps["total_pnl_rub"] == 1200.0
    assert abs(ps["total_pct"] - 1.2) < 1e-9            # 1200 / 100000 * 100
    assert 2.9 < ps["period_months"] < 3.1              # ~3 календарных месяца
    assert abs(ps["avg_month_rub"] - 1200.0 / ps["period_months"]) < 1e-9
    # помесячная разбивка: январь двумя сделками, февраль отсутствует, март убыточный
    assert ps["monthly"] == [
        ("2026-01", 1500.0, 1.5),
        ("2026-03", -300.0, -0.3),
    ]


def test_period_lines_render():
    s = j.daily_summary([_trade(100), _trade(-50)], [])
    ps = j.period_summary([_trade(100), _trade(-50)], 100_000.0,
                           date(2026, 3, 1), date(2026, 3, 31))
    lines = j.period_lines(s, ps)
    assert any("сумма прибыльных" in line for line in lines)
    assert any("в среднем за месяц" in line for line in lines)
    assert any("2026-03" in line for line in lines)


def test_append_backtest_run_rotates_old_header(tmp_path):
    path = tmp_path / "runs.csv"
    path.write_text("old,columns\n1,2\n", encoding="utf-8")
    summary = j.daily_summary([_trade()], [])
    j.append_backtest_run(path, "moex_iss", {}, summary, "2026-01-01", "2026-01-31", 100)
    # старый файл отложен в .bak, новый начат с правильным заголовком
    assert (tmp_path / "runs.csv.bak").is_file()
    with open(path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    assert rows[0]["source"] == "moex_iss"


def test_save_result_writes_both_files(tmp_path):
    cfg = bt.BacktestConfig(date_from=date(2026, 3, 11), date_till=date(2026, 3, 11),
                             cache_dir=tmp_path)
    res = bt.BacktestResult(trades=[_trade()], skip_reasons=["blocked_cbr"],
                             summary=j.daily_summary([_trade()], ["blocked_cbr"]))
    trades_path = tmp_path / "t.csv"
    runs_path = tmp_path / "r.csv"
    bt.save_result(cfg, res, source="quik_chart", trades_path=trades_path,
                    runs_path=runs_path, bars_count=42)

    assert trades_path.is_file() and runs_path.is_file()
    with open(runs_path, encoding="utf-8") as f:
        row = list(csv.DictReader(f))[0]
    assert row["source"] == "quik_chart"
    assert row["bars"] == "42"
    assert row["max_stop_pt"] == "600.0"
    assert row["expiration_zone_mode"] == "trading_days"
