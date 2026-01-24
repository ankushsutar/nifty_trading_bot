#!/bin/bash

# Colors
GREEN='\033[0;32m'
NC='\033[0m'

echo -e "${GREEN}>>> Starting Nifty Bot Auto-Trader 🤖${NC}"
echo -e "${GREEN}>>> Mode: Lifecycle Manager (Scheduler)${NC}"

# Execute Lifecycle Manager and pass all arguments (e.g. --test, --dry-run)
python3 lifecycle_manager.py "$@"
