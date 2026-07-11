#!/bin/bash

# Backtest Automation Script for NIFTY Trading Bot
# Usage: ./backtest.sh [days]
# Example: ./backtest.sh 60

# Navigate to project root directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR/.."

DAYS=${1:-30}
CAPITAL=${2:-38000}
VENV_PATH="./venv/bin/python3"

echo "--------------------------------------------------"
echo "🚀 NIFTY BOT BACKTEST AUTOMATION (Days: $DAYS | Capital: ${CAPITAL:-'Default'})"
echo "--------------------------------------------------"

# 1. Fetch Data
echo "📥 Step 1: Fetching $DAYS days of historical data..."
$VENV_PATH scripts/fetch_backtest_data.py $DAYS

if [ $? -ne 0 ]; then
    echo "❌ Error: Data fetch failed. Aborting."
    exit 1
fi

# 2. Run Ranked Backtest
echo ""
echo "📊 Step 2: Running Ranked Strategy Backtest..."
$VENV_PATH scripts/run_backtest.py $CAPITAL

if [ $? -ne 0 ]; then
    echo "❌ Error: Backtest execution failed."
    exit 1
fi

# 3. Run Hero Analysis
echo ""
echo "🎯 Step 3: Running Expiry & Hero Analysis..."
$VENV_PATH scripts/analyze_expiry_hero.py $CAPITAL

echo ""
echo "--------------------------------------------------"
echo "✅ Backtest Suite Complete!"
echo "--------------------------------------------------"
