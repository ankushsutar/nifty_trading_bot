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
    parser.add_argument("--dry-run", action="store_true", help="Run with Real Data but DO NOT place orders")
    args = parser.parse_args()

    if args.test:
        print("\n>>> [System] STARTING IN MOCK MODE 🟢")
        api = MockSmartConnect()
        loader = MockTokenLookup()
        loader.load_scrip_master() # Just to be consistent with the interface
    else:
        # 1. Initialize Connection
        if args.dry_run:
            print("\n>>> [System] STARTING IN DRY RUN MODE 🟡") 
            print("    (Real Data, No Orders)")
        
        api = get_angel_session()
        if not api:
            return

        # 2. Initialize Data Loader
        loader = TokenLookup()
        loader.load_scrip_master()

    # 3. Initialize Strategy
    # In test mode, api and loader are mocks. Strategy should work transparently.
    # In dry_run mode, we pass True to dry_run arg of Strategy
    bot = NiftyStrategy(api, loader, dry_run=args.dry_run)

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
