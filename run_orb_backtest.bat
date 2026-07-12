@echo off
chcp 65001 >nul
rem (chcp 65001 — консоль в UTF-8, иначе кириллица в этом файле может ломать разбор команд)
rem ============================================================
rem  Launcher: orb_robot.py --mode backtest
rem  Прогон на истории MOEX ISS (см. backtest: в config_orb.yaml).
rem  QUIK для этого режима не требуется.
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

echo Используется интерпретатор (если модуль не найден — ставь пакеты именно сюда):
%PY% -c "import sys; print(sys.executable)"
echo.
echo Running: %PY% orb_robot.py --mode backtest
echo.
%PY% orb_robot.py --mode backtest
set "RC=%ERRORLEVEL%"

echo.
echo --- Finished (exit code %RC%) ---
pause
endlocal
