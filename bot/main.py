import argparse
import sys
import signal
import datetime
from bot.core.angel_connect import get_angel_session
from bot.utils.token_lookup import TokenLookup
from bot.strategies.nifty_straddle import NiftyStrategy
from bot.core.mock_connect import MockSmartConnect, MockTokenLookup
from bot.utils.expiry_calculator import get_next_weekly_expiry
from bot.strategies.orb_strategy import ORBStrategy
from bot.strategies.momentum_strategy import MomentumStrategy
from bot.strategies.vwap_strategy import VWAPStrategy
from bot.strategies.ohl_strategy import OHLStrategy
from bot.strategies.inside_bar_strategy import InsideBarStrategy
from bot.core.decision_engine import DecisionEngine
from bot.utils.logger import logger

# Global variable for graceful shutdown
bot_instance = None

def signal_handler(sig, frame):
    """Handles Ctrl+C and Termination Signals"""
    logger.info(f"\n>>> [System] Signal Received ({sig}). Initiating Graceful Shutdown...")
    
    if bot_instance:
        logger.info(">>> [System] Cleaning up Active Positions...")
        if hasattr(bot_instance, 'stop'):
             bot_instance.stop()
        else:
             logger.warning(">>> [Warning] Strategy does not support graceful '.stop()'. Checking if active...")
             
    sys.exit(0)

def run_bot():
    global bot_instance
    
    # Register Signals
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    parser = argparse.ArgumentParser(description="Nifty Options Trading Bot")
    parser.add_argument("--test", action="store_true", help="Run in Mock Mode for local testing")
    parser.add_argument("--dry-run", action="store_true", help="Run with Real Data but DO NOT place orders")
    parser.add_argument("--strategy", type=str, default="STRADDLE", choices=["STRADDLE", "ORB", "MOMENTUM", "VWAP", "OHL", "INSIDE_BAR"], help="Choose Strategy")
    parser.add_argument("--auto", action="store_true", help="Enable Smart Auto-Mode (AI Selects Strategy)")
    args = parser.parse_args()

    if args.test:
        logger.info("\n>>> [System] STARTING IN MOCK MODE 🟢")
        api = MockSmartConnect()
        loader = MockTokenLookup()
        loader.load_scrip_master() 
    else:
        # 1. Initialize Connection
        if args.dry_run:
            logger.info("\n>>> [System] STARTING IN DRY RUN MODE 🟡") 
            logger.info("    (Real Data, No Orders)")
        
        api = get_angel_session()
        if not api:
            logger.error(">>> [System] Failed to establish API session. Exiting.")
            return

        # 2. Initialize Data Loader
        loader = TokenLookup()
        loader.load_scrip_master()

    risk_multiplier = 1.0

    # 3. Smart Auto-Selection (The Brain)
    if args.auto:
        logger.info("\n>>> [System] 🧠 SMART AUTO-MODE ACTIVATED")
        engine = DecisionEngine(api, loader, dry_run=args.dry_run)
        selected_strategy, risk_multiplier = engine.analyze_and_select()
        
        if selected_strategy:
            logger.info(f">>> [Auto] 🤖 Brain selected: {selected_strategy} (Risk Multiplier: {risk_multiplier:.2f}x)")
            args.strategy = selected_strategy
        else:
            logger.warning(">>> [Auto] ❌ Brain could not select a strategy (Low Funds or Market Closed). Exiting.")
            return

    # 4. Initialize Strategy
    if args.strategy == "ORB":
        logger.info(f"\n>>> [Strategy] Selected: Open Range Breakout (ORB)")
        bot = ORBStrategy(api, loader, dry_run=args.dry_run)
    elif args.strategy == "MOMENTUM":
        logger.info(f"\n>>> [Strategy] Selected: Momentum (EMA Crossover) ⚡")
        bot = MomentumStrategy(api, loader, dry_run=args.dry_run)
        bot.risk_multiplier = risk_multiplier
    elif args.strategy == "VWAP":
        logger.info(f"\n>>> [Strategy] Selected: VWAP Institutional Trend (Pro Mode) 🚀")
        bot = VWAPStrategy(api, loader, dry_run=args.dry_run)
        bot.risk_multiplier = risk_multiplier
    elif args.strategy == "OHL":
        logger.info(f"\n>>> [Strategy] Selected: Open High Low (OHL) Scalp 🎯")
        bot = OHLStrategy(api, loader, dry_run=args.dry_run)
    elif args.strategy == "INSIDE_BAR":
        logger.info(f"\n>>> [Strategy] Selected: Inside Bar Breakout 🔥")
        bot = InsideBarStrategy(api, loader, dry_run=args.dry_run)
    else:
        logger.info(f"\n>>> [Strategy] Selected: 9:20 Straddle (Short) 📉")
        bot = NiftyStrategy(api, loader, dry_run=args.dry_run)
        
    bot_instance = bot 

    # 5. Setup Parameters
    logger.info("\n--- NIFTY OPTION TRADER ---")
    expiry = get_next_weekly_expiry()
    logger.info(f">>> [Setup] Target Expiry: {expiry}")
    
    # SAFEGUARD: Prevent using past expiry
    try:
        exp_date = datetime.datetime.strptime(expiry, "%d%b%Y").date()
        if exp_date < datetime.date.today():
             logger.critical(f">>> [CRITICAL ERROR] Calculated Expiry {expiry} is in the PAST! Aborting.")
             return
    except Exception as e:
        logger.warning(f">>> [Warning] Expiry Date Parsing Failed: {e}")

    # 6. Execute Strategy
    if args.strategy in ["ORB", "OHL", "INSIDE_BAR"]:
        bot.execute(expiry=expiry, action="BUY")
    elif args.strategy == "MOMENTUM":
        bot.execute(expiry=expiry)
    else:
        bot.execute(expiry=expiry, action="SELL")

if __name__ == "__main__":
    run_bot()


if __name__ == "__main__":
    run_bot()
