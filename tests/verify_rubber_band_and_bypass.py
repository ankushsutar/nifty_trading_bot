import os
import json
import shutil
import datetime
import time
import sys
from unittest.mock import MagicMock, patch

# Ensure the project root is in the path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from bot.core.safety_checks import SafetyGatekeeper
from bot.strategies.gamma_blast_strategy import GammaBlastStrategy
from bot.strategies.momentum_strategy import MomentumStrategy
from bot.core.mock_connect import MockSmartConnect, MockTokenLookup

def setup_clean_session_stats():
    stats_file = os.path.join(os.getcwd(), "data", "session_stats.json")
    if os.path.exists(stats_file):
        os.remove(stats_file)
    os.makedirs(os.path.dirname(stats_file), exist_ok=True)

def test_rubber_band_limit_bug_fix():
    print("\n--- [Test 1] Verifying Rubber Band Limit Bug Fix ---")
    setup_clean_session_stats()
    
    api = MockSmartConnect()
    gatekeeper = SafetyGatekeeper(api, dry_run=False)
    
    # Mock get_current_capital to return 100,000 initially (Starting Capital)
    gatekeeper.get_current_capital = MagicMock(return_value=100000.0)
    
    # 1. Fetch starting capital
    starting = gatekeeper.get_starting_capital()
    print(f"Starting Capital Initialized: ₹{starting:,.2f}")
    assert starting == 100000.0, f"Expected 100000.0, got {starting}"
    
    # Verify it is persisted in session_stats.json
    stats_file = os.path.join(os.getcwd(), "data", "session_stats.json")
    assert os.path.exists(stats_file), "session_stats.json was not created!"
    with open(stats_file, "r") as f:
        stats = json.load(f)
    today = datetime.date.today().isoformat()
    assert today in stats, "Today's date not in stats!"
    assert stats[today]["starting_capital"] == 100000.0, "starting_capital not persisted correctly!"
    
    # 2. Simulate margin being blocked by an open trade (available cash drops to 50,000)
    gatekeeper.get_current_capital = MagicMock(return_value=50000.0)
    
    # Fetch starting capital again — it should remain 100,000 (not rubber band to 50,000!)
    starting_again = gatekeeper.get_starting_capital()
    print(f"Starting Capital after margin blocked: ₹{starting_again:,.2f}")
    assert starting_again == 100000.0, f"Expected 100000.0, got {starting_again}"
    
    # 3. Verify max_loss limit remains fixed under check_max_daily_loss
    # Under MEDIUM tier (capital = 100k), max_daily_loss_pct = 0.06, so limit should be ₹-6,000.
    # If the rubber band bug was still active, it would calculate against 50k, making the limit ₹-3,000 (tightening).
    
    # Let's mock realized pnl to be -5,000.
    # -5,000 is greater than -6,000 (limit on 100k starting cap), so it should PASS.
    # (If rubber banded to 50k, limit would be -3,000, so -5,000 <= -3,000 would TRIGGER breach!)
    gatekeeper.get_daily_realized_pnl = MagicMock(return_value=-5000.0)
    
    passed = gatekeeper.check_max_daily_loss(active_unrealized_pnl=0.0)
    print(f"Check Max Daily Loss (PnL = -5,000): {'PASSED' if passed else 'FAILED'}")
    assert passed is True, "Gatekeeper breached daily loss limit incorrectly due to rubber band capital!"
    
    # 4. Now simulate a true daily loss limit breach (PnL = -7,000)
    gatekeeper.get_daily_realized_pnl = MagicMock(return_value=-7000.0)
    passed = gatekeeper.check_max_daily_loss(active_unrealized_pnl=0.0)
    print(f"Check Max Daily Loss (PnL = -7,000): {'PASSED' if passed else 'BREACH DETECTED (CORRECT)'}")
    assert passed is False, "Gatekeeper failed to detect a true daily loss breach of -7,000!"
    
    print("✅ TEST 1 PASSED: Rubber Band Limit Bug is completely fixed!")

def test_gamma_blast_candle_momentum_bypass_removal():
    print("\n--- [Test 2] Verifying Gamma Blast Candle Momentum Bypass Removal ---")
    
    api = MockSmartConnect()
    loader = MockTokenLookup()
    strategy = GammaBlastStrategy(api, loader, dry_run=True)
    
    # Setup mocks to force selection and simulate a squeeze / extreme trend
    import pandas as pd
    
    # Create fake candles where only 1 of the last 3 candles is bullish (should fail CE entry)
    candles_data = {
        'open': [100, 101, 102],
        'high': [102, 102, 103],
        'low': [99, 100, 101],
        'close': [99, 100, 103], # 99<100 (bearish), 100=100 (flat), 103>102 (bullish) -> 1 bullish
        'volume': [1000, 1000, 1000]
    }
    df = pd.DataFrame(candles_data)
    strategy.data_fetcher.fetch_latest_candles = MagicMock(return_value=df)
    
    # Make sure other gates pass
    strategy.gatekeeper.is_market_open = MagicMock(return_value=True)
    strategy.gatekeeper.is_blackout_period = MagicMock(return_value=False)
    strategy.gatekeeper.check_max_daily_loss = MagicMock(return_value=True)
    strategy.oi_analyzer.get_oi_velocity = MagicMock(return_value={'bias': 'BULLISH', 'pcr_velocity': 0.1, 'pcr': 1.2})
    
    # Mock EMA values such that ema9 > ema21 -> Leg = CE
    # Mock calculating ADX to be 50.0 (Extreme Trend)
    # Squeeze is also True (pcr_velocity = 0.1 > 0.05)
    # Bypassing the candle filter would enter the trade. 
    # But enforcing it should block the trade because _bull < 2.
    
    # Let's mock the market data values
    from backend.market_service import market_service
    market_service.get_market_data = MagicMock(return_value={
        'nifty': 25000,
        'analysis': {
            'regime': 'TRENDING',
            'adx': 50.0,
            'ema9': 100.0,
            'ema21': 95.0,
            'vix': 15.0
        }
    })
    
    strategy.place_entry = MagicMock()
    
    # Patch time.sleep to avoid waiting during the test
    with patch('time.sleep', return_value=None):
        # We run the strategy loop. Because of the candle confirmation failure, it should 'continue' and sleep.
        # We can stop the loop after 1 iteration by making running = False inside place_entry or having it raise a test exception.
        # But actually we can just run a single check by mocking time.sleep to raise an exception or simply set strategy.running = False in it.
        def stop_loop(*args, **kwargs):
            strategy.running = False
            
        with patch('time.sleep', side_effect=stop_loop):
            strategy.execute(expiry="27FEB26")
            
    # If the bypass was still active, it would have called place_entry.
    # If the bypass is successfully removed, it should NOT have called place_entry!
    called = strategy.place_entry.called
    print(f"Gamma Blast place_entry called: {called}")
    assert not called, "Expected place_entry NOT to be called because Candle Momentum Filter failed!"
    print("✅ TEST 2 PASSED: Gamma Blast Candle Momentum Filter is successfully enforced under Squeeze/Extreme Trend!")

def test_momentum_candle_momentum_bypass_removal():
    print("\n--- [Test 3] Verifying Momentum Strategy Candle Momentum Bypass Removal ---")
    
    api = MockSmartConnect()
    loader = MockTokenLookup()
    strategy = MomentumStrategy(api, loader, dry_run=True)
    
    import pandas as pd
    
    # Create fake candles where only 1 of the last 3 candles is bullish (should fail CE entry)
    candles_data = {
        'open': [100, 101, 102],
        'high': [102, 102, 103],
        'low': [99, 100, 101],
        'close': [99, 100, 103], # 1 bullish candle
        'volume': [1000, 1000, 1000]
    }
    df = pd.DataFrame(candles_data)
    strategy.data_fetcher.fetch_latest_candles = MagicMock(return_value=df)
    
    # Setup state
    strategy.last_analysis = {
        'regime': 'TRENDING',
        'adx': 50.0,
        'ema9': 100.0,
        'ema21': 95.0,
        'rsi': 50.0,
        'atr': 20.0,
        'sentiment': 'BULLISH',
        'pcr': 1.2
    }
    strategy.oi_data = {'bias': 'BULLISH'}
    strategy.oi_analyzer.get_oi_velocity = MagicMock(return_value={'bias': 'BULLISH', 'pcr_velocity': 0.1, 'pcr': 1.2})
    
    # Mock calculate_htf_trend to return BULLISH
    strategy.calculate_htf_trend = MagicMock(return_value="BULLISH")
    
    # With regime = TRENDING and htf_aligned = True, _min_candles = 1.
    # Wait, our _bull_count is 1. Let's make _bull_count = 0 to fail even _min_candles = 1.
    candles_data_bearish = {
        'open': [100, 101, 102],
        'high': [102, 102, 103],
        'low': [99, 100, 101],
        'close': [98, 99, 101], # 0 bullish candles
        'volume': [1000, 1000, 1000]
    }
    df_bearish = pd.DataFrame(candles_data_bearish)
    strategy.data_fetcher.fetch_latest_candles = MagicMock(return_value=df_bearish)
    
    strategy.order_manager.place_smart_limit = MagicMock()
    
    # Call enter_position
    strategy.enter_position(expiry="27FEB26", leg="CE")
    
    # If the bypass was still active, it would have set _min_candles = 0 and placed the order.
    # Since the bypass is removed, it should fail candle confirmation and NOT place the order!
    called = strategy.order_manager.place_smart_limit.called
    print(f"Momentum place_smart_limit called: {called}")
    assert not called, "Expected place_smart_limit NOT to be called because Candle Momentum Filter failed!"
    print("✅ TEST 3 PASSED: Momentum Strategy Candle Momentum Filter is successfully enforced under Squeeze/Extreme Trend!")

if __name__ == "__main__":
    try:
        test_rubber_band_limit_bug_fix()
        test_gamma_blast_candle_momentum_bypass_removal()
        test_momentum_candle_momentum_bypass_removal()
        print("\n🎉 ALL TESTS PASSED SUCCESSFULLY! Fixes are 100% verified. 🎉\n")
    except AssertionError as e:
        print(f"\n❌ ASSERTION ERROR: {e}\n")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ UNEXPECTED ERROR: {e}\n")
        sys.exit(1)
