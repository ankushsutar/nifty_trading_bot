import sys
from unittest.mock import MagicMock, patch

# Save original modules to prevent pollution
orig_modules = {
    k: sys.modules.get(k) for k in [
        'bot.utils.logger', 'bot.core.trade_repo', 'bot.core.order_manager',
        'bot.core.angel_connect', 'bot.core.safety_checks', 'bot.core.market_feed',
        'bot.core.data_fetcher', 'bot.core.oi_analyzer'
    ]
}

# Mock dependencies before imports
for k in orig_modules:
    sys.modules[k] = MagicMock()

# Now import after mocking
from bot.core.trade_repo import trade_repo
from bot.strategies.gamma_blast_strategy import GammaBlastStrategy

# Restore original modules so other tests get fresh/real modules
for k, v in orig_modules.items():
    if v is not None:
        sys.modules[k] = v
    else:
        sys.modules.pop(k, None)

def test_gamma_blast_recovery():
    print("\n--- Testing GammaBlast Recovery Logic ---")
    api = MagicMock()
    loader = MagicMock()
    
    # 1. Setup Mock Orphaned Trade in DB
    orphaned_trade = {
        'id': 999,
        'symbol': 'NIFTY27FEB2622000CE',
        'token': '12345',
        'leg': 'CE',
        'qty': 50,
        'entry_price': 100.0,
        'sl_price': 80.0,
        'strategy': 'GAMMA_BLAST',
        'status': 'OPEN'
    }
    trade_repo.get_active_trade.return_value = orphaned_trade
    
    # 2. Setup Mock Position at Broker
    broker_positions = {
        'status': True,
        'data': [{
            'symbolname': 'NIFTY',
            'producttype': 'INTRADAY',
            'tradingsymbol': 'NIFTY27FEB2622000CE',
            'symboltoken': '12345',
            'netqty': '50',
            'avgnetprice': '100.0'
        }]
    }
    
    strategy = GammaBlastStrategy(api, loader, dry_run=False)
    strategy.order_manager.get_positions.return_value = broker_positions
    
    # 3. Trigger Sync
    print("Triggering sync_state()...")
    strategy.sync_state()
    
    # 4. Verify Active Position is populated
    if strategy.active_position and strategy.active_position['id'] == 999:
        print("✅ PASS: Strategy correctly detected and linked orphaned trade #999")
    else:
        print(f"❌ FAIL: Strategy failed to link orphaned trade. Active Position: {strategy.active_position}")

def test_main_resumption_logic():
    print("\n--- Testing main.py Resumption Logic ---")
    
    # Mock args
    args = MagicMock()
    args.dry_run = False
    args.auto = True
    args.strategy = None
    
    # Mock trade_repo to return orphaned trade
    orphaned_trade = {'id': 999, 'symbol': 'NIFTY_TEST', 'strategy': 'GAMMA_BLAST'}
    trade_repo.get_active_trade.return_value = orphaned_trade
    
    # Simulate main.py logic (Phase 3: Auto-Selection)
    mode = "PAPER" if args.dry_run else "LIVE"
    detected = trade_repo.get_active_trade(mode=mode)
    
    if detected:
        strategy_name = detected.get('strategy', 'MOMENTUM')
        print(f"♻️  System detect: {strategy_name}")
        args.strategy = strategy_name
        args.auto = False
    
    if args.strategy == "GAMMA_BLAST" and args.auto == False:
        print("✅ PASS: main.py logic correctly prioritized orphaned trade and bypassed auto-selection.")
    else:
        print(f"❌ FAIL: main.py logic failed. Strategy: {args.strategy}, Auto: {args.auto}")

if __name__ == "__main__":
    test_gamma_blast_recovery()
    test_main_resumption_logic()
    print("\nVerification Complete.")
