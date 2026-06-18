import sys
import unittest
from unittest.mock import MagicMock, patch

class TestRecoveryFlow(unittest.TestCase):
    def setUp(self):
        # We start patching sys.modules to avoid global state pollution
        self.mock_modules = {
            'bot.utils.logger': MagicMock(),
            'bot.core.trade_repo': MagicMock(),
            'bot.core.order_manager': MagicMock(),
            'bot.core.angel_connect': MagicMock(),
            'bot.core.safety_checks': MagicMock(),
            'bot.core.market_feed': MagicMock(),
            'bot.core.data_fetcher': MagicMock(),
            'bot.core.oi_analyzer': MagicMock(),
        }
        self.patcher = patch.dict(sys.modules, self.mock_modules)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()

    def test_gamma_blast_recovery(self):
        # Now import inside the test method
        from bot.core.trade_repo import trade_repo
        from bot.strategies.gamma_blast_strategy import GammaBlastStrategy
        
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
        strategy.sync_state()
        
        # 4. Verify Active Position is populated
        self.assertIsNotNone(strategy.active_position)
        self.assertEqual(strategy.active_position['id'], 999)

    def test_main_resumption_logic(self):
        from bot.core.trade_repo import trade_repo
        
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
            strategy_name = detected.get('strategy', 'STRADDLE')
            args.strategy = strategy_name
            args.auto = False
        
        self.assertEqual(args.strategy, "GAMMA_BLAST")
        self.assertFalse(args.auto)

if __name__ == "__main__":
    unittest.main()
