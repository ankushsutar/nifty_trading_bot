#!/bin/bash
# --- PROCESS GUARD: Prevent Collisions ---
echo ">>> Authorization: Ensuring clean state..."
# Check for port 8000 and kill any existing python processes
LSOF_CMD=$(command -v lsof)
if [ -n "$LSOF_CMD" ]; then
    PORT_PID=$($LSOF_CMD -t -i:8000 2>/dev/null)
    if [ -n "$PORT_PID" ]; then
        echo ">>> [System] Found existing Backend (PID: $PORT_PID). Terminating..."
        kill -9 $PORT_PID 2>/dev/null
        sleep 1
    fi
fi
# Aggressive cleanup of orphaned bot/backend processes
pkill -f "bot/main.py" 2>/dev/null
pkill -f "backend/server.py" 2>/dev/null
# -----------------------------------------

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

export ACTIVE_SYMBOL=$SYMBOL
echo -e "${BLUE}>>> 🚀 INITIALIZING TRADE: ${SYMBOL} (${STRATEGY}) <<<${NC}"

# Check for .env
if [ ! -f ".env" ]; then
    echo -e "${RED}Error: .env file not found!${NC}"
    exit 1
fi

echo -e "${GREEN}>>> Mode: Live/Dry (Pass extra flags like --dry-run or --auto to this script)${NC}"

# Clear stale intelligence so the bot waits for fresh data
rm -f data/market_analysis.json data/market_status.json

# --- BACKEND SERVICE (MASTER) ---
# Check if backend is already running on port 8000
if ! python3 -c "import socket; s = socket.socket(socket.AF_INET, socket.SOCK_STREAM); s.settimeout(1); s.connect(('127.0.0.1', 8000))" 2>/dev/null; then
    echo -e "${BLUE}>>> Starting Backend Service (MASTER)...${NC}"
    PROCESS_TYPE=BACKEND ./venv/bin/python3 -m uvicorn backend.server:app --port 8000 > data/backend.log 2>&1 &
    BACKEND_PID=$!
    
    # Cleanup on exit
    trap "kill $BACKEND_PID 2>/dev/null; exit" SIGINT SIGTERM
    
    echo -e "${YELLOW}>>> Waiting for backend to warm up (logs in data/backend.log)...${NC}"
    sleep 10
fi

# --- START BOT ---
PYTHONPATH=. ./venv/bin/python3 bot/main.py --symbol "$SYMBOL" --strategy "$STRATEGY" "${@:3}"
