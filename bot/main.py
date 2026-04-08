import argparse
import sys
import signal
import datetime
import time
import os
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
from bot.strategies.gamma_blast_strategy import GammaBlastStrategy
from bot.core.decision_engine import DecisionEngine
from bot.core.order_feed import order_feed
from bot.utils.logger import logger

# Global variable for graceful shutdown
bot_instance = None
shutting_down = False

def signal_handler(sig, frame):
    """Handles Ctrl+C and Termination Signals"""
    global shutting_down
    if shutting_down:
        return
    shutting_down = True
    
    logger.info(f"\n>>> [System] Signal Received ({sig}). Initiating Graceful Shutdown...")
    
    try:
        if bot_instance:
            logger.info(">>> [System] Cleaning up Active Positions...")
            if hasattr(bot_instance, 'stop'):
                 bot_instance.stop()
            else:
                 logger.warning(">>> [Warning] Strategy does not support graceful '.stop()'. Checking if active...")
    except Exception as e:
        logger.error(f">>> [System] Shutdown error: {e}")
    finally:
        # Use os._exit to bypass messy library-level atexit tracebacks (like pymongo)
        os._exit(0)

def run_bot():
    global bot_instance
    
    # 0. Start Order Feed (Real-Time Status)
    from bot.core.order_feed import order_feed
    order_feed.start()
    
    # Register Signals
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Move parser before the loop logic
    parser = argparse.ArgumentParser(description="Nifty Options Trading Bot")
    parser.add_argument("--test", action="store_true", help="Run in Mock Mode for local testing")
    parser.add_argument("--dry-run", action="store_true", help="Run with Real Data but DO NOT place orders")
    parser.add_argument("--strategy", type=str, default="STRADDLE", choices=["STRADDLE", "ORB", "MOMENTUM", "VWAP", "OHL", "INSIDE_BAR", "GAMMA_BLAST"], help="Choose Strategy")
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

        # 2a. Broker Position Reconciliation (Startup Sync)
        # Closes any DB trades that were manually exited on the broker platform.
        try:
            from bot.core.trade_repo import trade_repo
            trade_repo.cleanup_stale_trades()  # Close overnight leftovers
            trade_repo.reconcile_with_broker(api)  # Sync manually closed trades
        except Exception as e:
            logger.warning(f">>> [System] Startup reconciliation warning: {e}")

    risk_multiplier = 1.0

    # 3. Smart Auto-Selection (The Brain)
    # Check for Orphaned Trades first for auto-resumption
    from bot.core.trade_repo import trade_repo  # Guaranteed import (test + live paths)
    mode = "PAPER" if args.dry_run else "LIVE"
    orphaned_trade = trade_repo.get_active_trade(mode=mode)
    
    if orphaned_trade:
        strategy_name = orphaned_trade.get('strategy', 'STRADDLE')
        logger.info(f"\n>>> [System] ♻️ ORPHANED TRADE DETECTED: {orphaned_trade['symbol']} ({strategy_name})")
        logger.info(f"    Resuming monitoring for Trade #{orphaned_trade['id']}...")
        args.strategy = strategy_name
        # Skip auto-selection since we have a task at hand
        args.auto = False 
    
    elif args.auto:
        logger.info("\n>>> [System] 🧠 SMART AUTO-MODE ACTIVATED")
        engine = DecisionEngine(api, loader, dry_run=args.dry_run)
        
        while True:
            selected_strategy, risk_multiplier = engine.analyze_and_select()
            
            if selected_strategy:
                logger.info(f">>> [Auto] 🤖 Brain selected: {selected_strategy} (Risk Multiplier: {risk_multiplier:.2f}x)")
                args.strategy = selected_strategy
                break
            else:
                # Check for fatal exits (like daily limit) inside DecisionEngine, 
                # but if it just says "Market Conditions not met", we loop.
                # If Market is literally CLOSED according to logic, we should probably exit.
                from bot.core.safety_checks import SafetyGatekeeper
                gate = SafetyGatekeeper(api, dry_run=args.dry_run)
                if not gate.is_market_open():
                    logger.warning(">>> [Auto] ❌ Market is Closed. Exiting.")
                    return

                logger.info(">>> [Auto] 💤 No A+ setup found. Retrying in 60 seconds...")
                time.sleep(60)

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
    elif args.strategy == "GAMMA_BLAST":
        logger.info(f"\n>>> [Strategy] Selected: Gamma Blast (OTM Momentum) 🚀💎")
        bot = GammaBlastStrategy(api, loader, dry_run=args.dry_run)
        bot.risk_multiplier = risk_multiplier
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
    elif args.strategy in ["MOMENTUM", "GAMMA_BLAST"]:
        bot.execute(expiry=expiry)
    else:
        bot.execute(expiry=expiry, action="SELL")

    # 7. Record trade for daily limit tracking (only in auto mode)
    # This increments the DecisionEngine's daily counter so the 2-trade cap works.
    if args.auto and 'engine' in locals():
        engine.record_trade()
        logger.info(">>> [System] Trade recorded for daily limit tracking.")

if __name__ == "__main__":
    run_bot()
