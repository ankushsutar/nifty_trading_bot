import argparse
import sys
from core.angel_connect import get_angel_session
from utils.token_lookup import TokenLookup
from strategies.nifty_straddle import NiftyStrategy
from core.mock_connect import MockSmartConnect, MockTokenLookup
from utils.expiry_calculator import get_next_weekly_expiry
from strategies.orb_strategy import ORBStrategy
from strategies.momentum_strategy import MomentumStrategy
from strategies.vwap_strategy import VWAPStrategy
from strategies.ohl_strategy import OHLStrategy
from strategies.inside_bar_strategy import InsideBarStrategy

def run_bot():
    parser = argparse.ArgumentParser(description="Nifty Options Trading Bot")
    parser.add_argument("--test", action="store_true", help="Run in Mock Mode for local testing")
    parser.add_argument("--dry-run", action="store_true", help="Run with Real Data but DO NOT place orders")
    parser.add_argument("--strategy", type=str, default="STRADDLE", choices=["STRADDLE", "ORB", "MOMENTUM", "VWAP", "OHL", "INSIDE_BAR"], help="Choose Strategy")
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
    
    if args.strategy == "ORB":
        print(f"\n>>> [Strategy] Selected: Open Range Breakout (ORB)")
        bot = ORBStrategy(api, loader, dry_run=args.dry_run)
    elif args.strategy == "MOMENTUM":
        print(f"\n>>> [Strategy] Selected: Momentum (EMA Crossover) ⚡")
        bot = MomentumStrategy(api, loader, dry_run=args.dry_run)
    elif args.strategy == "VWAP":
        print(f"\n>>> [Strategy] Selected: VWAP Institutional Trend (Pro Mode) 🚀")
        bot = VWAPStrategy(api, loader, dry_run=args.dry_run)
    elif args.strategy == "OHL":
        print(f"\n>>> [Strategy] Selected: Open High Low (OHL) Scalp 🎯")
        bot = OHLStrategy(api, loader, dry_run=args.dry_run)
    elif args.strategy == "INSIDE_BAR":
        print(f"\n>>> [Strategy] Selected: Inside Bar Breakout 🔥")
        bot = InsideBarStrategy(api, loader, dry_run=args.dry_run)
    else:
        print(f"\n>>> [Strategy] Selected: 9:20 Straddle (Short) 📉")
        bot = NiftyStrategy(api, loader, dry_run=args.dry_run)

    # 4. Input Trade Parameters
    print("\n--- NIFTY OPTION TRADER ---")
    
    # Calculate Dynamic Weekly Expiry (Next Tuesday)
    expiry = get_next_weekly_expiry()
    print(f">>> [Setup] Target Expiry: {expiry}")
    
    # 5. Execute Strategy
    # WARNING: This places a REAL order if credentials are valid (and not in test mode).
    # Strike is now calculated dynamically (ATM)
    
    if args.strategy in ["ORB", "OHL", "INSIDE_BAR"]:
        # Directional buying
        bot.execute(expiry=expiry, action="BUY")
    elif args.strategy == "MOMENTUM":
        bot.execute(expiry=expiry)
    else:
        # Straddle (Short)
        bot.execute(expiry=expiry, action="SELL")

if __name__ == "__main__":
    run_bot()
