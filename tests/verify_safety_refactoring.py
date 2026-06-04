import sys
import os
import unittest
import datetime
import time
from unittest.mock import MagicMock, patch

# Mock pymongo before importing anything that uses trade_repo
sys.modules['pymongo'] = MagicMock()

# Ensure project root is in path
sys.path.append(os.getcwd())

from bot.core.trade_repo import TradeRepository
from bot.core.safety_checks import SafetyGatekeeper

class TestSafetyRefactoring(unittest.TestCase):
    def setUp(self):
        # Setup mock trade repository client
        self.mock_collection = MagicMock()
        self.trade_repo = TradeRepository()
        self.trade_repo.collection = self.mock_collection
        self.trade_repo.client = MagicMock()
        
        # Patch the singleton trade_repo
        self.repo_patcher = patch('bot.core.trade_repo.trade_repo', self.trade_repo)
        self.repo_patcher.start()

        # Clean up trade journal file if it exists
        self.journal_file = os.path.join(os.getcwd(), "logs", "trade_journal.csv")
        if os.path.exists(self.journal_file):
            try:
                os.remove(self.journal_file)
            except Exception:
                pass

    def tearDown(self):
        self.repo_patcher.stop()
        if os.path.exists(self.journal_file):
            try:
                os.remove(self.journal_file)
            except Exception:
                pass

    def test_timezone_robust_today_trades(self):
        """Verify get_today_trades uses timezone-robust IST start of day."""
        self.mock_collection.find.return_value.sort.return_value = []
        
        # Run method
        self.trade_repo.get_today_trades(mode="LIVE")
        
        # Verify query checks created_at $gte
        self.mock_collection.find.assert_called_once()
        args, kwargs = self.mock_collection.find.call_args
        query = args[0]
        self.assertIn("created_at", query)
        self.assertIn("$gte", query["created_at"])
        
        # Check start day timestamp is naive datetime (MongoDB compatible)
        today_start_dt = query["created_at"]["$gte"]
        self.assertIsInstance(today_start_dt, datetime.datetime)
        self.assertIsNone(today_start_dt.tzinfo)

    def test_centralized_journaling_close_trade(self):
        """Verify close_trade triggers TradeJournal.log_trade automatically."""
        mock_trade = {
            "id": 404,
            "symbol": "NIFTY27FEB2622000CE",
            "qty": 50,
            "entry_price": 100.0,
            "pnl": 0.0,
            "status": "OPEN",
            "strategy": "MOMENTUM"
        }
        
        updated_trade = {
            "id": 404,
            "symbol": "NIFTY27FEB2622000CE",
            "qty": 50,
            "entry_price": 100.0,
            "exit_price": 120.0,
            "pnl": 1000.0,
            "status": "CLOSED",
            "strategy": "MOMENTUM",
            "exit_reason": "TARGET_HIT"
        }
        
        # Mock database actions
        self.mock_collection.find_one.side_effect = [mock_trade, updated_trade]
        
        # Close trade which should trigger journaling
        self.trade_repo.close_trade(trade_id=404, exit_price=120.0, exit_reason="TARGET_HIT")
        
        # Verify the CSV log file was generated or updated
        self.assertTrue(os.path.exists(self.journal_file), "trade_journal.csv was not created by close_trade")
        
        # Read the file content and check values
        with open(self.journal_file, "r") as f:
            content = f.read()
            self.assertIn("MOMENTUM", content)
            self.assertIn("NIFTY27FEB2622000CE", content)
            self.assertIn("1000.0", content)
            self.assertIn("TARGET_HIT", content)

    def test_broker_realized_pnl_calculation(self):
        """Verify get_broker_realized_pnl correctly computes realized PnL from Angel One positions."""
        mock_api = MagicMock()
        mock_api.position.return_value = {
            "status": True,
            "data": [
                {
                    "tradingsymbol": "NIFTY27FEB2622000CE",
                    "buyqty": "100",
                    "sellqty": "100",
                    "buyavgprice": "100.0",
                    "sellavgprice": "120.0",
                    "realisedprice": "2000.0"
                },
                {
                    "tradingsymbol": "NIFTY27FEB2622000PE",
                    "buyqty": "50",
                    "sellqty": "50",
                    "buyavgprice": "150.0",
                    "sellavgprice": "130.0",
                    "realisedprice": "-1000.0"
                }
            ]
        }
        
        gatekeeper = SafetyGatekeeper(mock_api, dry_run=False)
        pnl = gatekeeper.get_broker_realized_pnl()
        
        # Total expected P&L = 2000 - 1000 = 1000.0
        self.assertEqual(pnl, 1000.0)

    def test_instrument_cooldown_detection(self):
        """Verify check_instrument_cooldown handles descending sort index and closed_at correctly."""
        # Setup mock trades sorted descending (index 0 is the most recent trade)
        recent_lost_trade = {
            "id": 502,
            "symbol": "COOLDOWN_TEST_CE",
            "status": "CLOSED",
            "pnl": -500.0,
            "closed_at": datetime.datetime.now() - datetime.timedelta(minutes=30) # 30 mins ago
        }
        older_won_trade = {
            "id": 501,
            "symbol": "COOLDOWN_TEST_CE",
            "status": "CLOSED",
            "pnl": 1000.0,
            "closed_at": datetime.datetime.now() - datetime.timedelta(hours=2) # 2 hours ago
        }
        
        self.mock_collection.find.return_value.sort.return_value = [recent_lost_trade, older_won_trade]
        
        gatekeeper = SafetyGatekeeper(MagicMock(), dry_run=False)
        allowed = gatekeeper.check_instrument_cooldown("COOLDOWN_TEST_CE")
        
        # Cooldown should block re-entry (returns False) because the most recent trade was a loss
        self.assertFalse(allowed)

        # Now test with a winning recent trade
        recent_won_trade = {
            "id": 503,
            "symbol": "COOLDOWN_TEST_CE",
            "status": "CLOSED",
            "pnl": 500.0,
            "closed_at": datetime.datetime.now() - datetime.timedelta(minutes=10) # 10 mins ago
        }
        self.mock_collection.find.return_value.sort.return_value = [recent_won_trade, recent_lost_trade, older_won_trade]
        allowed_after_win = gatekeeper.check_instrument_cooldown("COOLDOWN_TEST_CE")
        
        # Should allow trading because the latest trade won
        self.assertTrue(allowed_after_win)

    def test_cancel_order_retry(self):
        """Verify OrderManager.cancel_order retries up to 3 times on failure."""
        from bot.core.order_manager import OrderManager
        mock_api = MagicMock()
        mock_api.cancelOrder.side_effect = [
            {"status": False, "message": "API error"},
            {"status": False, "message": "API error"},
            {"status": True}
        ]
        
        with patch('bot.core.order_manager.rate_limiter') as mock_limiter, \
             patch('bot.core.order_manager.is_kill_switch_active', return_value=False), \
             patch('time.sleep') as mock_sleep:
            order_manager = OrderManager(mock_api, dry_run=False)
            order_manager.live_trade_enabled = True
            
            result = order_manager.cancel_order("12345", variety="STOPLOSS")
            
            self.assertTrue(result)
            self.assertEqual(mock_api.cancelOrder.call_count, 3)

    def test_double_exit_protection_momentum(self):
        """Verify MomentumStrategy aborts exit if SL is already filled."""
        from bot.strategies.momentum_strategy import MomentumStrategy
        
        mock_api = MagicMock()
        strategy = MomentumStrategy(mock_api, MagicMock(), dry_run=False)
        strategy.active_position = {
            "id": 101,
            "symbol": "NIFTY27FEB2622000CE",
            "token": "12345",
            "qty": 50,
            "entry_price": 100.0,
            "sl_price": 80.0,
            "sl_order_id": "SL_12345",
            "strategy": "MOMENTUM"
        }
        
        # Mock cancel_order to fail (False)
        strategy.order_manager.cancel_order = MagicMock(return_value=False)
        
        # Mock get_order_status to return FILLED
        strategy.order_manager.get_order_status = MagicMock(return_value={"status": "FILLED", "price": 79.5})
        
        # Mock place_order (it should NOT be called!)
        strategy.order_manager.place_order = MagicMock()
        
        # Mock trade_repo.close_trade
        with patch('bot.strategies.momentum_strategy.trade_repo') as mock_repo:
            strategy.close_position(reason="TEST_REASON")
            
            # Verify close_trade was called with SL_HIT and correct exit price
            mock_repo.close_trade.assert_called_once_with(
                trade_id=101, exit_price=79.5, exit_reason="SL_HIT"
            )
            # Verify market order exit was NOT placed
            strategy.order_manager.place_order.assert_not_called()
            # Verify active position is cleared
            self.assertIsNone(strategy.active_position)

    def test_double_exit_protection_gammablast(self):
        """Verify GammaBlastStrategy aborts exit if SL is already filled."""
        from bot.strategies.gamma_blast_strategy import GammaBlastStrategy
        
        mock_api = MagicMock()
        strategy = GammaBlastStrategy(mock_api, MagicMock(), dry_run=False)
        strategy.active_position = {
            "id": 202,
            "symbol": "NIFTY27FEB2622000PE",
            "token": "67890",
            "qty": 50,
            "entry_price": 120.0,
            "sl_price": 100.0,
            "sl_order_id": "SL_67890"
        }
        
        # Mock cancel_order to fail (False)
        strategy.order_manager.cancel_order = MagicMock(return_value=False)
        
        # Mock get_order_status to return FILLED
        strategy.order_manager.get_order_status = MagicMock(return_value={"status": "FILLED", "price": 99.0})
        
        # Mock place_order (should not be called)
        strategy.order_manager.place_order = MagicMock()
        
        # Mock data_fetcher.get_ltp
        strategy.data_fetcher.get_ltp = MagicMock(return_value=99.0)
        
        # Mock trade_repo.close_trade
        with patch('bot.strategies.gamma_blast_strategy.trade_repo') as mock_repo:
            result = strategy.exit_market(
                token="67890",
                symbol="NIFTY27FEB2622000PE",
                qty=50,
                reason="TEST_REASON",
                trade_id=202,
                sl_oid="SL_67890"
            )
            
            self.assertTrue(result)
            # Verify close_trade was called with SL_HIT
            mock_repo.close_trade.assert_called_once_with(
                trade_id=202, exit_price=99.0, exit_reason="SL_HIT"
            )
            # Verify market order exit was NOT placed
            strategy.order_manager.place_order.assert_not_called()

    def test_market_feed_watchdog(self):
        """Verify MarketFeedService triggers connection watchdog on silence during market hours."""
        from bot.core.market_feed import MarketFeedService
        
        feed = MarketFeedService()
        feed.is_connected = True
        feed.running = True
        feed.last_tick_time = time.time() - 40 # 40s ago (stale)
        feed.sws = MagicMock()
        
        class MockDateTimeClass(datetime.datetime):
            @classmethod
            def utcnow(cls):
                return datetime.datetime(2026, 6, 4, 4, 30) # 4:30 AM UTC = 10:00 AM IST
                
        with patch('bot.core.market_feed.datetime.datetime', MockDateTimeClass), \
             patch('time.sleep') as mock_sleep:
            
            feed._manage_dynamic_subscriptions()
            
            # Watchdog should set is_connected to False and close connection
            self.assertFalse(feed.is_connected)
            feed.sws.close_connection.assert_called_once()

    def test_market_feed_rest_fallback(self):
        """Verify dynamic subscriptions fallback to REST when LTP is not in cache."""
        from bot.core.market_feed import MarketFeedService
        
        feed = MarketFeedService()
        feed.is_connected = True
        feed.running = True
        feed.last_tick_time = time.time() # Fresh tick time (no watchdog)
        feed.latest_data.clear() # No cached LTP
        
        mock_api = MagicMock()
        mock_api.ltpData.return_value = {
            "status": True,
            "data": {"ltp": 22000.50}
        }
        
        # Patch get_angel_session to return mock_api
        with patch('bot.core.market_feed.get_angel_session', return_value=mock_api), \
             patch('bot.core.market_feed.get_next_weekly_expiry', return_value="11JUN26"), \
             patch.object(feed.token_lookup, 'get_option_bucket', return_value={}) as mock_bucket, \
             patch('time.sleep', side_effect=InterruptedError("stop")):
             
            try:
                feed._manage_dynamic_subscriptions()
            except InterruptedError:
                pass
            
            # Verify REST API was called
            mock_api.ltpData.assert_called_once_with("NSE", "Nifty 50", "99926000")

if __name__ == "__main__":
    unittest.main()
