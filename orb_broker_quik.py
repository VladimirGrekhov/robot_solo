"""
orb_broker_quik.py — отправка заявок ORB через QuikPy (QLUA-транзакции).

ВНИМАНИЕ (важно перед live): формат транзакции ниже — стандартный для QUIK/QLUA
(поля ACTION/OPERATION/CLASSCODE/SECCODE/TRANS_ID и т.д., как в SendTransaction),
но здесь он НЕ проверен против реального терминала — QUIK и QuikPy физически
недоступны в этом окружении (Windows-only терминал). Перед live ОБЯЗАТЕЛЬНО:
  1. свериться, что у qp есть метод send_transaction(dict) именно с такой сигнатурой
     (в опубликованной QuikPy Чинарова он есть; при другой версии — поправь _send);
  2. прогнать на paper и глазами свериться, что заявки в QUIK выглядят ожидаемо;
  3. стоп — ВСЕГДА настоящая стоп-заявка в терминале (send_stop_order), а не
     программный стоп: если робот упадёт, позиция должна остаться защищённой;
  4. сверка исполнения (execution.verify в конфиге) требует, чтобы у qp работали
     get_futures_holding (чтение позиции, см. orb_account.read_position) и
     get_stop_orders (список стопов, см. active_stop_orders ниже) — имена/поля
     зависят от версии QuikPy, проверь на своём терминале; если методов нет,
     сверка деградирует до старого поведения (заявки вслепую) с предупреждением.
"""

from __future__ import annotations

import itertools
import logging
from dataclasses import dataclass

log = logging.getLogger("orb_broker_quik")

_trans_id_counter = itertools.count(1)


def _next_trans_id() -> int:
    return next(_trans_id_counter)


@dataclass(frozen=True)
class OrderResult:
    ok: bool
    trans_id: int
    raw: dict
    error: str | None = None


def send_market_order(qp, account: str, class_code: str, sec_code: str, side: str,
                       qty: int, client_code: str = "") -> OrderResult:
    """Рыночная заявка на вход. side: 'long' -> покупка, 'short' -> продажа."""
    trans_id = _next_trans_id()
    transaction = {
        "ACTION": "NEW_ORDER",
        "ACCOUNT": account,
        "CLASSCODE": class_code,
        "SECCODE": sec_code,
        "OPERATION": "B" if side == "long" else "S",
        "TYPE": "M",
        "QUANTITY": str(int(qty)),
        "PRICE": "0",
        "TRANS_ID": str(trans_id),
    }
    if client_code:
        transaction["CLIENT_CODE"] = client_code
    return _send(qp, transaction, trans_id)


def send_stop_order(qp, account: str, class_code: str, sec_code: str, closing_side: str,
                     qty: int, stop_price: float, client_code: str = "") -> OrderResult:
    """Простая стоп-заявка (не стоп-лимит) на закрытие позиции по stop_price.

    closing_side — сторона ЗАКРЫВАЮЩЕЙ заявки, противоположная открытой позиции:
    для длинной позиции стоп на продажу ('short'), для короткой — на покупку ('long')."""
    trans_id = _next_trans_id()
    transaction = {
        "ACTION": "NEW_STOP_ORDER",
        "ACCOUNT": account,
        "CLASSCODE": class_code,
        "SECCODE": sec_code,
        "OPERATION": "B" if closing_side == "long" else "S",
        "STOPPRICE": f"{stop_price:.2f}",
        "QUANTITY": str(int(qty)),
        "TRANS_ID": str(trans_id),
        "EXPIRY_DATE": "GTC",
    }
    if client_code:
        transaction["CLIENT_CODE"] = client_code
    return _send(qp, transaction, trans_id)


def send_flat_market_order(qp, account: str, class_code: str, sec_code: str, position_side: str,
                            qty: int, client_code: str = "") -> OrderResult:
    """Рыночная заявка на закрытие позиции position_side (используется для EOD/cbr_flat)."""
    closing_side = "short" if position_side == "long" else "long"
    return send_market_order(qp, account, class_code, sec_code, closing_side, qty, client_code)


def kill_order(qp, class_code: str, sec_code: str, order_num: int) -> OrderResult:
    trans_id = _next_trans_id()
    transaction = {"ACTION": "KILL_ORDER", "CLASSCODE": class_code, "SECCODE": sec_code,
                   "ORDER_KEY": str(order_num), "TRANS_ID": str(trans_id)}
    return _send(qp, transaction, trans_id)


def kill_stop_order(qp, class_code: str, sec_code: str, stop_order_num: int) -> OrderResult:
    trans_id = _next_trans_id()
    transaction = {"ACTION": "KILL_STOP_ORDER", "CLASSCODE": class_code, "SECCODE": sec_code,
                   "STOP_ORDER_KEY": str(stop_order_num), "TRANS_ID": str(trans_id)}
    return _send(qp, transaction, trans_id)


def active_stop_orders(qp, class_code: str, sec_code: str) -> list[int] | None:
    """Номера АКТИВНЫХ стоп-заявок по контракту (best-effort через qp.get_stop_orders()).

    None — прочитать не удалось (метод недоступен/ошибка); [] — активных стопов нет.
    Активным считаем стоп с flags без бита снятия/исполнения (bit0 — активна в QUIK)."""
    getter = getattr(qp, "get_stop_orders", None)
    if getter is None:
        return None
    try:
        res = getter()
    except Exception as e:  # noqa: BLE001
        log.warning("get_stop_orders() упал: %r", e)
        return None
    data = res.get("data") if isinstance(res, dict) and "data" in res else res
    if not isinstance(data, list):
        return None
    nums: list[int] = []
    for so in data:
        if not isinstance(so, dict):
            continue
        if so.get("sec_code") != sec_code or (class_code and so.get("class_code") not in (class_code, None)):
            continue
        flags = int(so.get("flags", 0) or 0)
        active = bool(flags & 0x1)  # бит0 выставлен — заявка активна (не снята/не исполнена)
        num = so.get("order_num") or so.get("stop_order_num") or so.get("number")
        if active and num is not None:
            try:
                nums.append(int(num))
            except (TypeError, ValueError):
                pass
    return nums


def active_stop_price(qp, class_code: str, sec_code: str) -> float | None:
    """Цена (STOPPRICE) первой активной стоп-заявки по контракту — для восстановления
    стопа подхваченной позиции при рестарте (пункт №2). None — не найдено/не прочитать."""
    getter = getattr(qp, "get_stop_orders", None)
    if getter is None:
        return None
    try:
        res = getter()
    except Exception:  # noqa: BLE001
        return None
    data = res.get("data") if isinstance(res, dict) and "data" in res else res
    if not isinstance(data, list):
        return None
    for so in data:
        if not isinstance(so, dict):
            continue
        if so.get("sec_code") != sec_code or (class_code and so.get("class_code") not in (class_code, None)):
            continue
        if not (int(so.get("flags", 0) or 0) & 0x1):     # только активные
            continue
        for key in ("condition_price", "stopprice", "stop_price", "price"):
            if key in so:
                try:
                    v = float(so[key])
                    if v > 0:
                        return v
                except (TypeError, ValueError):
                    pass
    return None


def cancel_stops(qp, class_code: str, sec_code: str) -> int | None:
    """Снимает все активные стоп-заявки по контракту. Возвращает число снятых,
    или None если список активных стопов прочитать не удалось (нельзя гарантировать
    отмену — вызывающий код обязан предупредить о возможном висящем стопе)."""
    nums = active_stop_orders(qp, class_code, sec_code)
    if nums is None:
        return None
    killed = 0
    for num in nums:
        r = kill_stop_order(qp, class_code, sec_code, num)
        if r.ok:
            killed += 1
        else:
            log.warning("не удалось снять стоп-заявку %s: %r", num, r)
    return killed


def _send(qp, transaction: dict, trans_id: int) -> OrderResult:
    try:
        result = qp.send_transaction(transaction)
    except Exception as e:  # noqa: BLE001
        log.error("send_transaction(%s) упал: %r", transaction, e)
        return OrderResult(ok=False, trans_id=trans_id, raw={}, error=repr(e))
    ok = bool(result) and str((result or {}).get("data", "")).strip() not in ("", "0")
    if not ok:
        log.warning("send_transaction(%s) вернул неожиданный ответ: %r", transaction, result)
    return OrderResult(ok=ok, trans_id=trans_id, raw=result or {})
