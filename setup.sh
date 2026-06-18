#!/bin/bash

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}>>> Nifty Trading Bot Setup Details 🚀${NC}"

# 1. Python Details
echo -e "\n${BLUE}[1/2] Setting up Python Virtual Environment...${NC}"

# If venv exists but is broken (no activate script), remove it
if [ -d "venv" ] && [ ! -f "venv/bin/activate" ]; then
    echo -e "${YELLOW}>>> Broken venv detected. Cleaning up...${NC}"
    rm -rf venv
fi

if [ ! -d "venv" ]; then
    echo -e "${BLUE}>>> Creating virtual environment...${NC}"
    # Try standard creation first
    python3 -m venv venv 2>/dev/null
    
    # Check if venv was created successfully
    if [ ! -f "venv/bin/activate" ]; then
        echo -e "${YELLOW}>>> Standard venv creation failed (likely missing python3-venv/ensurepip). Trying workaround...${NC}"
        python3 -m venv venv --without-pip
        
        if [ -f "venv/bin/activate" ]; then
            echo -e "${BLUE}>>> Bootstrapping pip into venv...${NC}"
            if command -v curl >/dev/null 2>&1; then
                curl -sS https://bootstrap.pypa.io/get-pip.py -o get-pip.py
                ./venv/bin/python3 get-pip.py
                rm get-pip.py
            elif command -v wget >/dev/null 2>&1; then
                wget -q https://bootstrap.pypa.io/get-pip.py -O get-pip.py
                ./venv/bin/python3 get-pip.py
                rm get-pip.py
            else
                echo -e "${RED}❌ Error: curl or wget required for workaround.${NC}"
                exit 1
            fi
        else
            echo -e "${RED}❌ Critical Error: Failed to create virtual environment.${NC}"
            echo -e "Please install the venv module manually:"
            echo -e "  sudo apt update && sudo apt install python3-venv"
            exit 1
        fi
    fi
    echo -e "${GREEN}✅ Virtual environment ready.${NC}"
fi

source venv/bin/activate

echo -e "\n${BLUE}Installing Backend Dependencies in venv...${NC}"
if [ -f "requirements.txt" ]; then
    pip install --upgrade pip
    pip install -r requirements.txt
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}✅ Python dependencies installed in venv.${NC}"
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
