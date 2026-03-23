@echo off
echo ==========================================
echo    Starting Nifty Trading Bot System
echo ==========================================

:: 1. Start Backend
echo.
echo [1/2] Launching Backend API (Port 8000)...
start "Nifty Bot Backend" cmd /k "python -m uvicorn backend.server:app --reload"

:: 2. Start Frontend
echo.
echo [2/2] Launching Frontend Dashboard (Port 3000)...
if exist "frontend" (
    cd frontend
    start "Nifty Bot Frontend" cmd /k "npm run dev"
    cd ..
) else (
    echo [ERROR] Frontend directory not found!
    pause
    exit /b 1
)

echo.
echo ==========================================
echo    System Started!
echo    Backend: http://localhost:8000
echo    Frontend: http://localhost:3000
echo ==========================================
