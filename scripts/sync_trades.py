#!/usr/bin/env python3
"""
sync_trades.py  —  Fetch today's Angel One trade book and sync MongoDB.

Usage:
    python3 scripts/sync_trades.py          # show + fix open trades
    python3 scripts/sync_trades.py --dry    # show only, no DB writes
"""
import sys, os, datetime, argparse, time

# Allow running from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from bot.core.angel_connect import get_angel_session
from bot.core.trade_repo import trade_repo
from bot.utils.logger import logger


def fetch_trade_book(api):
    """Returns today's executed trades from Angel One."""
    try:
        time.sleep(1)           # respect rate limit
        resp = api.tradeBook()
        if resp and resp.get('status'):
            return resp.get('data') or []
        print(f"  [!] tradeBook() returned: {resp}")
    except Exception as e:
        print(f"  [!] tradeBook() error: {e}")
    return []


def fetch_order_book(api):
    """Returns today's orders from Angel One (for matched order data)."""
    try:
        time.sleep(1)
        resp = api.orderBook()
        if resp and resp.get('status'):
            return resp.get('data') or []
    except Exception as e:
        print(f"  [!] orderBook() error: {e}")
    return []


def build_exit_map(trades):
    """
    Groups trade-book rows by symbol → calculates weighted avg exit price
    for SELL side (options are bought, so SELL = exit).
    Returns dict: symbol -> {avg_price, qty, side}
    """
    sell_map = {}
    for t in trades:
        sym   = t.get('tradingsymbol', '')
        side  = t.get('transactiontype', '').upper()  # BUY / SELL
        qty   = int(t.get('quantity', 0) or 0)
        price = float(t.get('averageprice', 0) or 0)

        if side == 'SELL' and qty > 0:
            if sym not in sell_map:
                sell_map[sym] = {'total_value': 0.0, 'total_qty': 0}
            sell_map[sym]['total_value'] += price * qty
            sell_map[sym]['total_qty']   += qty

    # Weighted average price per symbol
    result = {}
    for sym, data in sell_map.items():
        if data['total_qty'] > 0:
            result[sym] = round(data['total_value'] / data['total_qty'], 2)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry', action='store_true', help='Show changes without writing to DB')
    args = parser.parse_args()

    print("\n=== Angel One → MongoDB Trade Sync ===")
    print(f"Mode: {'DRY RUN (no DB writes)' if args.dry else 'LIVE (will update DB)'}\n")

    # 1. Connect to Angel One
    print("Connecting to Angel One...")
    api = get_angel_session()
    if not api:
        print("ERROR: Could not establish session. Make sure the bot has logged in today.")
        sys.exit(1)
    print("Connected ✅\n")

    # 2. Fetch trade book
    print("Fetching today's trade book...")
    broker_trades = fetch_trade_book(api)
    print(f"  → {len(broker_trades)} trade(s) found in Angel One today.\n")

    if not broker_trades:
        print("No trades in Angel One today. Nothing to sync.")
        sys.exit(0)

    # 3. Print broker trades
    print("BROKER TRADE BOOK:")
    print(f"  {'Symbol':<35} {'Side':<6} {'Qty':<6} {'AvgPrice':<10} {'Status'}")
    print("  " + "-" * 70)
    for t in broker_trades:
        print(f"  {t.get('tradingsymbol',''):<35} {t.get('transactiontype',''):<6} "
              f"{t.get('quantity',''):<6} {t.get('averageprice',''):<10} {t.get('orderstatus','')}")

    # 4. Build exit price map (SELL fills → weighted avg)
    exit_map = build_exit_map(broker_trades)
    print(f"\nSELL exits detected: {list(exit_map.keys())}")

    # 5. Fetch OPEN DB trades
    open_trades = trade_repo.get_open_trades()
    print(f"\nOPEN trades in MongoDB: {len(open_trades)}")

    if not open_trades:
        print("No OPEN trades in DB — nothing to close.")
        return

    print(f"\n{'ID':<5} {'Symbol':<35} {'Entry':>8} {'ExitUsed':>10} {'PnL':>10}  Action")
    print("-" * 82)

    closed = 0
    for trade in open_trades:
        t_id       = trade.get('id')
        symbol     = trade.get('symbol', '')
        entry      = float(trade.get('entry_price', 0))
        qty        = int(trade.get('qty', 0))

        # Use real SELL fill if available, else fallback to entry price (PnL = 0)
        exit_price = exit_map.get(symbol)
        if exit_price:
            pnl    = round((exit_price - entry) * qty, 2)
            reason = "SYNC_FROM_BROKER"
            note   = f"real exit={exit_price}"
        else:
            exit_price = entry          # fallback → PnL = 0
            pnl        = 0.0
            reason     = "MANUAL_EXIT"
            note       = "no broker fill found → entry used as exit"

        action = f"→ CLOSE ({note}, pnl={pnl:+.2f})"

        if not args.dry:
            trade_repo.close_trade(
                trade_id=t_id,
                exit_price=exit_price,
                pnl=pnl,
                exit_reason=reason
            )
        closed += 1

        print(f"{t_id:<5} {symbol:<35} {entry:>8.2f} {exit_price:>10.2f} {pnl:>+10.2f}  {action}")

    print(f"\n{'[DRY] Would have closed' if args.dry else 'Closed'} {closed}/{len(open_trades)} trade(s).")

    # 6. Show final state
    if not args.dry:
        today_trades = trade_repo.get_today_trades()
        print(f"\nFinal DB state — Today's trades ({len(today_trades)}):")
        for t in today_trades:
            t.pop('_id', None)
            print(f"  ID:{t.get('id')} {t.get('symbol')} | {t.get('status')} | "
                  f"entry:{t.get('entry_price')} exit:{t.get('exit_price')} pnl:{t.get('pnl')}")

    print("\n=== Sync Complete ===\n")


if __name__ == '__main__':
    main()
