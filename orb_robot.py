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
import inspect
import logging
import shutil
import sys
import time as _time
from collections import deque
from dataclasses import replace as _replace
from datetime import date, datetime, time as _dtime, timedelta
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

import orb_account
import orb_broker_quik
import orb_calendar
import orb_journal
import orb_risk
import orb_strategy

HERE = Path(__file__).resolve().parent
log = logging.getLogger("orb_robot")


def load_config(path: str | Path | None = None) -> dict:
    path = Path(path) if path else HERE / "config_orb.yaml"
    # config_orb.yaml локальный (в git его нет) — при отсутствии создаём из шаблона,
    # чтобы свежий клон / случайно удалённый файл не роняли робота с FileNotFoundError
    if not path.exists():
        example = HERE / "config_orb.example.yaml"
        if example.exists():
            shutil.copyfile(example, path)
            log.warning("config_orb.yaml не найден — создан из config_orb.example.yaml. "
                        "Проверь настройки (account/тег/депозит) на вкладке «Настройки».")
        else:
            raise FileNotFoundError(
                f"нет ни {path.name}, ни config_orb.example.yaml в {path.parent}")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def save_config(cfg: dict, path: str | Path | None = None) -> None:
    """Атомарная запись config_orb.yaml: во временный файл и замена, чтобы при
    сбое посередине не остался повреждённый конфиг (см. pattern у старого робота)."""
    path = Path(path) if path else HERE / "config_orb.yaml"
    tmp = path.with_suffix(path.suffix + ".part")
    with open(tmp, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)
    tmp.replace(path)


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
    try:
        vol = float(candle.get("volume", 0.0) or 0.0)
    except (TypeError, ValueError):
        vol = 0.0
    return orb_strategy.Bar(dt, float(candle["open"]), float(candle["high"]),
                             float(candle["low"]), float(candle["close"]), vol)


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


def _read_last(qp, cls: str, sec: str) -> float | None:
    """Последняя цена (LAST) контракта sec напрямую по коду — для сверки графика."""
    try:
        d = qp.get_param_ex(cls, sec, "LAST").get("data") or {}
        if str(d.get("result")) == "1":
            v = float(d.get("param_value"))
            if v > 0:
                return v
    except Exception:  # noqa: BLE001
        pass
    return None


def chart_sec(qp, tag: str) -> str | None:
    """Best-effort: код инструмента, к которому привязан график с тегом tag.

    Зависит от версии QuikPy — метода может не быть; тогда None и охрана графика
    работает по цене/свежести. Пробуем несколько правдоподобных имён гейттеров,
    все обёрнуты в try/except (только чтение)."""
    for meth in ("get_tag_seccode", "get_chart_seccode", "get_datasource_seccode", "get_tag_info"):
        fn = getattr(qp, meth, None)
        if fn is None:
            continue
        try:
            r = fn(tag)
        except Exception:  # noqa: BLE001
            continue
        val = r.get("data") if isinstance(r, dict) and "data" in r else r
        if isinstance(val, dict):
            val = val.get("sec_code") or val.get("seccode")
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def _make_bmp(path, rgb: tuple, size: int = 9) -> None:
    """Пишет крошечный BMP-квадрат сплошного цвета (24-bit, без сжатия) — маркер
    для меток на графике QUIK (эта версия QuikPy показывает метку только картинкой)."""
    import struct
    r, g, b = rgb
    row = bytes((b, g, r)) * size + b"\x00" * ((-size * 3) % 4)   # BGR + паддинг строки
    pixels = row * size
    file_size = 14 + 40 + len(pixels)
    header = b"BM" + struct.pack("<IHHI", file_size, 0, 0, 54)
    dib = struct.pack("<IiiHHIIiiII", 40, size, size, 1, 24, 0, len(pixels), 2835, 2835, 0, 0)
    with open(path, "wb") as f:
        f.write(header + dib + pixels)


def _ensure_label_icons():
    """Создаёт (если нет) цветные картинки-маркеры и возвращает {имя: абс.путь}."""
    colors = {"buy": (0, 170, 0), "sell": (210, 0, 0), "win": (0, 110, 220), "loss": (230, 140, 0)}
    d = HERE / "label_icons"
    try:
        d.mkdir(exist_ok=True)
        out = {}
        for name, rgb in colors.items():
            p = d / f"{name}.bmp"
            if not p.exists():
                _make_bmp(p, rgb)
            out[name] = str(p)
        return out
    except Exception:  # noqa: BLE001
        return None


def _ascii_safe(s: str) -> str:
    """cp1251-безопасный текст: QUIK кодирует команду в cp1251, символы вне неё
    (▲▼ • и пр.) ломают addLabel2. Непереводимое заменяем на '?'. Пайп — разделитель."""
    return s.encode("cp1251", "replace").decode("cp1251").replace("|", "/")


def _add_label2(qp, tag, y_value, dt: datetime, text: str, rgb: tuple, align: str,
                hint: str = "", font_h: int = 12):
    """Метка с ТЕКСТОМ и цветом через сырую команду addLabel2 (qp.process_request).
    Так текст рисуется на версиях QuikPy, где у add_label нет поля TEXT (порядок полей
    и подход — как в chart.py робота pattern: tag|y|date|time|text|img|align|hint|r|g|b|
    transp|trans_bg|font|font_h)."""
    dn, tn = dt.strftime("%Y%m%d"), dt.strftime("%H%M%S")
    r, g, b = rgb
    data = "|".join([
        tag, f"{y_value:.6f}", dn, tn,
        _ascii_safe(text), "", align, _ascii_safe(hint or text),
        str(r), str(g), str(b), "0", "1", "Arial", str(font_h),
    ])
    return qp.process_request({"data": data, "id": 0, "cmd": "addLabel2", "t": ""})


def _label_dict(dt: datetime, price: float, text: str, rgb: tuple, align: str, image: str) -> dict:
    """Полный набор параметров метки (для dict-версии AddLabel других версий QuikPy)."""
    return {
        "TEXT": text, "IMAGE_PATH": image, "ALIGNMENT": align,
        "YVALUE": f"{price:.0f}", "DATE": dt.strftime("%Y%m%d"), "TIME": dt.strftime("%H%M%S"),
        "R": rgb[0], "G": rgb[1], "B": rgb[2],
        "TRANSPARENCY": 0, "TRANSPARENT_BACKGROUND": 1,
        "FONT_FACE_NAME": "Arial", "FONT_HEIGHT": "10",
        "HINT": f"{dt:%Y-%m-%d %H:%M} {text}",
    }


def _add_one_label(add, tag, dt, price, align, image, params):
    """Вызывает add_label, подстраиваясь под сигнатуру версии QuikPy: позиционная
    (price, cur_date, cur_time, qty, path, chart_tag, alignment, background) с
    картинкой в path, либо dict-форма add_label(tag, params). Возвращает id."""
    values = {
        "chart_tag": tag, "tag": tag,
        "label_params": params, "labelparams": params, "label": params, "params": params,
        "price": f"{price:.0f}", "y_value": f"{price:.0f}", "yvalue": f"{price:.0f}",
        "cur_date": dt.strftime("%Y%m%d"), "date": dt.strftime("%Y%m%d"),
        "cur_time": dt.strftime("%H%M%S"), "time": dt.strftime("%H%M%S"),
        "qty": 0, "path": image, "image_path": image,
        "alignment": align, "background": 0, "transparent_background": 1,
    }
    try:
        names = [p for p in inspect.signature(add).parameters if p != "self"]
        kwargs = {n: values[n] for n in names if n in values}
        if len(kwargs) >= max(1, len(names) - 1):     # покрыли (почти) все аргументы -> зовём по имени
            r = add(**kwargs)
        else:
            r = add(tag, params)                       # не распознали — пробуем dict-форму
    except (ValueError, TypeError):
        r = add(tag, params)
    return r.get("data") if isinstance(r, dict) and "data" in r else r


def _trade_marks(tr):
    """Две метки на сделку: вход (BUY/SELL, зелёный/красный) и выход (PnL, зелёный/красный).
    Возвращает список (dt, price, text, rgb, align, icon_key)."""
    long = tr.dir == "long"
    win = tr.pnl_rub > 0
    return [
        (tr.datetime_in, tr.entry, "BUY" if long else "SELL",
         (0, 180, 0) if long else (255, 40, 40), "BOTTOM" if long else "TOP",
         "buy" if long else "sell"),
        (tr.datetime_out, tr.exit, f"{tr.pnl_rub:+.0f}",
         (0, 180, 0) if win else (255, 40, 40), "TOP",
         "win" if win else "loss"),
    ]


def add_trade_labels(qp, tag: str, trades: list) -> tuple:
    """Рисует метки входа/выхода сделок на графике QUIK по тегу (для визуального
    разбора QUIK-бэктеста). Возвращает (число_меток, ошибка|None, диагностика).

    Основной путь — текстовые цветные метки через сырую команду addLabel2
    (qp.process_request): у add_label этой версии QuikPy нет поля TEXT, а addLabel2 есть.
    Если process_request недоступен — фолбэк на картинки-маркеры через add_label."""
    methods = [m for m in dir(qp) if "label" in m.lower()]
    for m in ("del_all_labels", "delete_all_labels", "DelAllLabels"):   # снять старые
        fn = getattr(qp, m, None)
        if fn is not None:
            try:
                fn(tag)
            except Exception:  # noqa: BLE001
                pass
            break
    diag = f"label-методы: {','.join(methods) or 'нет'}"

    pr = getattr(qp, "process_request", None)
    if callable(pr):                                # --- текст через addLabel2 (основной путь) ---
        n, first_id = 0, "?"
        for tr in trades:
            for dt, price, text, rgb, align, _icon in _trade_marks(tr):
                try:
                    r = _add_label2(qp, tag, price, dt, text, rgb, align,
                                    hint=f"{dt:%Y-%m-%d %H:%M} {text}")
                    if first_id == "?":
                        lid = r.get("data") if isinstance(r, dict) and "data" in r else r
                        first_id = f"{lid!r} ({type(lid).__name__})"
                    n += 1
                except Exception as e:  # noqa: BLE001
                    return n, repr(e), diag + "; способ=addLabel2"
        diag += f"; способ=addLabel2(текст+цвет); id1={first_id}"
        return n, None, diag

    # --- фолбэк: картинки-маркеры через add_label (версии без process_request) ---
    icons = _ensure_label_icons() or {}
    add = getattr(qp, "add_label", None) or getattr(qp, "AddLabel", None)
    try:                                            # какие поля принимает add_label этой версии
        sig = ",".join(p for p in inspect.signature(add).parameters if p != "self")
        diag += f"; add_label({sig})"
    except (TypeError, ValueError):
        pass
    if add is None:
        return 0, "QuikPy не поддерживает ни process_request, ни add_label — метки недоступны", diag
    setp = getattr(qp, "set_label_params", None) or getattr(qp, "SetLabelParams", None)
    n = 0
    first_id = "?"
    setp_err = None
    for tr in trades:
        for dt, price, text, rgb, align, icon in _trade_marks(tr):
            image = icons.get(icon, "")
            params = _label_dict(dt, price, text, rgb, align, image)
            try:
                lid = _add_one_label(add, tag, dt, price, align, image, params)
                if first_id == "?":
                    first_id = f"{lid!r} ({type(lid).__name__})"
                n += 1
                if setp is not None and lid is not None:   # текст/цвет отдельным вызовом (др. версии)
                    try:
                        setp(tag, lid, params)
                    except Exception as e:  # noqa: BLE001
                        setp_err = repr(e)
            except Exception as e:  # noqa: BLE001
                return n, repr(e), diag
    diag += f"; способ=картинки; иконки={'есть' if icons else 'НЕТ'}"
    diag += f"; set_label_params={'есть' if setp else 'НЕТ'}; id1={first_id}"
    if setp_err:
        diag += f"; set упал: {setp_err}"
    return n, None, diag


class OrbOrchestrator:
    """Общая логика обработки одного закрытого бара для paper и live.

    live=False (paper) — заявки не отправляются, только журнал и лог.
    live=True — при live_trading=True в конфиге реально шлёт заявки через orb_broker_quik.
    """

    def __init__(self, cfg: dict, qp, live: bool, on_event=None, name: str = "",
                 entry_filter=None, paths: dict | None = None):
        self.cfg = cfg
        self.qp = qp
        self.live = live
        self.on_event = on_event
        self.name = name                       # "" — чемпион; иначе теневой вариант
        self.entry_filter = entry_filter       # callable(side, bar, rel_vol, range_width)->bool или None
        self.state = orb_strategy.OrbState()
        risk_cfg = cfg.get("risk", {})
        self.risk_cfg = orb_risk.RiskConfig(**risk_cfg) if risk_cfg else orb_risk.RiskConfig()
        strat_cfg = cfg.get("strategy", {})
        self.allow_position_flip = bool(strat_cfg.get("allow_position_flip", False))
        self.expiration_zone_mode = strat_cfg.get("expiration_zone_mode", "trading_days")
        p = paths or cfg["paths"]              # теневой вариант пишет в свои файлы
        self.risk_path = HERE / p["risk_state_json"]
        self.risk_state = orb_risk.load_risk_state(self.risk_path)
        self.trades_path = HERE / p["trades_csv"]
        self.skips_path = HERE / p["skips_csv"]
        self._volwin: deque = deque(maxlen=8)  # скользящий объём для rel_vol (теневые фильтры)
        self._vol_day = None
        self._daily_ranges: deque = deque(maxlen=10)  # отн. дневной диапазон прошлых дней, % (режим волат.)
        self._day_hl = None                    # (high, low, close) текущего дня — копится по барам
        self.kill_dir = HERE / cfg.get("kill_switch_dir", ".")
        self.open_meta: dict | None = None
        self.pending_entry: orb_strategy.EntrySignal | None = None
        self.halted_by_kill_switch = False
        # сверка исполнения (пункт №1): читаем фактическую позицию/стопы из QUIK
        exe = cfg.get("execution", {})
        self.verify_exec = bool(exe.get("verify", True))
        self.confirm_timeout = float(exe.get("confirm_timeout_seconds", 10.0))
        self.confirm_poll = float(exe.get("confirm_poll_seconds", 1.0))
        self.close_retries = int(exe.get("close_retries", 3))
        self._acct: tuple = (None, None)      # (firm_id, trdacc) — ленивое определение
        self.stop_ref_active = False          # выставлена ли живая стоп-заявка по текущей позиции
        # охрана графика: график (источник сигналов) должен совпадать с торгуемым контрактом
        cg = cfg.get("chart_guard", {})
        self.chart_guard_on = bool(cg.get("enabled", True))
        self.chart_tol_pct = float(cg.get("price_tolerance_pct", 1.0))
        self.chart_max_age_min = float(cg.get("max_bar_age_minutes", 40.0))
        self.chart_tag = cfg.get("chart_tag", "")
        self.chart_block = False               # входы заблокированы из-за неверного графика
        self.chart_block_reason = ""
        # сайзинг (пункт №4): защита от мусорных чтений ГО/rpp + опционально от живого equity
        sz = cfg.get("sizing", {})
        self.size_from_equity = bool(sz.get("from_live_equity", False))
        self.go_min = float(sz.get("go_min_rub", 3000.0))
        self.go_max = float(sz.get("go_max_rub", 100000.0))
        self.equity_min = float(sz.get("equity_min_rub", 10000.0))
        # издержки для honest live/paper PnL (пункт 1): та же модель, что в бэктесте
        bt_cfg = cfg.get("backtest", {})
        self.commission_side = float(bt_cfg.get("commission_per_side_rub", 5.0))
        self.slippage_ticks = int(bt_cfg.get("slippage_ticks", 2))
        self.tick_size = float(cfg.get("tick_size", 1.0))

    def _emit(self, kind: str, data: dict) -> None:
        if self.on_event:
            self.on_event(kind, data)

    def _contract(self, d: date) -> str:
        return orb_calendar.active_contract(d)

    def _sizing_inputs(self, sec: str):
        """Стоимость пункта и ГО на контракт с защитой от мусорных чтений (пункт №4):
        rpp<=0 -> 1.0; ГО вне [go_min, go_max] или не прочитано -> go_per_contract_assumed."""
        cls = self.cfg["class_code"]
        tick = float(self.cfg.get("tick_size", 1.0))
        rpp = _rub_per_point(self.qp, cls, sec, tick) or 1.0
        if rpp <= 0:
            rpp = 1.0
        fallback_go = float(self.cfg.get("backtest", {}).get("go_per_contract_assumed", 12000.0))
        go_raw = _read_go(self.qp, cls, sec)
        if go_raw is not None and self.go_min <= go_raw <= self.go_max:
            go = go_raw
        else:
            if go_raw is not None:
                log.warning("ГО из QUIK %.0f вне [%.0f, %.0f] — беру фолбэк %.0f (защита сайзинга).",
                            go_raw, self.go_min, self.go_max, fallback_go)
            go = fallback_go
        return rpp, go

    def _sizing_basis(self) -> float:
        """Капитал для расчёта размера позиции. По умолчанию — deposit_rub из конфига;
        при sizing.from_live_equity — живой equity из QUIK (с фолбэком на deposit_rub,
        если чтение не удалось или подозрительно мало'). Риск-лимиты (дневной/недельный)
        всегда считаются от deposit_rub — это фиксированная точка отсчёта потерь."""
        deposit = float(self.cfg["deposit_rub"])
        if not self.size_from_equity:
            return deposit
        try:
            firm, trdacc = self._acct_ids()
            snap = orb_account.read_account(self.qp, firm, trdacc)
        except Exception:  # noqa: BLE001
            snap = None
        eq = snap.get("equity_derived") if snap else None
        if eq is None or eq < self.equity_min:
            log.warning("equity из QUIK не прочитан/мал (%s) — сайзинг от deposit_rub %.0f.", eq, deposit)
            return deposit
        return float(eq)

    def _acct_ids(self):
        if self._acct == (None, None):
            self._acct = orb_account.find_futures_account(self.qp)
        return self._acct

    def _net_position(self, sec: str) -> int | None:
        """Фактическая чистая позиция из QUIK (>0 лонг, <0 шорт, 0 плоско, None — не прочитать)."""
        firm, trdacc = self._acct_ids()
        return orb_account.read_position(self.qp, sec, firm, trdacc)

    def _confirm_net(self, sec: str, expected: int):
        """Опрашивает позицию до совпадения с expected или таймаута. -> (ok, actual|None)."""
        deadline = _time.monotonic() + self.confirm_timeout
        actual = self._net_position(sec)
        while actual != expected and _time.monotonic() < deadline:
            _time.sleep(max(self.confirm_poll, 0.0))
            actual = self._net_position(sec)
        return actual == expected, actual

    def adopt_live_position(self, sec: str) -> None:
        """Пункт №2: на старте LIVE сверяет позицию, восстановленную по барам, с
        ФАКТИЧЕСКОЙ в QUIK и приводит внутреннее состояние к реальности.

        Матрица: QUIK плоско + реконструкция плоско -> ок; QUIK плоско +
        реконструкция позиция -> закрылась пока робот был offline, сброс; QUIK
        позиция -> подхватываем её (qty/сторона из позиции, стоп из стоп-заявки,
        цена входа из средней позиции), помечаем сторону использованной. Если
        позиция «неожиданная» (реконструкция её не дала) — громкий алерт."""
        detail = orb_account.read_position_detail(self.qp, sec, *self._acct_ids())
        if detail is None:
            log.warning("СТАРТ: фактическую позицию из QUIK не прочитать — оставляю реконструкцию по барам, "
                        "сверь вручную!")
            self._emit("error", {"text": "позиция из QUIK не читается на старте — сверь вручную"})
            return
        net = detail["net"]
        recon = self.state.position

        if net == 0:
            if recon is not None:
                log.warning("СТАРТ: по барам позиция %s, но в QUIK плоско — закрылась, пока робот был offline. "
                            "Сбрасываю состояние в плоское.", recon.side)
                self._emit("error", {"text": f"позиция {recon.side} закрылась, пока робот был offline — "
                                             f"состояние сброшено в плоское"})
                used = {"long_used": True} if recon.side == "long" else {"short_used": True}
                self.state = _replace(self.state, position=None, **used)
                self.open_meta = None
            return

        side = "long" if net > 0 else "short"
        qty = abs(net)
        found_stop = orb_broker_quik.active_stop_price(self.qp, self.cfg["class_code"], sec)
        self.stop_ref_active = found_stop is not None
        rh = self.state.range_high if self.state.range_high is not None else 0.0
        rl = self.state.range_low if self.state.range_low is not None else 0.0
        stop = found_stop
        if stop is None:
            stop = rl if side == "long" else rh
            log.warning("СТАРТ: активной стоп-заявки по %s не нашёл — беру границу диапазона %.2f. "
                        "ПРОВЕРЬ/ВЫСТАВЬ стоп в QUIK вручную!", sec, stop)
            self._emit("error", {"text": f"не найдена стоп-заявка подхваченной позиции {side} — проверь стоп в QUIK!"})
        entry_price = detail.get("avg_price")
        if entry_price is None:
            entry_price = recon.entry_price if recon else stop

        pos = orb_strategy.Position(side=side, entry_time=datetime.now(), entry_price=entry_price,
                                    stop_price=stop, range_high=rh, range_low=rl)
        used = {"long_used": True} if side == "long" else {"short_used": True}
        self.state = _replace(self.state, position=pos, **used)
        rpp, _ = self._sizing_inputs(sec)
        self.open_meta = {"side": side, "entry_time": pos.entry_time, "entry_price": entry_price,
                          "stop_price": stop, "qty": qty, "rub_per_point": rpp,
                          "range_width": rh - rl}

        if recon is None or recon.side != side:
            log.error("СТАРТ: подхватил НЕОЖИДАННУЮ позицию из QUIK: %s %d (реконструкция по барам её не дала) — "
                      "СВЕРЬ ВРУЧНУЮ.", side, qty)
            self._emit("error", {"text": f"подхвачена неожиданная позиция из QUIK: {side} {qty} — сверь вручную"})
        else:
            log.info("СТАРТ: подхватил открытую позицию из QUIK: %s %d, стоп %.2f, вход~%.2f.",
                     side, qty, stop, entry_price)

    def _evaluate_chart(self, bar: orb_strategy.Bar, sec: str, now: datetime | None = None) -> None:
        """Сверяет, что график (источник сигналов) = торгуемый контракт sec.

        Три проверки по убыванию точности: (1) точное имя sec за тегом, если QuikPy
        умеет; (2) цена графика vs LAST контракта; (3) свежесть последнего бара.
        При несоответствии ставит self.chart_block (входы блокируются), иначе снимает."""
        if not self.chart_guard_on:
            self.chart_block = False
            return
        cls = self.cfg["class_code"]

        got = chart_sec(self.qp, self.chart_tag)
        if got is not None:
            if got != sec:
                self._set_chart_block("sec", f"график привязан к {got}, а торгуем {sec}")
            else:
                self._clear_chart_block()
            return

        last = _read_last(self.qp, cls, sec)
        if last is not None and last > 0:
            dev = abs(bar.close - last) / last * 100
            if dev > self.chart_tol_pct:
                self._set_chart_block(
                    "price", f"цена графика {bar.close:.0f} расходится с LAST {sec} {last:.0f} "
                             f"на {dev:.1f}% (>{self.chart_tol_pct:g}%)")
                return

        # свежесть проверяем только в окне торгов (10:00-18:45): вне сессии свежих
        # баров и не ждём, иначе вечером/на выходных ложная тревога
        now_dt = now or datetime.now()
        age_min = (now_dt - bar.dt).total_seconds() / 60.0
        in_session = _dtime(10, 0) <= now_dt.time() <= _dtime(18, 45)
        if in_session and age_min > self.chart_max_age_min:
            self._set_chart_block(
                "stale", f"последний бар графика {bar.dt} старше {self.chart_max_age_min:g} мин "
                         f"(возможно, истёкший/неверный контракт)")
            return

        self._clear_chart_block()

    def _set_chart_block(self, code: str, msg: str) -> None:
        if not self.chart_block or self.chart_block_reason != code:
            log.error("НЕВЕРНЫЙ ГРАФИК: %s — входы заблокированы, открытую позицию контролируй вручную.", msg)
            self._emit("error", {"text": f"неверный график: {msg} — входы заблокированы"})
        self.chart_block = True
        self.chart_block_reason = code

    def _clear_chart_block(self) -> None:
        if self.chart_block:
            log.info("график снова соответствует торгуемому контракту — блок входов снят.")
            self._emit("chart_ok", {"text": "график снова соответствует контракту"})
        self.chart_block = False
        self.chart_block_reason = ""

    def _rel_volume(self, bar: orb_strategy.Bar) -> float:
        """Относительный объём бара = объём / средний объём окна (дня). 1.0 если нет базы."""
        base = sum(self._volwin) / len(self._volwin) if self._volwin else 0.0
        return (bar.volume / base) if base > 0 else 1.0

    def _regime_vol(self):
        """Режим волатильности = среднее отн. дневного диапазона за прошлые дни, %.
        None, если истории мало (<5 дней) — фильтр в этом случае не блокирует."""
        if len(self._daily_ranges) < 5:
            return None
        return sum(self._daily_ranges) / len(self._daily_ranges)

    def handle_bar(self, bar: orb_strategy.Bar, replay: bool = False) -> None:
        day = bar.dt.date()
        if self._vol_day != day:          # смена дня: финализируем диапазон прошлого дня
            if self._day_hl is not None:
                hi, lo, cl = self._day_hl
                if cl > 0:
                    self._daily_ranges.append((hi - lo) / cl * 100.0)
            self._vol_day = day
            self._volwin.clear()
            self._day_hl = None
        if self._day_hl is None:
            self._day_hl = (bar.high, bar.low, bar.close)
        else:
            h, l, _ = self._day_hl
            self._day_hl = (max(h, bar.high), min(l, bar.low), bar.close)
        self._volwin.append(bar.volume)
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
                    self._close_position(self._contract(day), self.state.position.side,
                                         self.open_meta["qty"] if self.open_meta else 0, reason="kill")
                if not replay:
                    self._emit("trade", self._trade_payload(trade))
                self.open_meta = None
            self.state = _replace(self.state, position=None)
            return

        if self.risk_state.day != day:
            self.risk_state = orb_risk.sync_day(self.risk_state, day)

        sec = self._contract(day)

        # охрана графика: сверяем источник сигналов с торгуемым контрактом (не на реплее)
        if not replay:
            self._evaluate_chart(bar, sec)

        if self.pending_entry is not None:
            entry = self.pending_entry
            self.pending_entry = None
            if self.chart_block:
                # неверный график: вход не открываем, но обработку бара продолжаем
                # (открытая позиция должна и дальше вестись/закрываться штатно)
                log.warning("вход %s ОТМЕНЁН: неверный график (%s).", entry.side, self.chart_block_reason)
                if not replay:
                    orb_journal.append_skip(self.skips_path, bar.dt, entry.side, "wrong_chart")
                    self._emit("skip", {"side": entry.side, "reason": "wrong_chart",
                                        "bar": bar.dt.isoformat(sep=" ")})
            else:
                rpp, go = self._sizing_inputs(sec)
                basis = self._sizing_basis()
                stop_points = abs(bar.open - entry.stop_price)
                qty = orb_risk.position_size(basis, stop_points, rpp, go, self.risk_cfg)
                if qty > 0:
                    self.state = orb_strategy.open_position(self.state, entry, bar.open)
                    self.open_meta = {"side": entry.side, "entry_time": bar.dt, "entry_price": bar.open,
                                       "stop_price": entry.stop_price, "qty": qty, "rub_per_point": rpp,
                                       "range_width": entry.range_high - entry.range_low}
                    log.info("ВХОД %s: бар=%s цена~%.2f стоп=%.2f qty=%d (капитал=%.0f ГО=%.0f rpp=%.2f стоп=%.0fпт)",
                             entry.side.upper(), bar.dt, bar.open, entry.stop_price, qty,
                             basis, go, rpp, stop_points)
                    if self.live and not replay:
                        self._send_entry_orders(sec, entry.side, qty, entry.stop_price)
                    if not replay:
                        self._emit("entry", {"side": entry.side, "bar": bar.dt.isoformat(sep=" "),
                                              "price": bar.open, "stop": entry.stop_price, "qty": qty})
                else:
                    log.info("вход %s пропущен: нулевой размер позиции (риск/ГО-лимит)", entry.side)
                    if not replay:
                        orb_journal.append_skip(self.skips_path, bar.dt, entry.side, "zero_qty")
                        self._emit("skip", {"side": entry.side, "reason": "zero_qty",
                                            "bar": bar.dt.isoformat(sep=" ")})

        blocked, reason = orb_calendar.entry_gate(bar.dt, self.expiration_zone_mode)
        force_flat = orb_calendar.force_flat_gate(bar.dt)
        if not blocked:
            if self.chart_block:
                blocked, reason = True, "wrong_chart"
            elif self.risk_state.halted:
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
                self._close_position(sec, trade.dir, self.open_meta["qty"] if self.open_meta else 0,
                                     reason=trade.exit_reason)
            if not replay:
                self._emit("trade", self._trade_payload(trade))
            self.open_meta = None

        if result.entry is not None:
            if self.entry_filter is not None:
                rel = self._rel_volume(bar)
                rw = result.entry.range_high - result.entry.range_low
                regime = self._regime_vol()
                if not self.entry_filter(result.entry.side, bar, rel, rw, regime):
                    log.info("[%s] вход %s отфильтрован (rel_vol=%.2f, ширина=%.0f, режим=%s)",
                             self.name or "champ", result.entry.side, rel, rw,
                             f"{regime:.2f}%" if regime is not None else "н/д")
                    if not replay:
                        orb_journal.append_skip(self.skips_path, bar.dt, result.entry.side, "filtered")
                    result = _replace(result, entry=None)  # не открываем
            if result.entry is not None:
                self.pending_entry = result.entry

        if result.skip is not None:
            specific = reason if result.skip.reason == "blocked" and reason else result.skip.reason
            log.info("пропуск сигнала %s: %s", result.skip.side, specific)
            if not replay:
                orb_journal.append_skip(self.skips_path, bar.dt, result.skip.side, specific)
                self._emit("skip", {"side": result.skip.side, "reason": specific,
                                    "bar": bar.dt.isoformat(sep=" ")})

    def _close_trade(self, bar: orb_strategy.Bar, exit_sig: orb_strategy.ExitSignal) -> orb_journal.TradeRecord:
        """PnL с издержками (пункт 1): слиппедж на вход и выход + комиссия на обе
        стороны — та же модель, что в бэктесте, чтобы журнал не завышал результат.
        В live это приближение (реальные фил/комиссию из QUIK не читаем), но честнее
        идеализированного нуля. Ставим slippage_ticks/commission_per_side_rub=0 в
        секции backtest, если издержки учитывать не нужно."""
        m = self.open_meta
        long = m["side"] == "long"
        slip = self.slippage_ticks * self.tick_size
        entry_eff = m["entry_price"] + (slip if long else -slip)   # вход хуже на слиппедж
        exit_eff = (exit_sig.price - slip) if long else (exit_sig.price + slip)
        pnl_pt = (exit_eff - entry_eff) if long else (entry_eff - exit_eff)
        commission = self.commission_side * m["qty"] * 2
        pnl_rub = pnl_pt * m["rub_per_point"] * m["qty"] - commission
        return orb_journal.TradeRecord(
            datetime_in=m["entry_time"], datetime_out=bar.dt, dir=m["side"], qty=m["qty"],
            entry=entry_eff, exit=exit_eff, stop=m["stop_price"],
            pnl_pt=pnl_pt, pnl_rub=pnl_rub, exit_reason=exit_sig.reason,
            range_width_pt=m["range_width"], event_flags="")

    @staticmethod
    def _trade_payload(trade: orb_journal.TradeRecord) -> dict:
        return {"dir": trade.dir, "qty": trade.qty, "entry": trade.entry, "exit": trade.exit,
                "stop": trade.stop, "pnl_pt": trade.pnl_pt, "pnl_rub": trade.pnl_rub,
                "exit_reason": trade.exit_reason,
                "datetime_in": trade.datetime_in.isoformat(sep=" "),
                "datetime_out": trade.datetime_out.isoformat(sep=" ")}

    def _send_entry_orders(self, sec: str, side: str, qty: int, stop_price: float) -> None:
        """Вход + защитный стоп со сверкой (пункт №1): подтверждаем фил по факту
        позиции, приводим qty к фактическому, гарантируем, что стоп встал (иначе
        аварийно закрываемся — голую позицию не держим)."""
        cfg = self.cfg
        r = orb_broker_quik.send_market_order(self.qp, cfg["account"], cfg["class_code"], sec, side, qty,
                                               cfg.get("client_code", ""))
        log.info("заявка вход отправлена: %s", r)

        if self.verify_exec:
            signed = qty if side == "long" else -qty
            ok, actual = self._confirm_net(sec, signed)
            if actual is None:
                log.error("ВХОД %s: позицию из QUIK не прочитать — фил НЕ подтверждён, сверь вручную!", side)
                self._emit("error", {"text": f"вход {side}: фил не подтверждён (позиция не читается)"})
            elif actual == 0:
                log.error("ВХОД %s НЕ ИСПОЛНЕН (позиция QUIK=0) — сбрасываю внутреннее состояние в плоское.", side)
                self._emit("error", {"text": f"вход {side} не исполнён — робот сброшен в плоское"})
                orb_broker_quik.cancel_stops(self.qp, cfg["class_code"], sec)
                self.state = _replace(self.state, position=None)
                self.open_meta = None
                self.stop_ref_active = False
                return
            elif abs(actual) != qty:
                real_qty = abs(actual)
                log.warning("ВХОД %s частичный фил: ожидали %d, факт %d — привожу размер к факту.",
                            side, qty, real_qty)
                self._emit("error", {"text": f"частичный фил {side}: {real_qty}/{qty}"})
                if self.open_meta is not None:
                    self.open_meta["qty"] = real_qty
                qty = real_qty

        closing_side = "short" if side == "long" else "long"
        r2 = orb_broker_quik.send_stop_order(self.qp, cfg["account"], cfg["class_code"], sec, closing_side,
                                              qty, stop_price, cfg.get("client_code", ""))
        log.info("стоп-заявка отправлена: %s", r2)
        self.stop_ref_active = True
        if self.verify_exec and not r2.ok:
            log.error("СТОП-ЗАЯВКА не принята (%s) — повтор.", r2)
            r2 = orb_broker_quik.send_stop_order(self.qp, cfg["account"], cfg["class_code"], sec, closing_side,
                                                  qty, stop_price, cfg.get("client_code", ""))
            log.info("стоп-заявка (повтор) отправлена: %s", r2)
            if not r2.ok:
                log.error("СТОП не встал повторно — АВАРИЙНОЕ закрытие позиции (защиты нет).")
                self._emit("error", {"text": "стоп не встал — аварийно закрываю позицию"})
                self._close_position(sec, side, qty, reason="no_stop")
                self.state = _replace(self.state, position=None)
                self.open_meta = None

    def _close_position(self, sec: str, position_side: str, expected_qty: int, reason: str) -> None:
        """Закрытие позиции со сверкой (пункт №1): сначала снимаем висящий стоп
        (иначе GTC-стоп останется и может сработать позже), затем читаем ФАКТИЧЕСКУЮ
        позицию и закрываем ровно её — это чинит двойное исполнение на выходе по
        'stop' (брокерский стоп уже мог закрыть) и частичные закрытия. Повтор +
        громкий алерт, если позиция не ушла в ноль."""
        cfg = self.cfg
        killed = orb_broker_quik.cancel_stops(self.qp, cfg["class_code"], sec)
        if killed is None:
            log.warning("стоп-заявки по %s не прочитать/снять — проверь терминал вручную (может висеть GTC-стоп).",
                        sec)
        self.stop_ref_active = False

        if not self.verify_exec:
            self._send_flat_raw(sec, position_side, expected_qty, reason)
            return

        net = self._net_position(sec)
        if net is None:
            if reason == "stop":
                log.warning("выход stop: позиция не читается — рыночное закрытие НЕ шлю "
                            "(брокерский стоп мог уже закрыть; сверь вручную).")
                self._emit("error", {"text": "выход stop: позиция не подтверждена — сверь вручную"})
            else:
                log.error("закрытие %s: позиция не читается — шлю рыночное вслепую на %d, сверь вручную.",
                          reason, expected_qty)
                self._emit("error", {"text": f"закрытие {reason}: позиция не читается, закрываю вслепую"})
                self._send_flat_raw(sec, position_side, expected_qty, reason)
            return

        if net == 0:
            log.info("закрытие (%s): позиция уже плоская (net=0) — рыночное закрытие не требуется.", reason)
            return

        for attempt in range(1, self.close_retries + 1):
            real_side = "long" if net > 0 else "short"
            real_qty = abs(net)
            orb_broker_quik.send_flat_market_order(self.qp, cfg["account"], cfg["class_code"], sec,
                                                    position_side=real_side, qty=real_qty,
                                                    client_code=cfg.get("client_code", ""))
            ok, actual = self._confirm_net(sec, 0)
            if ok:
                log.info("закрытие (%s) подтверждено: позиция 0 (попытка %d).", reason, attempt)
                return
            log.error("закрытие (%s): позиция всё ещё %s после попытки %d/%d — повтор.",
                      reason, actual, attempt, self.close_retries)
            if actual is not None:
                net = actual
        log.error("КРИТИЧНО: %s не закрыт после %d попыток (net=%s) — ТРЕБУЕТСЯ РУЧНОЕ ВМЕШАТЕЛЬСТВО.",
                  sec, self.close_retries, net)
        self._emit("error", {"text": f"ПОЗИЦИЯ НЕ ЗАКРЫТА ({reason}) за {self.close_retries} попыток — закрой вручную!"})

    def _send_flat_raw(self, sec: str, position_side: str, qty: int, reason: str) -> None:
        """Рыночное закрытие вслепую (старое поведение) — только при verify=false
        или когда позицию не удалось прочитать."""
        if qty <= 0:
            return
        r = orb_broker_quik.send_flat_market_order(self.qp, self.cfg["account"], self.cfg["class_code"], sec,
                                                    qty=qty, position_side=position_side,
                                                    client_code=self.cfg.get("client_code", ""))
        log.info("закрытие позиции (%s) отправлено [без сверки]: %s", reason, r)


def _next_wake(now: datetime, tf: int, delay: float) -> datetime:
    minute = (now.minute // tf) * tf
    boundary = now.replace(minute=minute, second=0, microsecond=0) + timedelta(minutes=tf)
    return boundary + timedelta(seconds=delay)


def make_entry_filter(spec: dict):
    """Строит фильтр входа теневого варианта из спецификации. Ключи (все опц.):
    min_rel_volume / max_rel_volume — по относительному объёму пробойного бара;
    entry_before ('HH:MM') — не входить после времени; range_min / range_max —
    по ширине диапазона (пунктов); regime_min / regime_max — по режиму
    волатильности (трейлинг отн. дневной диапазон, %; напр. regime_max: 2.0 —
    стоять в высоковолатильном режиме). Возвращает callable(side, bar, rel_vol,
    range_width, regime_vol)->bool (True=разрешить) или None, если спецификация пуста."""
    if not spec:
        return None
    mnv, mxv = spec.get("min_rel_volume"), spec.get("max_rel_volume")
    rmn, rmx = spec.get("range_min"), spec.get("range_max")
    gmn, gmx = spec.get("regime_min"), spec.get("regime_max")
    eb_t = None
    if spec.get("entry_before"):
        hh, mm = str(spec["entry_before"]).split(":")
        eb_t = _dtime(int(hh), int(mm))

    def _f(side, bar, rel_vol, range_width, regime_vol):
        if mnv is not None and rel_vol < mnv:
            return False
        if mxv is not None and rel_vol > mxv:
            return False
        if eb_t is not None and bar.dt.time() >= eb_t:
            return False
        if rmn is not None and range_width < rmn:
            return False
        if rmx is not None and range_width > rmx:
            return False
        # режим известен только при достаточной истории (иначе regime_vol=None -> не блокируем)
        if regime_vol is not None:
            if gmn is not None and regime_vol < gmn:
                return False
            if gmx is not None and regime_vol >= gmx:
                return False
        return True
    return _f


def build_shadows(cfg: dict, qp) -> list:
    """Теневые варианты из cfg['shadow_variants']: параллельный расчёт на тех же
    барах, свои журналы (logs/shadow_<name>_*), НИКОГДА не шлют заявки (live=False),
    охрана графика выключена (доверяют данным чемпиона)."""
    shadows = []
    for v in cfg.get("shadow_variants", []) or []:
        name = v["name"]
        scfg = dict(cfg)
        scfg["chart_guard"] = {"enabled": False}
        paths = {"risk_state_json": f"logs/shadow_{name}_risk.json",
                 "trades_csv": f"logs/shadow_{name}_trades.csv",
                 "skips_csv": f"logs/shadow_{name}_skips.csv"}
        shadows.append(OrbOrchestrator(scfg, qp, live=False, on_event=None, name=name,
                                       entry_filter=make_entry_filter(v.get("filter", {})), paths=paths))
    return shadows


def _account_payload(snap: dict | None) -> dict:
    if snap is None:
        return {"ok": False, "lines": orb_account.format_account(None)}
    return {"ok": True, "currency": snap["currency"], "equity": snap["equity_derived"],
            "free": snap["free_derived"], "limit": snap["limit"], "used": snap["used"],
            "varmargin": snap["varmargin"], "commission": snap["commission"],
            "go": snap["go_planned"] or snap["go_without_orders"], "kgo": snap["kgo"],
            "risk_level": snap["risk_level"], "trdacc": orb_account._mask(snap["trdacc"]),
            "firm": snap["firm_id"], "lines": orb_account.format_account(snap)}


def _emit_account(qp, firm, acc, on_event) -> None:
    if not on_event:
        return
    try:
        snap = orb_account.read_account(qp, firm, acc)
    except Exception as e:  # noqa: BLE001
        log.warning("Чтение средств не удалось: %r", e)
        snap = None
    on_event("account", _account_payload(snap))


def run_paper_or_live(cfg: dict, live: bool, stop_event=None, on_event=None, confirmed: bool = False) -> None:
    """stop_event/on_event — для GUI (orb_window.py): остановка из окна и поток событий в интерфейс.
    confirmed=True пропускает интерактивное подтверждение live (его тогда должен показать сам GUI)."""
    if live and not cfg.get("live_trading", False):
        log.error("mode=live, но live_trading=false в конфиге — отказываюсь торговать по-настоящему.")
        if on_event:
            on_event("error", {"text": "live_trading=false в конфиге"})
        return
    if live and not confirmed:
        answer = input('Подтвердите запуск LIVE (реальные заявки) — введите "yes": ')
        if answer.strip().lower() != "yes":
            log.info("Не подтверждено — выхожу без запуска live.")
            return

    qp = connect_quik(cfg)
    if qp is None:
        log.error("Нет подключения к QUIK — работа невозможна.")
        if on_event:
            on_event("error", {"text": "Нет подключения к QUIK"})
        return

    tag = cfg["chart_tag"]
    tf = int(cfg.get("timeframe_minutes", 15))
    delay = float(cfg.get("bar_close_delay_seconds", 7))
    today = date.today()
    expected = orb_calendar.active_contract(today)
    log.info("Ожидаемый активный контракт на сегодня: %s — сверь, что график с тегом '%s' привязан к нему.",
             expected, tag)

    # доступна ли на этой версии QuikPy точная сверка sec за тегом (иначе — по цене/свежести)
    _probe = chart_sec(qp, tag)
    if _probe is not None:
        log.info("Охрана графика: QuikPy отдаёт sec за тегом '%s' = %s (ожидается %s) — точная сверка АКТИВНА.",
                 tag, _probe, expected)
        if _probe != expected:
            log.error("НЕВЕРНЫЙ ГРАФИК на старте: тег '%s' привязан к %s, а торгуем %s — входы будут заблокированы!",
                      tag, _probe, expected)
    else:
        log.info("Охрана графика: QuikPy НЕ отдаёт sec за тегом '%s' — сверка по имени недоступна, "
                 "работает сверка по цене/свежести последнего бара.", tag)

    orch = OrbOrchestrator(cfg, qp, live, on_event=on_event)
    shadows = build_shadows(cfg, qp)          # теневые варианты (paper, свои журналы, без заявок)
    if shadows:
        log.info("Теневые варианты (форвард-тест, без заявок): %s", ", ".join(s.name for s in shadows))

    def _stopped():
        return stop_event is not None and stop_event.is_set()

    # разметка сегодняшних баров (если робот запущен посреди дня) — реплей БЕЗ реальных заявок,
    # только чтобы восстановить диапазон/флаги/(предположительную) открытую позицию
    all_candles = _load_recent(qp, tag, want=None)
    no_data = len(all_candles) < 2          # нет закрытых баров = тег пуст/переименован (как в QUIK-бэктесте)
    todays = [c for c in all_candles if (_bar_dt(c) or datetime.min).date() == today]
    for c in todays[:-1] if todays else []:
        b = _to_bar(c)
        if b:
            orch.handle_bar(b, replay=True)
            for s in shadows:
                s.handle_bar(b, replay=True)
    if orch.state.position is not None and not (live and orch.verify_exec):
        # в live+verify реальную позицию подхватит adopt_live_position ниже; здесь — только paper/verify-off
        log.warning("ВНИМАНИЕ: по разметке сегодняшней истории должна быть открытая позиция %s — "
                    "робот запущен не с начала дня, сверь с реальными позициями/заявками в QUIK вручную!",
                    orch.state.position.side)

    last_dt = _bar_dt(todays[-1]) if todays else None
    log.info("Старт ORB. режим=%s live_trading=%s tf=%d мин", "live" if live else "paper",
             cfg.get("live_trading"), tf)
    if on_event:
        on_event("start", {"mode": "live" if live else "paper", "live_trading": cfg.get("live_trading"),
                           "contract": expected, "tag": tag, "tf": tf})

    # алерт «нет свечей» — ПОСЛЕ start, иначе start перекрасит лампу обратно в «работает»
    if no_data:
        log.error("НЕТ СВЕЧЕЙ по тегу '%s' — график с этим тегом не открыт в QUIK или тег переименован. "
                  "Робот не получает данных и торговать не будет — проверь график.", tag)
        if on_event:
            on_event("error", {"text": f"нет свечей по тегу '{tag}' — график не открыт/переименован"})

    # пункт №2: подхват реальной позиции из QUIK при рестарте/краше (только live, после start)
    if live and orch.verify_exec:
        orch.adopt_live_position(expected)

    acct_firm, acct_trdacc = orb_account.find_futures_account(qp)
    _emit_account(qp, acct_firm, acct_trdacc, on_event)
    acct_every = float(cfg.get("account_poll_seconds", 10))
    next_acct = datetime.now()

    try:
        while not _stopped():
            wake = _next_wake(datetime.now(), tf, delay)
            if on_event:
                on_event("waiting", {"until": wake.strftime("%H:%M:%S")})
            while datetime.now() < wake and not _stopped():
                if datetime.now() >= next_acct:
                    _emit_account(qp, acct_firm, acct_trdacc, on_event)
                    next_acct = datetime.now() + timedelta(seconds=acct_every)
                _time.sleep(min(1.0, (wake - datetime.now()).total_seconds()))
            if _stopped():
                break
            if on_event:
                on_event("wake", {"time": datetime.now().strftime("%H:%M:%S")})
            candles = _load_recent(qp, tag, want=max(80, 5))
            if len(candles) < 2:            # нет закрытых баров = тег пуст/переименован
                if not no_data:
                    log.error("НЕТ СВЕЖИХ СВЕЧЕЙ по тегу '%s' — график пропал/переименован. "
                              "Робот без данных, входы невозможны — проверь график в QUIK.", tag)
                    if on_event:
                        on_event("error", {"text": f"нет свечей по тегу '{tag}' — проверь график в QUIK"})
                    no_data = True
                continue
            if no_data:
                log.info("Свечи по тегу '%s' снова поступают — данные восстановлены.", tag)
                no_data = False
                if on_event:
                    on_event("chart_ok", {"text": "данные по графику восстановлены"})
            closed = candles[:-1]
            new = [c for c in closed if (_bar_dt(c) or datetime.min) > (last_dt or datetime.min)]
            for c in new:
                b = _to_bar(c)
                if b:
                    orch.handle_bar(b, replay=False)
                    for s in shadows:
                        s.handle_bar(b, replay=False)
                    last_dt = b.dt
            if orch.halted_by_kill_switch:
                log.warning("Остановлен kill switch'ем.")
                break
    except KeyboardInterrupt:
        log.info("Остановлен пользователем (Ctrl+C).")
    finally:
        # закрыть QuikPy-соединение, иначе его фоновый поток держит порты слота и
        # повторный Старт зависает на «подключение…» (connect_quik на занятый порт)
        try:
            qp.close_connection_and_thread()
            log.info("QUIK-соединение закрыто.")
        except Exception as e:  # noqa: BLE001
            log.warning("Не удалось закрыть QUIK-соединение: %r", e)
        if on_event:
            on_event("stopped", {})


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
        bars = orb_backtest.load_bars(b)
        res = orb_backtest.run(b, bars=bars)
        log.info("Бэктест завершён: %s", res.summary)
        for line in orb_journal.summary_lines(res.summary):
            log.info(line)
        paths = cfg.get("paths", {})
        trades_path = HERE / paths.get("backtest_trades_csv", "logs/orb_backtest_trades.csv")
        runs_path = HERE / paths.get("backtest_runs_csv", "logs/orb_backtest_runs.csv")
        period = orb_backtest.save_result(b, res, source="moex_iss", trades_path=trades_path,
                                           runs_path=runs_path, bars_count=len(bars))
        for line in orb_journal.period_lines(res.summary, period):
            log.info(line)
        log.info("Сделки прогона: %s · история прогонов: %s", trades_path, runs_path)
    elif mode == "paper":
        run_paper_or_live(cfg, live=False)
    elif mode == "live":
        run_paper_or_live(cfg, live=True)
    else:
        log.error("Неизвестный mode: %s", mode)


if __name__ == "__main__":
    main()
