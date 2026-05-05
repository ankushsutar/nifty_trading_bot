#!/bin/bash
# autostart.sh: Morning launch script for Nifty Trading Bot
# Designed for 09:10 AM IST Cron Job

PROJECT_DIR="/home/cwd/ankush/agent/nifty_trading_bot"
LOG_FILE="$PROJECT_DIR/logs/automation.log"

echo "$(date): --- 🚀 Starting Morning Auto-Launch ---" >> $LOG_FILE

# 1. Navigate to directory
cd $PROJECT_DIR

# 2. Cleanup old signals
rm -f "$PROJECT_DIR/.kill_trading_bot"

# 2. Check if already running
if pgrep -f "python3 bot/main.py" > /dev/null; then
    echo "$(date): ⚠️ Bot is already running. Skipping." >> $LOG_FILE
    exit 0
fi

# 3. Pull latest code (Optional - keep commented out for stability)
# git pull >> $LOG_FILE 2>&1

# 4. Activate Venv and Launch
export PYTHONPATH=$PYTHONPATH:.
nohup ./venv/bin/python3 bot/main.py --auto >> $PROJECT_DIR/logs/trading_bot.log 2>&1 &

echo "$(date): ✅ Bot launched successfully in background." >> $LOG_FILE
