from bot.core.angel_connect import get_angel_session
import json

def cross_check():
    api = get_angel_session()
    if not api:
        print("Failed to connect")
        return
    
    # Tokens from previous debug: PE (42545), CE (42544), Spot (99926000)
    tokens = [
        {"exchange": "NSE", "symbol": "Nifty 50", "token": "99926000"},
        {"exchange": "NFO", "symbol": "NIFTY10FEB2625950CE", "token": "42544"},
        {"exchange": "NFO", "symbol": "NIFTY10FEB2625950PE", "token": "42545"}
    ]
    
    print("\n--- Market Data Fetch ---")
    results = {}
    for t in tokens:
        resp = api.ltpData(t["exchange"], t["symbol"], t["token"])
        if resp and resp.get('status'):
            ltp = resp['data']['ltp']
            results[t['symbol']] = ltp
            print(f"{t['symbol']}: {ltp}")
        else:
            print(f"Failed to fetch {t['symbol']}")
            results[t['symbol']] = 0
            
    # Calculation
    qty = 65
    ce_entry = 9.95 # From user debug
    pe_entry = 34.5
    
    ce_ltp = results.get('NIFTY10FEB2625950CE', 0)
    pe_ltp = results.get('NIFTY10FEB2625950PE', 0)
    
    ce_pnl = (ce_entry - ce_ltp) * qty
    pe_pnl = (pe_entry - pe_ltp) * qty
    total = ce_pnl + pe_pnl
    
    print("\n--- P&L Manual Check ---")
    print(f"CE P&L: ({ce_entry} - {ce_ltp}) * {qty} = {round(ce_pnl, 2)}")
    print(f"PE P&L: ({pe_entry} - {pe_ltp}) * {qty} = {round(pe_pnl, 2)}")
    print(f"TOTAL: {round(total, 2)}")

cross_check()
