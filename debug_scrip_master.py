import pandas as pd
import requests
from config.settings import Config

def check_vix():
    print(">>> Downloading Scrip Master...")
    try:
        response = requests.get(Config.SCRIP_MASTER_URL)
        data = response.json()
        df = pd.DataFrame(data)
        
        print(">>> Loaded. Filtering for INDIA VIX...")
        
        # Searching for INDIA VIX
        vix_data = df[df['name'] == 'INDIA VIX']
        
        if not vix_data.empty:
            print(vix_data.iloc[0].to_dict())
        else:
            print("Direct 'INDIA VIX' match not found. Searching 'VIX' in symbol...")
            vix_approx = df[df['symbol'].str.contains('VIX', na=False)]
            print(vix_approx[['symbol', 'token', 'exch_seg', 'name']].head(10))

    except Exception as e:
        print(f"Error: {e}")
        
if __name__ == "__main__":
    check_vix()
