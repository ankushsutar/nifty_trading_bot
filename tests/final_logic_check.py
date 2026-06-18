
# Standalone logic verification
def reconcile_logic(active_trades, broker_trades):
    reconciled = []
    
    # --- Build fill maps ---
    fill_map = {}
    for t in broker_trades:
        sym   = t.get('tradingsymbol', '')
        side  = t.get('transactiontype', '').upper()
        qty   = int(t.get('quantity', 0))
        price = float(t.get('averageprice', 0))

        if qty > 0 and price > 0:
            if sym not in fill_map:
                fill_map[sym] = {}
            if side not in fill_map[sym]:
                fill_map[sym][side] = {'total_value': 0.0, 'total_qty': 0}
            fill_map[sym][side]['total_value'] += price * qty
            fill_map[sym][side]['total_qty']   += qty

    # Calculate weighted averages
    avg_prices = {}
    for sym, sides in fill_map.items():
        avg_prices[sym] = {}
        for side, data in sides.items():
            if data['total_qty'] > 0:
                avg_prices[sym][side] = round(data['total_value'] / data['total_qty'], 2)

    # --- Match DB trades against broker fills ---
    for trade in active_trades:
        symbol      = trade.get('symbol', '')
        trade_id    = trade.get('id')
        status      = trade.get('status')
        
        # 1. Handle PLACED trades -> Match with BUY fill
        if status == "PLACED":
            buy_price = avg_prices.get(symbol, {}).get('BUY')
            if buy_price:
                reconciled.append(f"Trade #{trade_id} ({symbol}): PLACED -> OPEN (Fill: {buy_price})")
            continue

        # 2. Handle OPEN trades -> Match with SELL fill
        if status == "OPEN":
            entry_price = float(trade.get('entry_price', 0))
            qty         = int(trade.get('qty', 0))
            sell_price  = avg_prices.get(symbol, {}).get('SELL')
            
            if sell_price:
                pnl = round((sell_price - entry_price) * qty, 2)
                reconciled.append(f"Trade #{trade_id} ({symbol}): OPEN -> CLOSED (Exit: {sell_price} | PnL: {pnl})")

    return reconciled

# Test Case 1: Placed trade that filled
db_trades = [{'id': 100, 'symbol': 'NIFTY_BUY_TEST', 'status': 'PLACED'}]
br_trades = [{'tradingsymbol': 'NIFTY_BUY_TEST', 'transactiontype': 'BUY', 'quantity': 50, 'averageprice': 105.0}]
print("Test 1 Result:", reconcile_logic(db_trades, br_trades))

# Test Case 2: Open trade that exited
db_trades = [{'id': 101, 'symbol': 'NIFTY_SELL_TEST', 'status': 'OPEN', 'entry_price': 100.0, 'qty': 50}]
br_trades = [{'tradingsymbol': 'NIFTY_SELL_TEST', 'transactiontype': 'SELL', 'quantity': 50, 'averageprice': 120.0}]
print("Test 2 Result:", reconcile_logic(db_trades, br_trades))

# Test Case 3: Mixed session
db_trades = [
    {'id': 102, 'symbol': 'S1', 'status': 'PLACED'},
    {'id': 103, 'symbol': 'S2', 'status': 'OPEN', 'entry_price': 200.0, 'qty': 50}
]
br_trades = [
    {'tradingsymbol': 'S1', 'transactiontype': 'BUY', 'quantity': 50, 'averageprice': 50.0},
    {'tradingsymbol': 'S2', 'transactiontype': 'SELL', 'quantity': 50, 'averageprice': 250.0}
]
print("Test 3 Result:", reconcile_logic(db_trades, br_trades))
