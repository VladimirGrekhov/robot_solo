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
