"""
pattern_window.py — окно живого режима pattern_robot (Tkinter, вкладки).

Минимальная первая версия: шапка-статус (всегда на виду) + вкладки «Робот» и «Лог».
Движок run_live крутится в фоновом потоке; события и строки лога приходят в окно
через очередь, GUI забирает их в главном потоке по таймеру (after).

Заявок не шлёт: execute_signal в движке — симуляция (live_trading=false).
Вкладки «Связь»/«Статистика»/«Настройки» добавим следующими шагами.

Запуск:  python pattern_window.py
"""

import logging
import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import ttk

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pattern_robot as P

# --- тёмная палитра (как во всех окнах проекта) --------------------------------
BG = "#1e1e26"
BG_PANEL = "#262630"
FG = "#e6e6ea"
FG_MUTED = "#9a9aa6"
ACCENT = "#3a3a48"
GREEN = "#3fb950"
RED = "#f25555"
GREY = "#5a5a66"
YELLOW = "#d8a23a"
FONT = ("Segoe UI", 10)
FONT_BOLD = ("Segoe UI", 11, "bold")
FONT_MONO = ("Consolas", 9)


class _QueueLogHandler(logging.Handler):
    """Кладёт строки лога в очередь окна как ('log', текст)."""
    def __init__(self, q: queue.Queue):
        super().__init__()
        self.q = q

    def emit(self, record):
        try:
            self.q.put(("log", self.format(record)))
        except Exception:
            pass


class App(tk.Tk):
    POLL_MS = 150

    def __init__(self, cfg: dict):
        super().__init__()
        self.cfg = cfg
        self.q: "queue.Queue" = queue.Queue()
        self._stop = threading.Event()
        self._worker = None
        self._cards = {}        # имя инструмента -> dict виджетов карточки
        self._an = {}           # имя инструмента -> последний payload истории (для вкладки Аналитика)
        self._tickers = {}      # имя инструмента -> тикер (для шапки карточки аналитики)

        self.title("pattern_robot — живой режим")
        self.configure(bg=BG)
        self.geometry(cfg.get("window_geometry", "800x600"))
        self.minsize(680, 480)

        self._build_header()
        self._build_tabs()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # лог движка -> очередь окна
        self._log_handler = _QueueLogHandler(self.q)
        self._log_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        P.log.addHandler(self._log_handler)
        P.log.setLevel(logging.INFO)

        self.after(self.POLL_MS, self._poll)

    # --- шапка (всегда видна) ---------------------------------------------------
    def _build_header(self):
        head = tk.Frame(self, bg=BG_PANEL)
        head.pack(fill="x", side="top")

        row1 = tk.Frame(head, bg=BG_PANEL)
        row1.pack(fill="x", padx=12, pady=(10, 2))
        self.lamp = tk.Canvas(row1, width=16, height=16, bg=BG_PANEL, highlightthickness=0)
        self.lamp.create_oval(2, 2, 14, 14, fill=GREY, outline="", tags="dot")
        self.lamp.pack(side="left")
        self.status_lbl = tk.Label(row1, text="остановлен", bg=BG_PANEL, fg=FG, font=FONT_BOLD)
        self.status_lbl.pack(side="left", padx=8)
        self.mode_lbl = tk.Label(row1, text="", bg=BG_PANEL, fg=FG_MUTED, font=FONT)
        self.mode_lbl.pack(side="right")

        row2 = tk.Frame(head, bg=BG_PANEL)
        row2.pack(fill="x", padx=12, pady=(0, 6))
        self.online_lbl = tk.Label(row2, text="", bg=BG_PANEL, fg=FG_MUTED, font=FONT)
        self.online_lbl.pack(side="left")

        # кнопки старт/стоп
        btns = tk.Frame(head, bg=BG_PANEL)
        btns.pack(fill="x", padx=12, pady=(0, 10))
        self.start_btn = tk.Button(btns, text="Старт", command=self._start, bg=ACCENT, fg=FG,
                                   activebackground="#4a4a5a", activeforeground=FG, relief="flat",
                                   font=FONT, padx=14, pady=4, cursor="hand2")
        self.start_btn.pack(side="left")
        self.stop_btn = tk.Button(btns, text="Стоп", command=self._stop_worker, bg=ACCENT, fg=FG,
                                  activebackground="#4a4a5a", activeforeground=FG, relief="flat",
                                  font=FONT, padx=14, pady=4, cursor="hand2", state="disabled")
        self.stop_btn.pack(side="left", padx=8)
        tk.Label(btns, text="заявки не шлются (симуляция)", bg=BG_PANEL, fg=FG_MUTED,
                 font=("Segoe UI", 9)).pack(side="right")

    # --- вкладки ----------------------------------------------------------------
    def _build_tabs(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", background=BG_PANEL, foreground=FG_MUTED,
                        padding=(14, 6), font=FONT)
        style.map("TNotebook.Tab", background=[("selected", ACCENT)], foreground=[("selected", FG)])

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=8, pady=8)

        # вкладка «Робот»
        self.tab_robot = tk.Frame(nb, bg=BG)
        nb.add(self.tab_robot, text="Робот")
        self.cards_frame = tk.Frame(self.tab_robot, bg=BG)
        self.cards_frame.pack(fill="x", padx=8, pady=8)
        tk.Label(self.tab_robot, text="Лента сигналов", bg=BG, fg=FG, font=FONT_BOLD).pack(
            anchor="w", padx=10, pady=(6, 2))
        self.feed = tk.Text(self.tab_robot, bg=BG_PANEL, fg=FG, font=FONT_MONO, relief="flat",
                            wrap="word", padx=10, pady=8, height=12)
        self.feed.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.feed.configure(state="disabled")

        # вкладка «Лог»
        self.tab_log = tk.Frame(nb, bg=BG)
        nb.add(self.tab_log, text="Лог")
        self.logbox = tk.Text(self.tab_log, bg=BG_PANEL, fg=FG_MUTED, font=FONT_MONO, relief="flat",
                              wrap="word", padx=10, pady=8)
        self.logbox.pack(fill="both", expand=True, padx=8, pady=8)
        self.logbox.configure(state="disabled")

        # вкладка «Настройки» — только блок стратегии reversal_3plus1
        self.tab_settings = tk.Frame(nb, bg=BG)
        nb.add(self.tab_settings, text="Настройки")
        self._build_settings(self.tab_settings)

        # вкладка «Аналитика» — метрики ожидания/винрейта + разбивка по риску
        self.tab_analytics = tk.Frame(nb, bg=BG)
        nb.add(self.tab_analytics, text="Аналитика")
        self._build_analytics(self.tab_analytics)

        # вкладка «Счёт» — средства FORTS (read-only, обновляется в ожидании свечи)
        self.tab_account = tk.Frame(nb, bg=BG)
        nb.add(self.tab_account, text="Счёт")
        self._build_account(self.tab_account)

    # --- вкладка настроек стратегии ---------------------------------------------
    # (поле, подпись, тип): bool -> галка, int/float -> числовое поле с проверкой
    SETTINGS_FIELDS = [
        ("rr_ratio", "Риск:прибыль (rr_ratio)", float),
        ("entry_retrace", "Откат входа в тело (entry_retrace, 0..1)", float),
        ("stop_offset_ticks", "Отступ стопа, тиков (stop_offset_ticks)", int),
        ("order_ttl_candles", "Жизнь заявки, свечей (order_ttl_candles)", int),
        ("trend_strict_monotonic", "Строгий монотонный тренд", bool),
        ("min_body_ticks", "Фильтр: мин. тело трендовой, тиков", int),
        ("min_trend_range_ticks", "Фильтр: мин. ход тренда, тиков", int),
        ("min_reversal_range_ticks", "Фильтр: мин. размах разворотной, тиков", int),
        ("trend_filter", "Тренд-фильтр: off / по тренду / против", ["off", "with", "against"]),
        ("trend_method", "Метрика тренда", ["sma_slope", "higher_tf"]),
        ("trend_sma_period", "Период SMA, свечей", int),
        ("trend_slope_lookback", "Наклон SMA: за сколько свечей", int),
        ("trend_htf_factor", "Старший ТФ: множитель ×ТФ (для higher_tf)", int),
    ]

    # дефолты для полей, которых может не быть в конфиге (тренд-фильтр выключен)
    SETTING_DEFAULTS = {
        "trend_filter": "off", "trend_method": "sma_slope",
        "trend_sma_period": 20, "trend_slope_lookback": 3, "trend_htf_factor": 3,
    }

    def _build_settings(self, parent):
        rev = self.cfg.get("reversal_3plus1", {})
        tk.Label(parent, text="Стратегия 3+1 (reversal_3plus1)", bg=BG, fg=FG, font=FONT_BOLD).pack(
            anchor="w", padx=12, pady=(12, 2))
        tk.Label(parent, text="Фильтр = 0 значит выключен. Изменения применятся после Стоп → Старт.",
                 bg=BG, fg=FG_MUTED, font=("Segoe UI", 9)).pack(anchor="w", padx=12, pady=(0, 8))

        form = tk.Frame(parent, bg=BG)
        form.pack(fill="x", padx=12)
        self._setting_vars = {}
        for i, (key, label, typ) in enumerate(self.SETTINGS_FIELDS):
            tk.Label(form, text=label, bg=BG, fg=FG, font=FONT).grid(row=i, column=0, sticky="w", pady=4)
            cur = rev.get(key, self.SETTING_DEFAULTS.get(key))
            if typ is bool:
                var = tk.BooleanVar(value=bool(cur))
                tk.Checkbutton(form, variable=var, bg=BG, fg=FG, selectcolor=BG_PANEL,
                               activebackground=BG, highlightthickness=0).grid(row=i, column=1, sticky="w", padx=10)
            elif isinstance(typ, list):
                var = tk.StringVar(value=str(cur if cur is not None else typ[0]))
                om = tk.OptionMenu(form, var, *typ)
                om.configure(bg=BG_PANEL, fg=FG, activebackground=ACCENT, activeforeground=FG,
                             relief="flat", highlightthickness=0, font=FONT, width=10, cursor="hand2")
                om["menu"].configure(bg=BG_PANEL, fg=FG, activebackground=ACCENT, activeforeground=FG)
                om.grid(row=i, column=1, sticky="w", padx=10)
            else:
                var = tk.StringVar(value=str(cur if cur is not None else ""))
                tk.Entry(form, textvariable=var, width=10, bg=BG_PANEL, fg=FG, insertbackground=FG,
                         relief="flat", font=FONT).grid(row=i, column=1, sticky="w", padx=10)
            self._setting_vars[key] = (var, typ)
        form.columnconfigure(0, weight=1)

        bar = tk.Frame(parent, bg=BG)
        bar.pack(fill="x", padx=12, pady=12)
        tk.Button(bar, text="Сохранить", command=self._save_settings, bg=ACCENT, fg=FG,
                  activebackground="#4a4a5a", activeforeground=FG, relief="flat",
                  font=FONT, padx=14, pady=4, cursor="hand2").pack(side="left")
        self.settings_msg = tk.Label(bar, text="", bg=BG, fg=FG_MUTED, font=FONT)
        self.settings_msg.pack(side="left", padx=12)

    def _save_settings(self):
        # 1) собрать и проверить значения
        new_vals = {}
        for key, (var, typ) in self._setting_vars.items():
            if isinstance(typ, list):
                new_vals[key] = var.get()
                continue
            if typ is bool:
                new_vals[key] = bool(var.get())
                continue
            raw = var.get().strip().replace(",", ".")
            try:
                val = typ(float(raw)) if typ is int else typ(raw)
            except (ValueError, TypeError):
                self.settings_msg.config(text=f"⚠ «{key}»: нужно число", fg=RED)
                return
            if key == "entry_retrace" and not (0.0 <= val <= 1.0):
                self.settings_msg.config(text="⚠ entry_retrace должен быть 0..1", fg=RED)
                return
            if val < 0:
                self.settings_msg.config(text=f"⚠ «{key}»: не может быть отрицательным", fg=RED)
                return
            new_vals[key] = val
        # 2) записать в конфиг атомарно
        self.cfg.setdefault("reversal_3plus1", {}).update(new_vals)
        self.cfg["window_geometry"] = self.geometry()
        try:
            P.save_config(self.cfg)
        except Exception as e:  # noqa: BLE001
            self.settings_msg.config(text=f"⚠ не сохранилось: {e!r}", fg=RED)
            return
        running = self._worker is not None and self._worker.is_alive()
        note = " · применится после Стоп → Старт" if running else " · применено"
        self.settings_msg.config(text="✓ сохранено" + note, fg=GREEN)

    def _make_card(self, name: str):
        card = tk.Frame(self.cards_frame, bg=BG_PANEL)
        card.pack(side="left", fill="x", expand=True, padx=6)
        tk.Label(card, text=name, bg=BG_PANEL, fg=FG, font=FONT_BOLD).pack(anchor="w", padx=10, pady=(8, 0))
        sec = tk.Label(card, text="тикер: …", bg=BG_PANEL, fg=FG_MUTED, font=("Segoe UI", 9))
        sec.pack(anchor="w", padx=10)
        wr = tk.Label(card, text="история: …", bg=BG_PANEL, fg=FG, font=FONT)
        wr.pack(anchor="w", padx=10)
        oi = tk.Label(card, text="ОИ: …", bg=BG_PANEL, fg=YELLOW, font=FONT)
        oi.pack(anchor="w", padx=10, pady=(0, 8))
        self._cards[name] = {"sec": sec, "wr": wr, "oi": oi}

    # --- вкладка «Аналитика» ----------------------------------------------------
    def _build_analytics(self, parent):
        tk.Label(parent, text="Аналитика истории", bg=BG, fg=FG, font=FONT_BOLD).pack(
            anchor="w", padx=12, pady=(12, 2))
        tk.Label(parent, text="Северная звезда — ожидание на сигнал, не винрейт. "
                              "Обновляется при разметке истории (Старт).",
                 bg=BG, fg=FG_MUTED, font=("Segoe UI", 9)).pack(anchor="w", padx=12, pady=(0, 8))
        self.an_body = tk.Frame(parent, bg=BG)
        self.an_body.pack(fill="both", expand=True, padx=8, pady=4)
        self._render_analytics()

    @staticmethod
    def _clr(v):
        """Цвет числа: зелёный для >=0, красный для <0, серый для None."""
        if v is None:
            return FG_MUTED
        return GREEN if v >= 0 else RED

    @staticmethod
    def _r(v, suffix="R"):
        return f"{v:+.2f}{suffix}" if v is not None else "н/д"

    def _make_an_card(self, parent, name, p):
        card = tk.Frame(parent, bg=BG_PANEL)
        card.pack(side="left", fill="both", expand=True, padx=6, pady=2)

        head = tk.Frame(card, bg=BG_PANEL)
        head.pack(fill="x", padx=12, pady=(10, 8))
        tk.Label(head, text=name, bg=BG_PANEL, fg=FG, font=FONT_BOLD).pack(side="left")
        tk.Label(head, text=f"{self._tickers.get(name, 'н/д')} · rr {p.get('rr', 3):g}:1",
                 bg=BG_PANEL, fg=FG_MUTED, font=("Segoe UI", 9)).pack(side="right")

        # период (охват графика) + частота
        if p.get("period_days") is not None:
            per = f"{p.get('period_from', '?')} → {p.get('period_to', '?')} · {p['period_days']} дн."
            if p.get("freq_per_week") is not None:
                per += f" · ≈ {p['freq_per_week']:.1f}/нед."
            tk.Label(card, text=per, bg=BG_PANEL, fg="#6f6f7a",
                     font=("Segoe UI", 8)).pack(anchor="w", padx=12, pady=(0, 6))

        # крупные метрики: ожидание на сигнал и на сделку
        big = tk.Frame(card, bg=BG_PANEL)
        big.pack(fill="x", padx=12, pady=(0, 8))
        for label, val in (("ожидание / сигнал", p.get("exp_per_signal")),
                           ("/ сделку", p.get("avg_R"))):
            col = tk.Frame(big, bg=BG_PANEL)
            col.pack(side="left", padx=(0, 18))
            tk.Label(col, text=label, bg=BG_PANEL, fg=FG_MUTED, font=("Segoe UI", 8)).pack(anchor="w")
            tk.Label(col, text=self._r(val), bg=BG_PANEL, fg=self._clr(val),
                     font=("Segoe UI", 17, "bold")).pack(anchor="w")

        # строка винрейт / Σ R / безубыток
        wr = p.get("win_rate")
        rr = p.get("rr", 3)
        be = 100.0 / (rr + 1.0) if rr else None
        stat = tk.Frame(card, bg=BG_PANEL)
        stat.pack(fill="x", padx=12, pady=(0, 8))
        tk.Label(stat, text=f"win rate {wr:.0f}%" if wr is not None else "win rate н/д",
                 bg=BG_PANEL, fg=FG, font=("Segoe UI", 9)).pack(side="left", padx=(0, 12))
        tk.Label(stat, text="Σ R ", bg=BG_PANEL, fg=FG_MUTED, font=("Segoe UI", 9)).pack(side="left")
        tk.Label(stat, text=f"{p.get('sum_R', 0):+g}", bg=BG_PANEL, fg=self._clr(p.get("sum_R", 0)),
                 font=("Segoe UI", 9)).pack(side="left", padx=(0, 12))
        tk.Label(stat, text=f"безуб. {be:.0f}%" if be is not None else "",
                 bg=BG_PANEL, fg=FG, font=("Segoe UI", 9)).pack(side="left")

        # рубль-P&L по 1 контракту (если стоимость пункта известна)
        rub = p.get("sum_rub")
        if rub is not None:
            rrow = tk.Frame(card, bg=BG_PANEL)
            rrow.pack(fill="x", padx=12, pady=(0, 8))
            tk.Label(rrow, text="1 контракт: ", bg=BG_PANEL, fg=FG_MUTED,
                     font=("Segoe UI", 9)).pack(side="left")
            tk.Label(rrow, text=f"{round(rub):+} \u20bd", bg=BG_PANEL, fg=self._clr(rub),
                     font=("Segoe UI", 12, "bold")).pack(side="left")

        # макс. просадка (риск капитала): в R, в ₽ и длина серии стопов
        dd = p.get("drawdown")
        if dd:
            ddrow = tk.Frame(card, bg=BG_PANEL)
            ddrow.pack(fill="x", padx=12, pady=(0, 8))
            tk.Label(ddrow, text="макс. просадка: ", bg=BG_PANEL, fg=FG_MUTED,
                     font=("Segoe UI", 9)).pack(side="left")
            ddtxt = f"{dd['max_dd_R']:.1f}R"
            if dd.get("max_dd_rub") is not None:
                ddtxt += f" · {round(dd['max_dd_rub'])} \u20bd"
            ddtxt += f" · серия −{dd['max_loss_streak']}"
            tk.Label(ddrow, text=ddtxt, bg=BG_PANEL, fg=RED if dd["max_dd_R"] > 0 else FG,
                     font=("Segoe UI", 9)).pack(side="left")

        # ГО (начальная маржа) — сколько биржа держит под 1 контракт
        go = p.get("go")
        gorow = tk.Frame(card, bg=BG_PANEL)
        gorow.pack(fill="x", padx=12, pady=(0, 8))
        tk.Label(gorow, text="ГО (маржа): ", bg=BG_PANEL, fg=FG_MUTED,
                 font=("Segoe UI", 9)).pack(side="left")
        gotxt = f"{round(go)} \u20bd/контракт" if go is not None else "н/д (нет в QUIK?)"
        tk.Label(gorow, text=gotxt, bg=BG_PANEL, fg=FG if go is not None else FG_MUTED,
                 font=("Segoe UI", 9)).pack(side="left")

        # счётчики
        mono_s = ("Consolas", 8)
        det = tk.Frame(card, bg=BG_PANEL)
        det.pack(fill="x", padx=12, pady=(2, 6))
        tk.Label(det, text=f"сигналов {p.get('total', 0)} · тейк {p.get('target', 0)} · "
                           f"стоп {p.get('stop', 0)} · н/в {p.get('no_fill', 0)}",
                 bg=BG_PANEL, fg=FG_MUTED, font=mono_s).pack(anchor="w")

        # разбивка по риску (мелкий/крупный по медиане)
        by = p.get("by_risk")
        risk = tk.Frame(card, bg=BG_PANEL)
        risk.pack(fill="x", padx=12, pady=(0, 10))
        if by:
            m = by["median_risk"]
            sm, lg = by["small"], by["large"]
            tk.Label(risk, text=f"по риску (медиана {m:.0f} п.):", bg=BG_PANEL, fg="#6f6f7a",
                     font=mono_s).pack(anchor="w")
            for tag, b, lt in ((f"мелкий<{m:.0f}", sm, "<"), (f"крупный≥{m:.0f}", lg, "≥")):
                rowf = tk.Frame(risk, bg=BG_PANEL)
                rowf.pack(anchor="w", fill="x")
                tk.Label(rowf, text=f"{tag}: {b['resolved']} сд.  R ", bg=BG_PANEL, fg=FG_MUTED,
                         font=mono_s).pack(side="left")
                tk.Label(rowf, text=f"{b['sum_R']:+g}", bg=BG_PANEL, fg=self._clr(b["sum_R"]),
                         font=mono_s).pack(side="left")
                tk.Label(rowf, text="  ожид. ", bg=BG_PANEL, fg=FG_MUTED, font=mono_s).pack(side="left")
                tk.Label(rowf, text=self._r(b["avg_R"]), bg=BG_PANEL, fg=self._clr(b["avg_R"]),
                         font=mono_s).pack(side="left")
        else:
            tk.Label(risk, text="по риску: —", bg=BG_PANEL, fg="#6f6f7a", font=mono_s).pack(anchor="w")

    def _render_analytics(self):
        for w in self.an_body.winfo_children():
            w.destroy()
        if not self._an:
            tk.Label(self.an_body, text="Нет данных. Нажми «Старт» — после разметки истории "
                                        "здесь появятся метрики.",
                     bg=BG, fg=FG_MUTED, font=FONT).pack(anchor="w", padx=6, pady=10)
            return

        cards = tk.Frame(self.an_body, bg=BG)
        cards.pack(fill="x")
        for name, p in self._an.items():
            self._make_an_card(cards, name, p)

        # строка «Вместе» — агрегат по инструментам
        tot = {"total": 0, "target": 0, "stop": 0, "no_fill": 0, "sum_R": 0.0}
        for p in self._an.values():
            for k in tot:
                tot[k] += p.get(k, 0) or 0
        resolved = tot["target"] + tot["stop"]
        decided = resolved + tot["no_fill"]
        exp_sig = (tot["sum_R"] / decided) if decided else None
        # рубли суммируются между инструментами (₽ есть ₽), None пропускаем
        rubs = [p.get("sum_rub") for p in self._an.values() if p.get("sum_rub") is not None]
        sum_rub = sum(rubs) if rubs else None

        strip = tk.Frame(self.an_body, bg="#15151b")
        strip.pack(fill="x", padx=6, pady=(8, 2))
        row1 = tk.Frame(strip, bg="#15151b")
        row1.pack(anchor="w", padx=14, pady=(10, 0))
        tk.Label(row1, text="Вместе", bg="#15151b", fg=FG, font=FONT_BOLD).pack(side="left", padx=(0, 18))
        tk.Label(row1, text="Σ R ", bg="#15151b", fg=FG_MUTED, font=("Segoe UI", 9)).pack(side="left")
        tk.Label(row1, text=f"{tot['sum_R']:+g}", bg="#15151b", fg=self._clr(tot["sum_R"]),
                 font=FONT_BOLD).pack(side="left", padx=(0, 18))
        tk.Label(row1, text="ожид./сигнал ", bg="#15151b", fg=FG_MUTED, font=("Segoe UI", 9)).pack(side="left")
        tk.Label(row1, text=self._r(exp_sig), bg="#15151b", fg=self._clr(exp_sig),
                 font=FONT_BOLD).pack(side="left", padx=(0, 18))
        if sum_rub is not None:
            tk.Label(row1, text="Σ ₽ ", bg="#15151b", fg=FG_MUTED, font=("Segoe UI", 9)).pack(side="left")
            tk.Label(row1, text=f"{round(sum_rub):+}", bg="#15151b", fg=self._clr(sum_rub),
                     font=FONT_BOLD).pack(side="left", padx=(0, 18))
        tk.Label(row1, text=f"сделок {resolved} · сигналов {decided}", bg="#15151b",
                 fg=FG_MUTED, font=("Segoe UI", 9)).pack(side="left")

        # суммарное ГО на 1 контракт каждого инструмента — минимум, чтобы войти (своя строка)
        gos = [(p.get("go") or 0) * (p.get("volume_lots") or 1)
               for p in self._an.values() if p.get("go") is not None]
        row2 = tk.Frame(strip, bg="#15151b")
        row2.pack(anchor="w", padx=14, pady=(2, 10))
        if gos:
            tk.Label(row2, text="ГО на весь набор (минимум, чтобы войти): ", bg="#15151b",
                     fg=FG_MUTED, font=("Segoe UI", 9)).pack(side="left")
            tk.Label(row2, text=f"{round(sum(gos))} \u20bd", bg="#15151b", fg=FG,
                     font=FONT_BOLD).pack(side="left")
        else:
            tk.Label(row2, text="ГО на набор: н/д (не прочиталось из QUIK)", bg="#15151b",
                     fg=FG_MUTED, font=("Segoe UI", 9)).pack(side="left")

        tk.Label(self.an_body, text="модель оптимистична — без спреда и комиссии; мелкие сигналы "
                                    "просядут сильнее. Рубли по 1 контракту — по текущей цене шага "
                                    "(для RTS плавает). ГО — минимум биржи под позицию; сверху нужен "
                                    "буфер под просадку (наблюдённая мала и занижена — закладывай с запасом).",
                 bg=BG, fg="#6f6f7a", font=("Segoe UI", 8), justify="left",
                 wraplength=800).pack(anchor="w", padx=8, pady=(6, 0))

    # --- вкладка «Счёт» ---------------------------------------------------------
    def _build_account(self, parent):
        tk.Label(parent, text="Средства счёта (FORTS)", bg=BG, fg=FG, font=FONT_BOLD).pack(
            anchor="w", padx=12, pady=(12, 2))
        tk.Label(parent, text="Только чтение · обновляется в ожидании свечи. "
                              "«*» — производное, сверь с «Клиентский портфель» в QUIK.",
                 bg=BG, fg=FG_MUTED, font=("Segoe UI", 9)).pack(anchor="w", padx=12, pady=(0, 8))

        self.acc_card = tk.Frame(parent, bg=BG_PANEL)
        self.acc_card.pack(fill="x", padx=10, pady=6)

        self.acc_head = tk.Label(self.acc_card, text="счёт: ожидание данных…",
                                 bg=BG_PANEL, fg=FG_MUTED, font=("Segoe UI", 9))
        self.acc_head.pack(anchor="w", padx=12, pady=(10, 6))

        big = tk.Frame(self.acc_card, bg=BG_PANEL)
        big.pack(fill="x", padx=12, pady=(0, 8))
        self.acc_big = {}
        for key, label in (("equity", "текущие средства*"), ("free", "свободно*")):
            col = tk.Frame(big, bg=BG_PANEL)
            col.pack(side="left", padx=(0, 22))
            tk.Label(col, text=label, bg=BG_PANEL, fg=FG_MUTED, font=("Segoe UI", 8)).pack(anchor="w")
            val = tk.Label(col, text="—", bg=BG_PANEL, fg=FG, font=("Segoe UI", 18, "bold"))
            val.pack(anchor="w")
            self.acc_big[key] = val

        grid = tk.Frame(self.acc_card, bg=BG_PANEL)
        grid.pack(fill="x", padx=12, pady=(0, 10))
        self.acc_rows = {}
        rows = [("limit", "Лимит (входящие)"), ("used", "Занято (ГО+заявки)"),
                ("varmargin", "Вариац. маржа"), ("commission", "Биржевой сбор"),
                ("go", "ГО"), ("risk", "Коэф. ГО · риск")]
        for i, (key, label) in enumerate(rows):
            r, c = divmod(i, 2)
            cell = tk.Frame(grid, bg=BG_PANEL)
            cell.grid(row=r, column=c, sticky="w", padx=(0, 28), pady=3)
            tk.Label(cell, text=label, bg=BG_PANEL, fg=FG_MUTED, font=("Segoe UI", 9)).pack(anchor="w")
            val = tk.Label(cell, text="—", bg=BG_PANEL, fg=FG, font=("Consolas", 10))
            val.pack(anchor="w")
            self.acc_rows[key] = val

        self.acc_note = tk.Label(parent, text="", bg=BG, fg="#6f6f7a",
                                 font=("Segoe UI", 8), justify="left", wraplength=760)
        self.acc_note.pack(anchor="w", padx=12, pady=(6, 0))

    def _render_account(self, p):
        if not p.get("ok"):
            self.acc_head.config(text="счёт: данные не получены (фьючерсный счёт не найден / лимит пуст)")
            for v in self.acc_big.values():
                v.config(text="н/д", fg=FG_MUTED)
            for v in self.acc_rows.values():
                v.config(text="н/д", fg=FG_MUTED)
            return
        cur = p.get("currency") or "SUR"

        def money(v):
            return f"{v:,.2f} {cur}".replace(",", " ") if v is not None else "н/д"

        self.acc_head.config(
            text=f"счёт {p.get('trdacc', '—')} · фирма {p.get('firm', '—')} · валюта {cur}",
            fg=FG)
        self.acc_big["equity"].config(text=money(p.get("equity")), fg=self._clr(p.get("equity")))
        self.acc_big["free"].config(text=money(p.get("free")), fg=self._clr(p.get("free")))
        self.acc_rows["limit"].config(text=money(p.get("limit")), fg=FG)
        self.acc_rows["used"].config(text=money(p.get("used")), fg=FG)
        self.acc_rows["varmargin"].config(text=money(p.get("varmargin")), fg=self._clr(p.get("varmargin")))
        self.acc_rows["commission"].config(text=money(p.get("commission")), fg=FG)
        self.acc_rows["go"].config(text=money(p.get("go")), fg=FG)
        self.acc_rows["risk"].config(
            text=f"{p.get('kgo', 1):g} · риск {p.get('risk_level', '—')}", fg=FG)
        self.acc_note.config(
            text="Текущие средства* = лимит + вариац. маржа; Свободно* = лимит − занятое. "
                 "Это расчёт (до клиринга вармаржа может быть ещё не влита в лимит) — "
                 "сверь с таблицей «Клиентский портфель», когда на счёте появятся средства.")

    # --- управление потоком движка ----------------------------------------------
    def _start(self):
        if self._worker and self._worker.is_alive():
            return
        self._stop.clear()
        self._set_lamp(YELLOW)
        self.status_lbl.config(text="подключение…")
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()

    def _run(self):
        try:
            P.run_live(self.cfg, stop_event=self._stop,
                       on_event=lambda kind, data: self.q.put((kind, data)))
        except Exception as e:  # noqa: BLE001
            self.q.put(("error", {"text": repr(e)}))

    def _stop_worker(self):
        self._stop.set()
        self.status_lbl.config(text="останавливаюсь…")
        self.stop_btn.config(state="disabled")

    # --- забор событий из очереди (главный поток) -------------------------------
    def _poll(self):
        try:
            while True:
                kind, data = self.q.get_nowait()
                self._handle(kind, data)
        except queue.Empty:
            pass
        self.after(self.POLL_MS, self._poll)

    def _handle(self, kind, data):
        if kind == "log":
            self._append(self.logbox, data)
            return
        if kind == "start":
            self._set_lamp(GREEN)
            self.status_lbl.config(text="работает")
            self.mode_lbl.config(text=f"режим: {data['mode']} · "
                                      + ("боевой" if data["live_trading"] else "симуляция"))
            for instr in data["instruments"]:
                if instr["name"] not in self._cards:
                    self._make_card(instr["name"])
                self._cards[instr["name"]]["sec"].config(
                    text=f"тег {instr['tag']} · тикер {instr['sec'] or 'н/д'}")
                self._tickers[instr["name"]] = instr["sec"] or "н/д"
        elif kind == "history":
            c = self._cards.get(data["instrument"])
            if c:
                wr = f"{data['win_rate']:.0f}%" if data["win_rate"] is not None else "н/д"
                c["wr"].config(text=f"история: сигналов {data['total']} · win rate {wr} · "
                                    f"R {data['sum_R']:+g}")
            self._an[data["instrument"]] = data      # для вкладки «Аналитика»
            self._render_analytics()
        elif kind == "online":
            self.online_lbl.config(text=f"онлайн · проверка каждые {data['tf']} мин (+{data['delay']:.0f} c)")
        elif kind == "waiting":
            self.online_lbl.config(text=f"жду следующую свечу до {data['until']}")
        elif kind == "wake":
            self.online_lbl.config(text=f"проверка в {data['time']}…")
        elif kind == "signal":
            side = "BUY" if data["side"] == "long" else "SELL"
            line = (f"{data['bar']} · {data['instrument']} · {side} · "
                    f"вход {data['entry']:.2f} стоп {data['stop']:.2f} тейк {data['target']:.2f} · "
                    f"{data['oi']} · стрелка {data['drawn']} · {data['source']}")
            self._append(self.feed, line)
            if data.get("oi", "").startswith("ОИ="):
                c = self._cards.get(data["instrument"])
                if c:
                    c["oi"].config(text=data["oi"])
        elif kind == "account":
            self._render_account(data)
        elif kind == "error":
            self._set_lamp(RED)
            self.status_lbl.config(text="ошибка")
            self._append(self.logbox, "ОШИБКА: " + data.get("text", ""))
        elif kind == "stopped":
            self._set_lamp(GREY)
            self.status_lbl.config(text="остановлен")
            self.online_lbl.config(text="")
            self.start_btn.config(state="normal")
            self.stop_btn.config(state="disabled")

    # --- мелочи -----------------------------------------------------------------
    def _set_lamp(self, color):
        self.lamp.itemconfig("dot", fill=color)

    def _append(self, widget: tk.Text, text: str):
        widget.configure(state="normal")
        widget.insert("end", text + "\n")
        widget.see("end")
        widget.configure(state="disabled")

    def _on_close(self):
        self._stop.set()
        self.after(200, self.destroy)


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    cfg = P.load_config()
    P.setup_logging(cfg)
    app = App(cfg)
    app.mainloop()


if __name__ == "__main__":
    main()
