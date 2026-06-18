import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient
from backend.server import app

client = TestClient(app)

def test_api_status():
    response = client.get("/api/status")
    assert response.status_code == 200
    print(f"Status Output: {response.json()}")
    assert response.json()["status"] == "STOPPED"

from unittest.mock import patch, MagicMock

# Mock the dependencies BEFORE importing backend.server implies importing bot_manager
# so we need to patch where they are used: backend.bot_manager

def test_start_and_stop():
    with patch('backend.bot_manager.get_angel_session') as mock_session, \
         patch('backend.bot_manager.TokenLookup') as mock_loader:
        
        # Setup Mocks
        mock_session.return_value = MagicMock() # Mock API object
        mock_loader.return_value = MagicMock()
        
        # 1. Start
        response = client.post("/api/start", json={"strategy": "MOMENTUM", "dry_run": True})
        assert response.status_code == 200
        print(f"Start Output: {response.json()}")
        
        # 2. Check Status
        response = client.get("/api/status")
        assert response.json()["status"] == "RUNNING"
        print(f"Status Running: {response.json()}")

        # 3. Stop
        response = client.post("/api/stop")
        assert response.status_code == 200
        print(f"Stop Output: {response.json()}")

        # 4. Check Status
        response = client.get("/api/status")
        print(f"Status After Stop: {response.json()}")

if __name__ == "__main__":
    test_api_status()
    test_start_and_stop()
