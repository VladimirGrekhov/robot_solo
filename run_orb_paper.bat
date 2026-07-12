@echo off
rem ============================================================
rem  Launcher: orb_robot.py --mode paper
rem  Живые бары из QUIK, реальные заявки НЕ выставляются.
rem  Держи открытым M15-график нужного контракта (chart_tag из config_orb.yaml).
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
echo Running: %PY% orb_robot.py --mode paper
echo.
%PY% orb_robot.py --mode paper
set "RC=%ERRORLEVEL%"

echo.
echo --- Finished (exit code %RC%) ---
pause
endlocal
