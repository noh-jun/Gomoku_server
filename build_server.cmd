@echo off
setlocal

cd /d "%~dp0"

set "SERVER_DIR=%~dp0"
set "BUILD_ROOT=%SERVER_DIR%build\windows"
set "BUILD_VENV=%BUILD_ROOT%\.venv"
set "BUILD_PYTHON=%BUILD_VENV%\Scripts\python.exe"
set "DIST_DIR=%SERVER_DIR%dist\windows"

if not exist "%SERVER_DIR%server.py" (
    echo [ERROR] Server entry point was not found: "%SERVER_DIR%server.py"
    exit /b 1
)

if not exist "%BUILD_PYTHON%" (
    echo [INFO] Creating the server build environment...
    py -3.13 -m venv "%BUILD_VENV%" 2>nul
    if errorlevel 1 py -3.11 -m venv "%BUILD_VENV%" 2>nul
    if errorlevel 1 python -m venv "%BUILD_VENV%"
    if errorlevel 1 (
        echo [ERROR] Python 3.11 or newer is required.
        exit /b 1
    )
)

echo [INFO] Installing server and build dependencies...
"%BUILD_PYTHON%" -m pip install -r "%SERVER_DIR%requirements.txt" pyinstaller
if errorlevel 1 (
    echo [ERROR] Failed to install build dependencies.
    exit /b 1
)

echo [INFO] Building GomokuServer.exe...
"%BUILD_PYTHON%" -m PyInstaller ^
    --noconfirm ^
    --clean ^
    --onefile ^
    --windowed ^
    --collect-submodules uvicorn ^
    --name GomokuServer ^
    --distpath "%DIST_DIR%" ^
    --workpath "%BUILD_ROOT%\work" ^
    --specpath "%BUILD_ROOT%" ^
    "%SERVER_DIR%server.py"

if errorlevel 1 (
    echo [ERROR] Server build failed.
    exit /b 1
)

echo.
echo [SUCCESS] Executable created:
echo "%DIST_DIR%\GomokuServer.exe"
echo [INFO] Runtime account data will be stored in "data\accounts.db" next to the executable.

exit /b 0
