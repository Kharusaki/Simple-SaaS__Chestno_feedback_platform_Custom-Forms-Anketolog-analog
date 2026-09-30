@echo off
rem One-click launcher. ASCII only on purpose:
rem cmd.exe reads .bat files in the OEM codepage, so Russian text here would
rem be parsed as commands, and the Cyrillic/Emoji project path would break.
rem All human-readable messages are printed by scripts\launcher.py instead.
rem
rem Russian version of this note lives in README.md, see the quick start part.

setlocal
chcp 65001 >nul
cd /d "%~dp0"

set "PY=%~dp0.venv\Scripts\python.exe"

if not exist "%PY%" (
    echo.
    echo [1/2] Creating virtual environment, this takes about a minute...
    python -m venv .venv
    if errorlevel 1 goto :no_python
    "%PY%" -m pip install --quiet --upgrade pip
    "%PY%" -m pip install --quiet -r requirements.txt
    if errorlevel 1 goto :failed
) else (
    "%PY%" -c "import fastapi, uvicorn" >nul 2>&1
    if errorlevel 1 (
        echo [1/2] Installing dependencies, this takes about a minute...
        "%PY%" -m pip install --quiet -r requirements.txt
        if errorlevel 1 goto :failed
    )
)

"%PY%" -m scripts.launcher
if errorlevel 1 goto :failed
exit /b 0

:no_python
echo.
echo Python 3.12 not found. Install it with "Add Python to PATH" checked,
echo then run this file again.
pause
exit /b 1

:failed
echo.
echo Startup failed. See the messages above.
pause
exit /b 1
