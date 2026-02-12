import sys
import os
import requests
import datetime
import time

# Add parent dir to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.core.trade_repo import trade_repo

# Ensure we use the correct DB path relative to execution
# The bot runs from /home/cwd/agent/nifty_trading_bot usually
# trade_repo uses "trades.db" relative to CWD.
# We will run this script from the root dir.

def setup_mock_trades():
    print("🛠️ Setting up mock trades in DB...")
    # trade_repo._get_connection uses DB_PATH global which is "trades.db"
    
    # Insert a CLOSED WIN trade
    trade_repo.save_trade("NIFTY24FEB22000CE", "123456", "CE", 50, 100.0, 80.0)
    # Get ID of just inserted trade
    trades = trade_repo.get_open_trades()
    t1 = next(t for t in trades if t['token'] == '123456')
    trade_repo.close_trade(t1['id'], exit_price=120.0, pnl=1000.0, exit_reason="TARGET_HIT")
    
    # Insert a CLOSED LOSS trade
    trade_repo.save_trade("NIFTY24FEB22000PE", "654321", "PE", 50, 100.0, 120.0)
    trades = trade_repo.get_open_trades()
    t2 = next(t for t in trades if t['token'] == '654321')
    trade_repo.close_trade(t2['id'], exit_price=90.0, pnl=-500.0, exit_reason="sl_hit")
    
    # Insert an OPEN trade
    trade_repo.save_trade("NIFTY24FEB22100CE", "789012", "CE", 50, 50.0, 40.0)
    print("✅ Mock trades inserted.")

def test_daily_summary_api():
    print("Testing /api/daily-summary...")
    try:
        response = requests.get("http://localhost:8000/api/daily-summary")
        if response.status_code == 200:
            data = response.json()
            print("Response:", data)
            
            pnl = data.get("daily_pnl")
            trades = data.get("trades")
            
            print(f"Daily P&L: {pnl}")
            print(f"Trades Count: {len(trades)}")
            
            # Simple assertions
            # We expect at least the 3 trades we inserted (plus any others from today)
            found_win = False
            found_loss = False
            found_open = False
            
            for t in trades:
                if t['token'] == '123456' and t['pnl'] == 1000.0: found_win = True
                if t['token'] == '654321' and t['pnl'] == -500.0: found_loss = True
                if t['token'] == '789012' and t['status'] == 'OPEN': found_open = True
            
            if found_win and found_loss and found_open:
                print("✅ API returned correct trade data.")
            else:
                print("❌ API failed to return expected mock trades.")
                
        else:
            print(f"❌ API Error: {response.status_code}")
    except Exception as e:
        print(f"❌ Connection Error: {e}")

if __name__ == "__main__":
    setup_mock_trades()
    # Wait a bit for server to handle it? No need, DB is sync.
    test_daily_summary_api()
