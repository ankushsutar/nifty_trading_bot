import sqlite3
import os

DB_PATH = "trades.db"

def clean_db():
    print("🧹 Cleaning invalid trades from DB...")
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Delete trades with specific fake tokens
        cursor.execute("DELETE FROM trades WHERE token IN ('123456', '654321', '789012')")
        deleted = cursor.rowcount
        conn.commit()
        conn.close()
        print(f"✅ Deleted {deleted} invalid trades.")
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    clean_db()
