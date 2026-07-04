"""
chart.py — рисование стрелок и уровней на графике QUIK через addLabel2.

Перенесено из pattern_app: метка вешается на свечу по её дате/времени и цене.
Стрелка лонга — над хаем разворотной свечи, шорта — под лоем. Уровни вход/стоп/тейк —
горизонтальными подписями. Если png-картинки стрелок нет рядом — фолбэк на текст ▲/▼.

Это только рисование на графике (аннотация), НИКАКИХ заявок здесь нет.
Свечи и метки привязаны к КОНКРЕТНОМУ графику по его тегу (chart_tag).
"""

import logging
import os
from pathlib import Path

_log = logging.getLogger("chart")

HERE = Path(__file__).resolve().parent

# Хранилище ID меток по тегу графика (чтобы удалять по одной, т.к. DelAllLabels не работает)
_label_ids: dict[str, list] = {}


def cv_date_time(candle: dict) -> tuple[int, int]:
    """Дата YYYYMMDD и время HHMMSS из поля candle['datetime'] (как ждёт addLabel2)."""
    dt = candle.get("datetime", {})
    if not isinstance(dt, dict):
        return 0, 0
    d = dt.get("year", 0) * 10000 + dt.get("month", 0) * 100 + dt.get("day", 0)
    t = dt.get("hour", 0) * 10000 + dt.get("min", 0) * 100 + dt.get("sec", 0)
    return d, t


def fmt_dt(candle: dict) -> str:
    dt = candle.get("datetime", {})
    if not isinstance(dt, dict):
        return "??-??-?? ??:??:??"
    return "{:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d}".format(
        dt.get("year", 0), dt.get("month", 0), dt.get("day", 0),
        dt.get("hour", 0), dt.get("min", 0), dt.get("sec", 0))


def _add_label(qp, tag, y_value, date_num, time_num, text="", image_path="",
               alignment="", hint="", r=-1, g=-1, b=-1, transparency=-1,
               trans_bg=-1, font_name="", font_height=-1):
    data = "|".join([
        tag, f"{y_value:.6f}".replace(",", "."), str(date_num), str(time_num),
        text.replace("|", "/"), image_path, alignment, hint.replace("|", "/"),
        str(r), str(g), str(b), str(transparency), str(trans_bg),
        font_name, str(font_height),
    ])
    result = qp.process_request({"data": data, "id": 0, "cmd": "addLabel2", "t": ""})
    label_id = (result or {}).get("data")
    if label_id is not None:
        try:
            label_id = int(label_id)
        except (TypeError, ValueError):
            label_id = None
    if label_id is not None and label_id != 0:
        _label_ids.setdefault(tag, []).append(label_id)
    return result


def _image_for(cfg_arrows: dict, key: str) -> str:
    """Путь к png-стрелке, если файл есть рядом; иначе пустая строка (-> фолбэк на текст)."""
    name = cfg_arrows.get(key, "")
    if name and (HERE / name).is_file():
        return str(HERE / name)
    return ""


def del_all_labels(qp, tag: str):
    try:
        result = qp.process_request({"data": tag, "id": 0, "cmd": "delAllLabels", "t": ""})
        _log.info("del_all_labels(tag=%s) -> %s", tag, result)
    except Exception as e:
        _log.warning("del_all_labels(tag=%s) failed: %r", tag, e)


def draw_arrow(qp, tag: str, cfg_arrows: dict, candle: dict, is_bull: bool,
               count: int, hint: str):
    """Стрелка на разворотной свече. png если есть, иначе цветное слово BUY/SELL.

    Текст ASCII (BUY/SELL), а не символы ▲/▼ — символы геометрии не влезают в cp1251,
    которой QUIK кодирует команду, и addLabel2 падал. Цвет слова под png-стрелки:
    лонг — красный (как bull-png), шорт — зелёный (как bear-png)."""
    dn, tn = cv_date_time(candle)
    min_len = float(cfg_arrows.get("min_len", 0) or 0)
    if is_bull:
        img = _image_for(cfg_arrows, "bull_image")
        ypos, align = float(candle["high"]) + min_len, "TOP"
        text = str(count) if img else f"BUY {count}"
        r, g, b = (-1, -1, -1) if img else (0, 180, 0)     # лонг — зелёный
    else:
        img = _image_for(cfg_arrows, "bear_image")
        ypos, align = float(candle["low"]) - min_len, "BOTTOM"
        text = str(count) if img else f"SELL {count}"
        r, g, b = (-1, -1, -1) if img else (255, 40, 40)   # шорт — красный
    _add_label(qp, tag, ypos, dn, tn, text=text, image_path=img, alignment=align,
               hint=hint, r=r, g=g, b=b, transparency=0, trans_bg=1,
               font_name="Arial", font_height=12)


def draw_levels(qp, tag: str, candle: dict, entry: float, stop: float, tp: float,
                rub_per_point=None, side=None):
    """Горизонтальные подписи вход/стоп/тейк цветом. Если rub_per_point задан — добавляем P&L."""
    dn, tn = cv_date_time(candle)
    line = "-" * 40
    if rub_per_point and side:
        long = side == "long"
        sl_pnl = -(entry - stop) * rub_per_point if long else -(stop - entry) * rub_per_point
        tp_pnl = (tp - entry) * rub_per_point if long else (entry - tp) * rub_per_point
    else:
        sl_pnl = tp_pnl = None
    for yval, lbl, r, g, b in [
        (entry, f"{line} ВХОД {entry:.2f}", 255, 255, 0),
        (stop,  f"{line} СТОП {stop:.2f}",  255, 0, 0),
        (tp,    f"{line} ТП   {tp:.2f}",    0, 200, 0),
    ]:
        if sl_pnl is not None:
            pnl = 0 if yval == entry else (sl_pnl if yval == stop else tp_pnl)
            sign = "+" if pnl > 0 else ""
            lbl += f" ({sign}{pnl:.0f} руб)"
        _add_label(qp, tag, yval, dn, tn, text=lbl, alignment="RIGHT", hint=lbl,
                   r=r, g=g, b=b, transparency=0, trans_bg=1,
                   font_name="Courier New", font_height=10)
