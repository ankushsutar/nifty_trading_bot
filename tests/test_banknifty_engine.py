import unittest
from unittest.mock import MagicMock
from bot.config.settings import Config
from bot.config.instruments import get_instrument
from bot.core.constituent_tracker import ConstituentTracker
from bot.core.market_regime_filter import MarketRegimeFilter


class TestBankNiftyEngine(unittest.TestCase):
    def setUp(self):
        Config.ACTIVE_SYMBOL = "BANKNIFTY"

    def test_banknifty_instrument_specs(self):
        instr = get_instrument("BANKNIFTY")
        self.assertEqual(instr.name, "BANKNIFTY")
        self.assertEqual(instr.lot_size, 30)
        self.assertEqual(instr.strike_step, 100)
        self.assertEqual(instr.expiry_day, 2)  # Wednesday
        self.assertEqual(instr.analysis_token, "99926009")
        self.assertEqual(instr.spot_symbol, "Nifty Bank")
        self.assertEqual(Config.NIFTY_LOT_SIZE, 30)

    def test_banknifty_tier_sl_and_slippage(self):
        # Small account tier (~₹50,000)
        tier = Config.get_tier(50000.0)
        self.assertEqual(tier.min_sl_points, 25.0)
        self.assertEqual(tier.entry_slippage_pct, 0.015)

        # Medium account tier (~₹1,50,000)
        tier_med = Config.get_tier(150000.0)
        self.assertEqual(tier_med.min_sl_points, 30.0)
        self.assertEqual(tier_med.entry_slippage_pct, 0.012)

    def test_banknifty_constituent_basket(self):
        ConstituentTracker._instance = None
        tracker = ConstituentTracker(MagicMock())
        basket = tracker.CONSTITUENTS

        # Reliance MUST NOT be in BankNifty basket
        self.assertNotIn("RELIANCE", basket)
        # Bank heavyweights MUST be present
        self.assertIn("HDFCBANK", basket)
        self.assertIn("ICICIBANK", basket)
        self.assertIn("SBIN", basket)
        self.assertIn("AXISBANK", basket)
        # Total weights must sum to 1.0
        total_weight = sum(item["weight"] for item in basket.values())
        self.assertAlmostEqual(total_weight, 1.0, places=2)

    def test_banknifty_regime_spot_token(self):
        regime = MarketRegimeFilter(MagicMock())
        self.assertEqual(regime.spot_token, "99926009")


if __name__ == "__main__":
    unittest.main()
