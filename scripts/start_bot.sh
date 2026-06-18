#!/bin/bash

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${GREEN}>>> Starting Nifty Bot Auto-Trader 🤖${NC}"
echo -e "${GREEN}>>> Mode: Lifecycle Manager (Scheduler)${NC}"

# --- Startup Sequencing Guard ---
# Wait for the backend to write its first market_analysis.json so the bot
# has fresh regime/OI data from the very first analysis pulse (avoids UNKNOWN).
ANALYSIS_FILE="data/market_analysis.json"
MAX_WAIT=30
WAITED=0

echo -e "${YELLOW}>>> Waiting for backend to write analysis data...${NC}"
while [ ! -f "$ANALYSIS_FILE" ] && [ $WAITED -lt $MAX_WAIT ]; do
    sleep 1
    WAITED=$((WAITED + 1))
done

if [ -f "$ANALYSIS_FILE" ]; then
    echo -e "${GREEN}>>> Backend data ready (waited ${WAITED}s). Starting bot...${NC}"
else
    echo -e "${YELLOW}>>> Backend data not yet available after ${MAX_WAIT}s. Starting bot anyway...${NC}"
fi

# Execute Lifecycle Manager and pass all arguments (e.g. --test, --dry-run)
python3 lifecycle_manager.py "$@"
