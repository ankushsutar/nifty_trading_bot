import requests
import pandas as pd
from config.settings import Config

class TokenLookup:
    def __init__(self):
        self.df = None

    def load_scrip_master(self):
        """Downloads the huge JSON file from Angel One once"""
        print(">>> [Data] Downloading Scrip Master (This may take 10s)...")
        try:
            response = requests.get(Config.SCRIP_MASTER_URL)
            data = response.json()
            self.df = pd.DataFrame(data)
            print(">>> [Data] Scrip Master Loaded.")
        except Exception as e:
            print(f">>> [Error] Failed to load Scrip Master: {e}")

    def get_token(self, symbol_name, expiry_date, strike, option_type):
        """
        Finds token for NIFTY Options.
        expiry_date: '29JAN2026'
        strike: 23000
        option_type: 'CE' or 'PE'
        """
        if self.df is None:
            self.load_scrip_master()

        # Angel One stores strike in paise (e.g., 23000 -> 2300000.0)
        strike_paise = str(float(strike) * 100.0)
        
        # Filter Logic to find exact match
        row = self.df[
            (self.df['name'] == 'NIFTY') & 
            (self.df['instrumenttype'] == 'OPTIDX') & 
            (self.df['strike'] == strike_paise) &
            (self.df['symbol'].str.endswith(option_type)) &
            (self.df['expiry'] == expiry_date)
        ]

        if not row.empty:
            return row.iloc[0]['token'], row.iloc[0]['symbol']
        return None, None
