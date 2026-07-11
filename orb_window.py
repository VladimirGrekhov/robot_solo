"""
orb_window.py — окно робота ORB (Tkinter, вкладки): Робот / Лог / Настройки / Аналитика / Счёт.

Движок run_paper_or_live крутится в фоновом потоке; события и строки лога
приходят в окно через очередь, GUI забирает их в главном потоке по таймеру
(after) — та же схема, что использовалась в прежнем окне «3+1».

Заявок в paper НЕ шлёт; для live требует live_trading=true в конфиге И
подтверждения в диалоговом окне перед стартом потока.

Запуск:  python orb_window.py
"""

from __future__ import annotations

import logging
import queue
import sys
import threading
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

_CRASH_LOG = HERE / "logs" / "orb_window_crash.log"


def _fatal_startup_error(title: str, detail: str) -> None:
    """Пишет причину падения на старте и в консоль, и в logs/orb_window_crash.log —
    чтобы окно не закрывалось молча без единой видимой строки."""
    _CRASH_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(_CRASH_LOG, "a", encoding="utf-8") as f:
        f.write(f"\n=== {title} ===\n{detail}\n")
    print(f"[ОШИБКА СТАРТА] {title}", file=sys.stderr)
    print(detail, file=sys.stderr)
    print(f"\nПодробности дописаны в {_CRASH_LOG}", file=sys.stderr)


try:
    import tkinter as tk
    from tkinter import messagebox, ttk
except Exception as e:  # noqa: BLE001
    _fatal_startup_error(
        "tkinter недоступен",
        "На Windows-установке python.org tkinter идёт «из коробки» — переустанови "
        "Python с сайта python.org (галка 'tcl/tk and IDLE' должна быть включена).\n"
        f"Исходная ошибка: {e!r}\n{traceback.format_exc()}")
    input("Нажми Enter, чтобы закрыть окно консоли...")
    sys.exit(1)

try:
    import orb_journal
    import orb_risk
    import orb_robot as R
except Exception as e:  # noqa: BLE001
    _fatal_startup_error(
        "не удалось импортировать модули робота",
        "Проверь, что рядом с orb_window.py лежат orb_robot.py/orb_journal.py/orb_risk.py/"
        "config_orb.yaml, и что установлен пакет pyyaml.\n"
        "ВАЖНО: если на компьютере несколько установленных Python, `pip install` мог "
        "поставить пакет НЕ в тот интерпретатор, который сейчас запускает этот скрипт.\n"
        f"Этот скрипт запущен через: {sys.executable}\n"
        f'Поставь пакет именно сюда командой:\n    "{sys.executable}" -m pip install pyyaml pandas requests pyarrow\n\n'
        f"Исходная ошибка: {e!r}\n{traceback.format_exc()}")
    input("Нажми Enter, чтобы закрыть окно консоли...")
    sys.exit(1)

log = logging.getLogger("orb_window")

# --- тёмная палитра (как в прежнем окне «3+1») ----------------------------------
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
    def __init__(self, q: "queue.Queue"):
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
        self._worker: threading.Thread | None = None

        self.title("orb_robot — ORB (Opening Range Breakout)")
        self.configure(bg=BG)
        self.geometry(cfg.get("window_geometry", "820x680"))
        self.minsize(700, 520)

        self._build_header()
        self._build_tabs()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._log_handler = _QueueLogHandler(self.q)
        self._log_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        R.log.addHandler(self._log_handler)
        R.log.setLevel(logging.INFO)

        self.after(self.POLL_MS, self._poll)

    # --- шапка (всегда видна) ----------------------------------------------------
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
        self.live_note = tk.Label(btns, text="", bg=BG_PANEL, fg=FG_MUTED, font=("Segoe UI", 9))
        self.live_note.pack(side="right")
        self._update_live_note()

    def _update_live_note(self):
        live = bool(self.cfg.get("live_trading", False))
        if live:
            self.live_note.config(text="⚠ live_trading: true — реальные заявки", fg=YELLOW)
        else:
            self.live_note.config(text="paper — заявки не шлются", fg=FG_MUTED)

    # --- вкладки ------------------------------------------------------------------
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

        self.tab_robot = tk.Frame(nb, bg=BG)
        nb.add(self.tab_robot, text="Робот")
        self._build_robot_tab(self.tab_robot)

        self.tab_log = tk.Frame(nb, bg=BG)
        nb.add(self.tab_log, text="Лог")
        self.logbox = tk.Text(self.tab_log, bg=BG_PANEL, fg=FG_MUTED, font=FONT_MONO, relief="flat",
                              wrap="word", padx=10, pady=8)
        self.logbox.pack(fill="both", expand=True, padx=8, pady=8)
        self.logbox.configure(state="disabled")

        self.tab_settings = tk.Frame(nb, bg=BG)
        nb.add(self.tab_settings, text="Настройки")
        self._build_settings(self.tab_settings)

        self.tab_backtest = tk.Frame(nb, bg=BG)
        nb.add(self.tab_backtest, text="Бэктест")
        self._build_backtest(self.tab_backtest)

        self.tab_analytics = tk.Frame(nb, bg=BG)
        nb.add(self.tab_analytics, text="Аналитика")
        self._build_analytics(self.tab_analytics)

        self.tab_account = tk.Frame(nb, bg=BG)
        nb.add(self.tab_account, text="Счёт")
        self._build_account(self.tab_account)

    # --- вкладка «Робот» — лента сигналов/сделок ---------------------------------
    def _build_robot_tab(self, parent):
        card = tk.Frame(parent, bg=BG_PANEL)
        card.pack(fill="x", padx=8, pady=8)
        self.robot_head = tk.Label(card, text="контракт: …", bg=BG_PANEL, fg=FG_MUTED, font=FONT)
        self.robot_head.pack(anchor="w", padx=10, pady=8)

        tk.Label(parent, text="Лента сигналов/сделок", bg=BG, fg=FG, font=FONT_BOLD).pack(
            anchor="w", padx=10, pady=(6, 2))
        self.feed = tk.Text(parent, bg=BG_PANEL, fg=FG, font=FONT_MONO, relief="flat",
                            wrap="word", padx=10, pady=8, height=16)
        self.feed.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.feed.configure(state="disabled")

    # --- вкладка «Настройки» ------------------------------------------------------
    RISK_FIELDS = [
        ("risk_per_trade", "Риск на сделку (доля депозита)", float),
        ("go_fraction", "Доля депозита на ГО контракта", float),
        ("max_stop_pt", "Макс. ширина диапазона, пунктов", float),
        ("daily_loss_limit", "Дневной лимит убытка (доля депозита)", float),
        ("weekly_halt_limit", "Недельный аварийный лимит (доля депозита)", float),
    ]
    STRATEGY_FIELDS = [
        ("allow_position_flip", "Разрешить разворот позиции (как в эталонном Pine)", bool),
        ("expiration_zone_mode", "Зона экспирации", ["trading_days", "calendar_days"]),
    ]
    TOP_FIELDS = [
        ("deposit_rub", "Депозит, ₽ (для сайзинга позиции)", float),
        ("chart_tag", "Тег M15-графика в QUIK", str),
        ("account", "ACCOUNT (для реальных заявок)", str),
        ("client_code", "CLIENT_CODE (если нужен брокеру)", str),
    ]

    def _build_settings(self, parent):
        canvas = tk.Canvas(parent, bg=BG, highlightthickness=0)
        vsb = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        form_host = tk.Frame(canvas, bg=BG)
        canvas.create_window((0, 0), window=form_host, anchor="nw")
        form_host.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))

        tk.Label(form_host, text="Режим и подключение", bg=BG, fg=FG, font=FONT_BOLD).pack(
            anchor="w", padx=12, pady=(12, 4))

        mode_row = tk.Frame(form_host, bg=BG)
        mode_row.pack(anchor="w", padx=12, pady=(0, 4))
        self._mode_var = tk.StringVar(value="live" if self.cfg.get("live_trading") else "paper")
        for val, label in (("paper", "paper (без реальных заявок)"), ("live", "live (реальные заявки)")):
            tk.Radiobutton(mode_row, text=label, variable=self._mode_var, value=val, bg=BG, fg=FG,
                          selectcolor=BG_PANEL, activebackground=BG, font=FONT).pack(side="left", padx=(0, 16))

        self._setting_vars: dict[str, tuple] = {}
        self._build_fields(form_host, self.TOP_FIELDS, self.cfg)

        tk.Label(form_host, text="Риск-модуль (risk:)", bg=BG, fg=FG, font=FONT_BOLD).pack(
            anchor="w", padx=12, pady=(14, 4))
        self._build_fields(form_host, self.RISK_FIELDS, self.cfg.get("risk", {}), prefix="risk.")

        tk.Label(form_host, text="Поведение стратегии (strategy:) — см. ORB_README.md, "
                                 "раздел «Сверка с эталонным Pine-скриптом»",
                 bg=BG, fg=FG, font=FONT_BOLD).pack(anchor="w", padx=12, pady=(14, 4))
        self._build_fields(form_host, self.STRATEGY_FIELDS, self.cfg.get("strategy", {}), prefix="strategy.")

        bar = tk.Frame(form_host, bg=BG)
        bar.pack(fill="x", padx=12, pady=16)
        tk.Button(bar, text="Сохранить", command=self._save_settings, bg=ACCENT, fg=FG,
                  activebackground="#4a4a5a", activeforeground=FG, relief="flat",
                  font=FONT, padx=14, pady=4, cursor="hand2").pack(side="left")
        self.settings_msg = tk.Label(bar, text="", bg=BG, fg=FG_MUTED, font=FONT)
        self.settings_msg.pack(side="left", padx=12)
        tk.Label(form_host, text="Изменения применятся после Стоп → Старт. Kill switch: создай "
                                 "пустой файл STOP в рабочей папке — немедленная остановка и закрытие позиции.",
                 bg=BG, fg=FG_MUTED, font=("Segoe UI", 9), wraplength=680, justify="left").pack(
            anchor="w", padx=12, pady=(0, 12))

    def _build_fields(self, parent, fields, source: dict, prefix: str = ""):
        form = tk.Frame(parent, bg=BG)
        form.pack(fill="x", padx=12)
        for i, (key, label, typ) in enumerate(fields):
            tk.Label(form, text=label, bg=BG, fg=FG, font=FONT).grid(row=i, column=0, sticky="w", pady=4)
            cur = source.get(key, "")
            full_key = prefix + key
            if typ is bool:
                var = tk.BooleanVar(value=bool(cur))
                tk.Checkbutton(form, variable=var, bg=BG, fg=FG, selectcolor=BG_PANEL,
                               activebackground=BG, highlightthickness=0).grid(row=i, column=1, sticky="w", padx=10)
            elif isinstance(typ, list):
                var = tk.StringVar(value=str(cur if cur else typ[0]))
                om = tk.OptionMenu(form, var, *typ)
                om.configure(bg=BG_PANEL, fg=FG, activebackground=ACCENT, activeforeground=FG,
                             relief="flat", highlightthickness=0, font=FONT, width=14, cursor="hand2")
                om["menu"].configure(bg=BG_PANEL, fg=FG, activebackground=ACCENT, activeforeground=FG)
                om.grid(row=i, column=1, sticky="w", padx=10)
            else:
                var = tk.StringVar(value=str(cur))
                tk.Entry(form, textvariable=var, width=22, bg=BG_PANEL, fg=FG, insertbackground=FG,
                         relief="flat", font=FONT).grid(row=i, column=1, sticky="w", padx=10)
            self._setting_vars[full_key] = (var, typ)
        form.columnconfigure(0, weight=1)

    def _save_settings(self):
        new_top, new_risk, new_strategy = {}, {}, {}
        for full_key, (var, typ) in self._setting_vars.items():
            if isinstance(typ, list):
                val = var.get()
            elif typ is bool:
                val = bool(var.get())
            else:
                raw = var.get().strip()
                if typ is str:
                    val = raw
                else:
                    try:
                        val = typ(raw.replace(",", "."))
                    except (ValueError, TypeError):
                        self.settings_msg.config(text=f"⚠ «{full_key}»: некорректное значение", fg=RED)
                        return
                    if val < 0:
                        self.settings_msg.config(text=f"⚠ «{full_key}»: не может быть отрицательным", fg=RED)
                        return
            if full_key.startswith("risk."):
                new_risk[full_key[len("risk."):]] = val
            elif full_key.startswith("strategy."):
                new_strategy[full_key[len("strategy."):]] = val
            else:
                new_top[full_key] = val

        live = self._mode_var.get() == "live"
        if live and not messagebox.askyesno(
                "Подтверждение live",
                "Включить live_trading: true?\nРобот будет отправлять РЕАЛЬНЫЕ заявки."):
            self.settings_msg.config(text="live не подтверждён — оставлен paper", fg=YELLOW)
            live = False
            self._mode_var.set("paper")

        self.cfg.update(new_top)
        self.cfg.setdefault("risk", {}).update(new_risk)
        self.cfg.setdefault("strategy", {}).update(new_strategy)
        self.cfg["live_trading"] = live
        self.cfg["mode"] = "live" if live else "paper"
        self.cfg["window_geometry"] = self.geometry()
        try:
            R.save_config(self.cfg)
        except Exception as e:  # noqa: BLE001
            self.settings_msg.config(text=f"⚠ не сохранилось: {e!r}", fg=RED)
            return
        self._update_live_note()
        running = self._worker is not None and self._worker.is_alive()
        note = " · применится после Стоп → Старт" if running else " · применено"
        self.settings_msg.config(text="✓ сохранено" + note, fg=GREEN)

    # --- вкладка «Бэктест» ---------------------------------------------------------
    def _build_backtest(self, parent):
        bt_cfg = self.cfg.get("backtest", {})
        tk.Label(parent, text="Бэктест на истории MOEX ISS", bg=BG, fg=FG, font=FONT_BOLD).pack(
            anchor="w", padx=12, pady=(12, 2))
        tk.Label(parent, text=f"период {bt_cfg.get('date_from', '?')} → {bt_cfg.get('date_till', '?')} "
                              f"(см. config_orb.yaml: backtest). QUIK не нужен.",
                 bg=BG, fg=FG_MUTED, font=("Segoe UI", 9)).pack(anchor="w", padx=12, pady=(0, 8))

        bar = tk.Frame(parent, bg=BG)
        bar.pack(fill="x", padx=12)
        self.bt_btn = tk.Button(bar, text="Запустить бэктест", command=self._run_backtest, bg=ACCENT, fg=FG,
                                activebackground="#4a4a5a", activeforeground=FG, relief="flat",
                                font=FONT, padx=14, pady=4, cursor="hand2")
        self.bt_btn.pack(side="left")
        self.bt_status = tk.Label(bar, text="", bg=BG, fg=FG_MUTED, font=FONT)
        self.bt_status.pack(side="left", padx=12)

        self.bt_text = tk.Text(parent, bg=BG_PANEL, fg=FG, font=FONT_MONO, relief="flat",
                               wrap="word", padx=10, pady=8, height=16)
        self.bt_text.pack(fill="both", expand=True, padx=8, pady=8)
        self.bt_text.configure(state="disabled")

    def _run_backtest(self):
        self.bt_btn.config(state="disabled")
        self.bt_status.config(text="считаю…", fg=YELLOW)
        threading.Thread(target=self._run_backtest_worker, daemon=True).start()

    def _run_backtest_worker(self):
        try:
            import orb_backtest  # ленивый импорт: тянет pandas/requests, нужен только тут
        except Exception as e:  # noqa: BLE001
            log.error("import orb_backtest не удался: %r", e, exc_info=True)
            self.q.put(("backtest_done", {"ok": False, "lines": [
                "Не установлены зависимости бэктеста.",
                "Выполни: pip install pandas requests pyarrow",
                f"Исходная ошибка: {e!r}"]}))
            return
        try:
            bt_cfg = self.cfg.get("backtest", {})
            strat_cfg = self.cfg.get("strategy", {})
            risk_cfg = orb_risk.RiskConfig(**self.cfg.get("risk", {})) if self.cfg.get("risk") else orb_risk.RiskConfig()
            from datetime import date as _date
            b = orb_backtest.BacktestConfig(
                date_from=_date.fromisoformat(bt_cfg["date_from"]),
                date_till=_date.fromisoformat(bt_cfg["date_till"]),
                cache_dir=Path(bt_cfg.get("cache_dir", "data_cache/moex_m15")),
                deposit_rub=float(bt_cfg.get("deposit_rub", self.cfg.get("deposit_rub", 300000.0))),
                rub_per_point=float(bt_cfg.get("rub_per_point", 1.0)),
                go_per_contract_assumed=float(bt_cfg.get("go_per_contract_assumed", 12000.0)),
                commission_per_side_rub=float(bt_cfg.get("commission_per_side_rub", 5.0)),
                slippage_ticks=int(bt_cfg.get("slippage_ticks", 2)),
                tick_size=float(self.cfg.get("tick_size", 1.0)),
                risk=risk_cfg,
                allow_position_flip=bool(strat_cfg.get("allow_position_flip", False)),
                expiration_zone_mode=strat_cfg.get("expiration_zone_mode", "trading_days"),
            )
            res = orb_backtest.run(b)
            lines = [f"Сделок: {res.summary['trades']}"] + orb_journal.summary_lines(res.summary)
            self.q.put(("backtest_done", {"ok": True, "lines": lines}))
        except Exception as e:  # noqa: BLE001
            self.q.put(("backtest_done", {"ok": False, "lines": [f"Ошибка: {e!r}"]}))

    # --- вкладка «Аналитика» — сводка по logs/orb_trades.csv/orb_skips.csv --------
    def _build_analytics(self, parent):
        tk.Label(parent, text="Сводка по журналу сделок", bg=BG, fg=FG, font=FONT_BOLD).pack(
            anchor="w", padx=12, pady=(12, 2))
        tk.Label(parent, text="Читается из logs/orb_trades.csv и logs/orb_skips.csv (paper/live).",
                 bg=BG, fg=FG_MUTED, font=("Segoe UI", 9)).pack(anchor="w", padx=12, pady=(0, 8))

        bar = tk.Frame(parent, bg=BG)
        bar.pack(fill="x", padx=12)
        tk.Button(bar, text="Обновить", command=self._render_analytics, bg=ACCENT, fg=FG,
                  activebackground="#4a4a5a", activeforeground=FG, relief="flat",
                  font=FONT, padx=14, pady=4, cursor="hand2").pack(side="left")

        self.an_body = tk.Frame(parent, bg=BG)
        self.an_body.pack(fill="both", expand=True, padx=8, pady=8)
        self._render_analytics()

    @staticmethod
    def _clr(v):
        if v is None:
            return FG_MUTED
        return GREEN if v >= 0 else RED

    def _read_journal(self):
        import csv as _csv
        from datetime import datetime as _dt
        trades, skip_reasons = [], []
        trades_path = Path(self.cfg["paths"]["trades_csv"])
        skips_path = Path(self.cfg["paths"]["skips_csv"])
        if trades_path.is_file():
            with open(trades_path, encoding="utf-8") as f:
                for row in _csv.DictReader(f):
                    try:
                        trades.append(orb_journal.TradeRecord(
                            datetime_in=_dt.fromisoformat(row["datetime_in"]),
                            datetime_out=_dt.fromisoformat(row["datetime_out"]),
                            dir=row["dir"], qty=int(row["qty"]), entry=float(row["entry"]),
                            exit=float(row["exit"]), stop=float(row["stop"]),
                            pnl_pt=float(row["pnl_pt"]), pnl_rub=float(row["pnl_rub"]),
                            exit_reason=row["exit_reason"], range_width_pt=float(row["range_width_pt"]),
                            event_flags=row.get("event_flags", "")))
                    except (KeyError, ValueError):
                        continue
        if skips_path.is_file():
            with open(skips_path, encoding="utf-8") as f:
                for row in _csv.DictReader(f):
                    if row.get("reason"):
                        skip_reasons.append(row["reason"])
        return trades, skip_reasons

    def _render_analytics(self):
        for w in self.an_body.winfo_children():
            w.destroy()
        trades, skip_reasons = self._read_journal()
        if not trades and not skip_reasons:
            tk.Label(self.an_body, text="Журнал пуст. Появится после первых сделок в paper/live.",
                     bg=BG, fg=FG_MUTED, font=FONT).pack(anchor="w", padx=6, pady=10)
            return
        s = orb_journal.daily_summary(trades, skip_reasons)
        card = tk.Frame(self.an_body, bg=BG_PANEL)
        card.pack(fill="x", padx=6, pady=4)
        big = tk.Frame(card, bg=BG_PANEL)
        big.pack(fill="x", padx=12, pady=(10, 8))
        for label, val in (("сделок", s["trades"]), ("winrate", f"{s['winrate']:.0f}%"),
                           ("PnL, ₽", f"{s['sum_pnl_rub']:+.0f}"),
                           ("PF", f"{s['profit_factor']:.2f}" if s["profit_factor"] not in (float("inf"),) else "∞")):
            col = tk.Frame(big, bg=BG_PANEL)
            col.pack(side="left", padx=(0, 22))
            tk.Label(col, text=label, bg=BG_PANEL, fg=FG_MUTED, font=("Segoe UI", 8)).pack(anchor="w")
            tk.Label(col, text=str(val), bg=BG_PANEL, fg=FG, font=("Segoe UI", 16, "bold")).pack(anchor="w")
        if s["skip_counts"]:
            parts = ", ".join(f"{r}={c}" for r, c in sorted(s["skip_counts"].items()))
            tk.Label(card, text=f"пропуски: {parts}", bg=BG_PANEL, fg=FG_MUTED,
                     font=("Segoe UI", 9)).pack(anchor="w", padx=12, pady=(0, 10))

    # --- вкладка «Счёт» -------------------------------------------------------------
    def _build_account(self, parent):
        tk.Label(parent, text="Средства счёта (FORTS)", bg=BG, fg=FG, font=FONT_BOLD).pack(
            anchor="w", padx=12, pady=(12, 2))
        tk.Label(parent, text="Только чтение · обновляется во время работы робота. "
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

        self.acc_head.config(text=f"счёт {p.get('trdacc', '—')} · фирма {p.get('firm', '—')} · валюта {cur}", fg=FG)
        self.acc_big["equity"].config(text=money(p.get("equity")), fg=self._clr(p.get("equity")))
        self.acc_big["free"].config(text=money(p.get("free")), fg=self._clr(p.get("free")))
        self.acc_rows["limit"].config(text=money(p.get("limit")), fg=FG)
        self.acc_rows["used"].config(text=money(p.get("used")), fg=FG)
        self.acc_rows["varmargin"].config(text=money(p.get("varmargin")), fg=self._clr(p.get("varmargin")))
        self.acc_rows["commission"].config(text=money(p.get("commission")), fg=FG)
        self.acc_rows["go"].config(text=money(p.get("go")), fg=FG)
        self.acc_rows["risk"].config(text=f"{p.get('kgo', 1):g} · риск {p.get('risk_level', '—')}", fg=FG)
        self.acc_note.config(
            text="Текущие средства* = лимит + вариац. маржа; Свободно* = лимит − занятое. "
                 "Сверь с таблицей «Клиентский портфель», когда на счёте появятся средства.")

    # --- управление потоком движка -------------------------------------------------
    def _start(self):
        if self._worker and self._worker.is_alive():
            return
        live = self._mode_var.get() == "live"
        confirmed = False
        if live:
            if not self.cfg.get("live_trading", False):
                messagebox.showerror("live_trading выключен",
                                      "Сначала сохрани настройки с включённым live (см. вкладку «Настройки»).")
                return
            confirmed = messagebox.askyesno(
                "Подтверждение LIVE",
                "Запустить робота в режиме LIVE?\nБудут отправляться РЕАЛЬНЫЕ заявки на бирже.")
            if not confirmed:
                return
        self._stop.clear()
        self._set_lamp(YELLOW)
        self.status_lbl.config(text="подключение…")
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self._worker = threading.Thread(target=self._run, args=(live, confirmed), daemon=True)
        self._worker.start()

    def _run(self, live: bool, confirmed: bool):
        try:
            R.run_paper_or_live(self.cfg, live=live, stop_event=self._stop,
                                on_event=lambda kind, data: self.q.put((kind, data)), confirmed=confirmed)
        except Exception as e:  # noqa: BLE001
            self.q.put(("error", {"text": repr(e)}))

    def _stop_worker(self):
        self._stop.set()
        self.status_lbl.config(text="останавливаюсь…")
        self.stop_btn.config(state="disabled")

    # --- забор событий из очереди (главный поток) ----------------------------------
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
            self.robot_head.config(text=f"контракт: {data['contract']} · график '{data['tag']}' · "
                                       f"ТФ {data['tf']} мин")
        elif kind == "waiting":
            self.online_lbl.config(text=f"жду следующую свечу до {data['until']}")
        elif kind == "wake":
            self.online_lbl.config(text=f"проверка в {data['time']}…")
        elif kind == "entry":
            side = "BUY" if data["side"] == "long" else "SELL"
            self._append(self.feed, f"{data['bar']} · ВХОД {side} · цена~{data['price']:.2f} "
                                    f"стоп {data['stop']:.2f} qty {data['qty']}")
        elif kind == "trade":
            self._append(self.feed, f"{data['datetime_out']} · ВЫХОД {data['dir'].upper()} · "
                                    f"причина {data['exit_reason']} · pnl {data['pnl_pt']:+.1f} пт / "
                                    f"{data['pnl_rub']:+.0f} ₽")
            self._render_analytics()
        elif kind == "skip":
            self._append(self.feed, f"{data['bar']} · пропуск {data['side']}: {data['reason']}")
        elif kind == "account":
            self._render_account(data)
        elif kind == "backtest_done":
            self.bt_btn.config(state="normal")
            self.bt_status.config(text="готово" if data["ok"] else "ошибка",
                                  fg=GREEN if data["ok"] else RED)
            self.bt_text.configure(state="normal")
            self.bt_text.delete("1.0", "end")
            self.bt_text.insert("end", "\n".join(data["lines"]))
            self.bt_text.configure(state="disabled")
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

    # --- мелочи ---------------------------------------------------------------------
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


def _setup_bootstrap_logging() -> None:
    """Лог ДО чтения конфига (setup_logging(cfg) читает cfg['paths']['log_dir'],
    а на этом этапе конфиг мог ещё не загрузиться) — пишем в logs/orb_window.log
    рядом со скриптом плюс дублируем в консоль, чтобы было видно, где застряло."""
    log_dir = HERE / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    log.setLevel(logging.DEBUG)
    log.handlers.clear()
    for h in (logging.StreamHandler(),
              logging.FileHandler(log_dir / "orb_window.log", encoding="utf-8")):
        h.setFormatter(fmt)
        log.addHandler(h)
    log.info("orb_window: старт. Python %s, файл %s, рабочая папка %s",
             sys.version.split()[0], __file__, HERE)


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    _setup_bootstrap_logging()
    try:
        log.info("Читаю config_orb.yaml…")
        cfg = R.load_config()
        log.info("Конфиг прочитан: mode=%s live_trading=%s chart_tag=%s",
                 cfg.get("mode"), cfg.get("live_trading"), cfg.get("chart_tag"))

        log.info("Настраиваю логирование движка (logs/orb_robot.log)…")
        R.setup_logging(cfg)

        log.info("Строю окно…")
        app = App(cfg)
        log.info("Окно построено, запускаю mainloop.")
        app.mainloop()
        log.info("Окно закрыто штатно.")
    except Exception as e:  # noqa: BLE001
        detail = traceback.format_exc()
        log.error("НЕОБРАБОТАННАЯ ОШИБКА: %r\n%s", e, detail)
        _fatal_startup_error("необработанная ошибка при запуске окна", detail)
        try:
            messagebox.showerror("orb_window — ошибка запуска",
                                  f"{e!r}\n\nПодробности: logs/orb_window.log и\n{_CRASH_LOG}")
        except Exception:
            pass
        input("Нажми Enter, чтобы закрыть окно консоли...")
        sys.exit(1)


if __name__ == "__main__":
    main()
