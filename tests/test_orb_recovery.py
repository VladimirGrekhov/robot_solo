"""Тесты восстановления позиции при рестарте (пункт №2): adopt_live_position
сверяет реконструкцию по барам с фактической позицией в QUIK и приводит
состояние к реальности. QUIK эмулируется."""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import orb_robot
import orb_strategy


class FakeQuik:
    def __init__(self, net=0, avg=None, stop_price=None, readable=True):
        self.net = net
        self.avg = avg
        self.stop_price = stop_price
        self.readable = readable
        self.accounts = [{"firm_id": "F", "trade_account_id": "ACC", "futures": True}]

    def get_futures_holding(self, firm, acc, sec, lt):
        if not self.readable:
            return {}
        d = {"totalnet": self.net}
        if self.avg is not None:
            d["avrposnprice"] = self.avg
        return {"data": d}

    def get_stop_orders(self):
        if self.stop_price is None:
            return {"data": []}
        return {"data": [{"sec_code": "SiU6", "class_code": "SPBFUT",
                          "flags": 0x1, "condition_price": self.stop_price}]}

    def get_param_ex(self, cls, sec, param):
        if param == "STEPPRICE":
            return {"data": {"result": "1", "param_value": "1.0"}}
        return {"data": {"result": "0"}}


def make_orch(tmp_path, qp):
    cfg = {
        "class_code": "SPBFUT", "account": "TEST", "client_code": "", "chart_tag": "si15m",
        "paths": {"risk_state_json": str(tmp_path / "r.json"),
                  "trades_csv": str(tmp_path / "t.csv"), "skips_csv": str(tmp_path / "s.csv")},
        "risk": {"risk_per_trade": 0.01, "go_fraction": 0.3},
        "kill_switch_dir": str(tmp_path), "execution": {"verify": True},
    }
    events = []
    orch = orb_robot.OrbOrchestrator(cfg, qp, live=True, on_event=lambda k, d: events.append((k, d)))
    orch.events = events
    return orch


def errors(orch):
    return [d.get("text", "") for k, d in orch.events if k == "error"]


def with_position(orch, side, rh=78500.0, rl=77600.0):
    from dataclasses import replace
    pos = orb_strategy.Position(side=side, entry_time=datetime(2026, 7, 16, 11, 15),
                                entry_price=rh if side == "long" else rl,
                                stop_price=rl if side == "long" else rh, range_high=rh, range_low=rl)
    orch.state = replace(orch.state, position=pos, range_high=rh, range_low=rl)
    return orch


def test_flat_and_no_reconstruction(tmp_path):
    orch = make_orch(tmp_path, FakeQuik(net=0))
    orch.adopt_live_position("SiU6")
    assert orch.state.position is None
    assert orch.open_meta is None
    assert errors(orch) == []


def test_flat_but_reconstruction_had_position(tmp_path):
    orch = make_orch(tmp_path, FakeQuik(net=0))
    with_position(orch, "long")
    orch.adopt_live_position("SiU6")
    assert orch.state.position is None            # сброшено
    assert orch.state.long_used is True           # сторона помечена, не переоткроем
    assert any("offline" in e for e in errors(orch))


def test_adopt_matching_position(tmp_path):
    orch = make_orch(tmp_path, FakeQuik(net=2, avg=78000.0, stop_price=77600.0))
    with_position(orch, "long")
    orch.adopt_live_position("SiU6")
    assert orch.state.position.side == "long"
    assert orch.open_meta["qty"] == 2             # размер из реальной позиции
    assert orch.open_meta["stop_price"] == 77600.0  # стоп из стоп-заявки QUIK
    assert orch.open_meta["entry_price"] == 78000.0  # средняя цена из QUIK
    assert orch.stop_ref_active is True
    assert errors(orch) == []                     # штатный подхват — без алерта


def test_adopt_unexpected_position(tmp_path):
    orch = make_orch(tmp_path, FakeQuik(net=-3, avg=79000.0, stop_price=79600.0))
    orch.adopt_live_position("SiU6")              # реконструкции нет
    assert orch.state.position.side == "short"
    assert orch.open_meta["qty"] == 3
    assert orch.state.short_used is True
    assert any("неожиданн" in e for e in errors(orch))


def test_adopt_without_stop_order_alerts(tmp_path):
    orch = make_orch(tmp_path, FakeQuik(net=2, avg=78000.0, stop_price=None))
    with_position(orch, "long")
    orch.adopt_live_position("SiU6")
    assert orch.state.position is not None
    assert orch.stop_ref_active is False
    assert any("стоп" in e.lower() for e in errors(orch))


def test_unreadable_position_keeps_state(tmp_path):
    orch = make_orch(tmp_path, FakeQuik(readable=False))
    with_position(orch, "long")
    orch.adopt_live_position("SiU6")
    assert orch.state.position is not None         # состояние не тронуто
    assert any("не читается" in e for e in errors(orch))
