@echo off
chcp 65001 >nul
rem Launcher for orb_robot.py --mode backtest (MOEX ISS history, no QUIK needed).
rem See the "backtest:" section in config_orb.yaml for the date range/params.
rem
rem NOTE: this file is kept ASCII-only on purpose - Cyrillic text inside a .bat
rem can get mis-parsed by cmd.exe depending on the active code page. All
rem Russian-language messages come from the Python scripts themselves, which
rem already handle UTF-8 output correctly.
setlocal
cd /d "%~dp0"

rem Prefer the py launcher: it resolves a REAL registered Python via the
rem Windows registry, instead of whatever "python.exe" happens to be first on
rem PATH (which is sometimes a stripped-down interpreter bundled with another
rem application - e.g. Inkscape/GIMP/Blender - with no pip and no tkinter).
set "PY=py"
where py >nul 2>nul || set "PY=python"

%PY% --version >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python not found. Install it from python.org and check "Add to PATH".
    pause
    exit /b 1
)

echo Using interpreter (if a module is missing, install packages into this one):
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
