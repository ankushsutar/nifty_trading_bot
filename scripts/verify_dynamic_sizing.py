import sys
import os
sys.path.append(os.getcwd())

from bot.core.safety_checks import SafetyGatekeeper
from bot.config.settings import Config
from unittest.mock import MagicMock

def verify_sizing():
    print("--- DYNAMIC POSITION SIZING VERIFICATION ---")
    api = MagicMock()
    # Mock rmsLimit to return 45k
    api.rmsLimit.return_value = {'status': True, 'data': {'net': '45000.0'}}
    
    gatekeeper = SafetyGatekeeper(api, dry_run=True)
    
    # Capital ~45,000 (SMALL tier)
    Config.SIMULATION_CAPITAL = 45000.0
    tier = Config.get_tier(45000)
    print(f"Detected Tier: {tier.name} | Max Lots Config: {tier.max_lots}")
    
    # Premium ~100 per share
    premium = 100.0
    margin_per_lot = premium * Config.NIFTY_LOT_SIZE # 100 * 65 = 6500
    
    # Expected base lots: floor(45000 / (6500 * 1.12)) = floor(45000 / 7280) = 6
    # But SMALL tier cap used to be 3, now it is 4.
    
    scenarios = [
        {"name": "Low Confidence (0.5x)", "multiplier": 0.5},
        {"name": "Normal Confidence (1.0x)", "multiplier": 1.0},
        {"name": "High Confidence (1.5x)", "multiplier": 1.5},
        {"name": "Ultra High Confidence (2.0x)", "multiplier": 2.0},
    ]
    
    for s in scenarios:
        lots = gatekeeper.get_compounded_lots(margin_per_lot, multiplier=s["multiplier"])
        print(f"Scenario: {s['name']:<25} | Multiplier: {s['multiplier']}x -> Lots: {lots}")

if __name__ == "__main__":
    verify_sizing()
