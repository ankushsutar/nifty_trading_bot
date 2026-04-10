#!/bin/bash

# --- COLORS ---
RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

SYMBOL=$1

if [ -z "$SYMBOL" ]; then
    echo -e "${RED}Usage: ./stop.sh <SYMBOL>${NC}"
    echo -e "Example: ./stop.sh CRUDEOIL"
    exit 1
fi

echo -e "${RED}>>> 🛑 STOPPING ALL INSTANCES FOR: ${SYMBOL} <<<${NC}"

# 1. Send SIGINT for graceful exit (allows strategy to close positions if programmed)
pkill -INT -f "bot/main\.py.*--symbol ${SYMBOL}"
pkill -INT -f "bot\.main.*--symbol ${SYMBOL}"

sleep 2

# 2. Force Kill if still running
pkill -9 -f "bot/main\.py.*--symbol ${SYMBOL}"
pkill -9 -f "bot\.main.*--symbol ${SYMBOL}"

echo -e "${GREEN}>>> Instances for ${SYMBOL} have been terminated. ✅${NC}"
