import requests
import time

def check_pnl_consistency():
    print("Testing P&L Consistency...")
    try:
        # Fetch Daily Summary
        res1 = requests.get("http://localhost:8000/api/daily-summary")
        data1 = res1.json()
        pnl1 = data1.get("daily_pnl", 0.0)
        
        # Fetch Market Data
        res2 = requests.get("http://localhost:8000/api/market-data")
        data2 = res2.json()
        pnl2 = data2.get("pnl", 0.0)
        
        print(f"Daily Summary P&L: {pnl1}")
        print(f"Market Data P&L:   {pnl2}")
        
        if abs(pnl1 - pnl2) < 0.01:
            print("✅ P&L Values Match!")
        else:
            print("❌ P&L Discrepancy Detected!")
            
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    check_pnl_consistency()
