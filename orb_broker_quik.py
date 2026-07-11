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
     программный стоп: если робот упадёт, позиция должна остаться защищённой.
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
