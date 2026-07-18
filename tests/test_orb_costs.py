"""Пункт 1: live/paper PnL должен учитывать издержки (слиппедж на вход/выход +
комиссия на обе стороны), как в бэктесте, чтобы журнал не завышал результат."""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import orb_robot
import orb_strategy as S


def make_orch(tmp_path, commission=5.0, slippage_ticks=2):
    cfg = {
        "class_code": "SPBFUT", "account": "", "chart_tag": "si15m", "tick_size": 1.0,
        "deposit_rub": 100000.0, "kill_switch_dir": str(tmp_path),
        "risk": {"risk_per_trade": 0.03, "go_fraction": 0.3},
        "backtest": {"commission_per_side_rub": commission, "slippage_ticks": slippage_ticks},
        "chart_guard": {"enabled": False},
        "paths": {"risk_state_json": str(tmp_path / "r.json"),
                  "trades_csv": str(tmp_path / "t.csv"), "skips_csv": str(tmp_path / "s.csv")},
    }
    return orb_robot.OrbOrchestrator(cfg, qp=None, live=False)


def _meta(side, entry, qty=2):
    return {"side": side, "entry_time": datetime(2026, 7, 16, 11, 15), "entry_price": entry,
            "stop_price": 79000.0, "qty": qty, "rub_per_point": 1.0, "range_width": 500.0}


def test_long_pnl_includes_costs(tmp_path):
    orch = make_orch(tmp_path)                       # slip=2, комиссия 5/сторона
    orch.open_meta = _meta("long", 80000.0)
    tr = orch._close_trade(S.Bar(datetime(2026, 7, 16, 18, 45), 1, 1, 1, 1),
                           S.ExitSignal("eod", 80300.0))
    # entry_eff 80002, exit_eff 80298 -> pnl_pt 296; комиссия 5*2*2=20; pnl 296*2-20=572
    assert tr.pnl_pt == 296.0
    assert tr.pnl_rub == 572.0
    assert tr.entry == 80002.0 and tr.exit == 80298.0


def test_short_pnl_includes_costs(tmp_path):
    orch = make_orch(tmp_path)
    orch.open_meta = _meta("short", 80000.0)
    tr = orch._close_trade(S.Bar(datetime(2026, 7, 16, 18, 45), 1, 1, 1, 1),
                           S.ExitSignal("eod", 79700.0))
    # entry_eff 79998, exit_eff 79702 -> pnl_pt 296; pnl 296*2-20=572
    assert tr.pnl_pt == 296.0
    assert tr.pnl_rub == 572.0


def test_zero_costs_config(tmp_path):
    orch = make_orch(tmp_path, commission=0.0, slippage_ticks=0)
    orch.open_meta = _meta("long", 80000.0)
    tr = orch._close_trade(S.Bar(datetime(2026, 7, 16, 18, 45), 1, 1, 1, 1),
                           S.ExitSignal("eod", 80300.0))
    assert tr.pnl_pt == 300.0
    assert tr.pnl_rub == 600.0                        # без издержек — как раньше
