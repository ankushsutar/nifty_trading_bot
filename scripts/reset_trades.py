import sqlite3
import os
import sys

# Add parent dir to path if needed for config, but we can just use DB_PATH
DB_PATH = "trades.db"

def reset_trades(mode=None):
    if not os.path.exists(DB_PATH):
        print(">>> Database not found.")
        return

    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        if mode:
            print(f">>> Clearing {mode} trades...")
            cursor.execute("DELETE FROM trades WHERE mode = ?", (mode,))
        else:
            print(">>> Clearing ALL trades...")
            cursor.execute("DELETE FROM trades")
        
        conn.commit()
        deleted = cursor.rowcount
        conn.close()
        print(f">>> Success: Deleted {deleted} records.")
    except Exception as e:
        print(f">>> Error: {e}")

if __name__ == "__main__":
    mode_arg = None
    if len(sys.argv) > 1:
        mode_arg = sys.argv[1].upper() # e.g. PAPER or LIVE
    
    reset_trades(mode_arg)
