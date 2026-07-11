@echo off
SETLOCAL EnableDelayedExpansion

:: Navigate to project root directory
cd /d "%~dp0.."

:: Force UTF-8 support in both the Command Prompt and Python runtime to prevent emoji/logging crashes
chcp 65001 >nul
set "PYTHONUTF8=1"

echo ====================================================
echo        Starting Nifty Trading Bot System (Windows)
echo ====================================================

:: 0. Check and Activate Virtual Environment
echo.
echo Checking Virtual Environment...
if exist "venv" (
    echo [OK] Virtual environment detected.
) else (
    echo [WARNING] venv not found! Please run bin\setup.bat first.
    pause
    exit /b 1
)

:: 0. Pre-start Cleanup
echo.
echo [0/4] Cleaning up existing processes on ports 8000 and 3000...
:: PowerShell one-liners to gracefully locate and kill processes bound to our ports (mimicking fuser)
powershell -Command "Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }" >nul 2>&1
powershell -Command "Get-NetTCPConnection -LocalPort 3000 -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }" >nul 2>&1
if exist ".stop_signal" (
    del .stop_signal
)
echo [OK] Cleanup completed.

:: 1. Start Backend (MASTER — allowed to call Angel One REST API)
echo.
echo [1/4] Launching Backend API (Port 8000)...
:: CRITICAL: Using quotes like set "VAR=VAL" prevents trailing spaces before the "&&" from corrupting environment vars.
start "Nifty Bot Backend" cmd /k "chcp 65001 >nul && set "PYTHONUTF8=1" && call venv\Scripts\activate.bat && set "PROCESS_TYPE=BACKEND" && python -m uvicorn backend.server:app --reload"

:: 2. Startup Sequencing Guard: Wait for Backend's first intelligence snapshot
echo.
set ANALYSIS_FILE=data\market_analysis.json
set MAX_WAIT=60
set WAITED=0
echo [2/4] Waiting for backend intelligence to be ready (up to %MAX_WAIT%s)...
echo Checking for fresh snapshot in: %ANALYSIS_FILE%

:wait_loop
if !WAITED! geq !MAX_WAIT! (
    echo.
    echo [WARNING] Backend intelligence not ready after !MAX_WAIT!s. Starting bot anyway...
    goto start_bot
)

:: Use powershell to safely check if the file exists and is less than 5 mins (300 seconds) old
powershell -Command "$file='%ANALYSIS_FILE%'; if (Test-Path $file) { if (((Get-Date) - (Get-Item $file).LastWriteTime).TotalSeconds -lt 300) { exit 0 } else { exit 1 } } else { exit 1 }" >nul 2>&1
if !ERRORLEVEL! EQU 0 (
    echo.
    echo [OK] Backend intelligence is ready!
    goto start_bot
)

<nul set /p =.
timeout /t 1 /nobreak >nul
set /a WAITED+=1
goto wait_loop

:start_bot
:: 3. Start Bot (CHILD — consumes shared intelligence from backend)
echo.
echo [3/4] Launching Bot Lifecycle Manager...
:: Pass through any CLI arguments provided to run.bat using %*
start "Nifty Bot Engine" cmd /k "chcp 65001 >nul && set "PYTHONUTF8=1" && call venv\Scripts\activate.bat && python -m bot.lifecycle_manager %*"

:: 4. Start Frontend
echo.
echo [4/4] Launching Frontend Dashboard (Port 3000)...
if exist "frontend" (
    cd frontend
    echo.
    echo ====================================================
    echo  Backend launched in separate window (Port 8000)
    echo  Bot Engine launched in separate window
    echo  Frontend running in this window (Port 3000)
    echo ====================================================
    echo.
    call npm run dev
) else (
    echo [ERROR] Frontend directory not found!
    pause
    exit /b 1
)
