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
    kill $BACKEND_PID
    exit
}

# Trap SIGINT (Ctrl+C)
trap cleanup SIGINT

# 1. Start Backend
echo -e "${GREEN}>>> Launching Backend API (Port 8000)...${NC}"
python3 -m uvicorn backend.server:app --reload &
BACKEND_PID=$!
sleep 2 # Wait for backend to warm up

# 2. Start Frontend
echo -e "${GREEN}>>> Launching Frontend Dashboard (Port 3000)...${NC}"
cd frontend
npm run dev

# Wait (Keep script running)
wait
