@echo off
SETLOCAL EnableDelayedExpansion

:: Navigate to project root directory
cd /d "%~dp0.."

:: Force UTF-8 support in the Command Prompt to prevent logging crashes
chcp 65001 >nul
set "PYTHONUTF8=1"

echo ====================================================
echo          Nifty Trading Bot Setup (Windows)
echo ====================================================

:: 1. Check Python and Setup Virtual Environment
echo.
echo [1/3] Setting up Python Virtual Environment...
python --version >nul 2>&1
IF %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Python is not installed or not in PATH.
    pause
    exit /b 1
)

:: Create Virtual Environment if it doesn't exist
IF NOT EXIST "venv" (
    echo Creating virtual environment...
    python -m venv venv
    IF !ERRORLEVEL! NEQ 0 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
    echo [OK] Virtual environment created.
) ELSE (
    echo [INFO] Virtual environment already exists.
)

:: Activate and Install Backend Dependencies
echo.
echo Installing/Updating Python dependencies in venv...
IF EXIST "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
) ELSE (
    echo [ERROR] Virtual environment activation script not found!
    pause
    exit /b 1
)

IF EXIST "requirements.txt" (
    echo Upgrading pip...
    python -m pip install --upgrade pip
    echo Installing requirements - this may take a few minutes...
    pip install -r requirements.txt
    IF !ERRORLEVEL! EQU 0 (
        echo [OK] Backend dependencies installed in venv.
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

:: 2. Check and setup Environment variables
echo.
echo [2/3] Checking Environment Configuration...
IF NOT EXIST ".env" (
    IF EXIST ".env.example" (
        echo [INFO] .env not found. Creating from .env.example...
        copy .env.example .env
        echo [WARNING] Created .env file! PLEASE EDIT IT WITH YOUR CREDENTIALS BEFORE RUNNING.
    ) ELSE (
        echo [WARNING] .env.example not found! Cannot auto-generate .env.
    )
) ELSE (
    echo [OK] .env file is already configured.
)

:: 3. Check Node.js and Install Frontend Dependencies
echo.
echo [3/3] Checking Frontend Dependencies...
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
    IF !ERRORLEVEL! EQU 0 (
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
echo ====================================================
echo       Setup Complete! 
echo       Make sure your .env credentials are updated.
echo       Run 'bin\run.bat' to start the bot.
echo ====================================================
pause
