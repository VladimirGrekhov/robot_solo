# robot_solo — торговый робот ORB для Si (MOEX FORTS)

Робот на стратегии ORB (Opening Range Breakout, пробой утреннего диапазона)
на фьючерсе Si, с календарным фильтром (заседания ЦБ, недельная CPI, клиринг,
зона экспирации) и риск-модулем (сайзинг позиции, дневной/недельный лимит,
kill switch). Три режима: `backtest` (история MOEX ISS), `paper` (живые данные
QUIK без реальных заявок) и `live` (реальные заявки через QuikPy).

Подробности — в `ORB_README.md` (схема режимов, чек-листы запуска paper/live,
результаты проверочного бэктеста) и `EVENT_CALENDAR.md` (таблица календарных
окон, формат `cbr_dates.csv`).

## Файлы

```
orb_robot.py         — точка входа: backtest / paper / live
orb_strategy.py       — чистая логика ORB (диапазон/вход/стоп/EOD), без QuikPy/pandas
orb_calendar.py       — блокировки входа + принудительное закрытие + ролловер контракта
orb_risk.py           — размер позиции, дневной/недельный лимит, kill switch
orb_journal.py        — CSV-журнал сделок/пропусков + дневная сводка
orb_data_moex.py      — загрузка M15 (агрегация из 1-мин) с MOEX ISS + parquet-кэш
orb_backtest.py       — движок бэктеста (склейка контрактов, комиссия/slippage)
orb_broker_quik.py    — отправка заявок через QuikPy (проверить перед live!)
config_orb.yaml       — конфиг робота

event_calendar.py     — календарь событий РФ (ЦБ/CPI/клиринг/экспирация), общий модуль
cbr_dates.csv         — даты заседаний ЦБ РФ 2024-2026 (date,note)

run_orb_backtest.bat  — запуск: --mode backtest (история MOEX ISS, QUIK не нужен)
run_orb_paper.bat     — запуск: --mode paper (живые данные QUIK, без реальных заявок)
run_orb_live.bat      — запуск: --mode live (РЕАЛЬНЫЕ заявки — см. чек-лист в ORB_README.md)

tests/                — pytest (event_calendar + все orb_*)
logs/                 — логи и CSV-журналы (в .gitignore)
data_cache/           — parquet-кэш свечей MOEX ISS для бэктеста (в .gitignore)
```

## Быстрый старт

```
pip install pandas pyyaml requests pyarrow pytest
pytest tests/                        # 50 тестов, без сети и без QUIK
python orb_robot.py --mode backtest  # прогон на истории MOEX ISS
```

На Windows — те же три режима через `run_orb_backtest.bat` / `run_orb_paper.bat` /
`run_orb_live.bat` (двойной клик или запуск из проводника).

Для `paper`/`live` нужен запущенный терминал QUIK с QuikPy и открытым M15-графиком
нужного контракта — подробный чек-лист в `ORB_README.md`.
