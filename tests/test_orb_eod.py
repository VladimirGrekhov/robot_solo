"""Тесты временного EOD-страховщика: закрытие позиции по ЧАСАМ (не по бару), чтобы
не унести её через ночь при обрыве данных. QUIK не нужен — проверяем paper-путь."""
import sys
from dataclasses import replace
from datetime import datetime, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import orb_robot
import orb_strategy


class NoQuik:
    accounts: list = []

    def get_param_ex(self, *a):
        return {"data": {"result": "0"}}


def make(tmp_path, live=False, cfg_extra=None):
    cfg = {
        "class_code": "SPBFUT", "account": "TEST", "client_code": "", "chart_tag": "si15m",
        "paths": {"risk_state_json": str(tmp_path / "r.json"),
                  "trades_csv": str(tmp_path / "t.csv"), "skips_csv": str(tmp_path / "s.csv")},
        "risk": {"risk_per_trade": 0.01, "go_fraction": 0.3},
        "kill_switch_dir": str(tmp_path), "execution": {"verify": True},
    }
    if cfg_extra:
        cfg.update(cfg_extra)
    return orb_robot.OrbOrchestrator(cfg, NoQuik(), live=live)


def open_pos(orch, side="long", entry=78000.0, stop=77600.0, qty=1):
    pos = orb_strategy.Position(side=side, entry_time=datetime(2026, 7, 16, 11, 15),
                                entry_price=entry, stop_price=stop, range_high=entry, range_low=stop)
    orch.state = replace(orch.state, position=pos)
    orch.open_meta = {"side": side, "entry_time": datetime(2026, 7, 16, 11, 15), "entry_price": entry,
                      "stop_price": stop, "qty": qty, "rub_per_point": 1.0, "range_width": entry - stop}
    orch._last_price = entry


def test_default_time_is_1844(tmp_path):
    assert make(tmp_path).eod_flat_time == time(18, 44)


def test_configurable_time(tmp_path):
    orch = make(tmp_path, cfg_extra={"strategy": {"eod_flat_time": "18:30"}})
    assert orch.eod_flat_time == time(18, 30)


def test_no_close_before_time(tmp_path):
    orch = make(tmp_path)
    open_pos(orch)
    assert orch.eod_time_flat(datetime(2026, 7, 16, 18, 30)) is False
    assert orch.state.position is not None          # ещё открыта


def test_closes_after_time(tmp_path):
    orch = make(tmp_path)
    open_pos(orch, side="long", entry=78000.0)
    orch._last_price = 78100.0                       # +100 пунктов на закрытие
    assert orch.eod_time_flat(datetime(2026, 7, 16, 18, 45)) is True
    assert orch.state.position is None
    assert orch.open_meta is None
    rows = (tmp_path / "t.csv").read_text(encoding="utf-8")
    assert "eod_time" in rows                         # причина выхода записана в журнал


def test_no_position_is_noop(tmp_path):
    orch = make(tmp_path)
    assert orch.eod_time_flat(datetime(2026, 7, 16, 18, 45)) is False


def test_flat_now_closes_with_reason(tmp_path):
    """flat_now — общий немедленный выход (kill / ручной flat): закрывает и журналит с причиной."""
    orch = make(tmp_path)
    open_pos(orch, side="long", entry=78000.0)
    assert orch.flat_now("kill", 77900.0, datetime(2026, 7, 16, 15, 0)) is True
    assert orch.state.position is None and orch.open_meta is None
    assert "kill" in (tmp_path / "t.csv").read_text(encoding="utf-8")
