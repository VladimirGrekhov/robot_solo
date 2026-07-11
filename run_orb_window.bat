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

set "PY=python"
where python >nul 2>nul || set "PY=py"

%PY% --version >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python не найден в PATH. Установи Python или добавь его в PATH.
    pause
    exit /b 1
)

echo Running: %PY% orb_window.py
echo (лог старта: logs\orb_window.log)
echo.
%PY% orb_window.py
set "RC=%ERRORLEVEL%"

echo.
echo --- Finished (exit code %RC%) ---
pause
endlocal
