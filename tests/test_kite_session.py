import unittest
from unittest.mock import MagicMock, patch, mock_open
import datetime
import json
import os

from bot.core.session.kite import KiteSessionProvider, SESSION_FILE_KITE


class TestKiteSessionProvider(unittest.TestCase):
    def setUp(self):
        self.provider = KiteSessionProvider()

    @patch("kiteconnect.KiteConnect")
    @patch("os.path.exists")
    def test_reusing_valid_session(self, mock_exists, mock_kite_cls):
        mock_exists.return_value = True
        mock_kite = MagicMock()
        mock_kite_cls.return_value = mock_kite
        mock_kite.profile.return_value = {"user_id": "DPH959"}

        sess_data = {
            "timestamp": datetime.datetime.now().isoformat(),
            "access_token": "valid_token_123"
        }
        with patch("builtins.open", mock_open(read_data=json.dumps(sess_data))):
            adapter = self.provider.get_session(force_refresh=False)
            self.assertIsNotNone(adapter)
            self.assertEqual(adapter.access_token, "valid_token_123")
            mock_kite.set_access_token.assert_called_with("valid_token_123")
            mock_kite.profile.assert_called_once()

    @patch("kiteconnect.KiteConnect")
    @patch("os.path.exists")
    @patch("os.remove")
    def test_invalid_session_triggers_cleanup(self, mock_remove, mock_exists, mock_kite_cls):
        # Test that if profile() fails on cached session, it removes the file and handles failure
        mock_exists.return_value = True
        mock_kite = MagicMock()
        mock_kite_cls.return_value = mock_kite
        mock_kite.profile.side_effect = Exception("Incorrect api_key or access_token")

        sess_data = {
            "timestamp": datetime.datetime.now().isoformat(),
            "access_token": "expired_token_123"
        }
        with patch("builtins.open", mock_open(read_data=json.dumps(sess_data))):
            with patch("bot.config.settings.Config.KITE_ACCESS_TOKEN", None), \
                 patch("bot.config.settings.Config.KITE_USER_ID", None):
                adapter = self.provider.get_session(force_refresh=False)
                mock_remove.assert_called_with(SESSION_FILE_KITE)


if __name__ == "__main__":
    unittest.main()
