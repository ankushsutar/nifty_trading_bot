import os
import sys
import datetime
from bot.core.trade_repo import trade_repo
from bot.config.settings import Config

def get_broker_client():
    """Initializes the active broker connection (Zerodha or Angel)."""
    try:
        from bot.core.session.factory import get_session
        return get_session()
    except Exception as e:
        print(f"[-] Could not connect to broker: {e}")
    return None

def run_reconciliation(auto_fix=True):
    print("=" * 70)
    print("🔍 CHRONOLOGICAL DAILY TRADING BOT RECONCILER")
    print("=" * 70)
    print(f"Active Broker Configuration: {Config.BROKER}")
    
    # 1. Fetch Today's trades from Database
    db_trades = trade_repo.get_today_trades(mode="LIVE")
    if not db_trades:
        print("[+] No live database trades recorded today.")
        return
        
    # Sort DB trades chronologically by created_at
    db_trades = sorted(db_trades, key=lambda x: x.get('created_at', datetime.datetime.min))
    print(f"[+] Loaded {len(db_trades)} live database trades for today.")
    
    # 2. Get Broker Connection
    broker = get_broker_client()
    if not broker:
        print("[-] Error: Broker connection unavailable. Cannot reconcile with live broker.")
        return
    
    # Fetch broker orders for today
    try:
        res = broker.orderBook()
        if not res or not res.get('status'):
            print(f"[-] Failed to fetch broker orders: {res.get('message')}")
            return
        broker_orders = res.get('data', [])
        # Sort completed orders chronologically by orderid
        completed_orders = sorted(
            [o for o in broker_orders if o['status'] == 'complete'],
            key=lambda x: str(x['orderid'])
        )
        print(f"[+] Retrieved {len(completed_orders)} completed orders from broker today.")
    except Exception as e:
        print(f"[-] Failed to fetch broker orders: {e}")
        return

    # Group completed broker orders by symbol
    symbol_fills = {}
    for o in completed_orders:
        sym = o['tradingsymbol']
        if sym not in symbol_fills:
            symbol_fills[sym] = {'BUY': [], 'SELL': []}
        symbol_fills[sym][o['transactiontype']].append(o)

    ghosts_fixed = 0
    price_corrected = 0
    net_pnl = 0.0

    print("\n--- Auditing Individual Trades (FIFO Matching) ---")
    for t in db_trades:
        tid = t['id']
        symbol = t['symbol']
        status = t['status']
        side = t.get('side', 'BUY')
        opposite_side = 'SELL' if side == 'BUY' else 'BUY'
        
        entry_price = t.get('entry_price', 0.0)
        exit_price = t.get('exit_price', 0.0)
        qty = t.get('qty', 0)
        pnl = t.get('pnl', 0.0)

        fills = symbol_fills.get(symbol, {'BUY': [], 'SELL': []})
        entry_fills = fills[side]
        exit_fills = fills[opposite_side]

        # 1. Match Entry Order (FIFO)
        if not entry_fills:
            # Ghost trade: recorded in DB but never filled on broker
            print(f"[⚠️ WARNING] Trade #{tid} ({symbol}) has no matching broker entry fills.")
            if status != 'CANCELLED':
                if auto_fix:
                    trade_repo.collection.update_one(
                        {'id': tid},
                        {'$set': {
                            'status': 'CANCELLED',
                            'pnl': 0.0,
                            'entry_price': entry_price,
                            'exit_price': entry_price,
                            'exit_reason': 'UNFILLED_ORDER'
                        }}
                    )
                    print(f"  [🔧 FIXED] Marked Trade #{tid} as CANCELLED, P&L set to 0.0.")
                    ghosts_fixed += 1
                else:
                    print("  [ ] Auto-fix is disabled. Needs manual cleanup.")
            continue

        # Pop the first available entry fill
        matched_entry = entry_fills.pop(0)
        actual_entry_price = matched_entry['averageprice']
        actual_qty = matched_entry['quantity']

        # 2. Match Exit Order if trade is closed (FIFO)
        actual_exit_price = exit_price
        if status == 'CLOSED':
            if exit_fills:
                matched_exit = exit_fills.pop(0)
                actual_exit_price = matched_exit['averageprice']
            else:
                # If no exit fill found but trade was closed, flag it
                print(f"[⚠️ WARNING] Trade #{tid} ({symbol}) is CLOSED in DB but has no matching broker exit fills left.")
                actual_exit_price = entry_price # Treat as cancelled/zero pnl or keep as is

        # 3. Check for differences and update
        is_dirty = False
        update_fields = {}

        if abs(actual_entry_price - entry_price) > 0.05:
            print(f"[✏️ Match] Trade #{tid} ({symbol}) Entry: DB={entry_price} -> Broker={actual_entry_price:.2f}")
            update_fields['entry_price'] = actual_entry_price
            is_dirty = True

        if status == 'CLOSED' and abs(actual_exit_price - exit_price) > 0.05:
            print(f"[✏️ Match] Trade #{tid} ({symbol}) Exit: DB={exit_price} -> Broker={actual_exit_price:.2f}")
            update_fields['exit_price'] = actual_exit_price
            is_dirty = True

        if actual_qty != qty:
            print(f"[✏️ Qty Match] Trade #{tid} ({symbol}) Qty: DB={qty} -> Broker={actual_qty}")
            update_fields['qty'] = actual_qty
            is_dirty = True

        # Compute correct P&L
        side_mult = 1 if side == 'BUY' else -1
        actual_pnl = round((actual_exit_price - actual_entry_price) * actual_qty * side_mult, 2)
        if status == 'CANCELLED':
            actual_pnl = 0.0

        if is_dirty or abs(actual_pnl - pnl) > 0.05:
            update_fields['pnl'] = actual_pnl
            if auto_fix:
                trade_repo.collection.update_one({'id': tid}, {'$set': update_fields})
                print(f"  [🔧 FIXED] Updated Trade #{tid} in MongoDB: Entry={actual_entry_price:.2f}, Exit={actual_exit_price:.2f}, Qty={actual_qty}, P&L=₹{actual_pnl:.2f}")
                price_corrected += 1
            else:
                print(f"  [ ] Auto-fix is disabled. Calculated P&L: ₹{actual_pnl:.2f}")
            net_pnl += actual_pnl
        else:
            net_pnl += pnl

    print("\n" + "=" * 70)
    print("📊 RECONCILIATION SUMMARY")
    print("=" * 70)
    print(f"  Ghost Trades Fixed      : {ghosts_fixed}")
    print(f"  Price Discrepancies Fixed: {price_corrected}")
    print(f"  Actual Net Daily P&L    : ₹{net_pnl:,.2f}")
    print("=" * 70)

if __name__ == "__main__":
    auto = "--no-fix" not in sys.argv
    run_reconciliation(auto_fix=auto)
