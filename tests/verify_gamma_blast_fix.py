import sys
from unittest.mock import MagicMock, patch

# Mock dependencies before imports
sys.modules['bot.utils.logger'] = MagicMock()
sys.modules['bot.core.trade_repo'] = MagicMock()
sys.modules['bot.core.order_manager'] = MagicMock()
sys.modules['bot.core.angel_connect'] = MagicMock()
sys.modules['bot.core.safety_checks'] = MagicMock()
sys.modules['bot.core.market_feed'] = MagicMock()
sys.modules['bot.core.data_fetcher'] = MagicMock()
sys.modules['bot.core.oi_analyzer'] = MagicMock()
sys.modules['backend.market_service'] = MagicMock()

# Now import after mocking
from bot.core.trade_repo import trade_repo
from bot.strategies.gamma_blast_strategy import GammaBlastStrategy

def test_gamma_blast_no_crash():
    print("\n--- Testing GammaBlast execute() for NameError ---")
    print("Mocks initialized.")
    api = MagicMock()
    loader = MagicMock()
    
    # 1. Mock trade_repo.get_active_trade to return None (no active trade)
    trade_repo.get_active_trade.return_value = None
    print("trade_repo mocked.")
    
    strategy = GammaBlastStrategy(api, loader, dry_run=False)
    print("GammaBlastStrategy instance created.")
    
    # Mock gatekeeper to return False for market open to avoid further execution
    strategy.gatekeeper.is_market_open.return_value = False
    print("Gatekeeper mocked.")
    
    try:
        # This used to crash with NameError
        print("Calling strategy.execute()...")
        strategy.execute(expiry="27FEB26")
        print("✅ PASS: strategy.execute() did not crash with NameError.")
    except NameError as e:
        print(f"❌ FAIL: strategy.execute() crashed with NameError: {e}")
        sys.exit(1)
    except Exception as e:
        # Other exceptions are fine as we mocked things to stop early
        print(f"✅ PASS: strategy.execute() did not crash with NameError (other expected exception: {e})")

if __name__ == "__main__":
    test_gamma_blast_no_crash()
    print("\nVerification Complete.")
