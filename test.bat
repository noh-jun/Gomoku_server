@echo off
rem ---------------------------------------------------------------
rem  Gomoku server test runner (Windows / cmd)
rem
rem    test.bat                       whole suite
rem    test.bat -v                    verbose
rem    test.bat tests\test_game.py    one file
rem    test.bat -k "board_size"       one pattern
rem
rem  Creates .venv and installs requirements.txt on first run.
rem ---------------------------------------------------------------
setlocal
cd /d "%~dp0"

set "VENV_PY=%~dp0.venv\Scripts\python.exe"
set "NEEDS_INSTALL="

if not exist "%VENV_PY%" (
    echo [test] Creating virtual environment in .venv ...
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
        echo [test] ERROR: no Python launcher found. Install Python 3.11+ first.
        exit /b 1
    )
    %BASE_PY% -m venv ".venv"
    if not exist "%VENV_PY%" (
        echo [test] ERROR: failed to create .venv
        exit /b 1
    )
    set "NEEDS_INSTALL=1"
)

"%VENV_PY%" -c "import pytest, fastapi, httpx" >nul 2>nul
if errorlevel 1 set "NEEDS_INSTALL=1"

if defined NEEDS_INSTALL (
    echo [test] Installing requirements ...
    "%VENV_PY%" -m pip install --quiet --upgrade pip
    "%VENV_PY%" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [test] ERROR: pip install failed
        exit /b 1
    )
)

if "%~1"=="" (
    echo [test] python -m pytest
    "%VENV_PY%" -m pytest
) else (
    echo [test] python -m pytest %*
    "%VENV_PY%" -m pytest %*
)
exit /b %errorlevel%
