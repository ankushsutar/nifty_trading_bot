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

    # Capital Settings (Tuned for ₹10,000 live account — Aggressive Compounding Mode)
    SIMULATION_CAPITAL = 10000.0         # Matches real account — dry run uses same sizing as live
    MIN_CAPITAL_THRESHOLD = 3000.0       # Stop bot if capital falls below ₹3,000 (unrecoverable territory)

    # MongoDB Settings for Historical Trade    # MongoDB
    MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
    MONGO_DB = os.getenv("MONGO_DB", "nifty_bot")
    MONGO_COLLECTION = os.getenv("MONGO_COLLECTION", "trades")

    # Notifications
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

    # Risk Management — Aggressive Compounding Mode (₹10,000 capital)
    # Strategy: GAMMA_BLAST + MOMENTUM only, ADX > 35 gate, 12% risk per trade.
    # Expected: ~32% monthly gain. Reaches ₹1,00,000 in ~8-9 months compounded.
    RISK_PER_TRADE_PERCENT = 0.12        # 12% risk = ₹1,200 per trade — survives 6 consecutive losses
    MAX_CAPITAL_USAGE_PERCENT = 0.90     # Use up to 90% of capital per trade (1 lot + buffer)
    ENTRY_SLIPPAGE_BUFFER_PERCENT = 0.01 # 1% buffer for LIMIT orders
    MAX_DAILY_LOSS = -1500.0             # Halt after ₹1,500 loss (15%) — allows recovery, prevents wipeout

    # ADX threshold to allow any trade at all (hard gate — no trend, no trade)
    MIN_ADX_TO_TRADE = 35.0

    # Infrastructure
    STATIC_IP = os.getenv("STATIC_IP") # Optional: Your whitelisted static IP
