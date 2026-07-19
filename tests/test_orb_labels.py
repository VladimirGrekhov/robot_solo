"""Тесты меток сделок на графике QUIK (визуальный разбор QUIK-бэктеста).
QUIK эмулируется — проверяем логику расстановки, не проводной формат AddLabel."""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import orb_robot
import orb_journal


class FakeQuik:
    def __init__(self):
        self.labels = []
        self.cleared = 0

    def del_all_labels(self, tag):
        self.cleared += 1

    def add_label(self, tag, params):
        self.labels.append(params)
        return {"data": len(self.labels)}


class FakeQuikNoLabels:
    pass                       # нет add_label -> метки недоступны


def _trade(side, entry, exit_, pnl):
    return orb_journal.TradeRecord(
        datetime_in=datetime(2026, 7, 16, 11, 15), datetime_out=datetime(2026, 7, 16, 18, 45),
        dir=side, qty=2, entry=entry, exit=exit_,
        stop=entry - 500 if side == "long" else entry + 500,
        pnl_pt=pnl / 2, pnl_rub=pnl, exit_reason="eod", range_width_pt=600, event_flags="")


def test_add_trade_labels():
    qp = FakeQuik()
    trades = [_trade("long", 80000, 80300, 600.0), _trade("short", 80000, 79800, -400.0)]
    n, err = orb_robot.add_trade_labels(qp, "si15m", trades)
    assert err is None
    assert n == 4                          # по 2 метки (вход+выход) на сделку
    assert qp.cleared == 1                 # старые метки сняты один раз
    texts = [p["TEXT"] for p in qp.labels]
    assert "Buy" in texts and "Sell" in texts        # направление входа
    assert "+600" in texts and "-400" in texts        # PnL на выходе
    # у метки есть дата/время/цена
    assert all({"DATE", "TIME", "YVALUE"} <= set(p) for p in qp.labels)


def test_add_trade_labels_unsupported():
    n, err = orb_robot.add_trade_labels(FakeQuikNoLabels(), "si15m", [_trade("long", 80000, 80300, 600.0)])
    assert n == 0
    assert err is not None and "add_label" in err
