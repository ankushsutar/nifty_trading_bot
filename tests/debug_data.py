from bot.core.angel_connect import get_angel_session
import json

def check():
    api = get_angel_session()
    if not api:
        print("Failed to connect")
        return
    
    # Nifty Spot
    nifty = api.ltpData("NSE", "Nifty 50", "99926000")
    print(f"Nifty Spot: {json.dumps(nifty, indent=2)}")
    
    # User's Tokens (from their screenshot/text)
    # PE: NIFTY10FEB2625950PE
    # Token for 25950 PE? I'll fetch it using TokenLookup if needed, 
    # but the user didn't give the token. 
    # I can search for it in the script or just check Nifty.
    
    # Actually let's just check Nifty. If Nifty is > 25950, then PE profit should be 100%.
    # If Nifty is < 25950, PE has intrinsic value.
    
check()
