from core.angel_connect import get_angel_session

def test_vix():
    api = get_angel_session()
    if not api:
        print("Login Failed")
        return

    # Using the correct token found from debug_scrip_master.py
    # {'token': '99926017', 'symbol': 'India VIX', 'name': 'INDIA VIX', ...}
    tokens_to_test = [
        ("INDIA VIX", "99926017"),
    ]

    print("\n--- Testing VIX Fetch ---")
    for symbol, token in tokens_to_test:
        try:
            print(f"Fetching {symbol} ({token})...")
            resp = api.ltpData("NSE", symbol, token)
            print(f"Response: {resp}")
        except Exception as e:
            print(f"Error: {e}")

if __name__ == "__main__":
    test_vix()
