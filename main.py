import argparse
import sys
from core.angel_connect import get_angel_session
from utils.token_lookup import TokenLookup
from strategies.nifty_straddle import NiftyStrategy
from core.mock_connect import MockSmartConnect, MockTokenLookup
from utils.expiry_calculator import get_next_weekly_expiry

def run_bot():
    parser = argparse.ArgumentParser(description="Nifty Options Trading Bot")
    parser.add_argument("--test", action="store_true", help="Run in Mock Mode for local testing")
    args = parser.parse_args()

    if args.test:
        print("\n>>> [System] STARTING IN MOCK MODE 🟢")
        api = MockSmartConnect()
        loader = MockTokenLookup()
        loader.load_scrip_master() # Just to be consistent with the interface
    else:
        # 1. Initialize Connection
        api = get_angel_session()
        if not api:
            return

        # 2. Initialize Data Loader
        loader = TokenLookup()
        loader.load_scrip_master()

    # 3. Initialize Strategy
    # In test mode, api and loader are mocks. Strategy should work transparently.
    bot = NiftyStrategy(api, loader)

    # 4. Input Trade Parameters
    print("\n--- NIFTY OPTION TRADER ---")
    
    # Calculate Dynamic Weekly Expiry (Next Thursday)
    expiry = get_next_weekly_expiry()
    print(f">>> [Setup] Target Expiry: {expiry}")
    # 5. Execute Strategy
    # WARNING: This places a REAL order if credentials are valid (and not in test mode).
    # Strike is now calculated dynamically (ATM)
    bot.execute(expiry=expiry, action="BUY")

if __name__ == "__main__":
    run_bot()
