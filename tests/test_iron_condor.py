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
        self.strategy.gatekeeper.get_starting_capital.return_value = 50000.0

    @patch('bot.strategies.straddle_scalp_strategy.trade_repo')
    @patch('bot.strategies.straddle_scalp_strategy.notifier')
    def test_happy_path_entry(self, mock_notifier, mock_trade_repo):
        """Test successful parallel entry into the Long Straddle basket."""
        # Mock Nifty spot LTP
        self.strategy.data_fetcher.get_ltp.side_effect = lambda token, exchange=None: {
            "99926000": 23520.0,      # Spot
            "token_23500_CE": 50.0,   # LC
            "token_23500_PE": 50.0,   # LP
        }.get(token, 20.0)

        # Mock order manager placing limit orders
        self.strategy.order_manager.place_smart_limit.side_effect = lambda *args, **kwargs: f"oid_{kwargs.get('symbol') or args[0]}_{kwargs.get('transaction_type') or args[4]}"

        # Mock trade saving
        mock_trade_repo.save_trade.return_value = "db_trade_id"

        # Execute entry
        self.strategy._enter_straddle(expiry="25JUN2026")

        # Verify parallel ordering: place_smart_limit should be called 2 times total
        self.assertEqual(self.strategy.order_manager.place_smart_limit.call_count, 2)
        
        # Verify state positions are stored
        self.assertIsNotNone(self.strategy.lc_position)
        self.assertIsNotNone(self.strategy.lp_position)

    @patch('bot.strategies.straddle_scalp_strategy.trade_repo')
    @patch('bot.strategies.straddle_scalp_strategy.notifier')
    def test_rollback_on_partial_fill(self, mock_notifier, mock_trade_repo):
        """Verify that if one leg fails to place, the strategy rolls back the filled leg cleanly."""
        self.strategy.data_fetcher.get_ltp.side_effect = lambda token, exchange=None: {
            "99926000": 23520.0,
            "token_23500_CE": 50.0,
            "token_23500_PE": 50.0,
        }.get(token, 20.0)

        # Mock order placement: CE succeeds, PE fails and returns None
        def place_smart_limit_mock(*args, **kwargs):
            sym = kwargs.get('symbol') or args[0]
            if "PE" in sym:
                return None
            return f"oid_{sym}_{kwargs.get('transaction_type') or args[4]}"
        self.strategy.order_manager.place_smart_limit.side_effect = place_smart_limit_mock

        # Run entry
        self.strategy._enter_straddle(expiry="25JUN2026")

        # Verify that we placed a market SELL to square off the filled CE
        self.strategy.order_manager.place_market.assert_called_once_with(
            symbol="NIFTY25JUN202623500CE", token="token_23500_CE", qty=65,
            transaction_type="SELL", strategy_name="STRADDLE_SCALP", mode="PAPER"
        )

    def test_monitor_straddle_take_profit(self):
        """Test take profit trigger when basket expands by 25%."""
        self.strategy.lc_position = {'entry_price': 50.0, 'token': 'LC_token', 'qty': 50}
        self.strategy.lp_position = {'entry_price': 50.0, 'token': 'LP_token', 'qty': 50}

        # Net debit paid = 50 + 50 = 100 points.
        # Target TP value is 100 * 1.25 = 125 points.
        # Mock current LTPs such that current basket value is 126 points (>= 125).
        self.strategy.data_fetcher.get_ltp.side_effect = lambda token, exchange=None: {
            "LC_token": 80.0,
            "LP_token": 46.0,
        }.get(token, 20.0)

        # Run monitor
        with patch.object(self.strategy, '_close_all') as mock_close:
            res = self.strategy._monitor_straddle()
            self.assertEqual(res, "PROFIT")
            mock_close.assert_called_once_with("TARGET")

    def test_monitor_straddle_stop_loss(self):
        """Test basket stop loss trigger when premium decays by 15%."""
        self.strategy.lc_position = {'entry_price': 50.0, 'token': 'LC_token', 'qty': 50}
        self.strategy.lp_position = {'entry_price': 50.0, 'token': 'LP_token', 'qty': 50}

        # Net debit = 100 points. Stop loss target is 100 * 0.85 = 85 points.
        # Mock current LTPs such that current basket value is 84 points (<= 85).
        self.strategy.data_fetcher.get_ltp.side_effect = lambda token, exchange=None: {
            "LC_token": 44.0,
            "LP_token": 40.0,
        }.get(token, 20.0)

        with patch.object(self.strategy, '_close_all') as mock_close:
            res = self.strategy._monitor_straddle()
            self.assertEqual(res, "LOSS")
            mock_close.assert_called_once_with("STOPLOSS")

    @patch('bot.strategies.straddle_scalp_strategy.trade_repo')
    @patch('bot.strategies.straddle_scalp_strategy.notifier')
    def test_close_all(self, mock_notifier, mock_trade_repo):
        """Verify that closing a Straddle exits both Long legs."""
        self.strategy.lc_position = {'symbol': 'LC_sym', 'entry_price': 50.0, 'token': 'LC_token', 'qty': 50}
        self.strategy.lp_position = {'symbol': 'LP_sym', 'entry_price': 50.0, 'token': 'LP_token', 'qty': 50}

        # Mock order manager placing exits
        self.strategy.order_manager.place_smart_limit.side_effect = lambda *args, **kwargs: f"exit_{kwargs.get('symbol') or args[0]}"
        self.strategy.data_fetcher.get_ltp.return_value = 20.0

        # Close all
        self.strategy._close_all("TEST_EXIT")

        # Verify placed exit orders
        self.assertEqual(self.strategy.order_manager.place_smart_limit.call_count, 2)
        
        call_symbols = []
        for call_args in self.strategy.order_manager.place_smart_limit.call_args_list:
            args, kwargs = call_args
            sym = kwargs.get('symbol') or (args[0] if len(args) > 0 else None)
            call_symbols.append(sym)
            
        self.assertIn('LC_sym', call_symbols)
        self.assertIn('LP_sym', call_symbols)
