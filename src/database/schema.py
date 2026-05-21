import asyncio
import os
from src.database.connection import get_db_conn, DB_PATH
from src.engine.utils.telemetry import get_logger

logger = get_logger(__name__)

async def ensure_column(db, table, column, definition):
    cursor = await db.execute(f"PRAGMA table_info({table})")
    cols = [r[1] for r in await cursor.fetchall()]
    if column not in cols:
        logger.info(f"Adding '{column}' column to '{table}'...")
        await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

async def init_db(db_path: str = None):
    target_path = db_path if db_path is not None else DB_PATH
    logger.info(f"Initializing database at {target_path}")
    
    db_dir = os.path.dirname(target_path)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir)

    async with get_db_conn(target_path) as db:
        # 1. exchanges
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
        await db.execute("INSERT OR IGNORE INTO exchanges (id, name, fee_rate, market_type) VALUES ('upbit', 'Upbit', 0.0005, 'crypto')")
        await db.execute("INSERT OR IGNORE INTO exchanges (id, name, fee_rate, market_type) VALUES ('kis', 'KIS', 0.00015, 'stock')")

        # 2. trades
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
        await ensure_column(db, 'trades', 'exchange', 'TEXT')

        # 3. portfolios
        await db.execute('''
            CREATE TABLE IF NOT EXISTS portfolios (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                exchange_id TEXT DEFAULT 'upbit',
                type TEXT NOT NULL,
                initial_cash REAL DEFAULT 1000000,
                cash REAL DEFAULT 0,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # 4. positions (PK 변경은 복잡하므로 컬럼 추가 후 데이터 정리)
        await db.execute('''
            CREATE TABLE IF NOT EXISTS positions (
                portfolio_id TEXT,
                symbol TEXT,
                quantity REAL DEFAULT 0,
                avg_price REAL DEFAULT 0,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (portfolio_id, symbol)
            )
        ''')
        await ensure_column(db, 'positions', 'exchange', 'TEXT')

        # 5. orders_history
        await db.execute('''
            CREATE TABLE IF NOT EXISTS orders_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                portfolio_id TEXT,
                exchange TEXT,
                strategy_id TEXT,
                symbol TEXT,
                side TEXT,
                price REAL,
                quantity REAL,
                fee REAL,
                timestamp INTEGER,
                reason TEXT,
                context TEXT
            )
        ''')
        await ensure_column(db, 'orders_history', 'exchange', 'TEXT')

        # 6. alerts (알림 내역)
        await db.execute('''
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                exchange TEXT,
                symbol TEXT,
                price REAL,
                msg TEXT,
                timestamp INTEGER
            )
        ''')
        await ensure_column(db, 'alerts', 'exchange', 'TEXT')

        # 7. candles PK 마이그레이션 검사 (exchange가 PK에 누락된 구 버전 대응)
        cursor = await db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='candles'")
        row = await cursor.fetchone()
        if row:
            sql = row[0]
            if "PRIMARY KEY" in sql and "exchange" not in sql.split("PRIMARY KEY")[1]:
                logger.info("[Migration] candles 테이블 Primary Key 구조 마이그레이션 시작 (exchange 컬럼 PK 편입)")
                # 백업본 생성
                await db.execute("CREATE TABLE candles_backup AS SELECT * FROM candles")
                # 구형 테이블 삭제
                await db.execute("DROP TABLE candles")
                # 신규 스키마 테이블 생성
                await db.execute('''
                    CREATE TABLE candles (
                        exchange TEXT,
                        symbol TEXT,
                        interval INTEGER,
                        timestamp INTEGER,
                        open REAL,
                        high REAL,
                        low REAL,
                        close REAL,
                        volume REAL,
                        PRIMARY KEY (exchange, symbol, interval, timestamp)
                    )
                ''')
                # 데이터 복원
                await db.execute('''
                    INSERT OR IGNORE INTO candles (exchange, symbol, interval, timestamp, open, high, low, close, volume)
                    SELECT COALESCE(exchange, 'upbit'), symbol, interval, timestamp, open, high, low, close, volume
                    FROM candles_backup
                ''')
                # 백업 삭제
                await db.execute("DROP TABLE candles_backup")
                # 인덱스 드랍 후 하단에서 재생성 유도
                await db.execute('DROP INDEX IF EXISTS idx_candles_exch_sym_time')
                logger.info("[Migration] candles 테이블 Primary Key 구조 마이그레이션 완수")

        await db.execute('''
            CREATE TABLE IF NOT EXISTS candles (
                exchange TEXT,
                symbol TEXT,
                interval INTEGER,
                timestamp INTEGER,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume REAL,
                PRIMARY KEY (exchange, symbol, interval, timestamp)
            )
        ''')
        await ensure_column(db, 'candles', 'exchange', 'TEXT')

        # 인덱스
        await db.execute('CREATE INDEX IF NOT EXISTS idx_trades_exch_sym_time ON trades (exchange, symbol, trade_timestamp DESC)')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_candles_exch_sym_time ON candles (exchange, symbol, interval, timestamp DESC)')
        
        await db.commit()
    
    await migrate_data(target_path)
    logger.info("Database initialization and migration complete.")

async def migrate_data(db_path: str = None):
    async with get_db_conn(db_path) as db:
        tables = ['trades', 'candles', 'positions', 'orders_history']
        for table in tables:
            try:
                await db.execute(f"UPDATE {table} SET exchange = 'upbit', symbol = REPLACE(symbol, 'UPB-', '') WHERE symbol LIKE 'UPB-%'")
                await db.execute(f"UPDATE {table} SET exchange = 'upbit', symbol = REPLACE(symbol, 'KRW-', '') WHERE symbol LIKE 'KRW-%'")
                await db.execute(f"UPDATE {table} SET exchange = 'kis', symbol = REPLACE(symbol, 'KIS-', '') WHERE symbol LIKE 'KIS-%'")
                await db.execute(f"UPDATE {table} SET exchange = 'upbit' WHERE exchange IS NULL")
            except Exception as e:
                logger.error(f"Migration error for {table}: {e}")
        await db.commit()

if __name__ == "__main__":
    asyncio.run(init_db())
