"""Тесты сайзинга (пункт №4): защита от мусорных чтений ГО/rpp из QUIK и выбор
капитала (deposit_rub vs живой equity). QUIK эмулируется."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import orb_robot


class FakeQuik:
    def __init__(self, go=13000.0, step=1.0, equity=None):
        self.go = go
        self.step = step
        self.equity = equity
        self.accounts = [{"firm_id": "F", "trade_account_id": "ACC", "futures": True}]

    def get_param_ex(self, cls, sec, param):
        if param == "STEPPRICE":
            return {"data": {"result": "1", "param_value": str(self.step)}}
        if param in ("BUYDEPO", "SELLDEPO"):
            if self.go is None:
                return {"data": {"result": "0"}}
            return {"data": {"result": "1", "param_value": str(self.go)}}
        return {"data": {"result": "0"}}

    def get_futures_limit(self, firm, acc, lt, curr):
        if self.equity is None:
            return {"data": {}}
        return {"data": {"cbplimit": self.equity, "varmargin": 0.0,
                         "cbplplanned": 0.0, "cbplused": 0.0, "currcode": "SUR"}}


def make_orch(tmp_path, qp, sizing=None):
    cfg = {
        "class_code": "SPBFUT", "account": "TEST", "client_code": "", "chart_tag": "si15m",
        "tick_size": 1.0, "deposit_rub": 300000.0,
        "paths": {"risk_state_json": str(tmp_path / "r.json"),
                  "trades_csv": str(tmp_path / "t.csv"), "skips_csv": str(tmp_path / "s.csv")},
        "risk": {"risk_per_trade": 0.01, "go_fraction": 0.3},
        "backtest": {"go_per_contract_assumed": 12000.0},
        "kill_switch_dir": str(tmp_path), "sizing": sizing or {},
    }
    return orb_robot.OrbOrchestrator(cfg, qp, live=False)


def test_go_valid_used_as_is(tmp_path):
    orch = make_orch(tmp_path, FakeQuik(go=13500.0))
    rpp, go = orch._sizing_inputs("SiU6")
    assert go == 13500.0
    assert rpp == 1.0


def test_go_missing_falls_back(tmp_path):
    orch = make_orch(tmp_path, FakeQuik(go=None))
    _, go = orch._sizing_inputs("SiU6")
    assert go == 12000.0                       # go_per_contract_assumed


def test_go_below_min_falls_back(tmp_path):
    orch = make_orch(tmp_path, FakeQuik(go=500.0), sizing={"go_min_rub": 3000})
    _, go = orch._sizing_inputs("SiU6")
    assert go == 12000.0                       # 500 < 3000 -> мусор -> фолбэк


def test_go_above_max_falls_back(tmp_path):
    orch = make_orch(tmp_path, FakeQuik(go=500000.0), sizing={"go_max_rub": 100000})
    _, go = orch._sizing_inputs("SiU6")
    assert go == 12000.0                       # 500000 > 100000 -> фолбэк


def test_rpp_nonpositive_defaults_one(tmp_path):
    orch = make_orch(tmp_path, FakeQuik(step=0.0))
    rpp, _ = orch._sizing_inputs("SiU6")
    assert rpp == 1.0


def test_basis_deposit_by_default(tmp_path):
    orch = make_orch(tmp_path, FakeQuik(equity=450000.0))
    assert orch._sizing_basis() == 300000.0    # from_live_equity=false -> deposit_rub


def test_basis_live_equity_when_enabled(tmp_path):
    orch = make_orch(tmp_path, FakeQuik(equity=450000.0), sizing={"from_live_equity": True})
    assert orch._sizing_basis() == 450000.0


def test_basis_falls_back_when_equity_tiny(tmp_path):
    orch = make_orch(tmp_path, FakeQuik(equity=500.0),
                     sizing={"from_live_equity": True, "equity_min_rub": 10000})
    assert orch._sizing_basis() == 300000.0    # 500 < 10000 -> deposit_rub


def test_basis_falls_back_when_unreadable(tmp_path):
    orch = make_orch(tmp_path, FakeQuik(equity=None), sizing={"from_live_equity": True})
    assert orch._sizing_basis() == 300000.0
