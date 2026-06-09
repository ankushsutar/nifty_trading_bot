import os
import json
import datetime
# pyrefly: ignore [missing-import]
import pyotp
import requests
from bot.config.settings import Config
from bot.utils.logger import logger
from bot.utils.rate_limiter import rate_limiter
from bot.core.broker_adapter import KiteBrokerAdapter
from bot.core.session.base import BaseSessionProvider

SESSION_FILE_KITE = os.path.join(os.getcwd(), "data", "session_kite.json")

class KiteSessionProvider(BaseSessionProvider):
    def get_session(self, force_refresh: bool = False):
        """Establishes or restores a Zerodha Kite Connect API session."""
        print(">>> [System] Connecting to Zerodha Kite Connect...")
        # pyrefly: ignore [missing-import]
        from kiteconnect import KiteConnect
        
        # Initialize client
        kite = KiteConnect(api_key=Config.KITE_API_KEY)
        
        # 1. Try to restore session from file
        if os.path.exists(SESSION_FILE_KITE) and not force_refresh:
            try:
                with open(SESSION_FILE_KITE, "r") as f:
                    sess_data = json.load(f)
                
                # Check if same day (Kite access tokens are valid for one day)
                sess_time = datetime.datetime.fromisoformat(sess_data['timestamp'])
                if sess_time.date() == datetime.date.today():
                    access_token = sess_data['access_token']
                    kite.set_access_token(access_token)
                    print(">>> [System] Reusing Zerodha Session ✅")
                    return KiteBrokerAdapter(kite, access_token)
            except Exception as e:
                logger.warning(f"Kite session restoration failed: {e}")
                
        # 2. Check if KITE_ACCESS_TOKEN is hardcoded in .env (Developer/Manual fallback)
        if Config.KITE_ACCESS_TOKEN:
            print(">>> [System] Using configured KITE_ACCESS_TOKEN from .env...")
            access_token = Config.KITE_ACCESS_TOKEN
            kite.set_access_token(access_token)
            
            # Save session
            sess_info = {
                "timestamp": datetime.datetime.now().isoformat(),
                "access_token": access_token
            }
            os.makedirs(os.path.dirname(SESSION_FILE_KITE), exist_ok=True)
            with open(SESSION_FILE_KITE, "w") as f:
                json.dump(sess_info, f)
                
            return KiteBrokerAdapter(kite, access_token)

        # 3. Perform automated login via Kite UI credentials (auto-authenticate flow)
        if Config.KITE_USER_ID and Config.KITE_PASSWORD and Config.KITE_TOTP_SECRET:
            try:
                print(">>> [System] Attempting automated login to Zerodha...")
                rate_limiter.wait()
                
                session = requests.Session()
                
                # Step A: Get login request
                resp = session.post("https://kite.zerodha.com/api/login", data={
                    "user_id": Config.KITE_USER_ID,
                    "password": Config.KITE_PASSWORD
                }, timeout=15)
                login_data = resp.json()
                
                if login_data.get("status") == "success":
                    request_id = login_data["data"]["request_id"]
                    totp_val = pyotp.TOTP(Config.KITE_TOTP_SECRET).now()
                    
                    # Step B: 2FA
                    resp2 = session.post("https://kite.zerodha.com/api/twofa", data={
                        "user_id": Config.KITE_USER_ID,
                        "request_id": request_id,
                        "twofa_value": totp_val
                    }, timeout=15)
                    twofa_data = resp2.json()
                    
                    if twofa_data.get("status") == "success":
                        # Step C: Authorize/Get Request Token (Follow redirects manually to avoid local server connection errors)
                        login_url = f"https://kite.trade/connect/login?api_key={Config.KITE_API_KEY}&v=3"
                        
                        current_url = login_url
                        final_url = None
                        for redirect_hop in range(10):
                            try:
                                resp3 = session.get(current_url, allow_redirects=False, timeout=15)
                                if 300 <= resp3.status_code < 400:
                                    next_url = resp3.headers.get("Location")
                                    if not next_url:
                                        break
                                    if not next_url.startswith("http"):
                                        next_url = requests.compat.urljoin(resp3.url, next_url)
                                    
                                    # Intercept the redirect when it contains the request_token
                                    if "request_token=" in next_url:
                                        final_url = next_url
                                        break
                                    current_url = next_url
                                else:
                                    final_url = resp3.url
                                    break
                            except Exception as ce:
                                logger.warning(f"Redirect failed during automated login hop: {ce}")
                                break
                                
                        if not final_url:
                            final_url = current_url
                            
                        import urllib.parse as urlparse
                        parsed = urlparse.urlparse(final_url)
                        request_tokens = urlparse.parse_qs(parsed.query).get('request_token')
                        
                        if request_tokens:
                            request_token = request_tokens[0]
                            # Step D: Generate Session
                            data = kite.generate_session(request_token, api_secret=Config.KITE_API_SECRET)
                            access_token = data["access_token"]
                            
                            sess_info = {
                                "timestamp": datetime.datetime.now().isoformat(),
                                "access_token": access_token
                            }
                            os.makedirs(os.path.dirname(SESSION_FILE_KITE), exist_ok=True)
                            with open(SESSION_FILE_KITE, "w") as f:
                                json.dump(sess_info, f)
                                
                            kite.set_access_token(access_token)
                            print(">>> [System] Automated Login Successful! 🟢")
                            return KiteBrokerAdapter(kite, access_token)
                        else:
                            print(">>> [Error] Failed to parse request_token from final URL.")
                    else:
                        print(f">>> [Error] 2FA Failed: {twofa_data.get('message')}")
                else:
                    print(f">>> [Error] Login API Failed: {login_data.get('message')}")
            except Exception as e:
                print(f">>> [Error] Automated Zerodha login failed: {e}")
                
        print(">>> [Error] Could not authenticate with Zerodha. Please check KITE credentials or set KITE_ACCESS_TOKEN.")
        return None
