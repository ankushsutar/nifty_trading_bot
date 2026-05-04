import unittest
from datetime import datetime, time
from bot.selling_engine.core.vix_gate import VIXGate
from bot.selling_engine.core.strike_selector import StrikeSelector
from bot.selling_engine.core.exit_manager import ExitManager
from bot.selling_engine.config import SELLING_CONFIG

class TestSellingEngineCore(unittest.TestCase):
    def setUp(self):
        self.config = SELLING_CONFIG.copy()
        # Ensure consistent test values
        self.config["vix_min"] = 11.0
        self.config["vix_max"] = 25.0
        self.config["vix_high_threshold"] = 20.0
        
    def test_vix_gate(self):
        gate = VIXGate(self.config)
        
        # Optimal range
        res = gate.check(15.0)
        self.assertTrue(res["allowed"])
        self.assertEqual(res["size_multiplier"], 1.0)
        
        # Elevated range
        res = gate.check(22.0)
        self.assertTrue(res["allowed"])
        self.assertEqual(res["size_multiplier"], 0.5)
        
        # Below min
        res = gate.check(10.0)
        self.assertFalse(res["allowed"])
        
        # Above max
        res = gate.check(26.0)
        self.assertFalse(res["allowed"])

    def test_strike_selector_atm(self):
        selector = StrikeSelector(self.config)
        self.assertEqual(selector.get_atm_strike(22123), 22100)
        self.assertEqual(selector.get_atm_strike(22149), 22150)
        self.assertEqual(selector.get_atm_strike(22176), 22200)

    def test_exit_manager_tp(self):
        manager = ExitManager(self.config)
        self.config["ic_take_profit_pct"] = 50.0
        
        position = {
            "strategy": "iron_condor",
            "entry_premium": 100.0,
            "current_premium": 40.0,
            "entry_time": datetime.now()
        }
        data = {"spot": 22000, "time": datetime.now(), "vix": 15.0}
        
        res = manager.check_exits(position, data)
        self.assertTrue(res["should_exit"])
        self.assertEqual(res["exit_type"], "take_profit")

    def test_exit_manager_sl(self):
        manager = ExitManager(self.config)
        self.config["ic_stop_loss_multiplier"] = 2.0
        
        position = {
            "strategy": "iron_condor",
            "entry_premium": 100.0,
            "current_premium": 210.0,
            "entry_time": datetime.now()
        }
        data = {"spot": 22000, "time": datetime.now(), "vix": 15.0}
        
        res = manager.check_exits(position, data)
        self.assertTrue(res["should_exit"])
        self.assertEqual(res["exit_type"], "stop_loss")

if __name__ == "__main__":
    unittest.main()
