"""
orb_account.py — READ-ONLY чтение средств фьючерсного счёта FORTS через QuikPy.

Источник — get_futures_limit(firm_id, trade_account_id, limit_type=0, curr) —
на QuikPy отдаёт числовой словарь денег FORTS. ЖЁСТКИЙ ИНВАРИАНТ: здесь нет
отправки заявок, только чтение лимитов (используется вкладкой «Счёт» в
orb_window.py и опционально в логе orb_robot.py).

Производные поля («Текущие средства», «Свободно») помечены *_derived — это
расчёт, а не прямое поле QUIK; сверяй с «Клиентский портфель» в терминале.
"""

from __future__ import annotations

import logging

log = logging.getLogger("orb_account")


def _f(d: dict, key: str, default: float = 0.0) -> float:
    """Достаём число по ключу (QuikPy может отдать и float, и строку)."""
    v = d.get(key, default)
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def find_futures_account(qp):
    """(firm_id, trade_account_id) фьючерсного счёта из qp.accounts. Иначе (None, None)."""
    accs = list(getattr(qp, "accounts", []) or [])
    if not accs and hasattr(qp, "get_trade_accounts"):
        try:
            d = qp.get_trade_accounts().get("data")
            accs = d if isinstance(d, list) else []
        except Exception:  # noqa: BLE001
            accs = []
    for a in accs:
        if a.get("futures") or "SPBFUT" in (a.get("class_codes") or []):
            return a.get("firm_id"), a.get("trade_account_id")
    return None, None


def _looks_empty(data) -> bool:
    if not isinstance(data, dict):
        return True
    return set(data.keys()) <= {"t", "id", "cmd", "data"}  # эхо-конверт без полезной нагрузки


def read_account(qp, firm_id=None, trdacc=None, currency: str = "SUR") -> dict | None:
    """Снимок средств фьючерсного счёта. Возвращает нормализованный dict или None.

    qp — уже подключённый QuikPy. firm/trdacc можно не передавать — найдём сами.
    Никаких заявок: только get_futures_limit (+ автоопределение счёта)."""
    if firm_id is None or trdacc is None:
        firm_id, trdacc = find_futures_account(qp)
    if not firm_id or not trdacc:
        return None

    raw = None
    for curr in (currency, "RUB", ""):
        try:
            res = qp.get_futures_limit(firm_id, trdacc, 0, curr)
        except Exception:  # noqa: BLE001
            continue
        data = res.get("data") if isinstance(res, dict) and "data" in res else res
        if not _looks_empty(data):
            raw = data
            currency = data.get("currcode", curr)
            break
    if raw is None:
        return None

    cbplimit = _f(raw, "cbplimit")
    used = _f(raw, "cbplplanned") or _f(raw, "cbplused")  # план включает заявки
    varm = _f(raw, "varmargin")
    return {
        "firm_id": firm_id,
        "trdacc": trdacc,
        "currency": currency,
        "limit": cbplimit,
        "used": used,
        "used_positions": _f(raw, "cbplused"),
        "varmargin": varm,
        "real_varmargin": _f(raw, "real_varmargin"),
        "commission": _f(raw, "ts_comission"),
        "go_planned": _f(raw, "go_planned"),
        "go_without_orders": _f(raw, "go_without_orders"),
        "kgo": _f(raw, "kgo", 1.0),
        "risk_level": raw.get("risk_level"),
        "equity_derived": cbplimit + varm,        # текущие средства ≈ лимит + вармаржа
        "free_derived": cbplimit - used,          # свободно ≈ лимит − занятое
        "raw": raw,
    }


def read_position(qp, sec_code: str, firm_id=None, trdacc=None) -> int | None:
    """Фактическая ЧИСТАЯ позиция по контракту sec_code из QUIK. READ-ONLY.

    >0 — длинная (лонг) на N контрактов, <0 — короткая, 0 — плоско.
    None — прочитать не удалось (метод недоступен / ошибка / нет счёта): вызывающий
    код обязан трактовать None как «неизвестно» и действовать консервативно.

    Источник — get_futures_holding(firm, trdacc, sec, limit_type=0), поле totalnet.
    ВНИМАНИЕ: имя метода/поля зависит от версии QuikPy — проверь на своём терминале
    (тот же дисклеймер, что и у get_futures_limit в этом модуле)."""
    if firm_id is None or trdacc is None:
        firm_id, trdacc = find_futures_account(qp)
    if not firm_id or not trdacc:
        return None
    getter = getattr(qp, "get_futures_holding", None)
    if getter is None:
        return None
    try:
        res = getter(firm_id, trdacc, sec_code, 0)
    except Exception:  # noqa: BLE001
        return None
    data = res.get("data") if isinstance(res, dict) and "data" in res else res
    if _looks_empty(data):
        return None
    for key in ("totalnet", "total_net", "net"):
        if key in data:
            try:
                return int(round(float(data[key])))
            except (TypeError, ValueError):
                return None
    return None


def format_account(snap: dict | None, mask_secrets: bool = True) -> list[str]:
    """Строки карточки средств — единый источник для лога и окна."""
    if snap is None:
        return ["счёт: данные не получены (фьючерсный счёт не найден или лимит пуст)"]

    cur = snap["currency"] or "SUR"

    def money(v):
        return f"{v:,.2f} {cur}".replace(",", " ")

    acc = _mask(snap["trdacc"]) if mask_secrets else snap["trdacc"]
    return [
        f"Счёт {acc} · фирма {snap['firm_id']} · валюта {cur}",
        f"Текущие средства*:  {money(snap['equity_derived'])}   "
        f"(лимит {money(snap['limit'])} + вармаржа {money(snap['varmargin'])})",
        f"Свободно*:          {money(snap['free_derived'])}   "
        f"(занято {money(snap['used'])})",
        f"Вариац. маржа:      {money(snap['varmargin'])}"
        + (f"  · реальная {money(snap['real_varmargin'])}"
           if snap['real_varmargin'] != snap['varmargin'] else ""),
        f"Биржевой сбор:      {money(snap['commission'])}",
        f"Коэф. ГО {snap['kgo']:g} · уровень риска {snap['risk_level']}",
        "* производное (расчёт), не прямое поле QUIK — сверь с «Клиентский портфель».",
    ]


def _mask(value) -> str:
    if value in (None, ""):
        return "—"
    s = str(value)
    if len(s) <= 4:
        return s[0] + "*" * (len(s) - 1)
    return s[:2] + "*" * (len(s) - 4) + s[-2:]


def main() -> None:
    """Соло-режим: своё подключение через orb_robot (слот из config_orb.yaml)."""
    import orb_robot as R

    cfg = R.load_config()
    R.setup_logging(cfg)
    qp = R.connect_quik(cfg)
    if qp is None:
        log.error("Подключение не удалось — терминал QUIK запущен? slot в config_orb.yaml верный?")
        return
    try:
        snap = read_account(qp)
        for line in format_account(snap):
            log.info("%s", line)
    finally:
        try:
            qp.close_connection_and_thread()
        except Exception:
            pass


if __name__ == "__main__":
    main()
