import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient
from backend.server import app
import json
import time

# Mock dependencies
from unittest.mock import patch, MagicMock

def test_websocket_logs():
    # Patch dependencies to avoid real API calls
    with patch('backend.bot_manager.get_angel_session') as mock_session, \
         patch('backend.bot_manager.TokenLookup') as mock_loader:
            
        mock_session.return_value = MagicMock()
        mock_loader.return_value = MagicMock()

        # Use context manager to trigger startup events (background tasks)
        with TestClient(app) as client:
            
            # 1. Connect to WebSocket
            with client.websocket_connect("/ws/logs") as websocket:
                
                # 2. Trigger Logs via API
                print(">>> [Test] Starting Bot via API...")
                client.post("/api/start", json={"strategy": "MOMENTUM", "dry_run": True})
                
                # 3. Listen for Logs
                print(">>> [Test] Listening for logs...")
                received_log = False
                for i in range(5):
                    try:
                        data = websocket.receive_text()
                        log_obj = json.loads(data)
                        print(f"Received: {log_obj}")
                        
                        if "Starting Bot Manager" in log_obj["message"]:
                            received_log = True
                            print(">>> ✅ Verified: Received Startup Log")
                            break
                    except Exception as e:
                        print(f"Waiting... {e}")
                    
                    # Wait a bit? receive_text() is blocking in TestClient usually
                
                assert received_log, "Did not receive expected log message"

                # 4. Stop
                client.post("/api/stop")

if __name__ == "__main__":
    test_websocket_logs()
