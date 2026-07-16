"""Тесты сверки исполнения заявок (пункт №1): вход/стоп/закрытие против
ФАКТИЧЕСКОЙ позиции из QUIK. QUIK эмулируется FakeQuik — проверяем логику
оркестратора (подтверждение фила, частичный фил, неисполненный вход,
идемпотентное закрытие = фикс двойного исполнения, алерт при незакрытии),
а не проводной формат транзакций (он Windows-only и здесь недоступен)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import orb_robot


class FakeQuik:
    """Минимальный эмулятор QUIK: рыночные заявки двигают чистую позицию,
    стоп-заявки регистрируются/снимаются, позиция читается get_futures_holding."""

    def __init__(self, market_mode="full", fill_cap=None, stop_ok=True, readable=True):
        self.net = 0
        self.stops = []          # [{sec_code, class_code, order_num, flags}]
        self.sent = []           # все отправленные транзакции
        self.market_mode = market_mode   # full | none | cap
        self.fill_cap = fill_cap
        self.stop_ok = stop_ok
        self.readable = readable
        self.accounts = [{"firm_id": "F", "trade_account_id": "ACC", "futures": True}]

    def send_transaction(self, tr):
        self.sent.append(dict(tr))
        a = tr["ACTION"]
        if a == "NEW_ORDER":
            q = int(tr["QUANTITY"])
            filled = q if self.market_mode == "full" else (
                min(q, self.fill_cap) if self.market_mode == "cap" and self.fill_cap is not None else 0)
            self.net += filled if tr["OPERATION"] == "B" else -filled
            return {"data": "1"}
        if a == "NEW_STOP_ORDER":
            if self.stop_ok:
                self.stops.append({"sec_code": tr["SECCODE"], "class_code": tr["CLASSCODE"],
                                   "order_num": len(self.stops) + 1, "flags": 0x1})
                return {"data": "1"}
            return {"data": "0"}
        if a == "KILL_STOP_ORDER":
            key = int(tr["STOP_ORDER_KEY"])
            for s in self.stops:
                if s["order_num"] == key:
                    s["flags"] = 0
            return {"data": "1"}
        return {"data": "1"}

    def get_futures_holding(self, firm, acc, sec, lt):
        if not self.readable:
            return {}
        return {"data": {"totalnet": self.net}}

    def get_stop_orders(self):
        return {"data": list(self.stops)}

    def active_stops(self):
        return [s for s in self.stops if s["flags"] & 0x1]


def make_orch(tmp_path, qp, **exe):
    ex = {"verify": True, "confirm_timeout_seconds": 0.05, "confirm_poll_seconds": 0.0,
          "close_retries": 2}
    ex.update(exe)
    cfg = {
        "class_code": "SPBFUT", "account": "TEST", "client_code": "",
        "paths": {"risk_state_json": str(tmp_path / "risk.json"),
                  "trades_csv": str(tmp_path / "t.csv"), "skips_csv": str(tmp_path / "s.csv")},
        "risk": {"risk_per_trade": 0.01, "go_fraction": 0.3},
        "kill_switch_dir": str(tmp_path), "execution": ex,
    }
    events = []
    orch = orb_robot.OrbOrchestrator(cfg, qp, live=True, on_event=lambda k, d: events.append((k, d)))
    orch.events = events
    return orch


def errors(orch):
    return [d.get("text", "") for k, d in orch.events if k == "error"]


def test_entry_confirmed_places_stop(tmp_path):
    qp = FakeQuik(market_mode="full")
    orch = make_orch(tmp_path, qp)
    orch.open_meta = {"side": "long", "qty": 2}
    orch._send_entry_orders("SiZ5", "long", 2, 79000.0)
    assert qp.net == 2                      # вход исполнился
    assert len(qp.active_stops()) == 1      # стоп встал
    assert orch.open_meta["qty"] == 2       # размер не менялся
    assert orch.stop_ref_active is True
    assert errors(orch) == []


def test_entry_partial_fill_adjusts_qty(tmp_path):
    qp = FakeQuik(market_mode="cap", fill_cap=1)
    orch = make_orch(tmp_path, qp)
    orch.open_meta = {"side": "long", "qty": 2}
    orch._send_entry_orders("SiZ5", "long", 2, 79000.0)
    assert qp.net == 1
    assert orch.open_meta["qty"] == 1       # размер приведён к факту
    assert len(qp.active_stops()) == 1      # стоп на фактический размер
    assert any("частичный" in e for e in errors(orch))


def test_entry_not_filled_resets_state(tmp_path):
    qp = FakeQuik(market_mode="none")       # рынок не исполнился, net=0
    orch = make_orch(tmp_path, qp)
    orch.state = orb_robot.orb_strategy.open_position(
        orch.state, _entry("long", 79000.0), 79050.0)
    orch.open_meta = {"side": "long", "qty": 2}
    orch._send_entry_orders("SiZ5", "long", 2, 79000.0)
    assert orch.state.position is None       # состояние сброшено в плоское
    assert orch.open_meta is None
    assert orch.stop_ref_active is False
    assert any("не исполнён" in e for e in errors(orch))


def test_close_flattens_and_cancels_stop(tmp_path):
    qp = FakeQuik(market_mode="full")
    qp.net = 2                               # открыт лонг 2
    qp.stops.append({"sec_code": "SiZ5", "class_code": "SPBFUT", "order_num": 1, "flags": 0x1})
    orch = make_orch(tmp_path, qp)
    orch._close_position("SiZ5", "long", 2, reason="eod")
    assert qp.net == 0                       # закрылось
    assert qp.active_stops() == []           # висящий стоп снят
    assert errors(orch) == []


def test_close_idempotent_when_already_flat(tmp_path):
    """Фикс двойного исполнения: брокерский стоп уже закрыл позицию (net=0) —
    робот НЕ должен слать рыночное закрытие (иначе откроет противоположную)."""
    qp = FakeQuik(market_mode="full")
    qp.net = 0
    qp.stops.append({"sec_code": "SiZ5", "class_code": "SPBFUT", "order_num": 1, "flags": 0x1})
    orch = make_orch(tmp_path, qp)
    orch._close_position("SiZ5", "long", 2, reason="stop")
    assert qp.net == 0                        # осталось плоско
    market_orders = [t for t in qp.sent if t["ACTION"] == "NEW_ORDER"]
    assert market_orders == []                # ни одной рыночной заявки на закрытие
    assert qp.active_stops() == []            # стоп на всякий случай снят


def test_close_alerts_when_stuck(tmp_path):
    qp = FakeQuik(market_mode="none")         # рыночные закрытия не двигают позицию
    qp.net = 2
    orch = make_orch(tmp_path, qp, close_retries=2)
    orch._close_position("SiZ5", "long", 2, reason="eod")
    assert any("НЕ ЗАКРЫТА" in e for e in errors(orch))
    assert len([t for t in qp.sent if t["ACTION"] == "NEW_ORDER"]) == 2  # повторы


def test_unreadable_position_stop_reason_no_blind_close(tmp_path):
    qp = FakeQuik(market_mode="full", readable=False)
    qp.net = 2
    orch = make_orch(tmp_path, qp)
    orch._close_position("SiZ5", "long", 2, reason="stop")
    assert [t for t in qp.sent if t["ACTION"] == "NEW_ORDER"] == []   # вслепую НЕ закрываем на stop
    assert any("сверь вручную" in e for e in errors(orch))


def test_unreadable_position_eod_closes_blind(tmp_path):
    qp = FakeQuik(market_mode="full", readable=False)
    qp.net = 2
    orch = make_orch(tmp_path, qp)
    orch._close_position("SiZ5", "long", 2, reason="eod")
    assert len([t for t in qp.sent if t["ACTION"] == "NEW_ORDER"]) == 1  # закрываем вслепую


def test_verify_disabled_uses_blind_close(tmp_path):
    qp = FakeQuik(market_mode="full")
    qp.net = 2
    orch = make_orch(tmp_path, qp, verify=False)
    orch._close_position("SiZ5", "long", 2, reason="eod")
    assert len([t for t in qp.sent if t["ACTION"] == "NEW_ORDER"]) == 1


def _entry(side, stop):
    return orb_robot.orb_strategy.EntrySignal(side, __import__("datetime").datetime(2026, 7, 15, 11, 15),
                                              79500.0, 79000.0, stop)
