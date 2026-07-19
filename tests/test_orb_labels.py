"""Тесты меток сделок на графике QUIK (визуальный разбор QUIK-бэктеста).
Проверяем подстройку под разные сигнатуры add_label (позиционная у пользователя
и dict-версия) + текст/цвет через set_label_params. Проводной формат не тестируем."""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import orb_robot
import orb_journal


class FlatQuik:
    """Сигнатура как в версии пользователя: позиционные аргументы, текст — отдельно."""
    def __init__(self):
        self.labels = []
        self.text_params = []
        self.cleared = 0

    def del_all_labels(self, chart_tag):
        self.cleared += 1

    def add_label(self, price, cur_date, cur_time, qty, path, chart_tag, alignment, background):
        self.labels.append((price, cur_date, cur_time, chart_tag, alignment))
        return {"data": len(self.labels)}

    def set_label_params(self, chart_tag, label_id, params):
        self.text_params.append(params)


class DictQuik:
    """Сигнатура add_label(tag, params) — dict-версия."""
    def __init__(self):
        self.labels = []

    def add_label(self, chart_tag, label_params):
        self.labels.append(label_params)
        return len(self.labels)


class NoLabels:
    pass


def _trade(side, entry, exit_, pnl):
    return orb_journal.TradeRecord(
        datetime_in=datetime(2026, 7, 16, 11, 15), datetime_out=datetime(2026, 7, 16, 18, 45),
        dir=side, qty=2, entry=entry, exit=exit_,
        stop=entry - 500 if side == "long" else entry + 500,
        pnl_pt=pnl / 2, pnl_rub=pnl, exit_reason="eod", range_width_pt=600, event_flags="")


TRADES = [_trade("long", 80000, 80300, 600.0), _trade("short", 80000, 79800, -400.0)]


def test_flat_signature_with_text():
    qp = FlatQuik()
    n, err, diag = orb_robot.add_trade_labels(qp, "si15m", TRADES)
    assert err is None
    assert n == 4                          # 2 метки на сделку
    assert qp.cleared == 1
    assert len(qp.labels) == 4
    assert qp.labels[0][3] == "si15m"      # chart_tag подставлен правильно
    texts = [p["TEXT"] for p in qp.text_params]
    assert "Buy" in texts and "Sell" in texts and "+600" in texts and "-400" in texts
    assert "set_label_params=есть" in diag


def test_dict_signature():
    qp = DictQuik()
    n, err, diag = orb_robot.add_trade_labels(qp, "si15m", TRADES)
    assert err is None
    assert n == 4
    assert all("TEXT" in p and "YVALUE" in p for p in qp.labels)


def test_unsupported():
    n, err, diag = orb_robot.add_trade_labels(NoLabels(), "si15m", TRADES)
    assert n == 0
    assert err is not None and "add_label" in err
