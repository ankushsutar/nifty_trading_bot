
import requests
import pandas as pd
from config.settings import Config

def check_token():
    print("Downloading Scrip Master...")
    try:
        response = requests.get(Config.SCRIP_MASTER_URL)
        data = response.json()
        df = pd.DataFrame(data)
        print("Loaded Scrip Master.")
        print(f"Total Rows: {len(df)}")
        print(f"Columns: {df.columns.tolist()}")
    except Exception as e:
        print(f"Failed to load: {e}")
        return

    # Check NIFTY Names
    print("\n--- Unique Names starting with NIFTY ---")
    nifty_names = df[df['name'].str.startswith('NIFTY')]['name'].unique()
    print(nifty_names)

    # Check NIFTY Instrument Types
    print("\n--- Instrument Types for name='NIFTY' ---")
    nifty_rows = df[df['name'] == 'NIFTY']
    if not nifty_rows.empty:
        print(nifty_rows['instrumenttype'].unique())
        
        # Check Expiries for OPTIDX
        optidx = nifty_rows[nifty_rows['instrumenttype'] == 'OPTIDX']
        if not optidx.empty:
            print("\n--- Top 10 Expiries for NIFTY OPTIDX ---")
            print(optidx['expiry'].unique()[:10])
            
            # Check for ANY 2026 expiry
            print("\n--- 2026 Expiries ---")
            ex26 = optidx[optidx['expiry'].str.contains('2026')]
            if not ex26.empty:
               print(ex26['expiry'].unique())
            else:
               print("No 2026 Expiries found!")
        else:
            print("No OPTIDX found for NIFTY.")
    else:
        print("No rows with name='NIFTY'. checking 'Nifty 50'...")
        nifty50 = df[df['name'] == 'Nifty 50']
        if not nifty50.empty:
             print("Found 'Nifty 50' rows.")
        else:
             print("Neither 'NIFTY' nor 'Nifty 50' found.")

if __name__ == "__main__":
    check_token()
