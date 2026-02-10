import requests
import time

BASE_URL = "http://localhost:8000/api"

def verify_mode():
    print("🧪 Verifying Simulation/Live Mode Switching...")
    time.sleep(3) # Wait for server
    
    try:
        # 1. Default State
        res = requests.get(f"{BASE_URL}/daily-summary")
        data = res.json()
        print(f"Default Mode: {data.get('mode')}")
        if data.get('mode') != "PAPER":
            print("❌ Default mode should be PAPER")
            return
            
        # 2. Start Simulation
        print("▶️ Starting Simulation...")
        requests.post(f"{BASE_URL}/start", json={"strategy": "MOMENTUM", "dry_run": True})
        time.sleep(1)
        res = requests.get(f"{BASE_URL}/daily-summary")
        mode = res.json().get('mode')
        print(f"Simulation Mode: {mode}")
        if mode != "PAPER":
             print("❌ Failed to switch to PAPER mode")
        
        # 3. Stop
        print("⏹️ Stopping...")
        requests.post(f"{BASE_URL}/stop")
        time.sleep(1)
        
        # 4. Start Live
        print("▶️ Starting LIVE...")
        requests.post(f"{BASE_URL}/start", json={"strategy": "MOMENTUM", "dry_run": False})
        time.sleep(1)
        res = requests.get(f"{BASE_URL}/daily-summary")
        mode = res.json().get('mode')
        print(f"Live Mode: {mode}")
        if mode != "LIVE":
             print("❌ Failed to switch to LIVE mode")
        
        # 5. Stop
        requests.post(f"{BASE_URL}/stop")
        print("✅ Mode Verification Complete")

    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    verify_mode()
