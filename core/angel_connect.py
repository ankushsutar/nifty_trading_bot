from SmartApi import SmartConnect
import pyotp
from config.settings import Config

def get_angel_session():
    print(">>> [System] Connecting to Angel One...")
    try:
        api = SmartConnect(api_key=Config.API_KEY)
        totp = pyotp.TOTP(Config.TOTP_SECRET).now()
        
        data = api.generateSession(Config.CLIENT_ID, Config.PASSWORD, totp)
        
        if data['status']:
            print(">>> [System] Login Successful!")
            return api
        else:
            print(f">>> [Error] Login Failed: {data['message']}")
            return None
    except Exception as e:
        print(f">>> [Error] Connection Error: {e}")
        return None
