"""
pattern_robot.py — автономный робот паттерна 3+1 (шаг 1: режим СКАН истории).

Что делает сейчас:
  - по каждому инструменту из config.json открывает его график в QUIK по тегу,
    стирает старые метки, грузит свечи, прогоняет детектор 3+1 (с фильтрами),
    рисует стрелки и уровни вход/стоп/тейк на графике, пишет лог;
  - НИКАКИХ заявок: execute_signal пока всегда в ветке «симуляция» (live_trading=false).

Чего ещё НЕТ (следующие шаги): живой режим по таймеру, ОИ с дельтой, кнопки ручного
режима. Здесь только скан, чтобы проверить на реальном терминале, что стрелки ложатся.

Запуск:  python pattern_robot.py          (скан по графикам из конфига)
         python pattern_robot.py --mock   (без QUIK: прогон по тестовым свечам в лог)
"""

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import chart
import account
from detector import detect
from analyze import (evaluate_outcome, drawdown_stats, period_stats,
                     summarize, summarize_by_risk, summary_report)

HERE = Path(__file__).resolve().parent
log = logging.getLogger("pattern_robot")


def load_config() -> dict:
    with open(HERE / "config.json", "r", encoding="utf-8") as f:
        return json.load(f)


def save_config(cfg: dict):
    """Атомарная запись config.json: пишем во временный файл и заменяем (как в robot_a),
    чтобы при сбое посередине не остался повреждённый конфиг."""
    import os
    path = HERE / "config.json"
    tmp = path.with_suffix(".json.part")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=4)
    os.replace(tmp, path)


def setup_logging(cfg: dict):
    log_dir = HERE / cfg.get("log_dir", "logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    log.setLevel(logging.INFO)
    log.handlers.clear()
    for h in (logging.StreamHandler(), logging.FileHandler(log_dir / "pattern_robot.log", encoding="utf-8")):
        h.setFormatter(fmt)
        log.addHandler(h)


def execute_signal(cfg: dict, instr: dict, sig, candle, source: str):
    """Единая точка «выставить заявку». live_trading=false -> только лог (симуляция)."""
    if cfg.get("live_trading", False):
        log.warning("[%s] live_trading включён, но боевая ветка ещё не подключена — симулирую.",
                    instr["name"])
    mode = cfg.get("mode", "auto")
    log.info("[%s] %s (СИМУЛЯЦИЯ, режим=%s): выставил бы %s  вход=%.2f стоп=%.2f цель=%.2f  объём=%d лот",
             instr["name"], source.upper(), mode, sig.side.upper(),
             sig.entry, sig.stop, sig.target, int(cfg.get("volume_lots", 1)))


def scan_instrument(qp, cfg: dict, instr: dict):
    name = instr["name"]
    tag = instr.get("chart_tag", "")
    step = float(instr.get("price_step", 1))
    rev = cfg.get("reversal_3plus1", {})
    arrows = cfg.get("arrows", {})

    log.info("[%s] график тег='%s', шаг цены %.0f", name, tag, step)
    n = _num_candles(qp, tag)
    if n is None:
        log.error("[%s] график с тегом '%s' не найден или пуст — пропускаю инструмент.", name, tag)
        return
    if n < 4:
        log.error("[%s] свечей всего %d — для 3+1 мало. Открой график нужного ТФ.", name, n)
        return
    log.info("[%s] свечей на графике: %d. Чищу старые метки и сканирую…", name, n)
    chart.del_all_labels(qp, tag)

    candles = _load_candles(qp, tag, n, int(cfg.get("batch_size", 500)))
    if len(candles) < 4:
        log.error("[%s] загрузить свечи не удалось (получено %d).", name, len(candles))
        return

    found = 0
    outcomes = []
    for i in range(4, len(candles) + 1):
        sig = detect(candles[:i], rev, step)
        if sig is None:
            continue
        outcomes.append(evaluate_outcome(candles, i - 1, sig, rev))
        cv4 = candles[i - 1]
        found += 1
        is_bull = sig.side == "long"
        note = (" | " + "; ".join(sig.notes)) if sig.notes else ""
        log.info("[%s] СКАН сигнал #%d: %s · бар %s · вход=%.2f стоп=%.2f тейк=%.2f риск=%.0f п. · ОИ=н/д (история)%s",
                 name, found, sig.side.upper(), chart.fmt_dt(cv4),
                 sig.entry, sig.stop, sig.target, sig.risk, note)
        try:
            hint = f"3+1 {sig.side} Вход={sig.entry:.2f} СЛ={sig.stop:.2f} ТП={sig.target:.2f}"
            chart.draw_arrow(qp, tag, arrows, cv4, is_bull, found, hint)
            chart.draw_levels(qp, tag, cv4, sig.entry, sig.stop, sig.target)
            log.info("[%s]   стрелка нарисована на баре %s", name, chart.fmt_dt(cv4))
        except Exception as e:  # noqa: BLE001
            log.warning("[%s]   стрелку нарисовать не удалось: %r", name, e)
        execute_signal(cfg, instr, sig, cv4, "scan")

    log.info("[%s] скан завершён: сигналов %d на %d свечах.", name, found, len(candles))
    s = summarize(outcomes)
    if s["total"]:
        sec = instr.get("ticker") or _safe_ticker(qp, cfg["class_code"], instr)
        rpp = _rub_per_point(qp, cfg["class_code"], sec, instr)
        period = period_stats(_bar_dt(candles[0]), _bar_dt(candles[-1]), s["target"] + s["stop"])
        for line in summary_report(outcomes, rub_per_point=rpp, period=period):
            log.info("[%s] %s", name, line)
        go = _read_go(qp, cfg["class_code"], sec)
        log.info("[%s] ГО (нач. маржа) ≈ %s ₽/контракт", name, f"{go:.0f}" if go else "н/д")


def _num_candles(qp, tag):
    try:
        r = qp.get_num_candles(tag)
        return (r.get("data", 0) if r else 0)
    except Exception as e:  # noqa: BLE001
        log.warning("get_num_candles('%s') не удался: %r", tag, e)
        return None


def _load_candles(qp, tag, n, batch_size):
    candles, loaded = [], 0
    while loaded < n:
        batch = min(batch_size, n - loaded)
        r = qp.get_candles(tag, 0, loaded, batch)
        data = r.get("data") if r else None
        if not data:
            break
        chunk = data if isinstance(data, list) else [data[k] for k in sorted(data, key=lambda x: int(x))]
        candles.extend(chunk)
        loaded += len(chunk)
        if len(chunk) < batch:
            break
    return candles


def connect_quik(cfg: dict):
    """Прямое подключение к QUIK на своём слоте (как pattern_app). Возвращает qp или None."""
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


def run_scan(cfg: dict):
    qp = connect_quik(cfg)
    if qp is None:
        log.error("Нет подключения к QUIK — скан невозможен.")
        return
    log.info("Старт. ТФ=%d мин, инструментов %d, режим=%s, live_trading=%s",
             cfg.get("timeframe_minutes"), len(cfg.get("instruments", [])),
             cfg.get("mode"), cfg.get("live_trading"))
    log.info("ВАЖНО: открой в QUIK графики нужного ТФ с тегами из конфига — на них и рисую.")
    try:
        for instr in cfg.get("instruments", []):
            scan_instrument(qp, cfg, instr)
    finally:
        try:
            qp.close_connection_and_thread()
        except Exception:
            pass
    log.info("Готово. Лог: %s", HERE / cfg.get("log_dir", "logs") / "pattern_robot.log")


def _run_mock(cfg: dict):
    """Гоняет скан-логику на тестовых свечах, рисование заменено заглушкой-логом."""
    log.info("MOCK-режим: QUIK не используется, проверяю логику скана и формат лога.")

    def candle(y, mo, d, h, mi, o, hi, lo, c):
        return {"datetime": {"year": y, "month": mo, "day": d, "hour": h, "min": mi, "sec": 0},
                "open": o, "high": hi, "low": lo, "close": c}

    candles = [
        candle(2026, 6, 26, 7, 0, 100, 101, 96, 97),
        candle(2026, 6, 26, 7, 5, 97, 98, 93, 94),
        candle(2026, 6, 26, 7, 10, 94, 95, 90, 91),
        candle(2026, 6, 26, 7, 15, 91, 105, 90, 103),
        candle(2026, 6, 26, 7, 20, 103, 122, 96, 118),
    ]

    class MockQP:
        def get_num_candles(self, tag): return {"data": len(candles)}
        def get_candles(self, tag, a, off, batch): return {"data": candles[off:off + batch]}
        def del_all_labels(self, tag): log.info("MOCK: очистил метки графика '%s'", tag)
        def process_request(self, req):
            log.info("MOCK: addLabel2 -> %s", req["data"]); return {"data": "ok"}
        def close_connection_and_thread(self): pass

    instr = next(iter(cfg.get("instruments", [{"name": "RTS", "chart_tag": "rts5m", "price_step": 1}])))
    instr = dict(instr); instr["price_step"] = 1; instr["rub_per_point"] = 1.0
    scan_instrument(MockQP(), cfg, instr)
    log.info("MOCK завершён.")


# --- живой режим ---------------------------------------------------------------
import re as _re
import time as _time
from datetime import datetime as _dt, timezone as _tz, timedelta as _td

MONTH_CODES = "FGHJKMNQUVXZ"


def resolve_ticker(qp, cls, instr):
    """Тикер для чтения ОИ: из конфига, иначе фронт-контракт по ближайшей экспирации."""
    if instr.get("ticker"):
        return instr["ticker"]
    prefix = instr.get("prefix", "")
    try:
        secs = (qp.get_class_securities(cls).get("data") or "").split(",")
    except Exception:  # noqa: BLE001
        return None
    pat = _re.compile("^" + _re.escape(prefix) + "[" + MONTH_CODES + r"]\d$")
    cands = [s for s in secs if s and pat.match(s)]
    today = int(_dt.now(_tz.utc).strftime("%Y%m%d"))
    best, bm = None, None
    for s in cands:
        try:
            mat = int((qp.get_security_info(cls, s).get("data") or {}).get("mat_date"))
        except (TypeError, ValueError, AttributeError):
            continue
        if mat >= today and (bm is None or mat < bm):
            best, bm = s, mat
    return best


def _read_oi(qp, cls, sec):
    """Текущий открытый интерес (NUMCONTRACTS) по инструменту, либо None."""
    if not sec:
        return None
    try:
        data = qp.get_param_ex(cls, sec, "NUMCONTRACTS").get("data") or {}
        if str(data.get("result")) != "1":
            return None
        v = data.get("param_value")
        return float(v) if v not in (None, "") else None
    except Exception:  # noqa: BLE001
        return None


def _rub_per_point(qp, cls, sec, instr):
    """Стоимость 1 пункта цены в рублях для 1 контракта.

    = STEPPRICE (стоимость шага цены, ₽) / price_step (шаг в пунктах).
    Живьём из QUIK; для RTS плавает по курсу доллара (Si = ровно 1 ₽/пункт).
    Фолбэк — instr['rub_per_point'] из конфига; иначе None (рубли не считаем).
    """
    step = float(instr.get("price_step", 1)) or 1.0
    if sec:
        try:
            d = qp.get_param_ex(cls, sec, "STEPPRICE").get("data") or {}
            if str(d.get("result")) == "1":
                sp = float(d.get("param_value"))
                if sp > 0:
                    return sp / step
        except Exception:  # noqa: BLE001
            pass
    rpp = instr.get("rub_per_point")
    return float(rpp) if rpp else None


def _safe_ticker(qp, cls, instr):
    """resolve_ticker, но любая ошибка -> None (для скан-режима без падений)."""
    try:
        return resolve_ticker(qp, cls, instr)
    except Exception:  # noqa: BLE001
        return None


def _read_go(qp, cls, sec):
    """ГО (начальная маржа) на 1 контракт, ₽. Параметры QUIK FORTS: BUYDEPO/SELLDEPO.

    Берём max(ГО покупателя, ГО продавца) — консервативно: позиция может быть и лонг,
    и шорт. None, если не прочиталось (тогда во вкладке покажется «н/д»).
    """
    if not sec:
        return None
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


def _bar_dt(candle):
    dt = candle.get("datetime", {})
    try:
        return _dt(int(dt["year"]), int(dt["month"]), int(dt["day"]),
                   int(dt.get("hour", 0)), int(dt.get("min", 0)))
    except (KeyError, TypeError, ValueError):
        return None


def _load_recent(qp, tag, want=None):
    """Грузит последние want свечей с графика (или все, если want=None). Возвращает (свечи, n)."""
    n = _num_candles(qp, tag)
    if not n or n < 1:
        return [], (n or 0)
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
    return candles, n


def _oi_string(st, oi_now, bar_dt, tf, is_newest, initial):
    """Строка ОИ для лога: история -> н/д; живьём -> значение и дельта свеча-к-свече."""
    if initial:
        return "ОИ=н/д (история)"
    if not is_newest:
        return "ОИ=н/д (догон)"
    if oi_now is None:
        return "ОИ=н/д"
    if st["last_oi"] is None:
        return f"ОИ={oi_now:.0f} (Δ н/д — первая свеча)"
    if st["last_oi_dt"] is not None and bar_dt is not None and (bar_dt - st["last_oi_dt"]) == _td(minutes=tf):
        return f"ОИ={oi_now:.0f} (Δ {oi_now - st['last_oi']:+.0f})"
    return f"ОИ={oi_now:.0f} (Δ через разрыв)"


def _emit_signal(qp, cfg, st, sig, candle, source, oi_str, on_event=None):
    """Рисует стрелку и уровни, пишет строку сигнала, дёргает execute_signal (заглушка)."""
    instr = st["instr"]; name = instr["name"]; tag = instr.get("chart_tag", "")
    st["found"] += 1
    num = st["found"]
    note = (" | " + "; ".join(sig.notes)) if sig.notes else ""
    drawn = "да"
    try:
        hint = f"3+1 {sig.side} Вход={sig.entry:.2f} СЛ={sig.stop:.2f} ТП={sig.target:.2f}"
        chart.draw_arrow(qp, tag, cfg["arrows"], candle, sig.side == "long", num, hint)
        chart.draw_levels(qp, tag, candle, sig.entry, sig.stop, sig.target)
    except Exception as e:  # noqa: BLE001
        drawn = f"нет ({e!r})"
    log.info("[%s] %s #%d: %s · бар %s · вход=%.2f стоп=%.2f тейк=%.2f риск=%.0f п. · %s · стрелка=%s · режим=%s%s",
             name, source.upper(), num, sig.side.upper(), chart.fmt_dt(candle),
             sig.entry, sig.stop, sig.target, sig.risk, oi_str, drawn, cfg.get("mode"), note)
    execute_signal(cfg, instr, sig, candle, source)
    if on_event:
        on_event("signal", {"instrument": name, "source": source, "num": num,
                            "side": sig.side, "bar": chart.fmt_dt(candle),
                            "entry": sig.entry, "stop": sig.stop, "target": sig.target,
                            "risk": sig.risk, "oi": oi_str, "drawn": drawn,
                            "mode": cfg.get("mode"), "notes": list(sig.notes)})


def _process_bars(qp, cfg, st, initial, on_event=None):
    """Добирает закрытые бары новее последнего обработанного и обрабатывает по порядку.

    initial=True: проход по всей истории (разметка), исход считаем (данные полные).
    initial=False: онлайн-добор, ОИ читаем живьём, исход ещё неизвестен.
    """
    instr = st["instr"]; tag = instr.get("chart_tag", "")
    cls = cfg["class_code"]; step = float(instr.get("price_step", 1))
    rev = cfg["reversal_3plus1"]; tf = int(cfg["timeframe_minutes"])

    candles, n = _load_recent(qp, tag, want=None if initial else max(80, 5))
    if len(candles) < 5:
        if initial:
            log.error("[%s] свечей мало (%d) — проверь тег графика.", instr["name"], len(candles))
        return
    closed = candles[:-1]  # последний бар формируется — исключаем из анализа
    if initial and closed:
        st["hist_from"] = _bar_dt(closed[0])
        st["hist_to"] = _bar_dt(closed[-1])
    new = [(idx, c) for idx, c in enumerate(closed)
           if st["last_dt"] is None or (_bar_dt(c) is not None and _bar_dt(c) > st["last_dt"])]
    if not new:
        return

    oi_now = None if initial else _read_oi(qp, cls, st["sec"])
    last_dt_seen = None
    for k, (idx, c) in enumerate(new):
        sig = detect(closed[:idx + 1], rev, step)
        bdt = _bar_dt(c)
        if sig is not None:
            oi_str = _oi_string(st, oi_now, bdt, tf, k == len(new) - 1, initial)
            _emit_signal(qp, cfg, st, sig, c, "scan" if initial else "live", oi_str, on_event)
            if initial:
                st["outcomes"].append(evaluate_outcome(closed, idx, sig, rev))
        st["last_dt"] = bdt
        last_dt_seen = bdt
    if oi_now is not None and last_dt_seen is not None:
        st["last_oi"] = oi_now
        st["last_oi_dt"] = last_dt_seen
    return len(new), oi_now


def _next_wake(now, tf, delay):
    """Следующая граница бара (UTC) + запас delay секунд."""
    minute = (now.minute // tf) * tf
    boundary = now.replace(minute=minute, second=0, microsecond=0) + _td(minutes=tf)
    return boundary + _td(seconds=delay)


def _account_payload(snap):
    """Собирает payload события 'account' для окна из снимка account.read_account."""
    if snap is None:
        return {"ok": False, "lines": account.format_account(None)}
    return {"ok": True,
            "currency": snap["currency"],
            "equity": snap["equity_derived"],
            "free": snap["free_derived"],
            "limit": snap["limit"],
            "used": snap["used"],
            "varmargin": snap["varmargin"],
            "commission": snap["commission"],
            "go": snap["go_planned"] or snap["go_without_orders"],
            "kgo": snap["kgo"],
            "risk_level": snap["risk_level"],
            "trdacc": account._mask(snap["trdacc"]),
            "firm": snap["firm_id"],
            "lines": account.format_account(snap)}


def _emit_account(qp, firm, acc, on_event, log_it=False):
    """Читает средства (read-only) и шлёт событие 'account' в окно. Никаких заявок."""
    try:
        snap = account.read_account(qp, firm, acc)
    except Exception as e:  # noqa: BLE001
        log.warning("Чтение средств не удалось: %r", e)
        snap = None
    if log_it and snap is not None:
        for line in account.format_account(snap):
            log.info("[счёт] %s", line)
    if on_event:
        on_event("account", _account_payload(snap))


def run_live(cfg, qp=None, max_iterations=None, now_fn=None, sleep_fn=None,
             stop_event=None, on_event=None):
    """Оба этапа: разметка истории при старте, затем онлайн по таймеру.

    max_iterations/now_fn/sleep_fn — точки тестируемости; в бою не задаются.
    stop_event/on_event — для окна: остановка из GUI и поток событий в интерфейс.
    """
    own = qp is None
    if qp is None:
        qp = connect_quik(cfg)
        if qp is None:
            log.error("Нет подключения к QUIK — живой режим невозможен.")
            if on_event:
                on_event("error", {"text": "Нет подключения к QUIK"})
            return
    now_fn = now_fn or (lambda: _dt.now(_tz.utc))
    sleep_fn = sleep_fn or _time.sleep
    cls = cfg["class_code"]; tf = int(cfg["timeframe_minutes"]); delay = float(cfg["bar_close_delay_seconds"])

    log.info("Старт LIVE. ТФ=%d мин, инструментов %d, режим=%s, live_trading=%s",
             tf, len(cfg.get("instruments", [])), cfg.get("mode"), cfg.get("live_trading"))
    log.info("ВАЖНО: держи открытыми графики нужного ТФ с тегами из конфига — на них рисую.")

    state = {}
    for instr in cfg.get("instruments", []):
        sec = resolve_ticker(qp, cls, instr)
        state[instr["name"]] = {"instr": instr, "sec": sec, "last_dt": None,
                                "last_oi": None, "last_oi_dt": None, "found": 0, "outcomes": []}
        log.info("[%s] тег='%s', тикер для ОИ=%s", instr["name"], instr.get("chart_tag"), sec or "н/д")
    if on_event:
        on_event("start", {"tf": tf, "mode": cfg.get("mode"), "live_trading": cfg.get("live_trading"),
                           "instruments": [{"name": n, "tag": s["instr"].get("chart_tag"), "sec": s["sec"]}
                                           for n, s in state.items()]})

    # начальный проход: разметить историю и встать на последний закрытый бар
    for st in state.values():
        chart.del_all_labels(qp, st["instr"].get("chart_tag", ""))
        _process_bars(qp, cfg, st, initial=True, on_event=on_event)
        rpp = _rub_per_point(qp, cls, st["sec"], st["instr"])
        go = _read_go(qp, cls, st["sec"])
        lots = int(cfg.get("volume_lots", 1))
        s = summarize(st["outcomes"], rub_per_point=rpp)
        if s["total"]:
            period = period_stats(st.get("hist_from"), st.get("hist_to"), s["target"] + s["stop"])
            for line in summary_report(st["outcomes"], rub_per_point=rpp, period=period):
                log.info("[%s] %s", st["instr"]["name"], line)
            log.info("[%s] ГО (нач. маржа) ≈ %s ₽/контракт (×%d лот)",
                     st["instr"]["name"], f"{go:.0f}" if go else "н/д", lots)
            if on_event:
                pay = {"instrument": st["instr"]["name"], "total": s["total"],
                       "target": s["target"], "stop": s["stop"], "no_fill": s["no_fill"],
                       "win_rate": s["win_rate_resolved"], "sum_R": s["sum_R"],
                       "avg_R": s["avg_R"], "exp_per_signal": s["exp_per_signal"],
                       "sum_rub": s["sum_rub"], "rub_per_point": rpp,
                       "by_risk": summarize_by_risk(st["outcomes"], rub_per_point=rpp),
                       "drawdown": drawdown_stats(st["outcomes"], rub_per_point=rpp),
                       "go": go, "volume_lots": lots,
                       "rr": float(cfg.get("reversal_3plus1", {}).get("rr_ratio", 3.0)),
                       "report": summary_report(st["outcomes"], rub_per_point=rpp, period=period)}
                if period:
                    pay.update({"period_from": f"{period['from']:%d.%m.%Y}",
                                "period_to": f"{period['to']:%d.%m.%Y}",
                                "period_days": period["days"],
                                "freq_per_week": period["per_week"]})
                on_event("history", pay)
    log.info("История размечена. Онлайн: проверка каждые %d мин (+%.0f c после закрытия бара). Ctrl+C — стоп.",
             tf, delay)

    # начальный снимок средств (read-only) — чтобы вкладка «Счёт» сразу ожила
    acct_firm, acct_trdacc = account.find_futures_account(qp)
    if acct_firm and acct_trdacc:
        log.info("Счёт для средств: фирма %s · %s", acct_firm, account._mask(acct_trdacc))
    else:
        log.info("Фьючерсный счёт для средств не опознан — вкладка «Счёт» покажет н/д.")
    _emit_account(qp, acct_firm, acct_trdacc, on_event, log_it=True)
    acct_every = float(cfg.get("account_poll_seconds", 10))

    if on_event:
        on_event("online", {"tf": tf, "delay": delay})

    def _stopped():
        return stop_event is not None and stop_event.is_set()

    it = 0
    next_acct = now_fn()
    try:
        while (max_iterations is None or it < max_iterations) and not _stopped():
            wake = _next_wake(now_fn(), tf, delay)
            if on_event:
                on_event("waiting", {"until": wake.strftime("%H:%M:%S")})
            while now_fn() < wake and not _stopped():
                if now_fn() >= next_acct:
                    _emit_account(qp, acct_firm, acct_trdacc, on_event)
                    next_acct = now_fn() + _td(seconds=acct_every)
                sleep_fn(min(1.0, (wake - now_fn()).total_seconds()))
            if _stopped():
                break
            if on_event:
                on_event("wake", {"time": now_fn().strftime("%H:%M:%S")})
            for st in state.values():
                _process_bars(qp, cfg, st, initial=False, on_event=on_event)
            it += 1
    except KeyboardInterrupt:
        log.info("Live остановлен пользователем.")
    finally:
        if own:
            try:
                qp.close_connection_and_thread()
            except Exception:
                pass
    log.info("Live завершён.")
    if on_event:
        on_event("stopped", {})
    return state


def main():
    ap = argparse.ArgumentParser(description="pattern_robot — 3+1 стрелки на графике QUIK")
    ap.add_argument("--mode", choices=["scan", "live", "both"], default="both",
                    help="scan — разовый проход; live/both — история + онлайн")
    ap.add_argument("--mock", action="store_true", help="без QUIK: демонстрация на тестовых свечах")
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    cfg = load_config()
    setup_logging(cfg)

    if args.mock:
        _run_mock(cfg)
        return
    if args.mode == "scan":
        run_scan(cfg)
    else:
        run_live(cfg)


if __name__ == "__main__":
    main()
