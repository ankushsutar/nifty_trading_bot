import datetime
import sys
import os

# Add project root to path
sys.path.append(os.getcwd())

from bot.core.safety_checks import SafetyGatekeeper
from bot.config.settings import Config
from bot.config.instruments import get_instrument

def verify():
    gk = SafetyGatekeeper(None, dry_run=True)
    
    # 1. Check current state
    state = gk.get_market_state()
    print(f"Current Time: {datetime.datetime.now().time()}")
    print(f"Current Market State: {state}")
    
    # 2. Check Intraday Cutoffs
    nifty = get_instrument("NIFTY")
    crude = get_instrument("CRUDEOIL")
    
    Config.ACTIVE_SYMBOL = "NIFTY"
    print(f"NIFTY Cutoff: {gk.get_intraday_cutoff()}")
    
    Config.ACTIVE_SYMBOL = "CRUDEOIL"
    print(f"CRUDEOIL Cutoff: {gk.get_intraday_cutoff()}")
    
    # 3. Simulate Sizing Multipliers
    margin = 150000
    Config.SIMULATION_CAPITAL = 500000 # 5L
    
    print("\n--- Sizing Verification (CRUDEOIL) ---")
    
    states = ["WARM_UP", "AGGRESSIVE", "SLEEP", "COOL_DOWN"]
    
    # Mock get_market_state to test sizing
    original_get_state = gk.get_market_state
    
    for s in states:
        gk.get_market_state = lambda: s
        lots = gk.get_compounded_lots(margin_per_lot=margin)
        is_blackout = gk.is_blackout_period()
        print(f"State: {s:12} | Lots: {lots} | Blackout: {is_blackout}")
        
    gk.get_market_state = original_get_state

if __name__ == "__main__":
    verify()
