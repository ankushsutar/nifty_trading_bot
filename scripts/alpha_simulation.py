import sys
import os
from unittest.mock import MagicMock
# Add bot to path
sys.path.append(os.getcwd())

from bot.core.decision_engine import DecisionEngine
from bot.core.safety_checks import SafetyGatekeeper
from bot.config.settings import Config
from bot.utils.logger import logger

def simulate_alpha_behavior(capital):
    logger.info(f"--- 🧪 SIMULATION: Small Capital Alpha (Base: ₹{capital}) ---")
    
    # Mock API
    mock_api = MagicMock()
    # Mock capital check
    mock_api.rmsLimit.return_value = {
        'status': True,
        'data': {'net': str(capital)}
    }
    
    # 1. Test Compounding Logic
    gatekeeper = SafetyGatekeeper(mock_api, dry_run=False) # dry_run=False to use the mock_api logic
    margin_per_lot = 5000
    lots = gatekeeper.get_compounded_lots(margin_per_lot)
    
    logger.info(f"[Test 1] Lot Calculation: For ₹{capital}, bot will trade {lots} lots.")
    
    # 2. Test Cost Viability
    premium = 40
    qty = lots * Config.NIFTY_LOT_SIZE
    is_viable = gatekeeper.check_trade_viability(premium, qty)
    logger.info(f"[Test 2] Viability Check: Option at ₹{premium} with {lots} lots. Viable? {is_viable}")
    
    # 3. Test Decision Engine "Sniper Mode"
    engine = DecisionEngine(mock_api, MagicMock())
    engine.gatekeeper = gatekeeper # Inject our mock gatekeeper
    
    # Mock Market Data for a "B+" Setup (Not A+)
    from backend.market_service import market_service
    market_service.get_market_data = MagicMock(return_value={
        'analysis': {'regime': 'TRENDING', 'trend': 'BULLISH', 'adx': 20},
        'oi_data': {'bias': 'NEUTRAL'}
    })
    
    strategy, mult = engine.analyze_and_select()
    logger.info(f"[Test 3] Sniper Mode: With ADX=20 and NEUTRAL bias, Selected: {strategy}")

    # Mock Market Data for an "A+" Setup
    market_service.get_market_data = MagicMock(return_value={
        'analysis': {'regime': 'TRENDING', 'trend': 'BULLISH', 'adx': 30},
        'oi_data': {'bias': 'BULLISH'}
    })
    
    strategy, mult = engine.analyze_and_select()
    logger.info(f"[Test 4] Sniper Mode: With ADX=30 and BULLISH bias, Selected: {strategy}")

if __name__ == "__main__":
    # Test for User's exact ₹8,000
    simulate_alpha_behavior(8000)
    print("\n")
    # Test for the scale-up point ₹12,000
    simulate_alpha_behavior(12500)
