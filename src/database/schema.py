import aiosqlite
import asyncio
import os

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'data', 'backtest.db')

async def init_db():
    print(f"Initializing database at {DB_PATH}")
    async with aiosqlite.connect(DB_PATH) as db:
        # WAL 모드 활성화로 쓰기 성능 극대화
        await db.execute('PRAGMA journal_mode=WAL;')
        
        # 1. trades 테이블 생성
        await db.execute('''
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                exchange TEXT,
                symbol TEXT,
                trade_price REAL,
                trade_volume REAL,
                ask_bid TEXT,
                trade_timestamp INTEGER,
                sequential_id INTEGER,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # 2. trades 인덱스
        await db.execute('''
            CREATE INDEX IF NOT EXISTS idx_exch_sym_time 
            ON trades (exchange, symbol, trade_timestamp)
        ''')

        # 3. orderbooks 테이블 생성
        await db.execute('''
            CREATE TABLE IF NOT EXISTS orderbooks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                exchange TEXT,
                symbol TEXT,
                timestamp INTEGER,
                bids TEXT,
                asks TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # 4. orderbooks 인덱스
        await db.execute('''
            CREATE INDEX IF NOT EXISTS idx_ob_exch_sym_time 
            ON orderbooks (exchange, symbol, timestamp)
        ''')
        
        await db.commit()
    print("Database initialization complete.")

if __name__ == "__main__":
    asyncio.run(init_db())
