
import sys
import os
import datetime
import pandas as pd
from unittest.mock import MagicMock

# Add project root to path
sys.path.append(os.getcwd())

from bot.strategies.momentum_strategy import MomentumStrategy
from bot.utils.trade_journal import TradeJournal

def verify_ai_logging():
    print(">>> Verifying AI Data Collection...")
    
    # 1. Mock API
    mock_api = MagicMock()
    mock_token_loader = MagicMock()
    mock_token_loader.get_token.return_value = ("123456", "NIFTY20000CE")
    
    # 2. Init Strategy (Dry Run)
    strategy = MomentumStrategy(mock_api, mock_token_loader, dry_run=True)
    
    # 3. Inject Mock Analysis Data (Simulating what 'analyze_market_trend' does)
    strategy.last_analysis = {
        "ema9": 22100, "ema21": 22050, "rsi": 65.5, 
        "htf_trend": "BULLISH", "adx": 30.2,
        "atr": 45.5, "regime": "TRENDING",
        "bbw": 0.0025, "pcr": 1.15, "sentiment": "BULLISH"
    }
    
    # Mock Nifty LTP for entry calculation
    # strategy.get_nifty_ltp = MagicMock(return_value=22150) # Use this if mocking method
    # Or mock DataFetcher if used inside
    
    # Mock API placeOrder response
    mock_api.placeOrder.return_value = "test_order_id"
    
    # Mock ltpData response for close_position
    mock_api.ltpData.return_value = {
        "status": True,
        "data": {"ltp": 120.0}
    }
    
    # 4. Trigger Entry (Simulate Trade)
    # This calls trade_repo.save_trade -> which usually logs to DB (we care about CSV journal mostly for AI now)
    # Wait, TradeJournal.log_trade is called inside 'close_position' usually? 
    # Let's check MomentumStrategy. 
    # Ah, TradeJournal.log_trade IS called in close_position.
    # But does it log Entry info? Yes.
    # Does it log independent entry rows? No, it logs completed trades.
    
    # So we must simulate a CLOSE to see the log.
    
    # Set active position manually to simulate an open trade
    strategy.active_position = {
        'leg': 'CE', 'symbol': 'NIFTY20000CE', 'qty': 50, 'token': '123456',
        'entry_price': 100.0, 'sl_price': 90.0,
        'context': {
            'entry_ema9': 22100, 'entry_ema21': 22050, 'entry_rsi': 65.5, 'entry_adx': 30.2,
            'entry_atr': 45.5, 'regime': 'TRENDING', 'htf_trend': 'BULLISH',
            'entry_bbw': 0.0025, 'oi_pcr': 1.15, 'oi_sentiment': 'BULLISH'
        }
    }
    
    # 5. Trigger Close
    print(">>> Triggering Close Position...")
    strategy.close_position("TEST_AI_LOGGING")
    
    # 6. Verify CSV
    print(">>> Checking CSV...")
    df = pd.read_csv("logs/trade_journal.csv")
    last_row = df.iloc[-1]
    
    print("\n--- Last Logged Trade ---")
    print(last_row)
    
    # Check new fields
    assert last_row['entry_atr'] == 45.5, f"ATR Mismatch: {last_row['entry_atr']}"
    assert last_row['entry_bbw'] == 0.0025, f"BBW Mismatch: {last_row['entry_bbw']}"
    assert last_row['oi_pcr'] == 1.15, f"PCR Mismatch: {last_row['oi_pcr']}"
    
    print("\n✅ AI Data Collection Verified Successfully!")

if __name__ == "__main__":
    try:
        verify_ai_logging()
    except Exception as e:
        print(f"\n❌ Verification Failed: {e}")
        import traceback
        traceback.print_exc()
