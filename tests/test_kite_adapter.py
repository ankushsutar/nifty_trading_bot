import unittest
from unittest.mock import MagicMock, patch
import datetime

from bot.core.broker_adapter import KiteBrokerAdapter, KiteTickerMarketWrapper, KiteTickerOrderWrapper
from bot.config.settings import Config


class TestKiteBrokerAdapter(unittest.TestCase):
    def setUp(self):
        self.mock_kite = MagicMock()
        self.access_token = "mock_access_token"
        self.adapter = KiteBrokerAdapter(self.mock_kite, self.access_token)

    def test_place_order(self):
        self.mock_kite.place_order.return_value = "12345"
        self.mock_kite.VARIETY_REGULAR = "regular"
        self.mock_kite.PRODUCT_MIS = "MIS"
        self.mock_kite.ORDER_TYPE_LIMIT = "LIMIT"

        orderparams = {
            "exchange": "NFO",
            "tradingsymbol": "NIFTY26JUN23000CE",
            "transactiontype": "BUY",
            "quantity": 65,
            "producttype": "INTRADAY",
            "ordertype": "LIMIT",
            "price": 100.5
        }

        resp = self.adapter.placeOrder(orderparams)
        self.assertTrue(resp["status"])
        self.assertEqual(resp["data"]["orderid"], "12345")
        self.mock_kite.place_order.assert_called_once_with(
            variety="regular",
            exchange="NFO",
            tradingsymbol="NIFTY26JUN23000CE",
            transaction_type="BUY",
            quantity=65,
            product="MIS",
            order_type="LIMIT",
            price=100.5,
            trigger_price=None
        )

    def test_modify_order(self):
        self.mock_kite.modify_order.return_value = {"order_id": "12345"}
        self.mock_kite.VARIETY_REGULAR = "regular"
        self.mock_kite.ORDER_TYPE_SL = "SL"

        orderparams = {
            "orderid": "12345",
            "quantity": 130,
            "price": 105.0,
            "triggerprice": 104.0,
            "ordertype": "STOPLOSS_LIMIT"
        }

        resp = self.adapter.modifyOrder(orderparams)
        self.assertTrue(resp["status"])
        self.mock_kite.modify_order.assert_called_once_with(
            variety="regular",
            order_id="12345",
            quantity=130,
            price=105.0,
            order_type="SL",
            trigger_price=104.0
        )

    def test_cancel_order(self):
        self.mock_kite.cancel_order.return_value = {"order_id": "12345"}
        self.mock_kite.VARIETY_REGULAR = "regular"

        resp = self.adapter.cancelOrder("12345")
        self.assertTrue(resp["status"])
        self.mock_kite.cancel_order.assert_called_once_with(
            variety="regular",
            order_id="12345"
        )

    def test_order_book(self):
        self.mock_kite.orders.return_value = [
            {
                "order_id": "12345",
                "status": "COMPLETE",
                "tradingsymbol": "NIFTY26JUN23000CE",
                "instrument_token": 123456,
                "transaction_type": "BUY",
                "quantity": 65,
                "price": 100.5,
                "average_price": 100.5
            },
            {
                "order_id": "67890",
                "status": "REJECTED",
                "tradingsymbol": "NIFTY26JUN23000PE",
                "instrument_token": 789012,
                "transaction_type": "SELL",
                "quantity": 65,
                "price": 95.0,
                "average_price": 0.0
            }
        ]

        resp = self.adapter.orderBook()
        self.assertTrue(resp["status"])
        self.assertEqual(len(resp["data"]), 2)
        self.assertEqual(resp["data"][0]["status"], "complete")
        self.assertEqual(resp["data"][1]["status"], "rejected")

    def test_position(self):
        self.mock_kite.positions.return_value = {
            "net": [
                {
                    "tradingsymbol": "NIFTY26JUN23000CE",
                    "instrument_token": 123456,
                    "buy_quantity": 65,
                    "sell_quantity": 0,
                    "buy_price": 100.5,
                    "sell_price": 0.0,
                    "realised": 0.0,
                    "m2m": 250.0
                }
            ]
        }

        resp = self.adapter.position()
        self.assertTrue(resp["status"])
        self.assertEqual(len(resp["data"]), 1)
        pos = resp["data"][0]
        self.assertEqual(pos["tradingsymbol"], "NIFTY26JUN23000CE")
        self.assertEqual(pos["realisedpnl"], 250.0)
        self.assertEqual(pos["netqty"], 65)
        self.assertEqual(pos["avgnetprice"], 100.5)
        self.assertEqual(pos["producttype"], "INTRADAY")
        self.assertEqual(pos["symbolname"], "NIFTY")

    def test_rms_limit(self):
        self.mock_kite.margins.return_value = {
            "equity": {
                "net": 150000.0,
                "available": {"cash": 120000.0}
            }
        }

        resp = self.adapter.rmsLimit()
        self.assertTrue(resp["status"])
        self.assertEqual(resp["data"]["net"], "150000.00")
        self.assertEqual(resp["data"]["availableCash"], "120000.00")

    def test_get_profile(self):
        self.mock_kite.profile.return_value = {
            "user_name": "Test User",
            "user_id": "AB1234",
            "exchanges": ["NSE", "NFO"]
        }

        resp = self.adapter.getProfile()
        self.assertTrue(resp["status"])
        self.assertEqual(resp["data"]["name"], "Test User")
        self.assertEqual(resp["data"]["clientcode"], "AB1234")

    @patch('bot.core.broker_adapter.get_lookup')
    def test_get_market_data(self, mock_get_lookup):
        mock_lookup = MagicMock()
        mock_lookup.get_instrument_by_token.side_effect = lambda tok: {
            "99926000": ("NIFTY 50", "NSE"),
            "12345": ("NIFTY26JUN23000CE", "NFO")
        }.get(str(tok), (None, None))
        mock_get_lookup.return_value = mock_lookup

        self.mock_kite.quote.return_value = {
            "NSE:NIFTY 50": {"oi": 0, "last_price": 23100.5},
            "NFO:NIFTY26JUN23000CE": {"oi": 500000, "last_price": 100.5}
        }

        params = {
            "NSE": ["99926000"],
            "NFO": ["12345"]
        }

        resp = self.adapter.getMarketData("FULL", params)
        self.assertTrue(resp["status"])
        self.assertEqual(resp["message"], "SUCCESS")
        fetched = resp["data"]["fetched"]
        self.assertEqual(len(fetched), 2)
        # Nifty spot
        self.assertEqual(fetched[0]["symbolToken"], "99926000")
        self.assertEqual(fetched[0]["ltp"], 23100.5)
        # Call option
        self.assertEqual(fetched[1]["symbolToken"], "12345")
        self.assertEqual(fetched[1]["opnInterest"], 500000)
        self.assertEqual(fetched[1]["ltp"], 100.5)

    def test_trade_book(self):
        self.mock_kite.trades.return_value = [
            {
                "tradingsymbol": "NIFTY26JUN23000CE",
                "transaction_type": "BUY",
                "quantity": 65,
                "average_price": 100.5
            }
        ]

        resp = self.adapter.tradeBook()
        self.assertTrue(resp["status"])
        self.assertEqual(len(resp["data"]), 1)
        t = resp["data"][0]
        self.assertEqual(t["tradingsymbol"], "NIFTY26JUN23000CE")
        self.assertEqual(t["transactiontype"], "BUY")
        self.assertEqual(t["quantity"], 65)
        self.assertEqual(t["averageprice"], 100.5)

    @patch("os.path.exists")
    @patch("os.remove")
    @patch("builtins.open", new_callable=unittest.mock.mock_open)
    def test_handle_token_exception(self, mock_file_open, mock_remove, mock_exists):
        # Setup mock_exists to return True for session files
        mock_exists.side_effect = lambda path: "session_kite.json" in path or "session_refresh.flag" in path
        
        # Mock kite client method to raise a token exception
        self.mock_kite.orders.side_effect = Exception("Incorrect api_key or access_token")
        
        # Call orderBook, which should catch the exception and handle it
        resp = self.adapter.orderBook()
        
        # Assertions
        self.assertFalse(resp["status"])
        self.assertIn("Incorrect api_key or access_token", resp["message"])
        
        # Ensure it attempted to remove the stale session file
        mock_remove.assert_called_once()
        self.assertTrue(any("session_kite.json" in str(arg) for arg in mock_remove.call_args[0]))
        
        # Ensure it wrote the session refresh flag
        mock_file_open.assert_called_once()
        self.assertTrue(any("session_refresh.flag" in str(arg) for arg in mock_file_open.call_args[0]))
        mock_file_open().write.assert_called_once_with("REFRESH_REQUIRED")


@patch("kiteconnect.KiteTicker")
class TestKiteTickerWrappers(unittest.TestCase):
    def test_market_wrapper_tick_mapping(self, mock_ticker_class):
        mock_ticker = MagicMock()
        mock_ticker_class.return_value = mock_ticker

        wrapper = KiteTickerMarketWrapper("api_key", "access_token")
        
        # Test on_ticks mapping
        mock_on_data = MagicMock()
        wrapper.on_data = mock_on_data

        test_ticks = [
            {
                "instrument_token": 256265, # NIFTY 50 spot in Zerodha
                "last_price": 23100.5,
                "timestamp": datetime.datetime(2026, 6, 9, 12, 0, 0),
                "volume_traded": 1500000,
                "depth": {
                    "buy": [{"price": 23100.0}],
                    "sell": [{"price": 23101.0}]
                }
            }
        ]

        # Trigger internal on_ticks callback
        wrapper._on_ticks(None, test_ticks)

        mock_on_data.assert_called_once()
        mapped_ticks = mock_on_data.call_args[0][1]
        self.assertEqual(len(mapped_ticks), 1)
        # Verify NIFTY 50 spot maps back to Angel token '99926000'
        self.assertEqual(mapped_ticks[0]["token"], "99926000")
        self.assertEqual(mapped_ticks[0]["last_traded_price"], 2310050) # to Paise
        self.assertEqual(mapped_ticks[0]["best_5_buy_data"][0]["price"], 2310000)

    def test_order_wrapper_update_mapping(self, mock_ticker_class):
        mock_ticker = MagicMock()
        mock_ticker_class.return_value = mock_ticker

        wrapper = KiteTickerOrderWrapper("api_key", "access_token")
        
        mock_on_message = MagicMock()
        wrapper.on_message = mock_on_message

        test_order = {
            "order_id": "12345",
            "status": "COMPLETE",
            "average_price": 100.5,
            "status_message": "Order filled successfully"
        }

        wrapper._on_order_update(None, test_order)

        mock_on_message.assert_called_once()
        mapped_msg = mock_on_message.call_args[0][1]
        self.assertEqual(mapped_msg["orderid"], "12345")
        self.assertEqual(mapped_msg["status"], "complete")
        self.assertEqual(mapped_msg["averageprice"], 100.5)


if __name__ == "__main__":
    unittest.main()
