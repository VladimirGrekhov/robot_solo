"""Тесты меток сделок на графике QUIK (визуальный разбор QUIK-бэктеста).
Основной путь — текст+цвет через сырую addLabel2 (process_request); фолбэк —
картинки-маркеры через add_label (позиционная и dict-версии)."""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import orb_robot
import orb_journal


class RawQuik:
    """Версия с process_request — текстовые метки через addLabel2 (путь пользователя)."""
    def __init__(self):
        self.requests = []
        self.cleared = 0

    def del_all_labels(self, chart_tag):
        self.cleared += 1

    def process_request(self, req):
        self.requests.append(req)
        return {"data": len(self.requests)}


class FlatQuik:
    """Сигнатура как в версии пользователя: позиционные аргументы, текст — отдельно."""
    def __init__(self):
        self.labels = []
        self.text_params = []
        self.cleared = 0

    def del_all_labels(self, chart_tag):
        self.cleared += 1

    def add_label(self, price, cur_date, cur_time, qty, path, chart_tag, alignment, background):
        self.labels.append((price, cur_date, cur_time, chart_tag, alignment, path))
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


def test_raw_addlabel2_text():
    """Основной путь: текст+цвет через addLabel2. Проверяем текст, тег и цвет в data."""
    qp = RawQuik()
    n, err, diag = orb_robot.add_trade_labels(qp, "si15m", TRADES)
    assert err is None
    assert n == 6                          # 3 метки на сделку (вход, стоп, выход)
    assert qp.cleared == 1
    assert len(qp.requests) == 6
    assert all(r["cmd"] == "addLabel2" for r in qp.requests)
    fields = [r["data"].split("|") for r in qp.requests]
    assert all(f[0] == "si15m" for f in fields)          # тег — первое поле
    texts = [f[4] for f in fields]                       # text — пятое поле
    assert "BUY" in texts and "SELL" in texts and "+600" in texts and "-400" in texts
    assert any(t.startswith("SL ") for t in texts)       # метка стоп-лосса
    # вход-лонг зелёный (r,g,b на позициях 8,9,10)
    buy = fields[0]
    assert (buy[8], buy[9], buy[10]) == ("0", "200", "0")
    # стоп-лосс лонга — оранжевый, на цене стопа (79500)
    sl = fields[1]
    assert sl[4] == "SL 79500" and (sl[8], sl[9], sl[10]) == ("255", "140", "0")
    assert "способ=addLabel2" in diag


def test_flat_signature_fallback_images():
    """Фолбэк (нет process_request): картинки-маркеры через add_label."""
    qp = FlatQuik()
    n, err, diag = orb_robot.add_trade_labels(qp, "si15m", TRADES)
    assert err is None
    assert n == 6
    assert qp.cleared == 1
    assert len(qp.labels) == 6
    assert qp.labels[0][3] == "si15m"      # chart_tag подставлен правильно
    assert qp.labels[0][5].endswith(".bmp")   # картинка-маркер подставлена в path
    assert "способ=картинки" in diag
    assert "иконки=есть" in diag


def test_dict_signature():
    qp = DictQuik()
    n, err, diag = orb_robot.add_trade_labels(qp, "si15m", TRADES)
    assert err is None
    assert n == 6
    assert all("TEXT" in p and "YVALUE" in p for p in qp.labels)


def test_unsupported():
    n, err, diag = orb_robot.add_trade_labels(NoLabels(), "si15m", TRADES)
    assert n == 0
    assert err is not None and "add_label" in err
