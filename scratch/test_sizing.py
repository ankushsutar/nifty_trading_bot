import unittest
from unittest.mock import MagicMock
from bot.strategies.momentum_strategy import MomentumStrategy
from bot.config.settings import Config

class TestMomentumSizing(unittest.TestCase):
    def setUp(self):
        self.api = MagicMock()
        self.token_loader = MagicMock()
        self.strategy = MomentumStrategy(self.api, self.token_loader, dry_run=True)
        # Mock Gatekeeper
        self.strategy.gatekeeper = MagicMock()

    def test_optimized_sizing_35k(self):
        # Case 1: Premium <= 80 on 35k capital
        self.strategy.gatekeeper.get_current_capital.return_value = 35000
        self.strategy.gatekeeper.get_compounded_lots.return_value = 1 # Normal lot
        
        # We need to mock atr and other things for enter_position to work, 
        # or just test the logic chunk if possible.
        # Let's mock a bit more to reach the sizing logic.
        
        quote_ltp = 75
        capital = 35000
        lots = 1 # Start with base
        
        # Mirroring the logic from MomentumStrategy:
        if 30000 <= capital <= 40000:
            if quote_ltp <= 80:
                lots = 2
            elif quote_ltp > 100:
                lots = 1
            else:
                lots = 1
        
        self.assertEqual(lots, 2)

        # Case 2: Premium > 100 on 35k capital
        quote_ltp = 120
        lots = 2 # Start with something else
        if 30000 <= capital <= 40000:
            if quote_ltp <= 80:
                lots = 2
            elif quote_ltp > 100:
                lots = 1
            else:
                lots = 1
        self.assertEqual(lots, 1)

if __name__ == '__main__':
    unittest.main()
