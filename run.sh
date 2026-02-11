#!/bin/bash

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${BLUE}>>> Starting Nifty Trading Bot System 📈${NC}"

# Function to kill processes on exit
cleanup() {
    echo -e "\n${RED}🛑 Shutting down system...${NC}"
    # Kill backend and any child processes
    if [ ! -z "$BACKEND_PID" ]; then
        kill $BACKEND_PID 2>/dev/null
    fi
    # Also kill anything on port 3000 (frontend)
    fuser -k 3000/tcp 2>/dev/null
    exit
}

# Trap SIGINT (Ctrl+C)
trap cleanup SIGINT

# 0. Pre-start Cleanup: Clear ports 8000 and 3000
echo -e "${BLUE}>>> Cleaning up existing processes on ports 8000 and 3000...${NC}"
fuser -k 8000/tcp 3000/tcp 2>/dev/null

# 1. Start Backend
echo -e "${GREEN}>>> Launching Backend API (Port 8000)...${NC}"
PROCESS_TYPE=BACKEND python3 -m uvicorn backend.server:app --reload &
BACKEND_PID=$!


sleep 2 # Wait for backend to warm up


# 2. Start Frontend
echo -e "${GREEN}>>> Launching Frontend Dashboard (Port 3000)...${NC}"
cd frontend
npm run dev

# Wait (Keep script running)
wait

