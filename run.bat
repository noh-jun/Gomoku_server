@echo off
rem ---------------------------------------------------------------
rem  Gomoku server launcher (Windows / cmd)
rem
rem    run.bat                          GUI mode
rem    run.bat --no-gui                 headless, 15 x 15
rem    run.bat --no-gui --board-size 19 headless, 19 x 19
rem    run.bat --help                   all options
rem
rem  Creates .venv and installs requirements.txt on first run.
rem ---------------------------------------------------------------
setlocal
cd /d "%~dp0"

set "VENV_PY=%~dp0.venv\Scripts\python.exe"
set "NEEDS_INSTALL="

if not exist "%VENV_PY%" (
    echo [run] Creating virtual environment in .venv ...
    call :create_venv
    if errorlevel 1 exit /b 1
    set "NEEDS_INSTALL=1"
)

"%VENV_PY%" -c "import fastapi, uvicorn" >nul 2>nul
if errorlevel 1 set "NEEDS_INSTALL=1"

if defined NEEDS_INSTALL (
    echo [run] Installing requirements ...
    "%VENV_PY%" -m pip install --quiet --upgrade pip
    "%VENV_PY%" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [run] ERROR: pip install failed
        exit /b 1
    )
)

echo [run] python server.py %*
"%VENV_PY%" server.py %*
exit /b %errorlevel%

:create_venv
set "BASE_PY="

where py >nul 2>nul
if not errorlevel 1 set "BASE_PY=py -3"

if not defined BASE_PY (
    where python3 >nul 2>nul
    if not errorlevel 1 set "BASE_PY=python3"
)

if not defined BASE_PY (
    where python >nul 2>nul
    if not errorlevel 1 set "BASE_PY=python"
)

if not defined BASE_PY (
    echo [run] ERROR: no Python launcher found. Install Python 3.11+ first.
    exit /b 1
)

%BASE_PY% -m venv ".venv"
if not exist "%VENV_PY%" (
    echo [run] ERROR: failed to create .venv
    exit /b 1
)

exit /b 0
