from bot.core.angel_connect import get_angel_session
from pprint import pprint

session = get_angel_session()
if session:
    print("\n--- FETCHING ACTUAL BROKER TRADE BOOK ---")
    response = session.tradeBook()
    if response and response.get('status'):
        trades = response.get('data', [])
        if not trades:
            print("No trades executed on the broker today.")
        else:
            print(f"Found {len(trades)} executed trades on Angel One:")
            for t in trades:
                print(f"Broker Order ID: {t.get('orderid')} | Symbol: {t.get('tradingsymbol')} | Type: {t.get('transactiontype')} | Qty: {t.get('fillsize')} | Fill Price: {t.get('fillprice')}")
    else:
        print(f"Failed to fetch trade book: {response}")

    print("\n--- FETCHING BROKER ORDER BOOK (includes Open/Cancelled) ---")
    ob_res = session.orderBook()
    if ob_res and ob_res.get('status'):
        orders = ob_res.get('data', [])
        if not orders:
            print("No orders placed on the broker today.")
        else:
            print(f"Found {len(orders)} orders on Angel One:")
            for o in orders:
                if 'DRY_' in str(o.get('orderid')): continue # skip dry run dummy orders if any
                print(f"ID: {o.get('orderid')} | Symbol: {o.get('tradingsymbol')} | Status: {o.get('status')} | Type: {o.get('transactiontype')} | Qty: {o.get('quantity')} | Price: {o.get('price')}")
else:
    print("Could not connect to Angel One.")
