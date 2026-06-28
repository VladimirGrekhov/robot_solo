@echo off
rem ============================================================
rem  Launcher: pattern_robot — scan mode (3+1 arrows on QUIK chart)
rem  Open the needed timeframe charts in QUIK with tags from config.json.
rem  No orders are sent (execute_signal is in simulation).
rem ============================================================
setlocal
cd /d "%~dp0"

set "PY=python"
where python >nul 2>nul || set "PY=py"

%PY% --version >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python not found in PATH. Install Python or add it to PATH.
    pause
    exit /b 1
)

echo Running: %PY% pattern_robot.py
echo.
%PY% pattern_robot.py
set "RC=%ERRORLEVEL%"

echo.
echo --- Finished (exit code %RC%) ---
pause
endlocal
