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
        await ensure_column(db, 'portfolios', 'duration', 'REAL DEFAULT 0.0')
        await ensure_column(db, 'portfolios', 'strategy_info', 'TEXT DEFAULT \'\'')

        # 3.5. portfolio_exchanges (중간 테이블)
        await db.execute('''
            CREATE TABLE IF NOT EXISTS portfolio_exchanges (
                portfolio_id TEXT,
                exchange_id TEXT,
                initial_cash REAL DEFAULT 0.0,
                cash REAL DEFAULT 0.0,
                metrics TEXT DEFAULT '{}',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (portfolio_id, exchange_id),
                FOREIGN KEY (portfolio_id) REFERENCES portfolios(id) ON UPDATE CASCADE ON DELETE CASCADE
            )
        ''')

        # 기존 portfolios를 기반으로 portfolio_exchanges 초기 레코드 동기화
        async with db.execute("SELECT id, exchange_id, initial_cash, cash FROM portfolios") as cursor:
            p_rows = await cursor.fetchall()
        for p_row in p_rows:
            p_id = p_row['id']
            p_ex = p_row['exchange_id'] or 'upbit'
            p_init = p_row['initial_cash']
            p_cash = p_row['cash']
            if p_ex != 'all':
                await db.execute('''
                    INSERT OR IGNORE INTO portfolio_exchanges (portfolio_id, exchange_id, initial_cash, cash)
                    VALUES (?, ?, ?, ?)
                ''', (p_id, p_ex, p_init, p_cash))

        # 기존 가상 'all' 거래소 잔고 정보 정제
        await db.execute("DELETE FROM portfolio_exchanges WHERE exchange_id = 'all'")

        # 기존 positions 테이블에 존재하는 실제 (portfolio_id, exchange) 쌍도 동기화 보장 (FK 제약 에러 방지)
        try:
            async with db.execute("SELECT DISTINCT portfolio_id, COALESCE(exchange, 'upbit') as ex FROM positions") as cursor:
                pos_pairs = await cursor.fetchall()
            for pair in pos_pairs:
                if pair['ex'] != 'all':
                    await db.execute('''
                        INSERT OR IGNORE INTO portfolio_exchanges (portfolio_id, exchange_id, initial_cash, cash)
                        VALUES (?, ?, 10000000.0, 10000000.0)
                    ''', (pair['portfolio_id'], pair['ex']))
        except Exception:
            pass

        # 4. positions
        await db.execute('''
            CREATE TABLE IF NOT EXISTS positions (
                portfolio_id TEXT,
                symbol TEXT,
                quantity REAL DEFAULT 0,
                avg_price REAL DEFAULT 0,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                exchange TEXT,
                PRIMARY KEY (portfolio_id, exchange, symbol),
                FOREIGN KEY (portfolio_id, exchange) REFERENCES portfolio_exchanges(portfolio_id, exchange_id) ON UPDATE CASCADE ON DELETE CASCADE
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
                context TEXT,
                FOREIGN KEY (portfolio_id) REFERENCES portfolios(id) ON UPDATE CASCADE ON DELETE CASCADE
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

        # [Migration] portfolio_exchanges 테이블 Foreign Key 제약조건 마이그레이션 (ON UPDATE CASCADE 추가)
        cursor = await db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='portfolio_exchanges'")
        row = await cursor.fetchone()
        if row:
            sql = row[0]
            if "FOREIGN KEY" not in sql or "ON UPDATE CASCADE" not in sql:
                logger.info("[Migration] portfolio_exchanges 테이블 Foreign Key 및 CASCADE 제약조건 마이그레이션 시작")
                await db.execute("CREATE TABLE portfolio_exchanges_backup AS SELECT * FROM portfolio_exchanges")
                await db.execute("DROP TABLE portfolio_exchanges")
                await db.execute('''
                    CREATE TABLE portfolio_exchanges (
                        portfolio_id TEXT,
                        exchange_id TEXT,
                        initial_cash REAL DEFAULT 0.0,
                        cash REAL DEFAULT 0.0,
                        metrics TEXT DEFAULT '{}',
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (portfolio_id, exchange_id),
                        FOREIGN KEY (portfolio_id) REFERENCES portfolios(id) ON UPDATE CASCADE ON DELETE CASCADE
                    )
                ''')
                await db.execute('''
                    INSERT OR IGNORE INTO portfolio_exchanges (portfolio_id, exchange_id, initial_cash, cash, metrics, created_at, updated_at)
                    SELECT portfolio_id, exchange_id, initial_cash, cash, metrics, created_at, updated_at
                    FROM portfolio_exchanges_backup
                    WHERE portfolio_id IN (SELECT id FROM portfolios)
                ''')
                await db.execute("DROP TABLE portfolio_exchanges_backup")
                logger.info("[Migration] portfolio_exchanges 테이블 마이그레이션 완료")

        # [Migration] positions 테이블의 portfolio_exchanges 참조 및 PK/FK 통합 마이그레이션
        cursor = await db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='positions'")
        row = await cursor.fetchone()
        if row:
            sql = row[0]
            if "portfolio_exchanges" not in sql:
                logger.info("[Migration] positions 테이블의 Foreign Key를 portfolio_exchanges 참조로 마이그레이션 시작")
                await db.execute("CREATE TABLE positions_backup AS SELECT * FROM positions")
                await db.execute("DROP TABLE positions")
                await db.execute('''
                    CREATE TABLE positions (
                        portfolio_id TEXT,
                        symbol TEXT,
                        quantity REAL DEFAULT 0,
                        avg_price REAL DEFAULT 0,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        exchange TEXT,
                        PRIMARY KEY (portfolio_id, exchange, symbol),
                        FOREIGN KEY (portfolio_id, exchange) REFERENCES portfolio_exchanges(portfolio_id, exchange_id) ON UPDATE CASCADE ON DELETE CASCADE
                    )
                ''')
                # 백업 테이블의 실제 포지션을 기준으로 portfolio_exchanges 쌍 미리 삽입 (FK 제약 에러 차단)
                async with db.execute("SELECT DISTINCT portfolio_id, COALESCE(exchange, 'upbit') as ex FROM positions_backup") as c:
                    backup_pairs = await c.fetchall()
                for pair in backup_pairs:
                    await db.execute('''
                        INSERT OR IGNORE INTO portfolio_exchanges (portfolio_id, exchange_id, initial_cash, cash)
                        VALUES (?, ?, 10000000.0, 10000000.0)
                    ''', (pair['portfolio_id'], pair['ex']))

                await db.execute('''
                    INSERT OR IGNORE INTO positions (portfolio_id, exchange, symbol, quantity, avg_price, updated_at)
                    SELECT portfolio_id, COALESCE(exchange, 'upbit'), symbol, quantity, avg_price, updated_at
                    FROM positions_backup
                    WHERE portfolio_id IN (SELECT id FROM portfolios)
                ''')
                await db.execute("DROP TABLE positions_backup")
                await db.execute('DROP INDEX IF EXISTS idx_positions_portfolio_id')
                logger.info("[Migration] positions 테이블 portfolio_exchanges 참조 마이그레이션 완료")

        # [Migration] orders_history 테이블 Foreign Key 추가 마이그레이션
        cursor = await db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='orders_history'")
        row = await cursor.fetchone()
        if row:
            sql = row[0]
            if "FOREIGN KEY" not in sql or "ON UPDATE CASCADE" not in sql:
                logger.info("[Migration] orders_history 테이블 Foreign Key 및 CASCADE 제약조건 마이그레이션 시작")
                await db.execute("CREATE TABLE orders_history_backup AS SELECT * FROM orders_history")
                await db.execute("DROP TABLE orders_history")
                await db.execute('''
                    CREATE TABLE orders_history (
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
                        context TEXT,
                        FOREIGN KEY (portfolio_id) REFERENCES portfolios(id) ON UPDATE CASCADE ON DELETE CASCADE
                    )
                ''')
                await db.execute('''
                    INSERT OR IGNORE INTO orders_history (id, portfolio_id, exchange, strategy_id, symbol, side, price, quantity, fee, timestamp, reason, context)
                    SELECT id, portfolio_id, exchange, strategy_id, symbol, side, price, quantity, fee, timestamp, reason, context
                    FROM orders_history_backup
                    WHERE portfolio_id IN (SELECT id FROM portfolios)
                ''')
                await db.execute("DROP TABLE orders_history_backup")
                await db.execute('DROP INDEX IF EXISTS idx_orders_history_portfolio_id')
                logger.info("[Migration] orders_history 테이블 마이그레이션 완료")

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
        await db.execute('CREATE INDEX IF NOT EXISTS idx_orders_history_portfolio_id ON orders_history (portfolio_id)')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_positions_portfolio_id ON positions (portfolio_id)')
        
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

        # --- 실시간 모의투자 단일 default 통합 마이그레이션 ---
        try:
            # 1. 기존 포트폴리오들 잔고 정보 로드
            async with db.execute("SELECT id, cash, initial_cash FROM portfolios WHERE id IN ('default', 'stock_default', 'bithumb_default')") as cursor:
                p_rows = await cursor.fetchall()
            p_data = {row['id']: (row['cash'], row['initial_cash']) for row in p_rows}

            # 2. 거래소별 현금 잔고 산출 (기존 포트폴리오가 존재했으면 그 잔고 사용, 없으면 기본값 1,000만 원)
            upbit_cash = p_data.get('default', (10000000.0, 10000000.0))[0]
            kis_cash = p_data.get('stock_default', (10000000.0, 10000000.0))[0]
            bithumb_cash = p_data.get('bithumb_default', (10000000.0, 10000000.0))[0]

            # 3. default 포트폴리오 보장 및 정보 갱신 (초기자금 30,000,000원, 총 가용금액 합산)
            total_cash = upbit_cash + kis_cash + bithumb_cash
            await db.execute('''
                INSERT OR REPLACE INTO portfolios (id, name, exchange_id, type, initial_cash, cash, updated_at)
                VALUES ('default', '기본 모의투자', 'all', 'simulation', 30000000.0, ?, datetime('now'))
            ''', (total_cash,))

            # 4. portfolio_exchanges 거래소별 레코드 삽입/갱신 (각각 초기자금 1,000만 원)
            await db.execute('''
                INSERT OR REPLACE INTO portfolio_exchanges (portfolio_id, exchange_id, initial_cash, cash, updated_at)
                VALUES ('default', 'upbit', 10000000.0, ?, datetime('now'))
            ''', (upbit_cash,))
            await db.execute('''
                INSERT OR REPLACE INTO portfolio_exchanges (portfolio_id, exchange_id, initial_cash, cash, updated_at)
                VALUES ('default', 'kis', 10000000.0, ?, datetime('now'))
            ''', (kis_cash,))
            await db.execute('''
                INSERT OR REPLACE INTO portfolio_exchanges (portfolio_id, exchange_id, initial_cash, cash, updated_at)
                VALUES ('default', 'bithumb', 10000000.0, ?, datetime('now'))
            ''', (bithumb_cash,))

            # 5. 기존 포지션 및 이력 데이터 default 포트폴리오 ID로 맵핑 변경 (외래키 제약을 위해 portfolio_exchanges 생성 후 수행)
            await db.execute("UPDATE positions SET portfolio_id = 'default' WHERE portfolio_id IN ('stock_default', 'bithumb_default')")
            await db.execute("UPDATE orders_history SET portfolio_id = 'default' WHERE portfolio_id IN ('stock_default', 'bithumb_default')")

            # 6. 병합 완료된 구버전 포트폴리오 마스터 및 하위 자금 레코드 삭제
            await db.execute("DELETE FROM portfolio_exchanges WHERE portfolio_id IN ('stock_default', 'bithumb_default')")
            await db.execute("DELETE FROM portfolios WHERE id IN ('stock_default', 'bithumb_default')")
            
            # 7. orders_history 중복 저장 이력 클리닝 (백테스트 체결 내역 2중 저장 버그 소거 대응)
            await db.execute('''
                DELETE FROM orders_history
                WHERE rowid NOT IN (
                    SELECT MIN(rowid)
                    FROM orders_history
                    GROUP BY portfolio_id, exchange, symbol, side, price, quantity, fee, timestamp, reason, context
                )
            ''')
            logger.info("Cleaned up duplicate orders_history records.")
            
            logger.info("Real-time simulation portfolios consolidation migration success.")
        except Exception as e:
            logger.error(f"Failed to migrate and consolidate simulation portfolios: {e}")

        await db.commit()

if __name__ == "__main__":
    asyncio.run(init_db())
