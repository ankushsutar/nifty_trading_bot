#!/bin/bash
# run_bg.sh - Launch Nifty Trading Bot stack in the background
PROJECT_DIR="/home/cwd/ankush/agent/nifty_trading_bot"
cd "$PROJECT_DIR" || exit 1

mkdir -p logs

# Check if already running
BACKEND_PID=$(pgrep -f "uvicorn backend.server:app" | head -n 1)
BOT_PID=$(pgrep -f "bot\.lifecycle_manager" | head -n 1)

if [ -n "$BACKEND_PID" ] || [ -n "$BOT_PID" ]; then
    echo "⚠️ Trading bot is already running (Backend PID: ${BACKEND_PID:-none}, Bot PID: ${BOT_PID:-none})."
    exit 0
fi

# Clean old stop signals
rm -f .stop_signal

# Launch run.sh in background detached from terminal
nohup bash "$PROJECT_DIR/run.sh" "$@" > "$PROJECT_DIR/logs/system.log" 2>&1 &
WRAPPER_PID=$!

echo "🚀 Launching Trading Bot System (Wrapper PID: $WRAPPER_PID)..."

# Give processes a few seconds to initialize
sleep 4

NEW_BACKEND_PID=$(pgrep -f "uvicorn backend.server:app" | head -n 1)
NEW_BOT_PID=$(pgrep -f "bot\.lifecycle_manager" | head -n 1)

if [ -n "$NEW_BACKEND_PID" ] || [ -n "$NEW_BOT_PID" ]; then
    echo "✅ Bot stack started successfully!"
    echo "• Backend: PID ${NEW_BACKEND_PID:-initializing} (Port 8000)"
    echo "• Bot Lifecycle: PID ${NEW_BOT_PID:-initializing}"
    echo "• Logs: logs/system.log"
else
    echo "⏳ Bot launch initiated. Checking logs/system.log..."
fi
