"""Тесты теневых вариантов (дверь Б): фабрика фильтров, сборка теней и то, что
фильтр реально отсекает вход, а чемпион (без фильтра) — берёт."""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import orb_robot
import orb_strategy as S


# ---------- фабрика фильтров ----------
def test_empty_spec_no_filter():
    assert orb_robot.make_entry_filter({}) is None
    assert orb_robot.make_entry_filter(None) is None


def test_min_rel_volume():
    f = orb_robot.make_entry_filter({"min_rel_volume": 1.5})
    bar = S.Bar(datetime(2026, 7, 16, 11, 0), 1, 1, 1, 1, 100)
    assert f("long", bar, 2.0, 300) is True     # объём достаточный
    assert f("long", bar, 1.0, 300) is False    # объём мал


def test_entry_before():
    f = orb_robot.make_entry_filter({"entry_before": "15:00"})
    early = S.Bar(datetime(2026, 7, 16, 12, 0), 1, 1, 1, 1)
    late = S.Bar(datetime(2026, 7, 16, 16, 0), 1, 1, 1, 1)
    assert f("long", early, 1.0, 300) is True
    assert f("long", late, 1.0, 300) is False


def test_range_band():
    f = orb_robot.make_entry_filter({"range_min": 200, "range_max": 600})
    bar = S.Bar(datetime(2026, 7, 16, 11, 0), 1, 1, 1, 1)
    assert f("long", bar, 1.0, 400) is True
    assert f("long", bar, 1.0, 100) is False    # уже min
    assert f("long", bar, 1.0, 900) is False    # шире max


# ---------- сборка теней ----------
def _cfg(tmp_path, variants):
    return {
        "class_code": "SPBFUT", "account": "", "chart_tag": "si15m", "tick_size": 1.0,
        "deposit_rub": 100000.0, "kill_switch_dir": str(tmp_path),
        "risk": {"risk_per_trade": 0.03, "go_fraction": 0.3},
        "backtest": {"go_per_contract_assumed": 12000.0},
        "chart_guard": {"enabled": False},     # тени и так выключают, но и чемпиону в тесте не нужна
        "paths": {"risk_state_json": str(tmp_path / "r.json"),
                  "trades_csv": str(tmp_path / "t.csv"), "skips_csv": str(tmp_path / "s.csv")},
        "shadow_variants": variants,
    }


def test_build_shadows(tmp_path):
    cfg = _cfg(tmp_path, [{"name": "vol15", "filter": {"min_rel_volume": 1.5}},
                          {"name": "plain", "filter": {}}])
    shadows = orb_robot.build_shadows(cfg, qp=None)
    assert [s.name for s in shadows] == ["vol15", "plain"]
    assert all(s.live is False for s in shadows)          # тени НИКОГДА не торгуют
    assert shadows[0].entry_filter is not None
    assert shadows[1].entry_filter is None
    # свои журналы
    assert "shadow_vol15_trades.csv" in str(shadows[0].trades_path)
    assert shadows[0].chart_guard_on is False


def _day_bars():
    """Утренний диапазон 79900-80100 + пробой вверх на НИЗКОМ объёме (rel<1)."""
    d = lambda h, m: datetime(2026, 7, 16, h, m)
    return [
        S.Bar(d(10, 0), 80000, 80050, 79950, 80000, 100),
        S.Bar(d(10, 15), 80000, 80100, 79900, 80000, 100),
        S.Bar(d(10, 30), 80000, 80080, 79920, 80000, 100),
        S.Bar(d(10, 45), 80000, 80090, 79910, 80000, 100),
        S.Bar(d(11, 0), 80100, 80250, 80090, 80200, 40),   # пробой вверх, объём НИЗКИЙ
        S.Bar(d(11, 15), 80200, 80320, 80180, 80300, 120),  # здесь чемпион открывает pending
    ]


def test_filter_blocks_entry_champion_takes(tmp_path):
    cfg = _cfg(tmp_path, [])
    champ = orb_robot.OrbOrchestrator(cfg, qp=None, live=False, name="")
    scfg = dict(cfg); scfg["chart_guard"] = {"enabled": False}
    filt = orb_robot.OrbOrchestrator(
        scfg, qp=None, live=False, name="vol15",
        entry_filter=orb_robot.make_entry_filter({"min_rel_volume": 1.5}),
        paths={"risk_state_json": str(tmp_path / "sr.json"),
               "trades_csv": str(tmp_path / "st.csv"), "skips_csv": str(tmp_path / "ss.csv")})
    for b in _day_bars():
        champ.handle_bar(b, replay=False)
        filt.handle_bar(b, replay=False)
    # чемпион вошёл в лонг, отфильтрованный вариант — нет
    assert champ.state.position is not None and champ.state.position.side == "long"
    assert filt.state.position is None
    assert filt.pending_entry is None
