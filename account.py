"""
account.py — READ-ONLY чтение средств фьючерсного счёта FORTS (модуль + соло-тест).

Источник — get_futures_limit(firm_id, trade_account_id, limit_type=0, curr='SUR'),
который на твоей QuikPy 12.8.4.9 отдаёт чистый числовой словарь денег FORTS.

ЖЁСТКИЙ ИНВАРИАНТ: тут НЕТ отправки заявок. Только чтение лимитов.

Двойное назначение (как у других модулей проекта):
  - read_account(qp, ...) — принимает УЖЕ подключённый qp; так его дёрнет живой цикл
    робота, не открывая второй коннект к одноклиентскому мосту;
  - main() — соло-режим: сам поднимает коннект на слоте из config.json, печатает
    карточку средств и кладёт в logs/account.log (UTF-8). Робот при этом останови.

Производные поля («Текущие средства», «Свободно») помечены *_derived — это расчёт,
а не прямое поле QUIK; сверить с таблицей «Клиентский портфель», когда счёт пополнен.

Запуск соло:  python account.py
"""

import json
import logging
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
log = logging.getLogger("account")

# Поля get_futures_limit, которые показываем. (ключ QUIK, подпись)
MONEY_FIELDS = [
    ("cbplimit", "Лимит откр. позиций (входящие средства)"),
    ("cbplused", "Занято под позиции"),
    ("cbplplanned", "Занято под позиции + заявки"),
    ("varmargin", "Вариационная маржа"),
    ("real_varmargin", "Вариац. маржа (реальная)"),
    ("go_planned", "ГО плановое"),
    ("go_without_orders", "ГО без заявок"),
    ("ts_comission", "Биржевой сбор"),
    ("accruedint", "Накопленный доход"),
    ("options_premium", "Премия по опционам"),
    ("kgo", "Коэффициент ГО"),
    ("risk_level", "Уровень риска"),
]


def _f(d: dict, key: str, default=0.0) -> float:
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
    # эхо-конверт запроса без полезной нагрузки
    return set(data.keys()) <= {"t", "id", "cmd", "data"}


def read_account(qp, firm_id=None, trdacc=None, currency="SUR"):
    """Снимок средств фьючерсного счёта. Возвращает нормализованный dict или None.

    qp — УЖЕ подключённый QuikPy. firm/trdacc можно не передавать — найдём сами.
    Никаких заявок: только get_futures_limit (+ автоопределение счёта).
    """
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
        # прямые поля QUIK
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
        # производные (пометить как расчёт)
        "equity_derived": cbplimit + varm,        # текущие средства ≈ лимит + вармаржа
        "free_derived": cbplimit - used,          # свободно ≈ лимит − занятое
        "raw": raw,
    }


def format_account(snap: dict, mask_secrets: bool = True) -> list[str]:
    """Строки карточки средств — единый источник для лога и (позже) для окна.

    Возвращает список строк. None-снимок -> понятная заглушка.
    """
    if snap is None:
        return ["счёт: данные не получены (фьючерсный счёт не найден или лимит пуст)"]

    cur = snap["currency"] or "SUR"

    def money(v):
        return f"{v:,.2f} {cur}".replace(",", " ")

    acc = _mask(snap["trdacc"]) if mask_secrets else snap["trdacc"]
    lines = [
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
    return lines


def _mask(value) -> str:
    if value in (None, ""):
        return "—"
    s = str(value)
    if len(s) <= 4:
        return s[0] + "*" * (len(s) - 1)
    return s[:2] + "*" * (len(s) - 4) + s[-2:]


# --- соло-режим: своё подключение на слоте из config.json ----------------------
def _load_slot() -> dict:
    p = HERE / "config.json"
    if p.is_file():
        try:
            cfg = json.load(open(p, "r", encoding="utf-8"))
            slot = dict(cfg.get("slot", {}))
            return slot
        except (OSError, ValueError):
            pass
    return {"host": "127.0.0.1", "requests_port": 34140, "callbacks_port": 34141}


def _connect(slot: dict):
    qpd = slot.get("quik_py_dir")
    for c in ([Path(qpd)] if qpd else []) + [HERE, *HERE.parents]:
        try:
            d = c / "QuikPy" if (c / "QuikPy").is_dir() else c
            if (d / "QuikPy.py").is_file():
                if str(d) not in sys.path:
                    sys.path.insert(0, str(d))
                break
        except OSError:
            continue
    from QuikPy import QuikPy
    return QuikPy(host=slot.get("host", "127.0.0.1"),
                  requests_port=int(slot.get("requests_port", 34140)),
                  callbacks_port=int(slot.get("callbacks_port", 34141)))


def _setup_logging():
    log_dir = HERE / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(message)s")
    log.setLevel(logging.INFO)
    log.handlers.clear()
    for h in (logging.StreamHandler(),
              logging.FileHandler(log_dir / "account.log", encoding="utf-8")):
        h.setFormatter(fmt)
        log.addHandler(h)


def main():
    _setup_logging()
    slot = _load_slot()
    log.info("Чтение средств счёта (read-only). Слот %s:%s/%s",
             slot.get("host", "127.0.0.1"), slot.get("requests_port"), slot.get("callbacks_port"))
    try:
        qp = _connect(slot)
    except Exception as e:  # noqa: BLE001
        log.info("Подключение не удалось: %r (робот остановлен? slot.quik_py_dir верный?)", e)
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
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
