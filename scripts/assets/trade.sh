#!/bin/bash

# --- COLORS ---
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

SYMBOL=$1
STRATEGY=${2:-MOMENTUM} # Default to Momentum if not provided

if [ -z "$SYMBOL" ]; then
    echo -e "${YELLOW}Usage: ./trade.sh <SYMBOL> [STRATEGY]${NC}"
    echo -e "Example: ./trade.sh CRUDEOIL GAMMA_BLAST"
    exit 1
fi

echo -e "${BLUE}>>> 🚀 INITIALIZING TRADE: ${SYMBOL} (${STRATEGY}) <<<${NC}"

# Check for .env
if [ ! -f ".env" ]; then
    echo -e "${RED}Error: .env file not found!${NC}"
    exit 1
fi

# Run the bot in the background or foreground?
# User usually wants to see the logs, so foreground.
# But if they want a start/stop script, maybe they want background?
# I'll default to foreground but provide a background option.

echo -e "${GREEN}>>> Mode: Live/Dry (Pass extra flags like --dry-run or --auto to this script)${NC}"

PYTHONPATH=. ./venv/bin/python3 bot/main.py --symbol "$SYMBOL" --strategy "$STRATEGY" "${@:3}"
