@echo off
cd /d "%~dp0"
set "PY_EXPLICIT=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if exist "%PY_EXPLICIT%" (
    "%PY_EXPLICIT%" pattern_window.py
) else (
    python pattern_window.py
)
pause
