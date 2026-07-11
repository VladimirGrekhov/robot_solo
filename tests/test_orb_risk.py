from datetime import date
from pathlib import Path

import orb_risk as r

CFG = r.RiskConfig()


def test_position_size_limited_by_risk():
    # риск-лимит: floor(300000*0.01/(150*1)) = floor(20) = 20
    # ГО-лимит:    floor(300000*0.30/50000)   = floor(1.8) = 1  <- лимитирует
    qty = r.position_size(deposit=300_000, stop_points=150, rub_per_point=1.0,
                           go_per_contract=50_000, cfg=CFG)
    assert qty == 1


def test_position_size_limited_by_go():
    # риск-лимит: floor(300000*0.01/(150*1)) = 20
    # ГО-лимит:    floor(300000*0.30/1000)    = floor(90) = 90 <- риск лимитирует
    qty = r.position_size(deposit=300_000, stop_points=150, rub_per_point=1.0,
                           go_per_contract=1_000, cfg=CFG)
    assert qty == 20


def test_position_size_floors_down_not_rounds():
    # риск-лимит: floor(300000*0.01/(190*1)) = floor(15.789..) = 15 (ГО-лимит шире, не давит)
    qty = r.position_size(deposit=300_000, stop_points=190, rub_per_point=1.0,
                           go_per_contract=5_000, cfg=CFG)
    assert qty == 15


def test_position_size_zero_on_bad_inputs():
    assert r.position_size(300_000, 0, 1.0, 10_000, CFG) == 0
    assert r.position_size(300_000, 150, 0, 10_000, CFG) == 0
    assert r.position_size(0, 150, 1.0, 10_000, CFG) == 0


def test_range_too_wide():
    assert r.range_too_wide(601, CFG) is True
    assert r.range_too_wide(600, CFG) is False


def test_daily_loss_limit():
    deposit = 300_000  # лимит = 6000 руб
    st = r.RiskState()
    st = r.record_trade_pnl(st, date(2026, 3, 11), -4000)
    assert r.daily_limit_hit(st, deposit, CFG) is False
    st = r.record_trade_pnl(st, date(2026, 3, 11), -2500)
    assert r.daily_limit_hit(st, deposit, CFG) is True


def test_daily_pnl_resets_on_new_day():
    st = r.RiskState()
    st = r.record_trade_pnl(st, date(2026, 3, 11), -5900)
    st = r.record_trade_pnl(st, date(2026, 3, 12), -100)  # новый день — накопитель с нуля
    assert st.day_pnl_rub == -100
    assert r.daily_limit_hit(st, 300_000, CFG) is False


def test_weekly_halt_triggers_and_requires_manual_reset():
    deposit = 300_000  # недельный лимит = 15000 руб
    st = r.RiskState()
    for pnl in (-4000, -4000, -4000, -4000):
        st = r.record_trade_pnl(st, date(2026, 3, 11), pnl)
    st = r.apply_halt_check(st, deposit, CFG)
    assert st.halted is True

    # новая неделя, прибыльная сделка — halted НЕ сбрасывается автоматически
    st = r.record_trade_pnl(st, date(2026, 3, 20), +50_000)
    st = r.apply_halt_check(st, deposit, CFG)
    assert st.halted is True

    st = r.reset_halt(st)
    assert st.halted is False


def test_kill_switch_file(tmp_path):
    assert r.kill_switch_active(tmp_path) is False
    (tmp_path / r.STOP_FILE_NAME).write_text("stop")
    assert r.kill_switch_active(tmp_path) is True


def test_risk_state_roundtrip(tmp_path):
    st = r.RiskState(day=date(2026, 3, 11), day_pnl_rub=-1234.5,
                      week_key=(2026, 11), week_pnl_rub=-6000.0, halted=True)
    path = tmp_path / "risk_state.json"
    r.save_risk_state(path, st)
    loaded = r.load_risk_state(path)
    assert loaded == st


def test_risk_state_missing_file_returns_default(tmp_path):
    loaded = r.load_risk_state(tmp_path / "does_not_exist.json")
    assert loaded == r.RiskState()
