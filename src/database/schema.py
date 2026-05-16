import asyncio
import os
from src.database.connection import get_db_conn, DB_PATH

async def init_db():
    print(f"Initializing database at {DB_PATH}")
    
    # 데이터 디렉토리 생성 확인
    db_dir = os.path.dirname(DB_PATH)
    if not os.path.exists(db_dir):
        os.makedirs(db_dir)

    async with get_db_conn() as db:
        # 1. exchanges 테이블 생성
        await db.execute('''
            CREATE TABLE IF NOT EXISTS exchanges (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                fee_rate REAL DEFAULT 0.0005,
                market_type TEXT DEFAULT 'crypto',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # 초기 거래소 데이터 삽입
        await db.execute("INSERT OR IGNORE INTO exchanges (id, name, fee_rate, market_type) VALUES ('upbit', 'Upbit', 0.0005, 'crypto')")
        await db.execute("INSERT OR IGNORE INTO exchanges (id, name, fee_rate, market_type) VALUES ('binance', 'Binance', 0.001, 'crypto')")

        # 2. trades 테이블 생성
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

        # Migration: exchange 컬럼 체크 (기존 데이터 호환성)
        cursor = await db.execute("PRAGMA table_info(trades)")
        columns = [row[1] for row in await cursor.fetchall()]
        if 'exchange' not in columns:
            await db.execute("ALTER TABLE trades ADD COLUMN exchange TEXT")
        
        # 3. trades 최적화 인덱스 (조회 성능 극대화)
        await db.execute('DROP INDEX IF EXISTS idx_exch_sym_time')
        await db.execute('''
            CREATE INDEX IF NOT EXISTS idx_trades_symbol_time_desc 
            ON trades (symbol, trade_timestamp DESC)
        ''')

        # 4. orderbooks 테이블 생성
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
        
        # 5. orderbooks 최적화 인덱스
        await db.execute('DROP INDEX IF EXISTS idx_ob_exch_sym_time')
        await db.execute('''
            CREATE INDEX IF NOT EXISTS idx_orderbooks_symbol_time_desc 
            ON orderbooks (symbol, timestamp DESC)
        ''')

        # 6. portfolios 테이블 생성
        await db.execute('''
            CREATE TABLE IF NOT EXISTS portfolios (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                exchange_id TEXT DEFAULT 'upbit',
                type TEXT NOT NULL, -- 'simulation' or 'real'
                initial_cash REAL DEFAULT 1000000,
                cash REAL DEFAULT 0,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (exchange_id) REFERENCES exchanges(id)
            )
        ''')

        # Migration: 컬럼 체크 (initial_cash, exchange_id)
        cursor = await db.execute("PRAGMA table_info(portfolios)")
        columns = [row[1] for row in await cursor.fetchall()]
        if 'initial_cash' not in columns:
            await db.execute("ALTER TABLE portfolios ADD COLUMN initial_cash REAL DEFAULT 1000000")
        if 'exchange_id' not in columns:
            await db.execute("ALTER TABLE portfolios ADD COLUMN exchange_id TEXT DEFAULT 'upbit'")

        # 7. positions 테이블 생성
        await db.execute('''
            CREATE TABLE IF NOT EXISTS positions (
                portfolio_id TEXT,
                symbol TEXT,
                quantity REAL DEFAULT 0,
                avg_price REAL DEFAULT 0,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (portfolio_id, symbol),
                FOREIGN KEY (portfolio_id) REFERENCES portfolios(id)
            )
        ''')

        # 8. orders_history 테이블 생성
        await db.execute('''
            CREATE TABLE IF NOT EXISTS orders_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                portfolio_id TEXT,
                strategy_id TEXT,
                symbol TEXT,
                side TEXT, -- 'BUY' or 'SELL'
                price REAL,
                quantity REAL,
                fee REAL,
                timestamp INTEGER,
                reason TEXT,
                context TEXT, -- JSON 포맷의 상세 지표 스냅샷
                FOREIGN KEY (portfolio_id) REFERENCES portfolios(id)
            )
        ''')

        # Migration: strategy_id, context 컬럼 체크
        cursor = await db.execute("PRAGMA table_info(orders_history)")
        columns = [row[1] for row in await cursor.fetchall()]
        if 'strategy_id' not in columns:
            await db.execute("ALTER TABLE orders_history ADD COLUMN strategy_id TEXT")
        if 'context' not in columns:
            await db.execute("ALTER TABLE orders_history ADD COLUMN context TEXT")
        
        # 9. candles 테이블 생성 (최적화된 워밍업용)
        await db.execute('''
            CREATE TABLE IF NOT EXISTS candles (
                symbol TEXT,
                interval INTEGER,
                timestamp INTEGER, -- 캔들 시작 시간
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume REAL,
                PRIMARY KEY (symbol, interval, timestamp)
            )
        ''')
        
        await db.execute('''
            CREATE INDEX IF NOT EXISTS idx_candles_symbol_interval_time
            ON candles (symbol, interval, timestamp DESC)
        ''')
        
        await db.commit()
    print("Database initialization complete.")

if __name__ == "__main__":
    asyncio.run(init_db())
