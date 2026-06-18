import unittest
from unittest.mock import MagicMock, patch
import datetime

from bot.utils.greeks import parse_expiry, calculate_black_scholes_delta, select_strike_by_delta
from bot.core.order_manager import OrderManager


class TestStrikeSelectionAndL1Walking(unittest.TestCase):
    
    def test_parse_expiry(self):
        # 1. Parse standard weekly expiry
        d1 = parse_expiry("06JAN2026")
        self.assertEqual(d1, datetime.date(2026, 1, 6))
        
        # 2. Parse monthly expiry
        d2 = parse_expiry("26JUN2026")
        self.assertEqual(d2, datetime.date(2026, 6, 26))
        
        # 3. Invalid format check
        with self.assertRaises(ValueError):
            parse_expiry("INVALID")

    def test_calculate_black_scholes_delta(self):
        # Spot = 23000, 5 days to expiry, VIX = 15.0%
        spot = 23000.0
        days_to_expiry = 5.0
        vix = 15.0
        
        # CE Options
        atm_ce = calculate_black_scholes_delta(spot, 23000.0, days_to_expiry, vix, "CE")
        otm_ce = calculate_black_scholes_delta(spot, 23200.0, days_to_expiry, vix, "CE")
        itm_ce = calculate_black_scholes_delta(spot, 22800.0, days_to_expiry, vix, "CE")
        
        self.assertAlmostEqual(atm_ce, 0.50, delta=0.05)
        self.assertTrue(otm_ce < atm_ce)
        self.assertTrue(itm_ce > atm_ce)
        
        # PE Options
        atm_pe = calculate_black_scholes_delta(spot, 23000.0, days_to_expiry, vix, "PE")
        otm_pe = calculate_black_scholes_delta(spot, 22800.0, days_to_expiry, vix, "PE") # OTM Put is lower strike
        itm_pe = calculate_black_scholes_delta(spot, 23200.0, days_to_expiry, vix, "PE") # ITM Put is higher strike
        
        self.assertAlmostEqual(atm_pe, -0.50, delta=0.05)
        self.assertTrue(abs(otm_pe) < abs(atm_pe))
        self.assertTrue(abs(itm_pe) > abs(atm_pe))

    def test_select_strike_by_delta(self):
        mock_lookup = MagicMock()
        mock_lookup.get_option_bucket.return_value = {
            "22800_CE": {"token": "t1", "symbol": "s1", "strike": 22800, "type": "CE"},
            "22900_CE": {"token": "t2", "symbol": "s2", "strike": 22900, "type": "CE"},
            "23000_CE": {"token": "t3", "symbol": "s3", "strike": 23000, "type": "CE"},
            "23100_CE": {"token": "t4", "symbol": "s4", "strike": 23100, "type": "CE"},
            "23200_CE": {"token": "t5", "symbol": "s5", "strike": 23200, "type": "CE"},
        }
        
        # Spot = 23000, target CE delta = 0.40
        # Under normal conditions (VIX=15, 5 days to go), 23100 CE (1-strike OTM) has delta closest to 0.40
        # Let's run selection with target_delta = 0.40
        import datetime as real_datetime
        class MockDate(real_datetime.date):
            @classmethod
            def today(cls):
                return real_datetime.date(2026, 6, 9)

        with patch('bot.utils.greeks.datetime.date', MockDate):
            strike, token, symbol = select_strike_by_delta(
                mock_lookup, spot=23000.0, expiry="14JUN2026", vix=15.0, option_type="CE", target_delta=0.40
            )
        
        self.assertEqual(strike, 23100)
        self.assertEqual(token, "t4")
        self.assertEqual(symbol, "s4")

    @patch("bot.core.order_manager.is_kill_switch_active", return_value=False)
    @patch("bot.core.order_feed.order_feed.wait_for_fill")
    def test_place_smart_limit_l1_walking_buy(self, mock_wait_for_fill, mock_kill):
        mock_api = MagicMock()
        # Mock order placement returning Order ID "OID_123"
        mock_api.placeOrder.return_value = {"status": True, "data": {"orderid": "OID_123"}}
        mock_api.modifyOrder.return_value = {"status": True}
        
        # Mock L1 depth query to return bid/ask
        # On first walk, best bid=101.0, ask=102.0. So price should walk from 100.0 to min(102.0, max(101.0+0.05, 100.0+0.5)) = 101.05
        mock_api.get_order_book_l1.return_value = {"bid": 101.0, "ask": 102.0, "ltp": 101.5}
        
        # wait_for_fill returns TIMEOUT on first call, and FILLED on second call
        mock_wait_for_fill.side_effect = [
            {"status": "TIMEOUT", "price": 0.0},
            {"status": "FILLED", "price": 101.05}
        ]
        
        om = OrderManager(mock_api, dry_run=False)
        om.live_trade_enabled = True
        
        oid = om.place_smart_limit(
            symbol="NIFTY26JUN23000CE",
            token="12345",
            qty=50,
            initial_price=100.0,
            transaction_type="BUY",
            max_walk_ticks=2,
            strategy_name="MOMENTUM"
        )
        
        self.assertEqual(oid, "OID_123")
        # Check that we modified order to the L1 adaptive price of 101.05
        mock_api.modifyOrder.assert_called_with({
            "variety": "NORMAL",
            "orderid": "OID_123",
            "ordertype": "LIMIT",
            "producttype": "INTRADAY",
            "duration": "DAY",
            "price": 101.05,
            "quantity": 50,
            "tradingsymbol": "NIFTY26JUN23000CE",
            "symboltoken": "12345",
            "exchange": "NFO",
            "disclosedquantity": 0
        })

    @patch("bot.core.order_manager.is_kill_switch_active", return_value=False)
    @patch("bot.core.order_feed.order_feed.wait_for_fill")
    def test_place_smart_limit_l1_walking_sell(self, mock_wait_for_fill, mock_kill):
        mock_api = MagicMock()
        mock_api.placeOrder.return_value = {"status": True, "data": {"orderid": "OID_456"}}
        mock_api.modifyOrder.return_value = {"status": True}
        
        # Mock L1 depth query to return bid/ask
        # On first walk, best bid=98.0, ask=99.0.
        # SELL order price should walk from 100.0 to max(98.0, min(99.0-0.05, 100.0-0.5)) = 98.95
        mock_api.get_order_book_l1.return_value = {"bid": 98.0, "ask": 99.0, "ltp": 98.5}
        
        # wait_for_fill returns TIMEOUT on first call, and FILLED on second call
        mock_wait_for_fill.side_effect = [
            {"status": "TIMEOUT", "price": 0.0},
            {"status": "FILLED", "price": 98.95}
        ]
        
        om = OrderManager(mock_api, dry_run=False)
        om.live_trade_enabled = True
        
        oid = om.place_smart_limit(
            symbol="NIFTY26JUN23000CE",
            token="12345",
            qty=50,
            initial_price=100.0,
            transaction_type="SELL",
            max_walk_ticks=2,
            strategy_name="MOMENTUM"
        )
        
        self.assertEqual(oid, "OID_456")
        mock_api.modifyOrder.assert_called_with({
            "variety": "NORMAL",
            "orderid": "OID_456",
            "ordertype": "LIMIT",
            "producttype": "INTRADAY",
            "duration": "DAY",
            "price": 98.95,
            "quantity": 50,
            "tradingsymbol": "NIFTY26JUN23000CE",
            "symboltoken": "12345",
            "exchange": "NFO",
            "disclosedquantity": 0
        })

