import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'data', 'backtest.db')

conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

print("=== DB Verification ===")
try:
    trades_count = conn.execute('SELECT COUNT(*) FROM trades').fetchone()[0]
    print(f"Trades Count: {trades_count}")
    
    print("\nTrades Table Schema:")
    cursor.execute("PRAGMA table_info(trades)")
    columns = cursor.fetchall()
    for col in columns:
        print(col)
except Exception as e:
    print(f"Error: {e}")

try:
    orderbooks_count = conn.execute('SELECT COUNT(*) FROM orderbooks').fetchone()[0]
    print(f"\nOrderbooks Count: {orderbooks_count}")
except Exception as e:
    print(f"Error: {e}")

conn.close()
