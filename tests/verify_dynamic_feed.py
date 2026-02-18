
import sys
import os
import time

# Add project root to path
sys.path.append(os.getcwd())

from bot.core.market_feed import market_feed
from bot.utils.logger import logger

def verify_dynamic_feed():
    print(">>> 📡 Verifying Dynamic Option Chain Feed...")
    
    # 1. Start Service
    market_feed.start()
    
    # 2. Wait for Nifty Spot
    print(">>> Waiting for Nifty Spot Data...")
    spot_ltp = 0
    for i in range(15):
        spot_ltp = market_feed.get_ltp("99926000")
        if spot_ltp:
            print(f"✅ Nifty Spot Received: {spot_ltp}")
            break
        time.sleep(1)
        
    if not spot_ltp:
        print("❌ Nifty Spot Timeout!")
        market_feed.stop()
        return

    # 3. Wait for Option Subscription (Dynamic Logic runs in background)
    print(">>> Waiting for Option Subscriptions (max 10s)...")
    time.sleep(5) 
    
    # 4. Check subscribed tokens
    subs = market_feed.subscribed_tokens
    print(f"Total Subscribed Tokens: {len(subs)}")
    
    if len(subs) <= 1:
        print("❌ Dynamic Subscription Failed! Only Nifty Spot found.")
    else:
        print("✅ Options Subscribed!")
        print(f"Sample Tokens: {list(subs)[:5]}")
        
    # 5. Check Data flow for an Option
    print(">>> Checking Data Flow for Options...")
    option_data_count = 0
    for token in subs:
        if token == "99926000": continue
        
        quote = market_feed.get_quote(token)
        if quote:
            print(f"✅ Data for Option {token}: LTP={quote['ltp']}, Bid={quote['best_bid']}, Ask={quote['best_ask']}")
            option_data_count += 1
            if option_data_count >= 3: break
            
    if option_data_count == 0:
        print("⚠️ Subscribed but NO Option Data received. (Market Closed?)")
    else:
        print("✅ Dynamic Feed Fully Functional!")

    market_feed.stop()

if __name__ == "__main__":
    verify_dynamic_feed()
