@echo off
rem ============================================================
rem  Launcher: orb_robot.py --mode paper
rem  Живые бары из QUIK, реальные заявки НЕ выставляются.
rem  Держи открытым M15-график нужного контракта (chart_tag из config_orb.yaml).
rem ============================================================
setlocal
cd /d "%~dp0"

set "PY=python"
where python >nul 2>nul || set "PY=py"

%PY% --version >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python не найден в PATH. Установи Python или добавь его в PATH.
    pause
    exit /b 1
)

echo Running: %PY% orb_robot.py --mode paper
echo.
%PY% orb_robot.py --mode paper
set "RC=%ERRORLEVEL%"

echo.
echo --- Finished (exit code %RC%) ---
pause
endlocal
