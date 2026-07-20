"""Тесты пульса живости биржевого стопа (пункт №2): live-позиция открыта, а стопа нет
— переставить; при нечитаемых данных — не трогать (не поставить двойной стоп). QUIK эмулируется."""
import sys
from dataclasses import replace
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import orb_robot
import orb_strategy

SEC = "SiU6"


class FakeQuik:
    def __init__(self, net=2, stops="present", send_ok=True):
        self.net = net
        self.stops = stops              # "present" | "none" | "unreadable"
        self.send_ok = send_ok
        self.sent = []
        self.accounts = [{"firm_id": "F", "trade_account_id": "ACC", "futures": True}]

    def get_futures_holding(self, firm, acc, sec, lt):
        return {"data": {"totalnet": self.net}}

    def get_stop_orders(self):
        if self.stops == "unreadable":
            raise RuntimeError("нет связи")
        if self.stops == "none":
            return {"data": []}
        return {"data": [{"sec_code": SEC, "class_code": "SPBFUT", "flags": 0x1, "order_num": 123}]}

    def get_param_ex(self, cls, sec, param):
        return {"data": {"result": "0"}}

    def send_transaction(self, tx):
        self.sent.append(tx)
        return {"data": "1" if self.send_ok else "0"}


def make(tmp_path, qp):
    cfg = {
        "class_code": "SPBFUT", "account": "TEST", "client_code": "", "chart_tag": "si15m",
        "paths": {"risk_state_json": str(tmp_path / "r.json"),
                  "trades_csv": str(tmp_path / "t.csv"), "skips_csv": str(tmp_path / "s.csv")},
        "risk": {"risk_per_trade": 0.01, "go_fraction": 0.3},
        "kill_switch_dir": str(tmp_path),
        "execution": {"verify": True, "confirm_timeout_seconds": 0, "confirm_poll_seconds": 0,
                      "close_retries": 1},
    }
    events = []
    orch = orb_robot.OrbOrchestrator(cfg, qp, live=True, on_event=lambda k, d: events.append((k, d)))
    orch.events = events
    pos = orb_strategy.Position(side="long", entry_time=datetime(2026, 7, 16, 11, 15),
                                entry_price=78000.0, stop_price=77600.0, range_high=78000.0, range_low=77600.0)
    orch.state = replace(orch.state, position=pos)
    orch.open_meta = {"side": "long", "entry_time": datetime(2026, 7, 16, 11, 15), "entry_price": 78000.0,
                      "stop_price": 77600.0, "qty": 2, "rub_per_point": 1.0, "range_width": 400.0}
    return orch


def test_stop_present_no_action(tmp_path):
    qp = FakeQuik(net=2, stops="present")
    orch = make(tmp_path, qp)
    orch.check_stop_alive(SEC)
    assert qp.sent == []                                   # ничего не переставляли
    assert orch.state.position is not None


def test_stop_missing_is_replaced(tmp_path):
    qp = FakeQuik(net=2, stops="none", send_ok=True)
    orch = make(tmp_path, qp)
    orch.check_stop_alive(SEC)
    assert any(tx.get("ACTION") == "NEW_STOP_ORDER" for tx in qp.sent)  # стоп переставлен
    assert orch.state.position is not None                 # позиция сохранена
    assert orch.stop_ref_active is True


def test_stop_unreadable_no_replace(tmp_path):
    qp = FakeQuik(net=2, stops="unreadable")
    orch = make(tmp_path, qp)
    orch.check_stop_alive(SEC)
    assert qp.sent == []                                   # не читается — не трогаем (без двойного стопа)
    assert orch.state.position is not None


def test_flat_no_action(tmp_path):
    qp = FakeQuik(net=0, stops="none")
    orch = make(tmp_path, qp)
    orch.check_stop_alive(SEC)
    assert qp.sent == []


def test_replace_fails_emergency_close(tmp_path):
    qp = FakeQuik(net=2, stops="none", send_ok=False)       # стоп не переставится
    orch = make(tmp_path, qp)
    orch.check_stop_alive(SEC)
    assert orch.state.position is None                      # аварийно закрыли
    assert orch.open_meta is None
    assert any(k == "error" for k, _ in orch.events)
