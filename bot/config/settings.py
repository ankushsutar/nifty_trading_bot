import os
from dotenv import load_dotenv

# Load secrets from .env file
load_dotenv()

class Config:
    API_KEY = os.getenv("API_KEY")
    CLIENT_ID = os.getenv("CLIENT_ID")
    PASSWORD = os.getenv("PASSWORD")
    TOTP_SECRET = os.getenv("TOTP_SECRET")
    
    # Master Mode Controls
    LIVE_TRADE_ENABLED = os.getenv("LIVE_TRADE_ENABLED", "FALSE").upper() == "TRUE"
    
    # Nifty Constants (Updated for 2026)
    NIFTY_LOT_SIZE = 65 # Updated for 2026
    # URL to fetch token IDs for all stocks
    SCRIP_MASTER_URL = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"

    # Capital Settings (Tuned for ₹8,000 live account)
    SIMULATION_CAPITAL = 8000.0          # Matches real account — dry run uses same sizing as live
    MIN_CAPITAL_THRESHOLD = 5000.0       # Below this, bot halts (can't cover 1 lot + buffer)

    # MongoDB Settings for Historical Trade    # MongoDB
    MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
    MONGO_DB = os.getenv("MONGO_DB", "nifty_bot")
    MONGO_COLLECTION = os.getenv("MONGO_COLLECTION", "trades")

    # Notifications
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

    # Risk Management (Tuned for ₹8,000 capital)
    RISK_PER_TRADE_PERCENT = 0.06        # 6% risk = ~₹480 per trade (1 lot, ~7pt option SL)
    MAX_CAPITAL_USAGE_PERCENT = 0.90     # Allow up to ₹7,200 per trade (covers 1 lot ATM premium)
    ENTRY_SLIPPAGE_BUFFER_PERCENT = 0.01 # 1% buffer for LIMIT orders
    MAX_DAILY_LOSS = -800.0              # Halt after ₹800 loss (10% of capital — balanced room for volatility)

    # Infrastructure
    STATIC_IP = os.getenv("STATIC_IP") # Optional: Your whitelisted static IP
