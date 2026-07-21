"""Тесты пре-флайта боевого старта (пункт №4): серия проверок перед live, любой fail
запрещает торговлю. QUIK эмулируется."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import orb_robot

EXPECTED = "SiU6"
TAG = "si15m"


class FakeQuik:
    def __init__(self, connected=1, has_account=True, chart_sec="SiU6",
                 go=12000.0, stepprice=1.0):
        self.connected = connected
        self.chart = chart_sec
        self.go = go
        self.stepprice = stepprice
        self.accounts = [{"firm_id": "F", "trade_account_id": "ACC", "futures": True}] if has_account else []

    def is_connected(self):
        return {"data": self.connected}

    def get_tag_seccode(self, tag):
        return {"data": self.chart} if self.chart else {"data": ""}

    def get_param_ex(self, cls, sec, param):
        if param in ("BUYDEPO", "SELLDEPO") and self.go:
            return {"data": {"result": "1", "param_value": str(self.go)}}
        if param == "STEPPRICE" and self.stepprice:
            return {"data": {"result": "1", "param_value": str(self.stepprice)}}
        return {"data": {"result": "0"}}


def cfg_for(tmp_path, account="TEST"):
    return {"class_code": "SPBFUT", "account": account, "chart_tag": TAG, "tick_size": 1.0,
            "kill_switch_dir": str(tmp_path)}


def levels(checks):
    return {name: level for name, level, _ in checks}


def test_all_ok_live(tmp_path):
    checks, ok = orb_robot.preflight_checks(cfg_for(tmp_path), FakeQuik(), EXPECTED, TAG, live=True)
    assert ok is True
    lv = levels(checks)
    assert lv["Связь с QUIK"] == "ok"
    assert lv["Фьючерсный счёт"] == "ok"
    assert lv["График↔контракт"] == "ok"
    assert lv["Kill-switch"] == "ok"


def test_connection_down_fails(tmp_path):
    checks, ok = orb_robot.preflight_checks(cfg_for(tmp_path), FakeQuik(connected=0), EXPECTED, TAG, live=True)
    assert ok is False
    assert levels(checks)["Связь с QUIK"] == "fail"


def test_chart_mismatch_fails(tmp_path):
    checks, ok = orb_robot.preflight_checks(cfg_for(tmp_path), FakeQuik(chart_sec="SiZ6"), EXPECTED, TAG, live=True)
    assert ok is False
    assert levels(checks)["График↔контракт"] == "fail"


def test_no_account_fails_live_but_warns_paper(tmp_path):
    _, ok_live = orb_robot.preflight_checks(cfg_for(tmp_path), FakeQuik(has_account=False), EXPECTED, TAG, live=True)
    assert ok_live is False
    checks_p, ok_p = orb_robot.preflight_checks(cfg_for(tmp_path), FakeQuik(has_account=False), EXPECTED, TAG, live=False)
    assert ok_p is True                                   # в paper — только предупреждение
    assert levels(checks_p)["Фьючерсный счёт"] == "warn"


def test_kill_switch_active_fails(tmp_path):
    (tmp_path / "STOP").write_text("x", encoding="utf-8")
    checks, ok = orb_robot.preflight_checks(cfg_for(tmp_path), FakeQuik(), EXPECTED, TAG, live=True)
    assert ok is False
    assert levels(checks)["Kill-switch"] == "fail"


def test_go_unreadable_is_warn_not_fail(tmp_path):
    checks, ok = orb_robot.preflight_checks(cfg_for(tmp_path), FakeQuik(go=None), EXPECTED, TAG, live=True)
    assert ok is True                                     # ГО нечитаемо — предупреждение, не блок
    assert levels(checks)["ГО контракта"] == "warn"


def test_empty_account_autofilled_from_quik(tmp_path):
    # account в конфиге пуст, но фьючерсный счёт найден в QUIK -> авто-подстановка, ok
    checks, ok = orb_robot.preflight_checks(cfg_for(tmp_path, account=""), FakeQuik(), EXPECTED, TAG, live=True)
    assert ok is True
    assert levels(checks)["Счёт для заявок"] == "ok"


def test_no_account_anywhere_fails_live(tmp_path):
    # ни в конфиге, ни в QUIK -> заявки слать некуда -> fail в live
    checks, ok = orb_robot.preflight_checks(cfg_for(tmp_path, account=""),
                                            FakeQuik(has_account=False), EXPECTED, TAG, live=True)
    assert ok is False
    assert levels(checks)["Счёт для заявок"] == "fail"
