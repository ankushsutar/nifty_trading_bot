import sqlite3
conn = sqlite3.connect('trades.db')
cursor = conn.cursor()
cursor.execute("SELECT id, symbol, entry_price, status, mode FROM trades WHERE status = 'OPEN'")
rows = cursor.fetchall()
for row in rows:
    print(row)
conn.close()
