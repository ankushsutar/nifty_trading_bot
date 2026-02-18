import os
import json
import datetime
import pyotp
import time
import requests
from SmartApi import SmartConnect
from bot.config.settings import Config
from bot.utils.logger import logger
from bot.utils.rate_limiter import rate_limiter

# ----------------- ROOT CAUSE FIX: SDK PATCHING -----------------
def patch_sdk():
    """
    Overrides the hardcoded IPs in SmartConnect library.
    Prioritizes Config.STATIC_IP if provided, otherwise detects dynamically.
    """
    try:
        if Config.STATIC_IP:
            public_ip = Config.STATIC_IP
            logger.info(f">>> [System] Using Configured Static IP: {public_ip} 🛡️")
        else:
            # Detect Real Public IP
            public_ip = requests.get('https://api.ipify.org', timeout=5).text.strip()
            logger.info(f">>> [System] SDK Patched. Detected IP: {public_ip} 🌐")
        
        # Override Class Attributes (used by SmartConnect to generate headers)
        SmartConnect.clientPublicIp = public_ip
        SmartConnect.clientLocalIp = "127.0.0.1" 
        
    except Exception as e:
        logger.warning(f">>> [System] IP Detection failed, using default: {e}")

# Run once at module level
patch_sdk()
# -----------------------------------------------------------------

SESSION_FILE = os.path.join(os.getcwd(), "data", "session.json")

def get_angel_session(force_refresh=False):
    """Establishes or restores an Angel One API session."""
    print(">>> [System] Connecting to Angel One...")
    
    # Enable connection pooling at initialization using standard requests params
    api = SmartConnect(api_key=Config.API_KEY, pool={'pool_connections': 10, 'pool_maxsize': 10}) 

    # 1. Try to load existing session
    if os.path.exists(SESSION_FILE) and not force_refresh:
        try:
            with open(SESSION_FILE, "r") as f:
                sess_data = json.load(f)

            sess_time = datetime.datetime.fromisoformat(sess_data['timestamp'])
            session_age_hours = (datetime.datetime.now() - sess_time).total_seconds() / 3600

            # Angel One JWT tokens expire in ~4 hours — refresh proactively
            # Only reuse if: same day AND less than 4 hours old
            if sess_time.date() == datetime.date.today() and session_age_hours < 4:
                api = SmartConnect(api_key=Config.API_KEY)
                api.setAccessToken(sess_data['jwtToken'])
                api.setRefreshToken(sess_data['refreshToken'])
                api.setFeedToken(sess_data.get('feedToken', ''))

                # Verify session is still valid
                from bot.utils.rate_limiter import rate_limiter
                rate_limiter.wait()

                profile = api.getProfile(sess_data['refreshToken'])
                if profile and profile.get('status'):
                    print(f">>> [System] Reusing Session (age: {session_age_hours:.1f}h) ✅")
                    return api
                else:
                    print(">>> [System] Session invalid. Re-logging...")
            elif session_age_hours >= 4:
                print(f">>> [System] Session expired ({session_age_hours:.1f}h old). Refreshing...")
            else:
                print(">>> [System] Session is from a previous day. Re-logging...")
        except Exception as e:
            pass

    # 2. Generate New Session (Rate Limited Action)
    try:
        from bot.utils.rate_limiter import rate_limiter
        rate_limiter.wait()
        
        api = SmartConnect(api_key=Config.API_KEY)
        totp = pyotp.TOTP(Config.TOTP_SECRET).now()
        
        data = api.generateSession(Config.CLIENT_ID, Config.PASSWORD, totp)
        
        if data['status']:
            print(">>> [System] Login Successful!")
            
            # Save Session for other processes
            # Strip "Bearer " if present (SDK returns it with prefix in response data)
            jwt_token = data['data']['jwtToken']
            if jwt_token.startswith("Bearer "):
                jwt_token = jwt_token.replace("Bearer ", "")

            sess_info = {
                "timestamp": datetime.datetime.now().isoformat(),
                "jwtToken": jwt_token,
                "refreshToken": data['data']['refreshToken'],
                "feedToken": data['data'].get('feedToken') or getattr(api, 'feed_token', '')
            }
            
            if not os.path.exists(os.path.dirname(SESSION_FILE)):
                os.makedirs(os.path.dirname(SESSION_FILE))
                
            with open(SESSION_FILE, "w") as f:
                json.dump(sess_info, f)
                
            return api
        else:
            print(f">>> [Error] Login Failed: {data['message']}")
            return None
    except Exception as e:
        print(f">>> [Error] Connection Error: {e}")
        return None
