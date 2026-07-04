"""
detector.py — детектор разворотного паттерна 3+1 с фильтрами размаха.

База — наш проверенный strategy.py (бычий/медвежий разворот, пробой, вход на откате,
стоп за экстремумом свечи 4, цель по rr_ratio). Сверх него — фильтры из pattern_app,
которые отсекают «плоские» срабатывания (мелкие тела, вялый тренд, крошечный разворот):

  min_body_ticks            — тело каждой трендовой свечи должно быть не меньше, тиков;
  min_trend_range_ticks     — суммарный ход тренда (close первой → close последней), тиков;
  min_reversal_range_ticks  — размах разворотной свечи (high-low), тиков.

Фильтры выключены при значении 0. price_step передаётся per-инструмент (Si=1, RTS=10).
Модуль чистый: ни QUIK, ни отправки заявок здесь нет.
"""

from dataclasses import dataclass, field


@dataclass
class Signal:
    side: str                    # "long" | "short"
    entry: float
    stop: float
    target: float
    risk: float
    ref_index: int
    reversal_index: int
    notes: list[str] = field(default_factory=list)


def _f(c, k): return float(c[k])
def _body(c): return abs(_f(c, "close") - _f(c, "open"))
def _range(c): return _f(c, "high") - _f(c, "low")


def _is_bear(c, min_body): return _f(c, "close") < _f(c, "open") and _body(c) >= min_body
def _is_bull(c, min_body): return _f(c, "close") > _f(c, "open") and _body(c) >= min_body


def _basis_bounds(c, basis):
    if basis == "body":
        return min(_f(c, "open"), _f(c, "close")), max(_f(c, "open"), _f(c, "close"))
    return _f(c, "low"), _f(c, "high")


def _trend_dir(candles, cfg):
    """Направление старшего тренда на момент разворотной свечи: +1 вверх, -1 вниз, 0 неясно.

    Метрика (cfg['trend_method']):
      - 'sma_slope'  — наклон SMA(period) по закрытиям того же ТФ за trend_slope_lookback свечей;
      - 'higher_tf'  — то же, но по закрытиям, прорежённым в старший ТФ (множитель trend_htf_factor).
    Если данных мало — возвращаем 0 (тогда фильтр сигнал НЕ режет).
    """
    method = cfg.get("trend_method", "sma_slope")
    period = max(2, int(cfg.get("trend_sma_period", 20)))
    lookback = max(1, int(cfg.get("trend_slope_lookback", 3)))
    closes = [_f(c, "close") for c in candles]
    if method == "higher_tf":
        factor = max(1, int(cfg.get("trend_htf_factor", 3)))
        closes = closes[::-1][::factor][::-1]   # прореживаем, выравнивая по последней свече
    if len(closes) < period + lookback:
        return 0
    sma_now = sum(closes[-period:]) / period
    sma_prev = sum(closes[-(period + lookback):-lookback]) / period
    if sma_now > sma_prev:
        return 1
    if sma_now < sma_prev:
        return -1
    return 0


def _passes_trend_filter(candles, cfg, sig):
    """True, если сигнал проходит тренд-фильтр (или фильтр выключен / тренд неясен)."""
    mode = cfg.get("trend_filter", "off")
    if mode not in ("with", "against"):
        return True
    d = _trend_dir(candles, cfg)
    if d == 0:                      # тренд не определён — не режем
        sig.notes.append("тренд-фильтр: тренд неясен, пропущено без фильтра")
        return True
    with_trend = (sig.side == "long" and d > 0) or (sig.side == "short" and d < 0)
    up = "вверх" if d > 0 else "вниз"
    if mode == "with":
        sig.notes.append(f"тренд {up}, фильтр 'по тренду'")
        return with_trend
    sig.notes.append(f"тренд {up}, фильтр 'против тренда'")
    return not with_trend


def detect(candles, cfg, price_step):
    """Сетап 3+1 на последней свече окна или None. cfg — блок reversal_3plus1."""
    k = int(cfg.get("trend_candles", 3))
    need = k + 1
    if len(candles) < need:
        return None
    window = candles[-need:]
    trend, rev = window[:k], window[k]
    base = len(candles) - need

    step = float(price_step)
    min_body = float(cfg.get("min_body_ticks", 0)) * step
    min_trend = float(cfg.get("min_trend_range_ticks", 0)) * step
    min_rev = float(cfg.get("min_reversal_range_ticks", 0)) * step

    sig = (_detect(trend, rev, cfg, step, min_body, min_trend, min_rev, "long")
           or _detect(trend, rev, cfg, step, min_body, min_trend, min_rev, "short"))
    if sig is None:
        return None
    if not _passes_trend_filter(candles, cfg, sig):
        return None
    sig.ref_index += base
    sig.reversal_index += base
    return sig


def _detect(trend, rev, cfg, step, min_body, min_trend, min_rev, side):
    bull = side == "long"
    trend_ok = _is_bear if bull else _is_bull
    if not all(trend_ok(c, min_body) for c in trend):
        return None

    closes = [_f(c, "close") for c in trend]
    if cfg.get("trend_strict_monotonic", True):
        ok = all((closes[i] < closes[i - 1]) if bull else (closes[i] > closes[i - 1])
                 for i in range(1, len(closes)))
        if not ok:
            return None
    else:
        if not ((closes[-1] < closes[0]) if bull else (closes[-1] > closes[0])):
            return None

    # фильтр: суммарный ход тренда
    trend_move = (closes[0] - closes[-1]) if bull else (closes[-1] - closes[0])
    if min_trend > 0 and trend_move < min_trend:
        return None

    # разворотная свеча: противоположный цвет (с учётом min_body) + фильтр размаха
    rev_ok = _is_bull(rev, min_body) if bull else _is_bear(rev, min_body)
    if cfg.get("c4_opposite_color", True) and not rev_ok:
        return None
    if min_rev > 0 and _range(rev) < min_rev:
        return None

    # пробой
    ref = _f(trend[0], cfg.get("breakout_ref", "open"))
    confirm_key = "close" if cfg.get("breakout_confirm", "close") == "close" else ("high" if bull else "low")
    confirm = _f(rev, confirm_key)
    if not ((confirm > ref) if bull else (confirm < ref)):
        return None

    # вход (откат внутрь разворотной свечи)
    lo, hi = _basis_bounds(rev, cfg.get("entry_basis", "body"))
    retrace = float(cfg.get("entry_retrace", 0.5))
    entry = (hi - retrace * (hi - lo)) if bull else (lo + retrace * (hi - lo))

    # стоп / риск / цель
    off = float(cfg.get("stop_offset_ticks", 1)) * step
    stop = (_f(rev, "low") - off) if bull else (_f(rev, "high") + off)
    risk = (entry - stop) if bull else (stop - entry)
    if risk <= 0:
        return None
    rr = float(cfg.get("rr_ratio", 3.0))
    target = (entry + rr * risk) if bull else (entry - rr * risk)

    notes = []
    if bull and target > _f(rev, "high"):
        notes.append("Цель выше хая разворотной свечи — проверь достижимость 3R.")
    if not bull and target < _f(rev, "low"):
        notes.append("Цель ниже лоя разворотной свечи — проверь достижимость 3R.")

    return Signal(side, entry, stop, target, risk, 0, len(trend), notes)
