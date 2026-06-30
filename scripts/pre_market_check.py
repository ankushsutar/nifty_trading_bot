import sys
import os
import datetime
import re

# Add project root to sys.path
sys.path.append(os.getcwd())

from bot.core.trade_repo import TradeRepository, trade_repo
from bot.core.angel_connect import get_angel_session
from bot.utils.token_lookup import TokenLookup
from bot.utils.expiry_calculator import is_trading_day

def run_pre_market_checks():
    print("=" * 60)
    print("        NIFTY TRADING BOT PRE-MARKET SANITY CHECKER        ")
    print("=" * 60)

    # 1. Test Database Connection
    print("\n[1/4] Checking MongoDB Connection...")
    try:
        active_db_count = trade_repo.collection.count_documents({})
        open_trades = list(trade_repo.collection.find({"status": {"$in": ["OPEN", "PLACED"]}}))
        print(f"  ✅ MongoDB: OK (Total Trade Records: {active_db_count})")
        print(f"  👉 Active/Open Trades in DB: {len(open_trades)}")
        for t in open_trades:
            print(f"     - ID: {t.get('id')} | Symbol: {t.get('symbol')} | Status: {t.get('status')} | Leg: {t.get('leg')}")
    except Exception as e:
        print(f"  ❌ MongoDB Connection Error: {e}")

    # 2. Test Broker API Connection
    print("\n[2/4] Checking Broker API Session...")
    try:
        api = get_angel_session()
        if api:
            pos_resp = api.position()
            if pos_resp and pos_resp.get('status'):
                positions = pos_resp.get('data') or []
                active_pos = [p for p in positions if int(p.get('netqty', 0)) != 0]
                print(f"  ✅ Broker API: Connection Successful")
                print(f"  👉 Active Broker Positions: {len(active_pos)}")
                for p in active_pos:
                    print(f"     - Symbol: {p['tradingsymbol']} | Qty: {p['netqty']} | AvgPrice: {p['avgnetprice']}")
            else:
                print("  ⚠️ Broker Position API returned failure status. Session might need regeneration.")
        else:
            print("  ❌ Broker API Session: Failed to authenticate.")
    except Exception as e:
        print(f"  ❌ Broker API Connection Error: {e}")

    # 3. Test Expiry Calculation Logic
    print("\n[3/4] Testing Tuesday Expiry & Holiday Logic...")
    
    # Try finding active Nifty options in TokenLookup
    tl = TokenLookup()
    if tl.df is None:
        try:
            tl.load_scrip_master()
        except Exception:
            pass
            
    active_monthly_sym = None
    if tl.df is not None:
        # Match only actual weekly/monthly option symbols like NIFTY26JUN22000CE
        opt_mask = tl.df['symbol'].str.match(r'^NIFTY\d{2}[A-Z0-9]{3,5}\d+(CE|PE)$')
        nifty_rows = tl.df[opt_mask]
        if not nifty_rows.empty:
            active_monthly_sym = nifty_rows.iloc[0]['symbol']

    # If no options found, fallback to June 2026 monthly
    if not active_monthly_sym:
        active_monthly_sym = "NIFTY26JUN22000CE"

    test_symbols = [
        ("NIFTY24JUN0322000CE", True, "Definite past weekly"),
        ("NIFTY35JUN22000CE", True, "Definite future monthly (not in active scrip master list)"),
        (active_monthly_sym, False, "Active options contract (in active scrip master list)"),
    ]

    for sym, expected_expired, desc in test_symbols:
        expired = trade_repo._is_symbol_expired(sym)
        status_str = "EXPIRED" if expired else "ACTIVE"
        expected_str = "EXPIRED" if expected_expired else "ACTIVE"
        icon = "✅" if expired == expected_expired else "❌"
        print(f"  {icon} Symbol: {sym:<25} | Status: {status_str:<8} | Expected: {expected_str} | ({desc})")

    # 4. Check trading holiday database configuration
    print("\n[4/4] Checking NSE Holiday Configurations for 2026...")
    today = datetime.date.today()
    is_today_trading = is_trading_day(today)
    print(f"  👉 Today is {today.strftime('%A, %b %d, %Y')}")
    print(f"  👉 Is Today an NSE Trading Day?: {'✅ YES' if is_today_trading else '🚫 NO (Holiday or Weekend)'}")

    print("\n" + "=" * 60)
    print("  Sanity check complete. Run this script at any time to verify system health.")
    print("=" * 60)

if __name__ == "__main__":
    run_pre_market_checks()
