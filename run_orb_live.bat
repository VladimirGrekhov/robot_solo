@echo off
rem ============================================================
rem  Launcher: orb_robot.py --mode live
rem  РЕАЛЬНЫЕ ЗАЯВКИ. Требует live_trading: true в config_orb.yaml
rem  И ручного подтверждения "yes" при старте (спросит сам робот).
rem  Перед первым запуском live — пройди чек-лист в ORB_README.md.
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

echo ВНИМАНИЕ: режим live — реальные заявки на бирже.
echo Running: %PY% orb_robot.py --mode live
echo.
%PY% orb_robot.py --mode live
set "RC=%ERRORLEVEL%"

echo.
echo --- Finished (exit code %RC%) ---
pause
endlocal
