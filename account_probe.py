"""
account_probe.py — РАЗОВЫЙ read-only пробник средств счёта на FORTS (версия 2).

Версия 1 ошибалась: брала account_id (целое 0/1/2/3) вместо строки
trade_account_id ('763J9ir'), и считала эхо-пустышку {"t","id","cmd"} за успех.
Здесь исправлено: бьём по ФЬЮЧЕРСНОМУ счёту правильной строкой trade_account_id,
распознаём пустой ответ и перебираем тип лимита/валюту.

ЖЁСТКИЙ ИНВАРИАНТ: тут НЕТ отправки заявок. Только чтение лимитов/портфеля.

КАК ЗАПУСКАТЬ:
  - Останови робота (слот 34140/34141 одноклиентский), либо подними отдельный
    QuikSharp на 34142/34143 и впиши его в "slot" в config.json.
  - python account_probe.py  → дамп в консоль и logs/account_probe.log (UTF-8).
  - Пришли мне получившийся лог — по реальным полям соберём вкладку «Счёт».
"""

import inspect
import json
import logging
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
log = logging.getLogger("account_probe")

# Маскируем ЗНАЧЕНИЯ счёта/клиента (имена полей и финансовые числа оставляем).
_SENSITIVE_KEY = re.compile(r"(client|trdacc|trade_account|ucode|user)", re.IGNORECASE)


def setup_logging():
    log_dir = HERE / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(message)s")
    log.setLevel(logging.INFO)
    log.handlers.clear()
    for h in (logging.StreamHandler(),
              logging.FileHandler(log_dir / "account_probe.log", encoding="utf-8")):
        h.setFormatter(fmt)
        log.addHandler(h)


def load_slot() -> dict:
    cfg_path = HERE / "config.json"
    if cfg_path.is_file():
        try:
            cfg = json.load(open(cfg_path, "r", encoding="utf-8"))
            slot = dict(cfg.get("slot", {}))
            slot.setdefault("class_code", cfg.get("class_code", "SPBFUT"))
            return slot
        except (OSError, ValueError) as e:
            log.info("Не прочитал config.json (%r) — беру дефолты.", e)
    return {"host": "127.0.0.1", "requests_port": 34140, "callbacks_port": 34141,
            "class_code": "SPBFUT"}


def find_quikpy(slot: dict):
    qpd = slot.get("quik_py_dir")
    cands = ([Path(qpd)] if qpd else []) + [HERE, *HERE.parents]
    for c in cands:
        try:
            d = c / "QuikPy" if (c / "QuikPy").is_dir() else c
            if (d / "QuikPy.py").is_file():
                if str(d) not in sys.path:
                    sys.path.insert(0, str(d))
                break
        except OSError:
            continue
    from QuikPy import QuikPy
    return QuikPy


def mask_value(v):
    if v in (None, ""):
        return v
    s = str(v)
    if len(s) <= 4:
        return s[0] + "*" * (len(s) - 1)
    return s[:2] + "*" * (len(s) - 4) + s[-2:]


def is_empty(data) -> bool:
    """True, если ответ пустой или это эхо-конверт запроса (нет полезной нагрузки)."""
    if data is None:
        return True
    if isinstance(data, (list, dict)) and len(data) == 0:
        return True
    if isinstance(data, dict) and set(data.keys()) <= {"t", "id", "cmd", "data"}:
        inner = data.get("data")
        return inner in (None, "", {}, [])
    return False


def dump(obj, indent="  "):
    def _walk(o):
        if isinstance(o, dict):
            return {k: (mask_value(v) if _SENSITIVE_KEY.search(str(k)) and not isinstance(v, (dict, list))
                        else _walk(v)) for k, v in o.items()}
        if isinstance(o, list):
            return [_walk(x) for x in o]
        return o
    try:
        text = json.dumps(_walk(obj), ensure_ascii=False, indent=2)
    except Exception:
        text = repr(obj)
    for line in text.splitlines():
        log.info("%s%s", indent, line)


def show_signature(qp, name):
    fn = getattr(qp, name, None)
    if fn is None:
        log.info("  %-22s — НЕТ такого метода", name)
        return
    try:
        log.info("  %-22s %s", name, str(inspect.signature(fn)))
    except (TypeError, ValueError):
        log.info("  %-22s (сигнатура недоступна)", name)


def try_call(label, fn, *args):
    """Вызов с дампом. Возвращает (data, ok): ok=False если пусто/эхо."""
    log.info("• %s", label)
    try:
        res = fn(*args)
    except Exception as e:  # noqa: BLE001
        log.info("  → ошибка: %r", e)
        return None, False
    data = res.get("data") if isinstance(res, dict) and "data" in res else res
    if is_empty(data):
        log.info("  → пусто (эхо-конверт: по этому ключу данных нет)")
        return None, False
    dump(data)
    return data, True


def main():
    setup_logging()
    slot = load_slot()
    log.info("=" * 70)
    log.info("ПРОБНИК СРЕДСТВ v2 (read-only). Слот %s:%s/%s",
             slot.get("host", "127.0.0.1"), slot.get("requests_port"), slot.get("callbacks_port"))
    log.info("=" * 70)

    try:
        QuikPy = find_quikpy(slot)
    except Exception as e:  # noqa: BLE001
        log.info("Не нашёл/не импортировал QuikPy: %r. Укажи slot.quik_py_dir.", e)
        return
    try:
        qp = QuikPy(host=slot.get("host", "127.0.0.1"),
                    requests_port=int(slot.get("requests_port", 34140)),
                    callbacks_port=int(slot.get("callbacks_port", 34141)))
    except Exception as e:  # noqa: BLE001
        log.info("Подключение не удалось: %r", e)
        return

    try:
        log.info("QUIK %s · is_connected=%s",
                 qp.get_info_param("VERSION").get("data"),
                 qp.is_connected().get("data"))

        # --- счета ---------------------------------------------------------------
        accs = list(getattr(qp, "accounts", []) or [])
        if not accs:
            try:
                d = qp.get_trade_accounts().get("data")
                accs = d if isinstance(d, list) else []
            except Exception:  # noqa: BLE001
                pass

        fut = [a for a in accs if a.get("futures") or "SPBFUT" in (a.get("class_codes") or [])]
        log.info("")
        log.info("Фьючерсных счетов найдено: %d", len(fut))
        for a in fut:
            log.info("  firm_id=%s · trade_account_id=%s · client_code=%s",
                     a.get("firm_id"), mask_value(a.get("trade_account_id")),
                     mask_value(a.get("client_code")) or "(пусто)")
        if not fut:
            log.info("Фьючерсный счёт не опознан — пришли лог, разберёмся по class_codes.")
            return

        # --- ГЛАВНОЕ: фьючерсные лимиты по ПРАВИЛЬНОЙ строке trade_account_id ------
        log.info("")
        log.info("--- get_futures_limit(firm_id, trade_account_id, limit_type, curr_code) ---")
        # limit_type: 0 — деньги, 1 — залоговые; curr на FORTS обычно 'SUR'.
        for a in fut:
            firm = a.get("firm_id")
            acc = a.get("trade_account_id")
            for limit_type in (0, 1):
                for curr in ("SUR", "RUB", ""):
                    label = (f"firm={firm} acc={mask_value(acc)} "
                             f"limit_type={limit_type} curr='{curr}'")
                    _, ok = try_call(label, qp.get_futures_limit, firm, acc, limit_type, curr)
                    if ok:
                        break  # для этого limit_type валюту нашли — дальше не перебираем

        # --- запасной путь: портфель по фирме/клиенту ---------------------------
        log.info("")
        log.info("--- get_portfolio_info_ex(firm_id, client_code, limit_kind) [запасной] ---")
        for a in fut:
            firm = a.get("firm_id")
            for cc in dict.fromkeys([a.get("client_code") or "", a.get("trade_account_id")]):
                for kind in (0, 1, 2):
                    try_call(f"firm={firm} client={mask_value(cc) or '(пусто)'} limit_kind={kind}",
                             qp.get_portfolio_info_ex, firm, cc, kind)

        log.info("")
        log.info("Готово. Пришли logs/account_probe.log — соберём вкладку на этих полях.")
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
