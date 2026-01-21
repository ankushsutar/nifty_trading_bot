import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.angel_connect import get_angel_session
from utils.token_lookup import TokenLookup
from core.oi_analyzer import OIAnalyzer
from utils.expiry_calculator import get_next_weekly_expiry

def test_on_real_market():
    print(">>> [Test] Integrating with Real Angel One API...")
    
    # 1. Login
    api = get_angel_session()
    if not api:
        print(">>> [Error] Login Failed. Check credentials.")
        return

    # 2. Loader
    loader = TokenLookup()
    loader.load_scrip_master()
    
    # 3. Analyzer
    analyzer = OIAnalyzer(api, loader)
    
    # 4. Parameters
    expiry = get_next_weekly_expiry()
    
    # Fetch Nifty Spot to guess ATM
    try:
        data = api.ltpData("NSE", "Nifty 50", "99926000")
        ltp = data['data']['ltp']
        atm = round(ltp / 50) * 50
        print(f">>> [Market] Nifty Spot: {ltp} | Analyzed ATM: {atm} | Expiry: {expiry}")
        
        # 5. Run Scan
        pcr = analyzer.get_pcr(expiry, atm)
        sentiment = analyzer.analyze_sentiment(pcr)
        
        # DEBUG: deep dive into one token
        ce_token, _ = loader.get_token("NIFTY", expiry, atm, "CE")
        print(f"\n>>> DEBUG: Inspecting Raw Candle Data for {atm} CE ({ce_token}):")
        
        import datetime
        today = datetime.datetime.now()
        from_date = (today - datetime.timedelta(days=5)).strftime("%Y-%m-%d %H:%M")
        to_date = today.strftime("%Y-%m-%d %H:%M")
        
        print(f"\n>>> DEBUG: Inspecting ltpData for {atm} CE ({ce_token}):")
        try:
            l_resp = api.ltpData("NFO", f"NIFTY{expiry}CE", ce_token)
            print(l_resp)
        except Exception as e:
            print(e)
            
        print("\n" + "="*40)
        print(f"   PUT-CALL RATIO (PCR): {pcr}")
        print(f"   MARKET SENTIMENT:     {sentiment}")
        print("="*40 + "\n")
        
    except Exception as e:
        print(f">>> [Error] {e}")

if __name__ == "__main__":
    test_on_real_market()
