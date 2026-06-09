import unittest
from unittest.mock import MagicMock, patch
import datetime

# Import trade_repo early so pymongo resolves its imports with the un-patched global datetime module
from bot.core.trade_repo import trade_repo
from bot.strategies.straddle_scalp_strategy import StraddleScalpStrategy


class TestIronCondorStrategy(unittest.TestCase):

    def setUp(self):
        self.api = MagicMock()
        self.token_loader = MagicMock()
        
        # Configure token loader mock responses
        def get_token_mock(exchange, expiry, strike, opt_type):
            return f"token_{strike}_{opt_type}", f"NIFTY{expiry}{strike}{opt_type}"
        self.token_loader.get_token.side_effect = get_token_mock

        # Create strategy instance in dry_run=True (to avoid actual calls)
        self.strategy = StraddleScalpStrategy(self.api, self.token_loader, dry_run=True)
        
        # Mock dependencies to prevent side effects
        self.strategy.gatekeeper = MagicMock()
        self.strategy.order_manager = MagicMock()
        self.strategy.data_fetcher = MagicMock()
        
        # Setup basic mock gates
        self.strategy.gatekeeper.is_market_open.return_value = True
        self.strategy.gatekeeper.check_max_daily_loss.return_value = True
        self.strategy.gatekeeper.check_instrument_cooldown.return_value = True
        self.strategy.gatekeeper.check_trade_margin.return_value = True
        self.strategy.gatekeeper.get_compounded_lots.return_value = 1

    @patch('bot.strategies.straddle_scalp_strategy.trade_repo')
    @patch('bot.strategies.straddle_scalp_strategy.notifier')
    def test_happy_path_entry(self, mock_notifier, mock_trade_repo):
        """Test successful sequential entry into the Iron Condor basket."""
        # Mock Nifty spot LTP
        self.strategy.data_fetcher.get_ltp.side_effect = lambda token, exchange=None: {
            "99926000": 23520.0, # Spot
            "token_23700_CE": 15.0, # LC
            "token_23600_CE": 35.0, # SC
            "token_23400_PE": 40.0, # SP
            "token_23300_PE": 18.0, # LP
        }.get(token, 20.0)

        # Mock order manager placing limit orders
        self.strategy.order_manager.place_smart_limit.side_effect = lambda symbol, token, qty, price, side, strategy_name, mode: f"oid_{symbol}_{side}"
        self.strategy.order_manager.place_sl_order.side_effect = lambda symbol, token, qty, price, leg, transaction_type: f"sl_oid_{symbol}"

        # Mock trade saving
        mock_trade_repo.save_trade.return_value = "db_trade_id"

        # Execute entry
        self.strategy._enter_condor(expiry="25JUN2026")

        # Verify sequential ordering: place_smart_limit should be called 4 times total
        self.assertEqual(self.strategy.order_manager.place_smart_limit.call_count, 4)
        
        # Verify state positions are stored
        self.assertIsNotNone(self.strategy.lc_position)
        self.assertIsNotNone(self.strategy.sc_position)
        self.assertIsNotNone(self.strategy.sp_position)
        self.assertIsNotNone(self.strategy.lp_position)

        # Verify disaster stop loss orders were placed on the short legs
        self.assertEqual(self.strategy.order_manager.place_sl_order.call_count, 2)
        # SC Short SL price should be 1.5x entry of 35.0 = 52.5
        self.strategy.order_manager.place_sl_order.assert_any_call(
            "NIFTY25JUN202623600CE", "token_23600_CE", 65, 52.5, "SC", transaction_type="BUY"
        )

    @patch('bot.strategies.straddle_scalp_strategy.trade_repo')
    @patch('bot.strategies.straddle_scalp_strategy.notifier')
    def test_step1_rollback_longs_failed(self, mock_notifier, mock_trade_repo):
        """Verify that if step 1 (Long hedges) fails to fill, the strategy rolls back cleanly."""
        self.strategy.data_fetcher.get_ltp.side_effect = lambda token, exchange=None: {
            "99926000": 23520.0,
            "token_23700_CE": 15.0,
            "token_23600_CE": 35.0,
            "token_23400_PE": 40.0,
            "token_23300_PE": 18.0,
        }.get(token, 20.0)

        # Mock order placement OIDs
        self.strategy.order_manager.place_smart_limit.side_effect = lambda symbol, token, qty, price, side, strategy_name, mode: f"oid_{symbol}_{side}"

        # Mock one of the long hedges to fail (TIMEOUT)
        self.strategy._wait_fill = lambda order_id, fallback: {
            "oid_NIFTY25JUN202623700CE_BUY": {"status": "FILLED", "price": 15.0},
            "oid_NIFTY25JUN202623300PE_BUY": {"status": "TIMEOUT", "price": 18.0}
        }.get(order_id, {"status": "TIMEOUT", "price": 0.0})

        # Run entry
        sc_symbol = "NIFTY25JUN202623600CE"
        sp_symbol = "NIFTY25JUN202623400PE"
        self.strategy._enter_condor(expiry="25JUN2026")

        # Verify that we cancelled both order IDs
        self.strategy.order_manager.cancel_order.assert_any_call("oid_NIFTY25JUN202623700CE_BUY")
        self.strategy.order_manager.cancel_order.assert_any_call("oid_NIFTY25JUN202623300PE_BUY")

        # Verify that we sold back the filled Long Call (LC)
        self.strategy.order_manager.place_smart_limit.assert_any_call(
            "NIFTY25JUN202623700CE", "token_23700_CE", 65, 13.5, "SELL", "STRADDLE_SCALP", "PAPER"
        )
        
        # Verify that we did not place any short premium trades (SC or SP)
        for call_args in self.strategy.order_manager.place_smart_limit.call_args_list:
            symbol = call_args[0][0]
            side = call_args[0][4]
            if symbol in [sc_symbol, sp_symbol]:
                self.assertNotEqual(side, "SELL")

    @patch('bot.strategies.straddle_scalp_strategy.trade_repo')
    @patch('bot.strategies.straddle_scalp_strategy.notifier')
    def test_step2_rollback_shorts_failed(self, mock_notifier, mock_trade_repo):
        """Verify that if step 2 (Shorts) fails to fill, we roll back filled shorts and exit longs."""
        self.strategy.data_fetcher.get_ltp.side_effect = lambda token, exchange=None: {
            "99926000": 23520.0,
            "token_23700_CE": 15.0,
            "token_23600_CE": 35.0,
            "token_23400_PE": 40.0,
            "token_23300_PE": 18.0,
        }.get(token, 20.0)

        self.strategy.order_manager.place_smart_limit.side_effect = lambda symbol, token, qty, price, side, strategy_name, mode: f"oid_{symbol}_{side}"

        # Hedges fill, but one short fails
        self.strategy._wait_fill = lambda order_id, fallback: {
            "oid_NIFTY25JUN202623700CE_BUY": {"status": "FILLED", "price": 15.0},
            "oid_NIFTY25JUN202623300PE_BUY": {"status": "FILLED", "price": 18.0},
            "oid_NIFTY25JUN202623600CE_SELL": {"status": "FILLED", "price": 35.0},
            "oid_NIFTY25JUN202623400PE_SELL": {"status": "TIMEOUT", "price": 40.0}
        }.get(order_id, {"status": "TIMEOUT", "price": 0.0})

        # Run entry
        self.strategy._enter_condor(expiry="25JUN2026")

        # Verify that we cancelled short order IDs
        self.strategy.order_manager.cancel_order.assert_any_call("oid_NIFTY25JUN202623600CE_SELL")
        self.strategy.order_manager.cancel_order.assert_any_call("oid_NIFTY25JUN202623400PE_SELL")

        # Verify that we bought back the filled short CE (at 1.1x fallback)
        self.strategy.order_manager.place_smart_limit.assert_any_call(
            "NIFTY25JUN202623600CE", "token_23600_CE", 65, 38.5, "BUY", "STRADDLE_SCALP", "PAPER"
        )
        
        # Verify that we sold the Long protection hedges to return to flat cash
        self.strategy.order_manager.place_smart_limit.assert_any_call(
            "NIFTY25JUN202623700CE", "token_23700_CE", 65, 13.5, "SELL", "STRADDLE_SCALP", "PAPER"
        )
        self.strategy.order_manager.place_smart_limit.assert_any_call(
            "NIFTY25JUN202623300PE", "token_23300_PE", 65, 16.2, "SELL", "STRADDLE_SCALP", "PAPER"
        )

    def test_monitor_condor_take_profit(self):
        """Test take profit trigger when basket decays by 50%."""
        self.strategy.lc_position = {'entry_price': 15.0, 'token': 'LC_token', 'qty': 65}
        self.strategy.sc_position = {'entry_price': 35.0, 'token': 'SC_token', 'qty': 65, 'sl_oid': 'sl_1'}
        self.strategy.sp_position = {'entry_price': 40.0, 'token': 'SP_token', 'qty': 65, 'sl_oid': 'sl_2'}
        self.strategy.lp_position = {'entry_price': 18.0, 'token': 'LP_token', 'qty': 65}

        # Net credit collected = (35 + 40) - (15 + 18) = 75 - 33 = 42 points.
        # Target TP value is 42 * 0.50 = 21 points.
        # Let's mock current LTPs such that current basket value is 18 points (<= 21 target).
        self.strategy.data_fetcher.get_ltp.side_effect = lambda token, exchange=None: {
            "LC_token": 12.0,
            "SC_token": 20.0,
            "SP_token": 18.0,
            "LP_token": 8.0
        }.get(token, 20.0)

        # Run monitor
        with patch.object(self.strategy, '_close_all') as mock_close:
            res = self.strategy._monitor_condor()
            self.assertEqual(res, "PROFIT")
            mock_close.assert_called_once_with("TARGET")

    def test_monitor_condor_stop_loss(self):
        """Test basket stop loss trigger when premium doubles (200% of net credit)."""
        self.strategy.lc_position = {'entry_price': 15.0, 'token': 'LC_token', 'qty': 65}
        self.strategy.sc_position = {'entry_price': 35.0, 'token': 'SC_token', 'qty': 65, 'sl_oid': 'sl_1'}
        self.strategy.sp_position = {'entry_price': 40.0, 'token': 'SP_token', 'qty': 65, 'sl_oid': 'sl_2'}
        self.strategy.lp_position = {'entry_price': 18.0, 'token': 'LP_token', 'qty': 65}

        # Net credit = 42 points. Stop loss target is 42 * 2.0 = 84 points.
        # Mock current LTPs such that current basket value is 90 points (>= 84).
        self.strategy.data_fetcher.get_ltp.side_effect = lambda token, exchange=None: {
            "LC_token": 5.0,
            "SC_token": 80.0,
            "SP_token": 20.0,
            "LP_token": 5.0
        }.get(token, 20.0)

        with patch.object(self.strategy, '_close_all') as mock_close:
            res = self.strategy._monitor_condor()
            self.assertEqual(res, "LOSS")
            mock_close.assert_called_once_with("STOPLOSS")

    @patch('bot.strategies.straddle_scalp_strategy.trade_repo')
    @patch('bot.strategies.straddle_scalp_strategy.notifier')
    def test_close_all_sequential_ordering(self, mock_notifier, mock_trade_repo):
        """Verify that closing an Iron Condor cancels SLs, and closes Short legs first, then Long hedges."""
        self.strategy.lc_position = {'symbol': 'LC_sym', 'entry_price': 15.0, 'token': 'LC_token', 'qty': 65}
        self.strategy.sc_position = {'symbol': 'SC_sym', 'entry_price': 35.0, 'token': 'SC_token', 'qty': 65, 'sl_oid': 'sl_1'}
        self.strategy.sp_position = {'symbol': 'SP_sym', 'entry_price': 40.0, 'token': 'SP_token', 'qty': 65, 'sl_oid': 'sl_2'}
        self.strategy.lp_position = {'symbol': 'LP_sym', 'entry_price': 18.0, 'token': 'LP_token', 'qty': 65}

        # Mock order manager placing exits
        self.strategy.order_manager.place_smart_limit.side_effect = lambda symbol, token, qty, price, side, strategy_name, mode: f"exit_{symbol}"
        self.strategy.data_fetcher.get_ltp.return_value = 20.0

        # Close all
        self.strategy._close_all("TEST_EXIT")

        # Verify stop loss cancellations on the exchange
        self.strategy.order_manager.cancel_order.assert_any_call("sl_1")
        self.strategy.order_manager.cancel_order.assert_any_call("sl_2")

        # Check call order of place_smart_limit
        call_symbols = [call_args[0][0] for call_args in self.strategy.order_manager.place_smart_limit.call_args_list]
        
        # Shorts (SC_sym, SP_sym) must appear first in the call list
        self.assertIn(call_symbols[0], ['SC_sym', 'SP_sym'])
        self.assertIn(call_symbols[1], ['SC_sym', 'SP_sym'])
        
        # Longs (LC_sym, LP_sym) must appear last in the call list
        self.assertIn(call_symbols[2], ['LC_sym', 'LP_sym'])
        self.assertIn(call_symbols[3], ['LC_sym', 'LP_sym'])
