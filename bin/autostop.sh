#!/bin/bash
# autostop.sh: Safe evening shutdown for Nifty Trading Bot
# Designed for 03:30 PM IST Cron Job

PROJECT_DIR="/home/cwd/ankush/agent/nifty_trading_bot"
LOG_FILE="$PROJECT_DIR/logs/automation.log"

echo "$(date): --- 🛑 Starting Evening Auto-Stop ---" >> $LOG_FILE

# 1. Graceful Shutdown Signal
touch "$PROJECT_DIR/.kill_trading_bot"
echo "$(date): 📩 Stop signal sent (.kill_trading_bot)." >> $LOG_FILE

# 2. Wait 30 seconds for cleanup
sleep 30

# 3. Force Kill if still running
if pgrep -f "main.py" > /dev/null; then
    echo "$(date): ⚠️ Bot still alive. Force killing." >> $LOG_FILE
    pkill -f "main.py"
fi

# 4. Cleanup
rm -f "$PROJECT_DIR/.kill_trading_bot"
echo "$(date): ✅ Bot stopped and cleaned up." >> $LOG_FILE
