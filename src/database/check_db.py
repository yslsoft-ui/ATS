import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'data', 'backtest.db')

conn = sqlite3.connect(DB_PATH)
trades_count = conn.execute('SELECT COUNT(*) FROM trades').fetchone()[0]
orderbooks_count = conn.execute('SELECT COUNT(*) FROM orderbooks').fetchone()[0]

print("=== DB Verification ===")
print(f"Trades Count: {trades_count}")
print(f"Orderbooks Count: {orderbooks_count}")

conn.close()
