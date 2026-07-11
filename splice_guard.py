"""
splice_guard.py — маркировка фиктивных гэпов склейки контрактов Si в OHLCV.

На склеенной истории фьючерса в зоне стыка контрактов (EXPIRATION_ADJ, ±2
торговых дня от третьего четверга мар/июн/сен/дек) возникают гэпы за счёт
разницы контанго между старым и новым контрактом, а не реального движения
рынка (подтверждено данными: гэпы +5498, +3981, +3485 пунктов ровно перед
экспирацией). Бэктест должен исключать сделки, чей PnL пересекает такой гэп.
"""

from __future__ import annotations

import pandas as pd

from event_calendar import EventFlag, event_status


def mark_splice_gaps(df: pd.DataFrame) -> pd.DataFrame:
    """Добавляет булеву колонку `splice_suspect` для баров в зоне EXPIRATION_ADJ.

    df — OHLCV с DatetimeIndex (naive считается уже МСК, aware конвертируется).
    """
    out = df.copy()
    norm_index = out.index.normalize()
    unique_days = norm_index.unique()
    flags_by_day = {
        day: bool(event_status(day.to_pydatetime()) & EventFlag.EXPIRATION_ADJ)
        for day in unique_days
    }
    out["splice_suspect"] = norm_index.map(flags_by_day).to_numpy()
    return out


def trade_crosses_gap(df: pd.DataFrame, entry_idx, exit_idx) -> bool:
    """True, если между entry_idx и exit_idx (включительно) есть бар splice_suspect.

    df должен уже содержать колонку `splice_suspect` (см. mark_splice_gaps).
    entry_idx/exit_idx — метки из df.index (не позиции).
    """
    window = df.loc[entry_idx:exit_idx]
    return bool(window["splice_suspect"].any())
