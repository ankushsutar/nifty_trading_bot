import os
import sys
from bot.core.angel_connect import get_angel_session
from bot.utils.logger import logger

def check_broker_readiness():
    logger.info(">>> Checking Broker (Angel One) Readiness for Option Selling...")
    
    # 1. Get API Session
    api = get_angel_session()
    if not api:
        logger.error("❌ Failed to connect to Angel One API. Check your .env credentials.")
        return

    # 2. Check Profile & Segments
    try:
        profile = api.getProfile(api.refresh_token)
        if profile and profile.get('status'):
            data = profile.get('data', {})
            name = data.get('name', 'Unknown')
            exchanges = data.get('exchanges', [])
            
            logger.info(f"✅ Connected as: {name}")
            logger.info(f"📊 Active Exchanges: {', '.join(exchanges)}")
            
            if 'nse_fo' in exchanges or 'NFO' in exchanges:
                logger.info("✅ F&O Segment (NSE_FO) is ACTIVE.")
            else:
                logger.warning("❌ F&O Segment (NSE_FO) is NOT ACTIVE. You cannot sell options without this.")
        else:
            logger.error(f"❌ Could not fetch profile: {profile.get('message', 'Unknown Error')}")
    except Exception as e:
        logger.error(f"❌ Error fetching profile: {e}")

    # 3. Check Funds / Margin
    try:
        funds = api.rmsLimit()
        if funds and funds.get('status'):
            data = funds.get('data', {})
            # Angel One 'net' or 'availablecash' is usually the field
            net_cash = float(data.get('net', 0))
            logger.info(f"💰 Total Available Margin: ₹{net_cash:,.2f}")
            
            # Rough estimation for 1 lot Nifty Selling
            # Naked Selling: ~1.2L, Hedged (IC): ~50k
            if net_cash >= 120000:
                logger.info("✅ Margin is sufficient for Naked Selling (1 lot).")
            elif net_cash >= 50000:
                logger.info("🟡 Margin is sufficient for Hedged Selling (Iron Condor/Fly) only.")
            else:
                logger.warning(f"❌ Margin (₹{net_cash:,.2f}) is too low for option selling. Need at least ₹50k for hedged positions.")
        else:
             logger.error(f"❌ Could not fetch RMS limits: {funds.get('message', 'Unknown Error')}")
    except Exception as e:
        logger.error(f"❌ Error fetching RMS limits: {e}")

if __name__ == "__main__":
    check_broker_readiness()
