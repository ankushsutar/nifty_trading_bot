#!/bin/bash

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}>>> Nifty Trading Bot Setup Details 🚀${NC}"

# 1. Python Details
echo -e "\n${BLUE}[1/2] Installing Backend Dependencies...${NC}"
if [ -f "requirements.txt" ]; then
    pip install -r requirements.txt
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}✅ Python dependencies installed.${NC}"
    else
        echo "❌ Failed to install Python dependencies."
        exit 1
    fi
else
    echo "❌ requirements.txt not found!"
    exit 1
fi

# 2. Node.js Details
echo -e "\n${BLUE}[2/2] Installing Frontend Dependencies...${NC}"
if [ -d "frontend" ]; then
    cd frontend
    npm install
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}✅ Frontend dependencies installed.${NC}"
    else
        echo "❌ Failed to install Frontend dependencies."
        exit 1
    fi
    cd ..
else
    echo "❌ Frontend directory not found!"
    exit 1
fi

echo -e "\n${GREEN}🎉 Setup Complete! Run ./run.sh to start the bot.${NC}"
chmod +x run.sh
