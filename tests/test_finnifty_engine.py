import unittest
from unittest.mock import MagicMock
from bot.config.settings import Config
from bot.config.instruments import get_instrument
from bot.core.constituent_tracker import ConstituentTracker
from bot.core.market_regime_filter import MarketRegimeFilter


class TestFinNiftyEngine(unittest.TestCase):
    def setUp(self):
        Config.ACTIVE_SYMBOL = "FINNIFTY"

    def test_finnifty_instrument_specs(self):
        instr = get_instrument("FINNIFTY")
        self.assertEqual(instr.name, "FINNIFTY")
        self.assertEqual(instr.lot_size, 60)
        self.assertEqual(instr.strike_step, 50)
        self.assertEqual(instr.expiry_day, 1)  # Tuesday
        self.assertEqual(instr.analysis_token, "99926037")
        self.assertEqual(instr.spot_symbol, "Nifty Fin Service")
        self.assertEqual(Config.NIFTY_LOT_SIZE, 60)

    def test_finnifty_tier_sl_and_slippage(self):
        # Small account tier (~₹50,000)
        tier = Config.get_tier(50000.0)
        self.assertEqual(tier.min_sl_points, 10.0)
        self.assertEqual(tier.entry_slippage_pct, 0.01)

        # Medium account tier (~₹1,50,000)
        tier_med = Config.get_tier(150000.0)
        self.assertEqual(tier_med.min_sl_points, 12.0)
        self.assertEqual(tier_med.entry_slippage_pct, 0.008)

    def test_finnifty_constituent_basket(self):
        ConstituentTracker._instance = None
        tracker = ConstituentTracker(MagicMock())
        basket = tracker.CONSTITUENTS

        # FinNifty heavyweights MUST be present
        self.assertIn("HDFCBANK", basket)
        self.assertIn("ICICIBANK", basket)
        self.assertIn("BAJFINANCE", basket)
        self.assertIn("SBIN", basket)
        # Total weights must sum to 1.0
        total_weight = sum(item["weight"] for item in basket.values())
        self.assertAlmostEqual(total_weight, 1.0, places=2)

    def test_finnifty_regime_spot_token(self):
        regime = MarketRegimeFilter(MagicMock())
        self.assertEqual(regime.spot_token, "99926037")


if __name__ == "__main__":
    unittest.main()
