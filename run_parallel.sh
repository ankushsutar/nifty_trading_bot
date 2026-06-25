#!/bin/bash

# Setup cleanup on exit
cleanup() {
    echo -e "\n🛑 Stopping all parallel bots..."
    kill $(jobs -p) 2>/dev/null
    exit
}
trap cleanup SIGINT SIGTERM

# Activate Virtual Environment
if [ -d "venv" ]; then
    source venv/bin/activate
fi

# Create logs directory
mkdir -p logs

echo "📈 Starting NIFTY Bot..."
export ACTIVE_SYMBOL=NIFTY
python3 -m bot.lifecycle_manager > logs/nifty_bot.log 2>&1 &

echo "📈 Starting FINNIFTY Bot..."
export ACTIVE_SYMBOL=FINNIFTY
python3 -m bot.lifecycle_manager > logs/finnifty_bot.log 2>&1 &

echo "✨ Both bots are running in the background!"
echo "--------------------------------------------------"
echo "📋 To view Nifty logs:      tail -f logs/nifty_bot.log"
echo "📋 To view FinNifty logs:   tail -f logs/finnifty_bot.log"
echo "--------------------------------------------------"
echo "Press [Ctrl + C] at any time to stop both bots."

# Keep script alive to manage child processes
wait
