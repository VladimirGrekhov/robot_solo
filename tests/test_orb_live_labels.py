"""Тесты рисования live/paper сделок на графике QUIK: чемпион рисует через addLabel2,
тени и выключенный тумблер — нет; сбой/отсутствие process_request не роняет торговлю."""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import orb_robot
import orb_strategy


class DrawQuik:
    accounts: list = []

    def __init__(self):
        self.reqs = []

    def process_request(self, req):
        self.reqs.append(req)
        return {"data": len(self.reqs)}

    def get_param_ex(self, *a):
        return {"data": {"result": "0"}}


class NoDraw:               # без process_request — рисовать нечем, но и падать нельзя
    accounts: list = []


def make(tmp_path, qp, draw=True, name=""):
    cfg = {
        "class_code": "SPBFUT", "account": "TEST", "chart_tag": "si15m",
        "paths": {"risk_state_json": str(tmp_path / "r.json"),
                  "trades_csv": str(tmp_path / "t.csv"), "skips_csv": str(tmp_path / "s.csv")},
        "risk": {"risk_per_trade": 0.01}, "kill_switch_dir": str(tmp_path),
        "draw_live_labels": draw,
    }
    return orb_robot.OrbOrchestrator(cfg, qp, live=False, name=name)


def _partial(orch):
    bar = orb_strategy.Bar(datetime(2026, 7, 16, 11, 15), 78550, 78600, 78500, 78560, 0.0)
    entry = orb_strategy.EntrySignal("long", bar.dt, 78500.0, 77600.0, 77600.0)
    return orch._partial_trade(bar, entry, qty=1)


def test_champion_draws_entry_marks(tmp_path):
    qp = DrawQuik()
    orch = make(tmp_path, qp)
    orch._draw_marks([0, 1, 3, 4], _partial(orch))          # вход, SL, RH, RL
    assert len(qp.reqs) == 4
    assert all(r["cmd"] == "addLabel2" for r in qp.reqs)


def test_shadow_does_not_draw(tmp_path):
    qp = DrawQuik()
    orch = make(tmp_path, qp, name="be1")                    # теневой вариант
    assert orch.draw_labels is False
    orch._draw_marks([0, 1, 3, 4], _partial(orch))
    assert qp.reqs == []


def test_toggle_off_disables(tmp_path):
    qp = DrawQuik()
    orch = make(tmp_path, qp, draw=False)
    assert orch.draw_labels is False
    orch._draw_marks([2], _partial(orch))
    assert qp.reqs == []


def test_no_process_request_is_safe(tmp_path):
    orch = make(tmp_path, NoDraw())
    orch._draw_marks([0, 1, 2, 3, 4], _partial(orch))        # не должно падать
