
import sys
import os

# Add project root to sys.path
sys.path.append(os.getcwd())

from bot.core.angel_connect import get_angel_session
from bot.utils.token_lookup import TokenLookup
from bot.config.settings import Config
from bot.utils.logger import logger

def close_orphaned_position():
    print("--- Connecting to Broker ---")
    api = get_angel_session()
    if not api:
        print("❌ Error: Broker API not ready.")
        return

    print("--- Fetching Open Positions ---")
    pos_res = api.position()
    if not pos_res or not pos_res.get('status'):
        print("❌ Error fetching positions.")
        return

    positions = pos_res.get('data') or []
    open_positions = [p for p in positions if int(p.get('netqty', 0)) != 0]

    if not open_positions:
        print("✅ No open positions found on the broker.")
        return

    print(f"⚠️ Found {len(open_positions)} open positions. Attempting to close them...")
    
    loader = TokenLookup()
    loader.load_scrip_master()

    for p in open_positions:
        if not isinstance(p, dict):
            print(f"Skipping unexpected position entry type: {type(p)}")
            continue
            
        symbol = p['tradingsymbol']
        qty = int(p['netqty'])
        # If netqty is positive, we hold long (need to SELL). If negative, we hold short (need to BUY).
        transaction_type = "SELL" if qty > 0 else "BUY"
        abs_qty = abs(qty)
        
        token = p['symboltoken']
        exchange = p['exchange']
        
        print(f"Closing {symbol} (Qty: {qty}) via {transaction_type} order...")
        
        orderparams = {
            "variety": "NORMAL",
            "tradingsymbol": symbol,
            "symboltoken": token,
            "transactiontype": transaction_type,
            "exchange": exchange,
            "ordertype": "MARKET",
            "producttype": "INTRADAY",
            "duration": "DAY",
            "price": "0",
            "squareoff": "0",
            "stoploss": "0",
            "quantity": str(abs_qty)
        }
        
        try:
            if not Config.LIVE_TRADE_ENABLED:
                print(f"🧪 [DRY RUN] Would simulate MARKET exit for {symbol}")
                continue
                
            response = api.placeOrder(orderparams)
            if response and response.get('status'):
                order_id = response['data']['orderid']
                print(f"✅ Success: Order placed to close {symbol}. Order ID: {order_id}")
            else:
                print(f"❌ Failed to close {symbol}: {response}")
        except Exception as e:
            print(f"❌ Error closing {symbol}: {e}")

if __name__ == "__main__":
    close_orphaned_position()
