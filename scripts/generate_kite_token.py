import os
import sys
import urllib.parse as urlparse

# Add the parent directory to sys.path to allow importing bot config
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from bot.config.settings import Config
from dotenv import load_dotenv

def generate_token():
    load_dotenv()
    
    api_key = os.getenv("KITE_API_KEY")
    api_secret = os.getenv("KITE_API_SECRET")
    
    if not api_key or not api_secret:
        print("❌ [Error] KITE_API_KEY and KITE_API_SECRET must be set in your .env file first.")
        sys.exit(1)
        
    print("\n" + "="*60)
    print("🔑 Zerodha Kite Access Token Generator")
    print("="*60)
    
    # 1. Generate and print login URL
    login_url = f"https://kite.trade/connect/login?api_key={api_key}&v=3"
    print("\n👉 Step 1: Open the following URL in your web browser:")
    print(login_url)
    
    print("\n👉 Step 2: Log in and click 'Authorize' if prompted.")
    print("After successful login, your browser will redirect to a page (e.g. localhost or kite.trade).")
    
    # 2. Get the redirected URL from user
    redirect_url = input("\n👉 Step 3: Copy the entire redirected URL from your browser's address bar and paste it here:\n").strip()
    
    # Parse request_token
    request_token = None
    if "request_token=" in redirect_url:
        parsed = urlparse.urlparse(redirect_url)
        params = urlparse.parse_qs(parsed.query)
        request_token = params.get('request_token')[0]
    else:
        # User might have pasted just the token
        request_token = redirect_url
        
    if not request_token or len(request_token) < 5:
        print("❌ [Error] Could not find a valid request_token in the input.")
        sys.exit(1)
        
    print(f"\n🔄 Exchanging request_token: {request_token} ...")
    
    # 3. Exchange for access token using kiteconnect
    try:
        from kiteconnect import KiteConnect
        kite = KiteConnect(api_key=api_key)
        data = kite.generate_session(request_token, api_secret=api_secret)
        access_token = data["access_token"]
        user_name = data.get("user_name", "User")
        
        print(f"\n✅ [Success] Authenticated successfully as: {user_name}!")
        print(f"🔑 Your Access Token is: {access_token}")
        
        # 4. Save to data/session_kite.json
        import datetime
        import json
        sess_info = {
            "timestamp": datetime.datetime.now().isoformat(),
            "access_token": access_token
        }
        session_file = os.path.join(os.getcwd(), "data", "session_kite.json")
        os.makedirs(os.path.dirname(session_file), exist_ok=True)
        with open(session_file, "w") as f:
            json.dump(sess_info, f)
        print(f"💾 Saved session to: data/session_kite.json")
        
        # 5. Update .env file
        env_path = ".env"
        env_lines = []
        token_line_found = False
        
        if os.path.exists(env_path):
            with open(env_path, "r") as f:
                env_lines = f.readlines()
                
            for i, line in enumerate(env_lines):
                if line.startswith("KITE_ACCESS_TOKEN="):
                    env_lines[i] = f"KITE_ACCESS_TOKEN={access_token}\n"
                    token_line_found = True
                    break
            
            if not token_line_found:
                env_lines.append(f"\nKITE_ACCESS_TOKEN={access_token}\n")
                
            with open(env_path, "w") as f:
                f.writelines(env_lines)
            print("📝 Updated KITE_ACCESS_TOKEN in your .env file!")
        else:
            print("⚠️ .env file not found, please manually add: KITE_ACCESS_TOKEN=" + access_token)
            
        print("\n🎉 Setup complete! You are ready to run the bot.")
        
    except Exception as e:
        print(f"❌ [Error] Failed to generate access token: {e}")
        sys.exit(1)

if __name__ == "__main__":
    generate_token()
