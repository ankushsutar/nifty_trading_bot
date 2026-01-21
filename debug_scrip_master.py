import pandas as pd
import requests
from config.settings import Config

def check_expiries():
    print(">>> Downloading Scrip Master...")
    try:
        response = requests.get(Config.SCRIP_MASTER_URL)
        data = response.json()
        df = pd.DataFrame(data)
        
        print(">>> Loaded. Filtering NIFTY OPTIDX...")
        nifty_opts = df[
            (df['name'] == 'NIFTY') & 
            (df['instrumenttype'] == 'OPTIDX')
        ]
        
        print("\n>>> Inspecting 27JAN2026 Data:")
        jan27 = nifty_opts[nifty_opts['expiry'] == '27JAN2026']
        if not jan27.empty:
            print(jan27[['symbol', 'strike', 'instrumenttype', 'token']].head(5))
            print("\nStrike Format Example:", jan27.iloc[0]['strike'])
            print("Type of Strike:", type(jan27.iloc[0]['strike']))
        else:
            print("No data for 27JAN2026?")

    except Exception as e:
        print(e)
        
if __name__ == "__main__":
    check_expiries()
