"""
analyze.py — оценка исхода каждого сигнала 3+1 на исторических свечах (для win rate).

По каждому сигналу проходим вперёд по свечам:
  1) исполнился ли лимитный вход за order_ttl свечей (откат к цене входа)?
     — нет: «нет входа» (survivorship: считаем как НЕ-сделку, не выкидываем);
  2) если исполнился — что коснулось раньше, стоп или цель?
     консервативно: при одновременном касании на одной свече считаем СТОП (хуже для нас).

ВАЖНО про честность модели:
  - нет спреда и проскальзывания → результат ОПТИМИСТИЧЕН, реальный будет хуже;
  - исход внутри свечи определяется по диапазону high/low, без тиков;
  - каждый сигнал оценивается независимо (не «одна позиция за раз»); на редких
    сигналах это почти не отличается, но на густых даёт пересекающиеся сделки.

Северная звезда — НЕ винрейт, а ОЖИДАНИЕ (средний R). Винрейт легко надуть, опустив
цель (rr), но ожидание при этом не растёт. Поэтому здесь считаем и то, и другое, плюс
разбивку по размеру риска — чтобы видеть, не держится ли весь плюс на нескольких
крупных сделках и не убивают ли экономику мелкие.
"""


def evaluate_outcome(candles, i_rev, sig, cfg):
    """Исход одного сигнала. i_rev — индекс разворотной свечи в candles.

    В исход кладём risk и side — чтобы потом бить статистику по размеру риска/стороне.
    """
    ttl = int(cfg.get("order_ttl_candles", 7))
    rr = float(cfg.get("rr_ratio", 3.0))
    entry, stop, target = sig.entry, sig.stop, sig.target
    long = sig.side == "long"
    n = len(candles)
    meta = {"risk": float(sig.risk), "side": sig.side}

    fill = None
    for j in range(i_rev + 1, min(i_rev + 1 + ttl, n)):
        lo, hi = float(candles[j]["low"]), float(candles[j]["high"])
        if (lo <= entry) if long else (hi >= entry):
            fill = j
            break
    if fill is None:
        return {"filled": False, "outcome": "no_fill", "result_R": 0.0, **meta}

    for j in range(fill, n):
        lo, hi = float(candles[j]["low"]), float(candles[j]["high"])
        hit_stop = (lo <= stop) if long else (hi >= stop)
        hit_tgt = (hi >= target) if long else (lo <= target)
        if hit_stop:                       # консервативно: стоп раньше цели
            return {"filled": True, "outcome": "stop", "result_R": -1.0, **meta}
        if hit_tgt:
            return {"filled": True, "outcome": "target", "result_R": rr, **meta}
    return {"filled": True, "outcome": "open", "result_R": None, **meta}


def summarize(outcomes, rub_per_point=None):
    """Сводка по списку исходов: счётчики, win rate, сумма/средний R, ожидание на сигнал.

    Если задан rub_per_point (стоимость 1 пункта цены в рублях для 1 контракта),
    добавляем рублёвый P&L: по каждой завершённой сделке risk(пунктов)·rpp·result_R.
    """
    total = len(outcomes)
    no_fill = sum(1 for o in outcomes if o["outcome"] == "no_fill")
    target = sum(1 for o in outcomes if o["outcome"] == "target")
    stop = sum(1 for o in outcomes if o["outcome"] == "stop")
    opened = sum(1 for o in outcomes if o["outcome"] == "open")
    filled = target + stop + opened
    resolved = target + stop                       # завершённые сделки
    decided = resolved + no_fill                   # всё с определённым R (open исключаем)
    sum_R = sum(o["result_R"] for o in outcomes if o["result_R"] is not None)

    wr_resolved = (target / resolved * 100) if resolved else None
    wr_all = (target / total * 100) if total else None
    fill_rate = (filled / total * 100) if total else None
    avg_R = (sum_R / resolved) if resolved else None        # ожидание на ИСПОЛНЕННУЮ сделку
    exp_signal = (sum_R / decided) if decided else None     # ожидание на СИГНАЛ (нет-входа=0R)

    sum_rub = avg_rub = None
    if rub_per_point is not None:
        sum_rub = sum(o["risk"] * rub_per_point * o["result_R"]
                      for o in outcomes if o["result_R"] is not None)
        avg_rub = (sum_rub / resolved) if resolved else None
    return {
        "total": total, "no_fill": no_fill, "filled": filled,
        "target": target, "stop": stop, "open": opened,
        "resolved": resolved, "decided": decided,
        "win_rate_resolved": wr_resolved, "win_rate_all": wr_all,
        "fill_rate": fill_rate, "sum_R": sum_R,
        "avg_R": avg_R, "exp_per_signal": exp_signal,
        "sum_rub": sum_rub, "avg_rub": avg_rub,
    }


def _median(values):
    s = sorted(values)
    if not s:
        return None
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2


def summarize_by_risk(outcomes, rub_per_point=None):
    """Делит сигналы на «мелкий риск» и «крупный риск» по медиане и считает каждую группу.

    Порог — медиана риска внутри набора (инструмент-независимо: RTS и Si в разных шкалах).
    Возвращает None, если рисков нет. Группа «крупный» — риск ≥ медианы.
    """
    risks = [o["risk"] for o in outcomes if o.get("risk") is not None]
    med = _median(risks)
    if med is None:
        return None
    small = [o for o in outcomes if o["risk"] < med]
    large = [o for o in outcomes if o["risk"] >= med]
    return {"median_risk": med,
            "small": summarize(small, rub_per_point),
            "large": summarize(large, rub_per_point)}


def period_stats(first_dt, last_dt, resolved):
    """Календарный охват графика и частота сделок.

    first_dt / last_dt — datetime первого и последнего бара загруженной истории
    (весь охват графика, не по сигналам). resolved — число завершённых сделок,
    для частоты. Возвращает None, если дат нет.
    """
    if first_dt is None or last_dt is None:
        return None
    days = (last_dt.date() - first_dt.date()).days
    if days < 0:
        days = 0
    per_week = (resolved / days * 7) if days > 0 else None
    return {"from": first_dt, "to": last_dt, "days": days, "per_week": per_week}


def drawdown_stats(outcomes, rub_per_point=None):
    """Максимальная просадка по кривой эквити (в хронологии сделок).

    Идём по исходам по порядку, копим эквити (в R и, если задан rpp, в ₽), держим
    достигнутый пик и максимальное падение от него — это и есть просадка. Плюс самая
    длинная серия стопов подряд (no_fill серию не ломает и не продлевает; open пропускаем).
    Просадка считается от старта (пик=0): если кривая ушла в минус, это глубина минуса.
    """
    cum_R = peak_R = dd_R = 0.0
    cum_rub = peak_rub = dd_rub = 0.0
    streak = max_streak = 0
    have_rub = rub_per_point is not None
    any_resolved = False
    for o in outcomes:
        r = o["result_R"]
        if r is None:                 # open — ещё не закрыта, пропускаем
            continue
        cum_R += r
        peak_R = max(peak_R, cum_R)
        dd_R = max(dd_R, peak_R - cum_R)
        if have_rub:
            cum_rub += o["risk"] * rub_per_point * r
            peak_rub = max(peak_rub, cum_rub)
            dd_rub = max(dd_rub, peak_rub - cum_rub)
        if o["outcome"] == "stop":
            streak += 1
            max_streak = max(max_streak, streak)
            any_resolved = True
        elif o["outcome"] == "target":
            streak = 0
            any_resolved = True
    if not any_resolved:
        return None
    return {"max_dd_R": dd_R,
            "max_dd_rub": (dd_rub if have_rub else None),
            "max_loss_streak": max_streak}


def _rr_from(outcomes):
    """Восстанавливаем rr из любого тейка (result_R цели = rr). Для расчёта безубытка."""
    for o in outcomes:
        if o["outcome"] == "target" and o["result_R"]:
            return o["result_R"]
    return None


def summary_report(outcomes, rub_per_point=None, period=None):
    """Готовые строки сводки — единый источник для лога И для вкладки 'Аналитика'.

    Возвращает список строк (UTF-8, кириллица — в лог-файл и в окно одинаково).
    Если задан rub_per_point — добавляет строку рублёвого P&L по 1 контракту.
    Если задан period (из period_stats) — первой строкой идёт охват/частота.
    """
    s = summarize(outcomes, rub_per_point)
    if not s["total"]:
        return ["сигналов нет"]

    def pct(v):
        return f"{v:.0f}%" if v is not None else "н/д"

    def r(v):
        return f"{v:+.2f}" if v is not None else "н/д"

    lines = [
        f"сигналов {s['total']} · нет-входа {s['no_fill']} · тейк {s['target']} · "
        f"стоп {s['stop']} · открыто {s['open']}",
        f"win rate (исполн.)={pct(s['win_rate_resolved'])} · (от всех)={pct(s['win_rate_all'])} · "
        f"сумма R={s['sum_R']:+g}",
        f"ОЖИДАНИЕ: на исполн. сделку={r(s['avg_R'])}R · на сигнал={r(s['exp_per_signal'])}R "
        f"(нет-входа как 0R)",
    ]

    by = summarize_by_risk(outcomes, rub_per_point)
    if by:
        sm, lg = by["small"], by["large"]
        lines.append(
            f"по риску (медиана {by['median_risk']:.0f} п.): "
            f"мелкий<{by['median_risk']:.0f}: {sm['resolved']} сд., R={sm['sum_R']:+g}, "
            f"ожид.={r(sm['avg_R'])}R  |  "
            f"крупный≥{by['median_risk']:.0f}: {lg['resolved']} сд., R={lg['sum_R']:+g}, "
            f"ожид.={r(lg['avg_R'])}R"
        )

    if s["sum_rub"] is not None:
        extra = ""
        if by:
            extra = (f"  (мелкий {by['small']['sum_rub']:+.0f} ₽ · "
                     f"крупный {by['large']['sum_rub']:+.0f} ₽)")
        lines.append(
            f"по 1 контракту: итог {s['sum_rub']:+.0f} ₽ "
            f"(пункт ≈ {rub_per_point:.3f} ₽; для RTS плавает по курсу){extra}"
        )

    dd = drawdown_stats(outcomes, rub_per_point)
    if dd:
        dl = f"макс. просадка: {dd['max_dd_R']:.1f}R"
        if dd["max_dd_rub"] is not None:
            dl += f" · {dd['max_dd_rub']:.0f} ₽ (на 1 контракт)"
        dl += f" · серия стопов подряд: {dd['max_loss_streak']}"
        lines.append(dl)

    rr = _rr_from(outcomes)
    if rr:
        be = 100.0 / (rr + 1.0)        # безубыточный винрейт для данного rr
        lines.append(
            f"модель оптимистична (без спреда/проскальзывания); "
            f"безубыток при {rr:g}:1 = {be:.0f}%"
        )

    if period:
        pl = (f"Период (охват графика): {period['from']:%d.%m.%Y} → "
              f"{period['to']:%d.%m.%Y} · {period['days']} дн.")
        if period["per_week"] is not None:
            pl += f" · ≈ {period['per_week']:.1f} сделок/нед."
        lines.insert(0, pl)
    return lines
