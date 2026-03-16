#!/bin/bash

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${BLUE}>>> Starting Nifty Trading Bot System 📈${NC}"

# 0. Activate Virtual Environment
if [ -d "venv" ]; then
    echo -e "${BLUE}>>> Activating virtual environment...${NC}"
    source venv/bin/activate
else
    echo -e "${YELLOW}>>> Warning: venv not found. Please run ./setup.sh first.${NC}"
fi

# Function to kill all child processes on exit
cleanup() {
    echo -e "\n${RED}🛑 Shutting down system...${NC}"
    if [ ! -z "$BOT_PID" ]; then kill $BOT_PID 2>/dev/null; fi
    if [ ! -z "$BACKEND_PID" ]; then kill $BACKEND_PID 2>/dev/null; fi
    fuser -k 3000/tcp 2>/dev/null
    exit
}

trap cleanup SIGINT SIGTERM

# 0. Pre-start Cleanup
echo -e "${BLUE}>>> Cleaning up existing processes on ports 8000 and 3000...${NC}"
fuser -k 8000/tcp 3000/tcp 2>/dev/null
rm -f .stop_signal

# 1. Start Backend (MASTER — allowed to call Angel One REST API)
echo -e "${GREEN}>>> Launching Backend API (Port 8000)...${NC}"
PROCESS_TYPE=BACKEND python3 -m uvicorn backend.server:app --reload &
BACKEND_PID=$!

# 2. Startup Sequencing Guard: wait for backend's first market_analysis.json
# The bot must not start before the backend has written its first intelligence
# snapshot, otherwise it falls back to UNKNOWN regime on first analysis pulse.
ANALYSIS_FILE="data/market_analysis.json"
MAX_WAIT=60
WAITED=0

echo -e "${YELLOW}>>> Waiting for backend intelligence to be ready (up to ${MAX_WAIT}s)...${NC}"
while [ $WAITED -lt $MAX_WAIT ]; do
    if [ -f "$ANALYSIS_FILE" ]; then
        FILE_AGE=$(( $(date +%s) - $(date -r "$ANALYSIS_FILE" +%s 2>/dev/null || echo 0) ))
        if [ "$FILE_AGE" -lt 300 ]; then
            echo -e "${GREEN}>>> Backend intelligence ready (waited ${WAITED}s). 🟢${NC}"
            break
        fi
    fi
    sleep 1
    WAITED=$((WAITED + 1))
done

if [ $WAITED -ge $MAX_WAIT ]; then
    echo -e "${YELLOW}>>> Backend data not ready after ${MAX_WAIT}s. Starting bot anyway...${NC}"
fi

# 3. Start Bot (CHILD — consumes shared intelligence from backend)
echo -e "${GREEN}>>> Launching Bot Lifecycle Manager 🤖${NC}"
python3 -m bot.lifecycle_manager "$@" &
BOT_PID=$!

# 4. Start Frontend
echo -e "${GREEN}>>> Launching Frontend Dashboard (Port 3000)...${NC}"
cd frontend
npm run dev

# Keep script alive
wait

