#!/bin/bash

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m'

# Navigate to project root directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR/.."

echo -e "${BLUE}>>> Authorization: STOPPING Nifty Trading Bot System... 🛑${NC}"

# 1. Frontend
echo -e "Stopping Frontend..."
pkill -f "npm run dev"
pkill -f "node.*vite"

# 2. Send SIGINT first for graceful cleanup (allows strategies to close positions)
echo -e "Stopping Backend & Bot..."
# Match both module-style (-m bot.xxx) and file-style (xxx.py) launches
pkill -INT -f "bot\.lifecycle_manager"
pkill -INT -f "bot\.main"
pkill -INT -f "uvicorn"
pkill -INT -f "server\.py"

echo -e "Waiting for processes to exit..."
sleep 4

# 3. Force Kill — anything still running gets SIGKILL
echo -e "${RED}Ensuring all processes are dead...${NC}"
pkill -9 -f "bot\.lifecycle_manager"
pkill -9 -f "bot\.main"
pkill -9 -f "uvicorn"
pkill -9 -f "server\.py"
pkill -9 -f "run\.sh"
pkill -9 -f "lifecycle_manager\.py"   # legacy filename fallback
pkill -9 -f "main\.py"                # legacy filename fallback

# 4. Clean Ports
echo -e "Cleaning ports 8000 and 3000..."
fuser -k 8000/tcp 2>/dev/null
fuser -k 3000/tcp 2>/dev/null

# 5. Global Kill Switch Lock (Fallback for lifecycle manager)
touch .stop_signal
echo -e "🔒 Global Kill Switch Activated (.stop_signal created)"

echo -e "${GREEN}>>> System Shutdown Complete. ✅${NC}"
