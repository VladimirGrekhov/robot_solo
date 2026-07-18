"""Тесты охраны графика: робот должен блокировать входы, если график (источник
сигналов) не соответствует торгуемому контракту. Три проверки — точное имя sec за
тегом, сверка цены с LAST, свежесть последнего бара. QUIK эмулируется."""
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import orb_robot
import orb_strategy


class FakeQuik:
    def __init__(self, last=None, tag_sec=None):
        self.last = last
        self.tag_sec = tag_sec
        self.accounts = [{"firm_id": "F", "trade_account_id": "ACC", "futures": True}]

    def get_param_ex(self, cls, sec, param):
        if param == "LAST" and self.last is not None:
            return {"data": {"result": "1", "param_value": str(self.last)}}
        return {"data": {"result": "0"}}

    def get_tag_seccode(self, tag):      # имитируем метод точной сверки sec за тегом
        return {"data": self.tag_sec}    # None => QuikPy «не умеет» -> chart_sec вернёт None


def make_orch(tmp_path, qp, **cg):
    guard = {"enabled": True, "price_tolerance_pct": 1.0, "max_bar_age_minutes": 40.0}
    guard.update(cg)
    cfg = {
        "class_code": "SPBFUT", "account": "TEST", "client_code": "", "chart_tag": "si15m",
        "paths": {"risk_state_json": str(tmp_path / "r.json"),
                  "trades_csv": str(tmp_path / "t.csv"), "skips_csv": str(tmp_path / "s.csv")},
        "risk": {"risk_per_trade": 0.01, "go_fraction": 0.3},
        "kill_switch_dir": str(tmp_path), "chart_guard": guard,
    }
    events = []
    orch = orb_robot.OrbOrchestrator(cfg, qp, live=False, on_event=lambda k, d: events.append((k, d)))
    orch.events = events
    return orch


def bar(close, dt=None):
    dt = dt or datetime(2026, 7, 16, 14, 15)
    return orb_strategy.Bar(dt, close, close + 20, close - 20, close)


def errors(orch):
    return [d.get("text", "") for k, d in orch.events if k == "error"]


def test_price_match_no_block(tmp_path):
    orch = make_orch(tmp_path, FakeQuik(last=78000.0))
    b = bar(78050.0)                              # отклонение ~0.06% < 1%
    orch._evaluate_chart(b, "SiU6", now=b.dt + timedelta(minutes=5))
    assert orch.chart_block is False
    assert errors(orch) == []


def test_price_deviation_blocks(tmp_path):
    orch = make_orch(tmp_path, FakeQuik(last=78000.0))
    b = bar(81000.0)                              # отклонение ~3.8% > 1%
    orch._evaluate_chart(b, "SiU6", now=b.dt + timedelta(minutes=5))
    assert orch.chart_block is True
    assert orch.chart_block_reason == "price"
    assert any("расходится" in e for e in errors(orch))


def test_stale_bar_blocks(tmp_path):
    orch = make_orch(tmp_path, FakeQuik(last=78000.0))
    b = bar(78000.0)                              # цена совпадает, но бар старый
    # now в окне торгов (14:15 + 90м = 15:45) -> свежесть срабатывает
    orch._evaluate_chart(b, "SiU6", now=b.dt + timedelta(minutes=90))
    assert orch.chart_block is True
    assert orch.chart_block_reason == "stale"


def test_stale_bar_outside_session_no_block(tmp_path):
    from datetime import datetime
    orch = make_orch(tmp_path, FakeQuik(last=78000.0))
    b = bar(78000.0)                              # цена совпадает, бар старый
    # now ВНЕ окна торгов (21:00) -> свежесть НЕ блокирует (вне сессии баров и не ждём)
    orch._evaluate_chart(b, "SiU6", now=datetime(2026, 7, 16, 21, 0))
    assert orch.chart_block is False


def test_exact_sec_match_authoritative(tmp_path):
    # sec за тегом совпадает — доверяем ему даже при «разъехавшейся» цене
    orch = make_orch(tmp_path, FakeQuik(last=999999.0, tag_sec="SiU6"))
    b = bar(78000.0)
    orch._evaluate_chart(b, "SiU6", now=b.dt + timedelta(minutes=5))
    assert orch.chart_block is False


def test_exact_sec_mismatch_blocks(tmp_path):
    orch = make_orch(tmp_path, FakeQuik(last=78000.0, tag_sec="SiM6"))
    b = bar(78000.0)
    orch._evaluate_chart(b, "SiU6", now=b.dt + timedelta(minutes=5))
    assert orch.chart_block is True
    assert orch.chart_block_reason == "sec"
    assert any("SiM6" in e for e in errors(orch))


def test_recovery_clears_block(tmp_path):
    qp = FakeQuik(last=78000.0)
    orch = make_orch(tmp_path, qp)
    bad = bar(81000.0)
    orch._evaluate_chart(bad, "SiU6", now=bad.dt + timedelta(minutes=5))
    assert orch.chart_block is True
    good = bar(78040.0)
    orch._evaluate_chart(good, "SiU6", now=good.dt + timedelta(minutes=5))
    assert orch.chart_block is False


def test_disabled_never_blocks(tmp_path):
    orch = make_orch(tmp_path, FakeQuik(last=78000.0), enabled=False)
    b = bar(81000.0)
    orch._evaluate_chart(b, "SiU6", now=b.dt + timedelta(minutes=5))
    assert orch.chart_block is False


def test_chart_sec_probe_none_when_unavailable(tmp_path):
    # tag_sec=None => get_tag_seccode отдаёт None => chart_sec вернёт None (сверка по имени недоступна)
    assert orb_robot.chart_sec(FakeQuik(tag_sec=None), "si15m") is None
    assert orb_robot.chart_sec(FakeQuik(tag_sec="SiU6"), "si15m") == "SiU6"
