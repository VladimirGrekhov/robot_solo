"""
orb_robot.py — точка входа робота ORB (Opening Range Breakout), Si M15.

Режимы (--mode или config_orb.yaml: mode):
  backtest — прогон на истории MOEX ISS (см. orb_backtest.py), терминал QUIK не нужен;
  paper    — живые бары из QUIK через QuikPy, сигналы/«сделки» только в журнал,
             реальные заявки НЕ выставляются (режим по умолчанию);
  live     — реальные заявки. Требует live_trading: true в конфиге И ручного
             подтверждения "yes" при старте процесса.

Подключение к QUIK и чтение свечей по тегу графика — через QuikPy напрямую
(get_num_candles/get_candles по chart_tag, свой слот); вся торговая логика
(orb_strategy/orb_risk/orb_calendar) от QuikPy не зависит и тестируется без терминала.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time as _time
from dataclasses import replace as _replace
from datetime import date, datetime, timedelta
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

import orb_broker_quik
import orb_calendar
import orb_journal
import orb_risk
import orb_strategy

HERE = Path(__file__).resolve().parent
log = logging.getLogger("orb_robot")


def load_config(path: str | Path | None = None) -> dict:
    path = Path(path) if path else HERE / "config_orb.yaml"
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def setup_logging(cfg: dict) -> None:
    log_dir = HERE / cfg["paths"].get("log_dir", "logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    log.setLevel(logging.INFO)
    log.handlers.clear()
    for h in (logging.StreamHandler(), logging.FileHandler(log_dir / "orb_robot.log", encoding="utf-8")):
        h.setFormatter(fmt)
        log.addHandler(h)


def connect_quik(cfg: dict):
    """Подключение к QUIK напрямую через QuikPy на своём слоте (см. config_orb.yaml: slot)."""
    slot = cfg.get("slot", {})
    qpd = slot.get("quik_py_dir")
    for c in ([Path(qpd)] if qpd else []) + [HERE, *HERE.parents]:
        try:
            if (c / "QuikPy.py").is_file() or (c / "QuikPy").is_dir():
                d = c / "QuikPy" if (c / "QuikPy").is_dir() else c
                if str(d) not in sys.path:
                    sys.path.insert(0, str(d))
                break
        except OSError:
            continue
    try:
        from QuikPy import QuikPy
    except Exception as e:  # noqa: BLE001
        log.error("Не удалось импортировать QuikPy: %r. Укажи slot.quik_py_dir в конфиге.", e)
        return None
    try:
        return QuikPy(host=slot.get("host", "127.0.0.1"),
                      requests_port=int(slot.get("requests_port", 34142)),
                      callbacks_port=int(slot.get("callbacks_port", 34143)))
    except Exception as e:  # noqa: BLE001
        log.error("Не удалось подключиться к QUIK на слоте %s:%s — %r",
                  slot.get("host"), slot.get("requests_port"), e)
        return None


def _bar_dt(candle: dict) -> datetime | None:
    dt = candle.get("datetime", {})
    try:
        return datetime(int(dt["year"]), int(dt["month"]), int(dt["day"]),
                         int(dt.get("hour", 0)), int(dt.get("min", 0)))
    except (KeyError, TypeError, ValueError):
        return None


def _to_bar(candle: dict) -> orb_strategy.Bar | None:
    dt = _bar_dt(candle)
    if dt is None:
        return None
    return orb_strategy.Bar(dt, float(candle["open"]), float(candle["high"]),
                             float(candle["low"]), float(candle["close"]))


def _load_recent(qp, tag: str, want: int | None = None) -> list[dict]:
    """Грузит последние want свечей с графика tag (или все, если want=None)."""
    try:
        r = qp.get_num_candles(tag)
        n = (r.get("data", 0) if r else 0)
    except Exception as e:  # noqa: BLE001
        log.warning("get_num_candles('%s') не удался: %r", tag, e)
        return []
    if not n:
        return []
    start = 0 if want is None else max(0, n - want)
    count = n - start
    candles, loaded = [], 0
    while loaded < count:
        b = min(500, count - loaded)
        r = qp.get_candles(tag, 0, start + loaded, b)
        data = r.get("data") if r else None
        if not data:
            break
        chunk = data if isinstance(data, list) else [data[k] for k in sorted(data, key=lambda x: int(x))]
        candles.extend(chunk)
        loaded += len(chunk)
        if len(chunk) < b:
            break
    return candles


def _rub_per_point(qp, cls: str, sec: str, tick_size: float) -> float | None:
    """Стоимость 1 пункта цены в рублях для 1 контракта (QUIK-параметр STEPPRICE).
    Для Si обычно ровно 1.0 — читаем живьём на случай, если это когда-то изменится."""
    try:
        d = qp.get_param_ex(cls, sec, "STEPPRICE").get("data") or {}
        if str(d.get("result")) == "1":
            sp = float(d.get("param_value"))
            if sp > 0:
                return sp / tick_size
    except Exception:  # noqa: BLE001
        pass
    return None


def _read_go(qp, cls: str, sec: str) -> float | None:
    """ГО (нач. маржа) на 1 контракт — max(BUYDEPO, SELLDEPO), параметры QUIK FORTS."""
    vals = []
    for param in ("BUYDEPO", "SELLDEPO"):
        try:
            d = qp.get_param_ex(cls, sec, param).get("data") or {}
            if str(d.get("result")) == "1":
                v = float(d.get("param_value"))
                if v > 0:
                    vals.append(v)
        except Exception:  # noqa: BLE001
            pass
    return max(vals) if vals else None


class OrbOrchestrator:
    """Общая логика обработки одного закрытого бара для paper и live.

    live=False (paper) — заявки не отправляются, только журнал и лог.
    live=True — при live_trading=True в конфиге реально шлёт заявки через orb_broker_quik.
    """

    def __init__(self, cfg: dict, qp, live: bool):
        self.cfg = cfg
        self.qp = qp
        self.live = live
        self.state = orb_strategy.OrbState()
        risk_cfg = cfg.get("risk", {})
        self.risk_cfg = orb_risk.RiskConfig(**risk_cfg) if risk_cfg else orb_risk.RiskConfig()
        strat_cfg = cfg.get("strategy", {})
        self.allow_position_flip = bool(strat_cfg.get("allow_position_flip", False))
        self.expiration_zone_mode = strat_cfg.get("expiration_zone_mode", "trading_days")
        self.risk_path = HERE / cfg["paths"]["risk_state_json"]
        self.risk_state = orb_risk.load_risk_state(self.risk_path)
        self.trades_path = HERE / cfg["paths"]["trades_csv"]
        self.skips_path = HERE / cfg["paths"]["skips_csv"]
        self.kill_dir = HERE / cfg.get("kill_switch_dir", ".")
        self.open_meta: dict | None = None
        self.pending_entry: orb_strategy.EntrySignal | None = None
        self.halted_by_kill_switch = False

    def _contract(self, d: date) -> str:
        return orb_calendar.active_contract(d)

    def _sizing_inputs(self, sec: str):
        cls = self.cfg["class_code"]
        tick = float(self.cfg.get("tick_size", 1.0))
        rpp = _rub_per_point(self.qp, cls, sec, tick) or 1.0
        go = _read_go(self.qp, cls, sec) or self.cfg.get("backtest", {}).get("go_per_contract_assumed", 12000.0)
        return rpp, go

    def handle_bar(self, bar: orb_strategy.Bar, replay: bool = False) -> None:
        day = bar.dt.date()
        if orb_risk.kill_switch_active(self.kill_dir):
            if not self.halted_by_kill_switch:
                log.warning("KILL SWITCH: файл STOP найден в %s — закрываю позиции и останавливаюсь.",
                            self.kill_dir)
                self.halted_by_kill_switch = True
            if self.state.position is not None and self.open_meta is not None:
                exit_sig = orb_strategy.ExitSignal("kill", bar.close)
                trade = self._close_trade(bar, exit_sig)
                if not replay:
                    orb_journal.append_trade(self.trades_path, trade)
                self.risk_state = orb_risk.record_trade_pnl(self.risk_state, day, trade.pnl_rub)
                orb_risk.save_risk_state(self.risk_path, self.risk_state)
                log.info("ВЫХОД %s: причина=kill pnl=%.2f пт / %.2f руб", trade.dir, trade.pnl_pt, trade.pnl_rub)
                if self.live and not replay:
                    self._send_flat(self.state.position.side, reason="kill")
                self.open_meta = None
            self.state = _replace(self.state, position=None)
            return

        if self.risk_state.day != day:
            self.risk_state = orb_risk.sync_day(self.risk_state, day)

        sec = self._contract(day)

        if self.pending_entry is not None:
            entry = self.pending_entry
            self.pending_entry = None
            rpp, go = self._sizing_inputs(sec)
            stop_points = abs(bar.open - entry.stop_price)
            qty = orb_risk.position_size(self.cfg["deposit_rub"], stop_points, rpp, go, self.risk_cfg)
            if qty > 0:
                self.state = orb_strategy.open_position(self.state, entry, bar.open)
                self.open_meta = {"side": entry.side, "entry_time": bar.dt, "entry_price": bar.open,
                                   "stop_price": entry.stop_price, "qty": qty, "rub_per_point": rpp,
                                   "range_width": entry.range_high - entry.range_low}
                log.info("ВХОД %s: бар=%s цена~%.2f стоп=%.2f qty=%d",
                         entry.side.upper(), bar.dt, bar.open, entry.stop_price, qty)
                if self.live and not replay:
                    self._send_entry_orders(sec, entry.side, qty, entry.stop_price)
            else:
                log.info("вход %s пропущен: нулевой размер позиции (риск/ГО-лимит)", entry.side)
                if not replay:
                    orb_journal.append_skip(self.skips_path, bar.dt, entry.side, "zero_qty")

        blocked, reason = orb_calendar.entry_gate(bar.dt, self.expiration_zone_mode)
        force_flat = orb_calendar.force_flat_gate(bar.dt)
        if not blocked:
            if self.risk_state.halted:
                blocked, reason = True, "halted"
            elif orb_risk.daily_limit_hit(self.risk_state, self.cfg["deposit_rub"], self.risk_cfg):
                blocked, reason = True, "daily_limit"

        result = orb_strategy.process_bar(self.state, bar, blocked, force_flat, self.risk_cfg.max_stop_pt,
                                           allow_flip=self.allow_position_flip)
        self.state = result.state

        if result.exit is not None and self.open_meta is not None:
            trade = self._close_trade(bar, result.exit)
            if not replay:
                orb_journal.append_trade(self.trades_path, trade)
            self.risk_state = orb_risk.record_trade_pnl(self.risk_state, day, trade.pnl_rub)
            orb_risk.save_risk_state(self.risk_path, self.risk_state)
            log.info("ВЫХОД %s: причина=%s pnl=%.2f пт / %.2f руб", trade.dir, trade.exit_reason,
                     trade.pnl_pt, trade.pnl_rub)
            if self.live and not replay:
                self._send_flat(trade.dir, reason=trade.exit_reason)
            self.open_meta = None

        if result.entry is not None:
            self.pending_entry = result.entry

        if result.skip is not None:
            specific = reason if result.skip.reason == "blocked" and reason else result.skip.reason
            log.info("пропуск сигнала %s: %s", result.skip.side, specific)
            if not replay:
                orb_journal.append_skip(self.skips_path, bar.dt, result.skip.side, specific)

    def _close_trade(self, bar: orb_strategy.Bar, exit_sig: orb_strategy.ExitSignal) -> orb_journal.TradeRecord:
        m = self.open_meta
        long = m["side"] == "long"
        pnl_pt = (exit_sig.price - m["entry_price"]) if long else (m["entry_price"] - exit_sig.price)
        pnl_rub = pnl_pt * m["rub_per_point"] * m["qty"]  # без комиссии/слиппеджа — это paper/live, не бэктест
        return orb_journal.TradeRecord(
            datetime_in=m["entry_time"], datetime_out=bar.dt, dir=m["side"], qty=m["qty"],
            entry=m["entry_price"], exit=exit_sig.price, stop=m["stop_price"],
            pnl_pt=pnl_pt, pnl_rub=pnl_rub, exit_reason=exit_sig.reason,
            range_width_pt=m["range_width"], event_flags="")

    def _send_entry_orders(self, sec: str, side: str, qty: int, stop_price: float) -> None:
        cfg = self.cfg
        r = orb_broker_quik.send_market_order(self.qp, cfg["account"], cfg["class_code"], sec, side, qty,
                                               cfg.get("client_code", ""))
        log.info("заявка вход отправлена: %s", r)
        closing_side = "short" if side == "long" else "long"
        r2 = orb_broker_quik.send_stop_order(self.qp, cfg["account"], cfg["class_code"], sec, closing_side,
                                              qty, stop_price, cfg.get("client_code", ""))
        log.info("стоп-заявка отправлена: %s", r2)

    def _send_flat(self, position_side: str, reason: str) -> None:
        cfg = self.cfg
        sec = self._contract(date.today())
        qty = self.open_meta["qty"] if self.open_meta else 0
        if qty <= 0:
            return
        r = orb_broker_quik.send_flat_market_order(self.qp, cfg["account"], cfg["class_code"], sec, qty=qty,
                                                    position_side=position_side, client_code=cfg.get("client_code", ""))
        log.info("закрытие позиции (%s) отправлено: %s", reason, r)


def _next_wake(now: datetime, tf: int, delay: float) -> datetime:
    minute = (now.minute // tf) * tf
    boundary = now.replace(minute=minute, second=0, microsecond=0) + timedelta(minutes=tf)
    return boundary + timedelta(seconds=delay)


def run_paper_or_live(cfg: dict, live: bool) -> None:
    if live and not cfg.get("live_trading", False):
        log.error("mode=live, но live_trading=false в конфиге — отказываюсь торговать по-настоящему.")
        return
    if live:
        answer = input('Подтвердите запуск LIVE (реальные заявки) — введите "yes": ')
        if answer.strip().lower() != "yes":
            log.info("Не подтверждено — выхожу без запуска live.")
            return

    qp = connect_quik(cfg)
    if qp is None:
        log.error("Нет подключения к QUIK — работа невозможна.")
        return

    tag = cfg["chart_tag"]
    tf = int(cfg.get("timeframe_minutes", 15))
    delay = float(cfg.get("bar_close_delay_seconds", 7))
    today = date.today()
    expected = orb_calendar.active_contract(today)
    log.info("Ожидаемый активный контракт на сегодня: %s — сверь, что график с тегом '%s' привязан к нему.",
             expected, tag)

    orch = OrbOrchestrator(cfg, qp, live)

    # разметка сегодняшних баров (если робот запущен посреди дня) — реплей БЕЗ реальных заявок,
    # только чтобы восстановить диапазон/флаги/(предположительную) открытую позицию
    all_candles = _load_recent(qp, tag, want=None)
    todays = [c for c in all_candles if (_bar_dt(c) or datetime.min).date() == today]
    for c in todays[:-1] if todays else []:
        b = _to_bar(c)
        if b:
            orch.handle_bar(b, replay=True)
    if orch.state.position is not None:
        log.warning("ВНИМАНИЕ: по разметке сегодняшней истории должна быть открытая позиция %s — "
                    "робот запущен не с начала дня, сверь с реальными позициями/заявками в QUIK вручную!",
                    orch.state.position.side)

    last_dt = _bar_dt(todays[-1]) if todays else None
    log.info("Старт ORB. режим=%s live_trading=%s tf=%d мин", "live" if live else "paper",
             cfg.get("live_trading"), tf)

    try:
        while True:
            wake = _next_wake(datetime.now(), tf, delay)
            while datetime.now() < wake:
                _time.sleep(min(1.0, (wake - datetime.now()).total_seconds()))
            candles = _load_recent(qp, tag, want=max(80, 5))
            closed = candles[:-1] if candles else []
            new = [c for c in closed if (_bar_dt(c) or datetime.min) > (last_dt or datetime.min)]
            for c in new:
                b = _to_bar(c)
                if b:
                    orch.handle_bar(b, replay=False)
                    last_dt = b.dt
            if orch.halted_by_kill_switch:
                log.warning("Остановлен kill switch'ем.")
                break
    except KeyboardInterrupt:
        log.info("Остановлен пользователем (Ctrl+C).")


def main() -> None:
    ap = argparse.ArgumentParser(description="orb_robot — стратегия ORB (Opening Range Breakout), Si M15")
    ap.add_argument("--config", default=None, help="путь к config_orb.yaml")
    ap.add_argument("--mode", choices=["backtest", "paper", "live"], default=None,
                    help="переопределяет mode из конфига")
    args = ap.parse_args()

    cfg = load_config(args.config)
    if args.mode:
        cfg["mode"] = args.mode
    setup_logging(cfg)

    mode = cfg.get("mode", "paper")
    if mode == "backtest":
        import orb_backtest  # локальный импорт — бэктесту не нужен QuikPy/argparse-контекст live
        bt_cfg = cfg["backtest"]
        strat_cfg = cfg.get("strategy", {})
        risk_cfg = orb_risk.RiskConfig(**cfg.get("risk", {})) if cfg.get("risk") else orb_risk.RiskConfig()
        b = orb_backtest.BacktestConfig(
            date_from=date.fromisoformat(bt_cfg["date_from"]),
            date_till=date.fromisoformat(bt_cfg["date_till"]),
            cache_dir=HERE / bt_cfg["cache_dir"],
            deposit_rub=float(bt_cfg.get("deposit_rub", cfg.get("deposit_rub", 300000.0))),
            rub_per_point=float(bt_cfg.get("rub_per_point", 1.0)),
            go_per_contract_assumed=float(bt_cfg.get("go_per_contract_assumed", 12000.0)),
            commission_per_side_rub=float(bt_cfg.get("commission_per_side_rub", 5.0)),
            slippage_ticks=int(bt_cfg.get("slippage_ticks", 2)),
            tick_size=float(cfg.get("tick_size", 1.0)),
            risk=risk_cfg,
            allow_position_flip=bool(strat_cfg.get("allow_position_flip", False)),
            expiration_zone_mode=strat_cfg.get("expiration_zone_mode", "trading_days"),
        )
        res = orb_backtest.run(b)
        log.info("Бэктест завершён: %s", res.summary)
        for line in orb_journal.summary_lines(res.summary):
            log.info(line)
    elif mode == "paper":
        run_paper_or_live(cfg, live=False)
    elif mode == "live":
        run_paper_or_live(cfg, live=True)
    else:
        log.error("Неизвестный mode: %s", mode)


if __name__ == "__main__":
    main()
