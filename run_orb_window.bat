@echo off
rem ============================================================
rem  Launcher: orb_window.py — окно робота (вкладки Робот/Лог/
rem  Настройки/Бэктест/Аналитика/Счёт). Старт в окне запускает
rem  paper или live в зависимости от выбора на вкладке «Настройки».
rem
rem  Если окно не открылось — смотри logs\orb_window.log и
rem  logs\orb_window_crash.log (создаются рядом с этим файлом).
rem ============================================================
setlocal
cd /d "%~dp0"

rem py-лаунчер ищет РЕАЛЬНО установленные Python (через реестр), а не первый
rem попавшийся python.exe в PATH — им часто оказывается урезанный интерпретатор,
rem встроенный в другую программу (Inkscape/GIMP/Blender и т.п., без pip/tkinter).
set "PY=py"
where py >nul 2>nul || set "PY=python"

%PY% --version >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python не найден. Установи Python с python.org (галка "Add to PATH").
    pause
    exit /b 1
)

echo Используется интерпретатор (если модуль не найден — ставь пакеты именно сюда,
echo например: "путь_ниже" -m pip install -r requirements.txt):
%PY% -c "import sys; print(sys.executable)"
echo.
echo Running: %PY% orb_window.py
echo (лог старта: logs\orb_window.log)
echo.
%PY% orb_window.py
set "RC=%ERRORLEVEL%"

echo.
echo --- Finished (exit code %RC%) ---
pause
endlocal
