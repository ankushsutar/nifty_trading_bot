@echo off
SETLOCAL EnableDelayedExpansion

echo ==========================================
echo       Nifty Trading Bot Setup (Windows)
echo ==========================================

:: 1. Check Python and Install Backend Dependencies
echo.
echo [1/2] Checking Backend Dependencies...
python --version >nul 2>&1
IF %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Python is not installed or not in PATH.
    pause
    exit /b 1
)

IF EXIST "requirements.txt" (
    echo Installing Python dependencies...
    pip install -r requirements.txt
    IF %ERRORLEVEL% EQU 0 (
        echo [OK] Backend dependencies installed.
    ) ELSE (
        echo [ERROR] Failed to install backend dependencies.
        pause
        exit /b 1
    )
) ELSE (
    echo [ERROR] requirements.txt not found!
    pause
    exit /b 1
)

:: 2. Check Node.js and Install Frontend Dependencies
echo.
echo [2/2] Checking Frontend Dependencies...
call npm --version >nul 2>&1
IF %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Node.js/npm is not installed or not in PATH.
    echo Please install Node.js from https://nodejs.org/
    pause
    exit /b 1
)

IF EXIST "frontend" (
    cd frontend
    echo Installing Frontend dependencies...
    call npm install
    IF %ERRORLEVEL% EQU 0 (
        echo [OK] Frontend dependencies installed.
    ) ELSE (
        echo [ERROR] Failed to install frontend dependencies.
        cd ..
        pause
        exit /b 1
    )
    cd ..
) ELSE (
    echo [ERROR] Frontend directory not found!
    pause
    exit /b 1
)

echo.
echo ==========================================
echo       Setup Complete! 
echo       Run 'run.bat' to start the bot.
echo ==========================================
pause
