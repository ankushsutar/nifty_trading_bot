import pandas as pd
import requests
import time
from config.settings import Config
from core.angel_connect import get_angel_session
import datetime

def check_nifty_index():
    print(">>> Downloading Scrip Master...")
    try:
        response = requests.get(Config.SCRIP_MASTER_URL)
        data = response.json()
        df = pd.DataFrame(data)
        
        print(">>> Searching for Nifty Index...")
        # Search for Nifty 50 in NSE segment
        nifty = df[
            (df['exch_seg'] == 'NSE') & 
            ((df['name'] == 'NIFTY') | (df['symbol'].str.contains('NIFTY')))
        ]
        
        print(f"Found {len(nifty)} records.")
        if not nifty.empty:
            print(nifty[['symbol', 'name', 'token', 'exch_seg', 'instrumenttype']].to_string())
            
        # Try fetching candle data for the first match or known token
        token = "99926000"
        print(f"\n>>> Testing Candle Data for Token: {token} (Standard Nifty 50)")
        
        api = get_angel_session()
        if not api:
            print("Login failed.")
            return

        today = datetime.date.today().strftime("%Y-%m-%d")
        historicParam={
            "exchange": "NSE", 
            "symboltoken": token, 
            "interval": "FIVE_MINUTE",
            "fromdate": f"{today} 09:15", 
            "todate": f"{today} 15:30"
        }
        
        print(f"Request: {historicParam}")
        try:
            data = api.getCandleData(historicParam)
            print(f"Response: {data}")
        except Exception as e:
            print(f"Error: {e}")

    except Exception as e:
        print(e)

if __name__ == "__main__":
    check_nifty_index()
