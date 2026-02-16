#!/bin/bash

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${BLUE}>>> Authorization: STOPPING Nifty Trading Bot System... 🛑${NC}"

# 1. Frontend
echo -e "Stopping Frontend..."
pkill -f "npm run dev"
pkill -f "node.*vite"

# 2. Main Logic
echo -e "Stopping Backend & Bot..."
# Send SIGINT first for graceful cleanup (saving state)
pkill -INT -f "python3.*(main.py|server.py|lifecycle_manager.py|market_service.py)"
pkill -INT -f "uvicorn"

echo -e "Waiting for processes to exit..."
sleep 3

# 3. Force Kill
echo -e "${RED}Ensuring all processes are dead...${NC}"
pkill -9 -f "python3.*(main.py|server.py|lifecycle_manager.py|market_service.py)"
pkill -9 -f "uvicorn"
pkill -9 -f "run.sh"

# 4. Clean Ports
echo -e "Cleaning ports 8000 and 3000..."
fuser -k 8000/tcp 2>/dev/null
fuser -k 3000/tcp 2>/dev/null

echo -e "${GREEN}>>> System Shutdown Complete. ✅${NC}"
