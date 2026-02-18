
import sys
import os
import time
import signal

# Add project root to path
sys.path.append(os.getcwd())

from bot.core.market_feed import market_feed
from bot.utils.logger import logger

def verify_feed():
    print(">>> 📡 Verifying Real-Time Market Feed...")
    
    # 1. Start Service
    market_feed.start()
    
    # 2. Wait for Connection
    print(">>> Waiting for connection (max 10s)...")
    for _ in range(10):
        if market_feed.is_connected:
            break
        time.sleep(1)
        
    if not market_feed.is_connected:
        print("❌ Connection Timeout! Check logs.")
        market_feed.stop()
        return

    print("✅ Connected to Angel One WebSocket!")
    
    # 3. Monitor Data for Nifty Spot (99926000)
    print(">>> Monitoring Nifty 50 Spot (Token: 99926000)...")
    
    # Wait for data packet
    received = False
    for i in range(20):
        ltp = market_feed.get_ltp("99926000")
        if ltp:
            print(f"✅ Data Received! Nifty Spot: {ltp}")
            received = True
            break
        print(f"Waiting for tick... ({i+1}/20)")
        time.sleep(1)
        
    if not received:
        print("⚠️ Connected but NO Data received. Check Subscription Logic.")
        # Debug: Print latest_data keys
        print(f"Debug: Keys in Cache: {list(market_feed.latest_data.keys())}")
    
    # 4. Stop
    print(">>> Stopping Service...")
    market_feed.stop()
    print(">>> Verification Complete.")

if __name__ == "__main__":
    verify_feed()
