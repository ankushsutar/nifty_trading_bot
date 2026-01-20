import sys
import os

# Add the parent directory to sys.path to allow importing 'core'
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.angel_connect import get_angel_session

def verify_credentials():
    print("\n>>> [Verify] Attempting to login with .env credentials...")
    
    # helper function from core/angel_connect.py
    # performs login and returns the API object if successful
    api = get_angel_session()
    
    if api:
        print("\n>>> [Success] Authentication Passed! ✅")
        try:
            # Fetch generic profile data to prove the session is active
            profile = api.getProfile(api.refreshToken)
            if profile and profile.get('status'):
                data = profile['data']
                print(f"    Name: {data.get('name')}")
                print(f"    Client Code: {data.get('clientcode')}")
                print(f"    Exchanges: {data.get('exchanges')}")
            else:
                print("    (Could not fetch detailed profile, but login token seems valid of initial session generation)")
        except Exception as e:
            print(f"    Warning: Could not fetch profile details: {e}")
            
        print("\n>>> You are ready for Live Trading. Run 'python3 main.py' to execute strategies.")
    else:
        print("\n>>> [Failed] Authentication Rejected. ❌")
        print("    Please check your .env file for correct API_KEY, CLIENT_ID, PASSWORD, and TOTP_SECRET.")

if __name__ == "__main__":
    verify_credentials()
