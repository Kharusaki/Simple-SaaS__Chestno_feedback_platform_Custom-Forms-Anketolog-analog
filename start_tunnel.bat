@echo off
rem One-click launcher with a Cloudflare tunnel. ASCII only on purpose:
rem cmd.exe reads .bat files in the OEM codepage, so Russian text here would
rem be parsed as commands, and the Cyrillic/Emoji project path would break.
rem All human-readable messages are printed by scripts\tunnel.py instead.
rem
rem For a normal start without a tunnel use start.bat.

setlocal
chcp 65001 >nul
cd /d "%~dp0"

set "PY=%~dp0.venv\Scripts\python.exe"

if exist "%PY%" goto :have_venv

rem No virtual environment yet, so we need a system Python to build it.
call :find_python
if not defined BOOTPY (
    echo.
    echo [1/3] Python 3.12 is not installed. Installing it now,
    echo       about 2 minutes, no administrator rights needed.
    echo.
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install_python.ps1"
    if errorlevel 1 goto :no_python
    call :find_python
)
if not defined BOOTPY goto :no_python

echo.
echo [2/3] Creating virtual environment, about a minute...
%BOOTPY% -m venv .venv
if errorlevel 1 goto :no_python
"%PY%" -m pip install --quiet --upgrade pip
"%PY%" -m pip install --quiet -r requirements.txt
if errorlevel 1 goto :failed
goto :run

:have_venv
"%PY%" -c "import fastapi, uvicorn" >nul 2>&1
if not errorlevel 1 goto :run
echo.
echo [1/3] Installing dependencies, about a minute...
"%PY%" -m pip install --quiet -r requirements.txt
if errorlevel 1 goto :failed

:run
echo [3/3] Starting.
echo.
"%PY%" -m scripts.tunnel
if errorlevel 1 goto :failed
exit /b 0

rem Finds a usable Python 3.12 or newer and puts the command in BOOTPY.
rem The value can contain arguments ("py -3"), so it is never quoted as a
rem single path: callers use it as "%BOOTPY% -m venv" instead.
:find_python
set "BOOTPY="
call :try_python py -3.12
if defined BOOTPY goto :eof
call :try_python py -3
if defined BOOTPY goto :eof
call :try_python python
goto :eof

:try_python
%1 -c "import sys" >nul 2>&1
if errorlevel 1 goto :eof
set "VER="
for /f "usebackq delims=" %%v in (`%1 -c "import sys; print(sys.version_info[0]*100+sys.version_info[1])" 2^>nul`) do set "VER=%%v"
if not defined VER goto :eof
if %VER% LSS 312 goto :eof
set "BOOTPY=%1"
goto :eof

:no_python
echo.
echo Python could not be installed. Check the internet connection and try
echo again, or install Python 3.12 from https://www.python.org/downloads/
echo and tick "Add Python to PATH" during setup.
pause
exit /b 1

:failed
echo.
echo Startup failed. See the messages above.
pause
exit /b 1