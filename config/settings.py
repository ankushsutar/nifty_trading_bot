import os
from dotenv import load_dotenv

# Load secrets from .env file
load_dotenv()

class Config:
    API_KEY = os.getenv("API_KEY")
    CLIENT_ID = os.getenv("CLIENT_ID")
    PASSWORD = os.getenv("PASSWORD")
    TOTP_SECRET = os.getenv("TOTP_SECRET")
    
    # Nifty Constants (Updated for 2026)
    NIFTY_LOT_SIZE = 65
    # URL to fetch token IDs for all stocks
    SCRIP_MASTER_URL = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
    
    # Simulation Settings
    SIMULATION_CAPITAL = 500000.0 # 5 Lakhs default for Paper Trading

    # MongoDB Settings for Historical Trade Persistence
    MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
    MONGO_DB = os.getenv("MONGO_DB", "nifty_bot")
    MONGO_COLLECTION = os.getenv("MONGO_COLLECTION", "trades")

