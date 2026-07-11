@echo off
rem ============================================================
rem  Launcher: orb_robot.py --mode backtest
rem  Прогон на истории MOEX ISS (см. backtest: в config_orb.yaml).
rem  QUIK для этого режима не требуется.
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

echo Running: %PY% orb_robot.py --mode backtest
echo.
%PY% orb_robot.py --mode backtest
set "RC=%ERRORLEVEL%"

echo.
echo --- Finished (exit code %RC%) ---
pause
endlocal
