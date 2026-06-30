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
        def get_token_mock(symbol_name, expiry_date, strike, option_type, *args, **kwargs):
            return f"token_{strike}_{option_type}", f"NIFTY{expiry_date}{strike}{option_type}"
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
        self.strategy.order_manager.place_smart_limit.side_effect = lambda *args, **kwargs: f"oid_{kwargs.get('symbol') or args[0]}_{kwargs.get('transaction_type') or args[4]}"
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

        # Mock order placement: CE succeeds, PE fails and returns None
        def place_smart_limit_mock(*args, **kwargs):
            sym = kwargs.get('symbol') or args[0]
            if "23300PE" in sym:
                return None
            return f"oid_{sym}_{kwargs.get('transaction_type') or args[4]}"
        self.strategy.order_manager.place_smart_limit.side_effect = place_smart_limit_mock

        # Run entry
        sc_symbol = "NIFTY25JUN202623600CE"
        sp_symbol = "NIFTY25JUN202623400PE"
        self.strategy._enter_condor(expiry="25JUN2026")

        # Verify that we placed a market SELL to square off the filled CE
        self.strategy.order_manager.place_market.assert_any_call(
            symbol="NIFTY25JUN202623700CE", token="token_23700_CE", qty=65,
            transaction_type="SELL", strategy_name="STRADDLE_SCALP", mode="PAPER"
        )
        
        # Verify that we did not place any short premium trades (SC or SP)
        for call_args in self.strategy.order_manager.place_smart_limit.call_args_list:
            args, kwargs = call_args
            symbol = kwargs.get('symbol') or (args[0] if len(args) > 0 else None)
            side = kwargs.get('transaction_type') or (args[4] if len(args) > 4 else None)
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

        # Mock order placement: Hedges fill, SC succeeds, SP fails and returns None
        def place_smart_limit_mock(*args, **kwargs):
            sym = kwargs.get('symbol') or args[0]
            side = kwargs.get('transaction_type') or args[4]
            if "23400PE" in sym and side == "SELL":
                return None
            return f"oid_{sym}_{side}"
        self.strategy.order_manager.place_smart_limit.side_effect = place_smart_limit_mock

        # Run entry
        self.strategy._enter_condor(expiry="25JUN2026")

        # Verify that we bought back the filled short CE at market
        self.strategy.order_manager.place_market.assert_any_call(
            symbol="NIFTY25JUN202623600CE", token="token_23600_CE", qty=65,
            transaction_type="BUY", strategy_name="STRADDLE_SCALP", mode="PAPER"
        )
        
        # Verify that we sold the Long protection hedges to return to flat cash
        self.strategy.order_manager.place_market.assert_any_call(
            symbol="NIFTY25JUN202623700CE", token="token_23700_CE", qty=65,
            transaction_type="SELL", strategy_name="STRADDLE_SCALP", mode="PAPER"
        )
        self.strategy.order_manager.place_market.assert_any_call(
            symbol="NIFTY25JUN202623300PE", token="token_23300_PE", qty=65,
            transaction_type="SELL", strategy_name="STRADDLE_SCALP", mode="PAPER"
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
        self.strategy.order_manager.place_smart_limit.side_effect = lambda *args, **kwargs: f"exit_{kwargs.get('symbol') or args[0]}"
        self.strategy.data_fetcher.get_ltp.return_value = 20.0

        # Close all
        self.strategy._close_all("TEST_EXIT")

        # Verify stop loss cancellations on the exchange
        self.strategy.order_manager.cancel_order.assert_any_call("sl_1")
        self.strategy.order_manager.cancel_order.assert_any_call("sl_2")

        # Check call order of place_smart_limit
        call_symbols = []
        for call_args in self.strategy.order_manager.place_smart_limit.call_args_list:
            args, kwargs = call_args
            sym = kwargs.get('symbol') or (args[0] if len(args) > 0 else None)
            call_symbols.append(sym)
        
        # Shorts (SC_sym, SP_sym) must appear first in the call list
        self.assertIn(call_symbols[0], ['SC_sym', 'SP_sym'])
        self.assertIn(call_symbols[1], ['SC_sym', 'SP_sym'])
        
        # Longs (LC_sym, LP_sym) must appear last in the call list
        self.assertIn(call_symbols[2], ['LC_sym', 'LP_sym'])
        self.assertIn(call_symbols[3], ['LC_sym', 'LP_sym'])

    @patch('bot.strategies.straddle_scalp_strategy.trade_repo')
    @patch('bot.strategies.straddle_scalp_strategy.notifier')
    def test_close_all_skip_on_sl_hit(self, mock_notifier, mock_trade_repo):
        """Verify that if one Short leg SL is already filled, we skip buying it back."""
        self.strategy.lc_position = {'symbol': 'LC_sym', 'entry_price': 15.0, 'token': 'LC_token', 'qty': 65}
        self.strategy.sc_position = {'symbol': 'SC_sym', 'entry_price': 35.0, 'token': 'SC_token', 'qty': 65, 'sl_oid': 'sl_1'}
        self.strategy.sp_position = {'symbol': 'SP_sym', 'entry_price': 40.0, 'token': 'SP_token', 'qty': 65, 'sl_oid': 'sl_2'}
        self.strategy.lp_position = {'symbol': 'LP_sym', 'entry_price': 18.0, 'token': 'LP_token', 'qty': 65}

        # Mock order manager status query: sl_1 is COMPLETE (SL hit), sl_2 is CANCELLED (not hit)
        def mock_get_order_status(oid):
            if oid == "sl_1":
                return {"status": "COMPLETE", "price": 42.0}
            return {"status": "CANCELLED", "price": 0.0}
        self.strategy.order_manager.get_order_status.side_effect = mock_get_order_status

        # Mock order manager placing exits
        self.strategy.order_manager.place_smart_limit.side_effect = lambda *args, **kwargs: f"exit_{kwargs.get('symbol') or args[0]}"
        self.strategy.data_fetcher.get_ltp.return_value = 20.0

        # Close all
        self.strategy._close_all("TEST_EXIT")

        # Verify stop loss cancellations
        self.strategy.order_manager.cancel_order.assert_any_call("sl_1")
        self.strategy.order_manager.cancel_order.assert_any_call("sl_2")

        # Verify placed exit orders
        call_symbols = []
        for call_args in self.strategy.order_manager.place_smart_limit.call_args_list:
            args, kwargs = call_args
            sym = kwargs.get('symbol') or (args[0] if len(args) > 0 else None)
            call_symbols.append(sym)
        
        # We must skip buying back SC_sym since its SL was hit!
        self.assertNotIn('SC_sym', call_symbols)
        self.assertIn('SP_sym', call_symbols)
        self.assertIn('LC_sym', call_symbols)
        self.assertIn('LP_sym', call_symbols)

