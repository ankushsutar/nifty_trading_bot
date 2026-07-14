#!/usr/bin/env python3
import os
import sys
import datetime
from dotenv import load_dotenv

# Allow running from project root
sys.path.insert(0, os.getcwd())
load_dotenv(override=True)

from bot.core.trade_repo import trade_repo
from bot.utils.logger import logger

def reconcile():
    print("=" * 70)
    print("🔄 RECONCILING MISSING EXTERNAL TRADES AND RESETTING STOP SIGNAL")
    print("=" * 70)
    
    # 1. Reconcile NIFTY2671424150CE
    existing_ce = trade_repo.collection.find_one({"symbol": "NIFTY2671424150CE", "mode": "LIVE"})
    if existing_ce:
        print(f"[!] Trade for NIFTY2671424150CE already exists (ID: {existing_ce['id']}).")
    else:
        trade_id = trade_repo._get_next_sequence("trade_id")
        trade_doc = {
            "id": trade_id,
            "symbol": "NIFTY2671424150CE",
            "token": "13152514",
            "leg": "CE",
            "side": "BUY",
            "qty": 325,
            "remaining_qty": 325,
            "entry_price": 16.0,
            "sl_price": 0.0,
            "sl_order_id": None,
            "monitoring_stage": 0,
            "exit_price": 3.05,
            "pnl": -4208.75,
            "exit_reason": "MANUAL_CLOSE",
            "mode": "LIVE",
            "strategy": "MANUAL",
            "status": "CLOSED",
            "partially_booked": False,
            "created_at": datetime.datetime(2026, 7, 14, 11, 43, 8),
            "updated_at": datetime.datetime(2026, 7, 14, 13, 52, 54),
            "closed_at": datetime.datetime(2026, 7, 14, 13, 52, 54)
        }
        trade_repo.collection.insert_one(trade_doc)
        print(f"[+] Successfully inserted NIFTY2671424150CE (ID: {trade_id}, P&L: ₹-4,208.75)")

    # 2. Reconcile second NIFTY2671424000PE
    # The first one is ID 340. We look for a second one.
    all_pe = list(trade_repo.collection.find({"symbol": "NIFTY2671424000PE", "mode": "LIVE"}))
    if len(all_pe) > 1:
        print(f"[!] Second trade for NIFTY2671424000PE already exists (ID: {all_pe[1]['id']}).")
    else:
        trade_id = trade_repo._get_next_sequence("trade_id")
        trade_doc = {
            "id": trade_id,
            "symbol": "NIFTY2671424000PE",
            "token": "13150978",
            "leg": "PE",
            "side": "BUY",
            "qty": 325,
            "remaining_qty": 325,
            "entry_price": 15.25,
            "sl_price": 0.0,
            "sl_order_id": None,
            "monitoring_stage": 0,
            "exit_price": 10.15,
            "pnl": -1657.50,
            "exit_reason": "MANUAL_CLOSE",
            "mode": "LIVE",
            "strategy": "MANUAL",
            "status": "CLOSED",
            "partially_booked": False,
            "created_at": datetime.datetime(2026, 7, 14, 14, 53, 44),
            "updated_at": datetime.datetime(2026, 7, 14, 14, 56, 3),
            "closed_at": datetime.datetime(2026, 7, 14, 14, 56, 3)
        }
        trade_repo.collection.insert_one(trade_doc)
        print(f"[+] Successfully inserted second NIFTY2671424000PE (ID: {trade_id}, P&L: ₹-1,657.50)")

    # 3. Reset the stop signal
    stop_signal_path = ".stop_signal"
    if os.path.exists(stop_signal_path):
        os.remove(stop_signal_path)
        print("[+] Removed .stop_signal file.")
    else:
        print("[!] No .stop_signal file found.")

    print("\n--- Current Today's Trades in MongoDB ---")
    trades = trade_repo.get_today_trades()
    total_pnl = 0.0
    for t in trades:
        total_pnl += t.get('pnl', 0.0)
        print(f"  ID:{t.get('id')} | {t.get('symbol')} | {t.get('status')} | P&L: ₹{t.get('pnl'):,.2f} | Strategy: {t.get('strategy')}")
    print(f"\n  Total DB-Recorded P&L for Today: ₹{total_pnl:,.2f}")
    print("=" * 70)

if __name__ == "__main__":
    reconcile()
