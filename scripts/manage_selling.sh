#!/bin/bash

# --- Configuration ---
STRATEGY="SELLING"
LOG_FILE="logs/selling_engine.log"
PID_FILE="data/selling_engine.pid"
PYTHON_BIN="./venv/bin/python3"

# Fallback to system python if venv not found
if [ ! -f "$PYTHON_BIN" ]; then
    PYTHON_BIN="python3"
fi

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

mkdir -p logs data

usage() {
    echo "Usage: $0 {start|stop|status|restart} [--dry-run]"
    exit 1
}

start() {
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if ps -p "$PID" > /dev/null; then
            echo -e "${YELLOW}>>> Selling Engine is already running (PID: $PID)${NC}"
            return
        fi
    fi

    # Capture all arguments after start/stop
    ARGS="${@:2}"
    
    echo -e "${GREEN}>>> Starting Nifty Selling Engine ($STRATEGY)...${NC}"
    echo -e "${YELLOW}>>> Arguments: $ARGS${NC}"
    
    nohup "$PYTHON_BIN" -u -m bot.main --strategy "$STRATEGY" $ARGS >> "$LOG_FILE" 2>&1 &
    
    PID=$!
    echo $PID > "$PID_FILE"
    echo -e "${GREEN}>>> Started with PID: $PID${NC}"
    echo -e "${GREEN}>>> Logs: $LOG_FILE${NC}"
}

stop() {
    if [ ! -f "$PID_FILE" ]; then
        echo -e "${RED}>>> No PID file found. Is the engine running?${NC}"
        return
    fi

    PID=$(cat "$PID_FILE")
    echo -e "${YELLOW}>>> Stopping Selling Engine (PID: $PID)...${NC}"
    
    kill "$PID"
    sleep 2
    
    if ps -p "$PID" > /dev/null; then
        echo -e "${RED}>>> Process did not stop. Force killing...${NC}"
        kill -9 "$PID"
    fi
    
    rm "$PID_FILE"
    echo -e "${GREEN}>>> Selling Engine stopped.${NC}"
}

status() {
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if ps -p "$PID" > /dev/null; then
            echo -e "${GREEN}>>> Selling Engine is RUNNING (PID: $PID)${NC}"
            tail -n 5 "$LOG_FILE"
        else
            echo -e "${RED}>>> PID file exists but process is NOT running.${NC}"
        fi
    else
        echo -e "${YELLOW}>>> Selling Engine is NOT running.${NC}"
    fi
}

case "$1" in
    start)
        start "$@"
        ;;
    stop)
        stop
        ;;
    status)
        status
        ;;
    restart)
        stop
        start "$@"
        ;;
    *)
        usage
        ;;
esac
