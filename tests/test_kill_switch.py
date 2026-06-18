import unittest
from unittest.mock import MagicMock, patch
import os
import sys

# Allow running from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.core.kill_switch import activate_kill_switch, deactivate_kill_switch, is_kill_switch_active, LOCK_FILE
from bot.core.order_manager import OrderManager

class TestKillSwitch(unittest.TestCase):
    def setUp(self):
        # Ensure clean state
        if os.path.exists(LOCK_FILE):
            os.remove(LOCK_FILE)
        self.mock_api = MagicMock()
        self.order_manager = OrderManager(self.mock_api)

    def tearDown(self):
        # Cleanup
        if os.path.exists(LOCK_FILE):
            os.remove(LOCK_FILE)

    def test_kill_switch_lifecycle(self):
        """Test activation and deactivation of the kill switch."""
        self.assertFalse(is_kill_switch_active())
        
        activate_kill_switch()
        self.assertTrue(is_kill_switch_active())
        
        deactivate_kill_switch()
        self.assertFalse(is_kill_switch_active())

    def test_order_placement_blocked(self):
        """Test that place_order is blocked when kill switch is active."""
        activate_kill_switch()
        
        # Try to place order
        result = self.order_manager.place_order({"fake": "params"})
        
        # Expect None (rejected)
        self.assertIsNone(result)
        # Verify API was NOT called
        self.mock_api.placeOrder.assert_not_called()

    def test_order_placement_allowed(self):
        """Test that place_order is allowed when kill switch is inactive."""
        deactivate_kill_switch()
        
        # Mock API response
        self.mock_api.placeOrder.return_value = "12345"
        
        # Try to place order
        result = self.order_manager.place_order({"fake": "params"})
        
        # Expect Success
        self.assertEqual(result, "12345")
        # Verify API WAS called
        self.mock_api.placeOrder.assert_called_once()

if __name__ == '__main__':
    unittest.main()
