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
    import sqlite3
    
    max_retries = 10
    retry_delay = 0.5
    
    for attempt in range(max_retries):
        try:
            await _init_db_core(db_path)
            return
        except sqlite3.OperationalError as e:
            if "locked" in str(e) and attempt < max_retries - 1:
                logger.warning(f"Database is locked during init_db. Retrying in {retry_delay}s... (Attempt {attempt + 1}/{max_retries})")
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 1.5, 5.0)
            else:
                logger.error(f"Critical operational error during init_db: {e}")
                raise e

async def _init_db_core(db_path: str = None):
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
        await db.execute("INSERT OR IGNORE INTO exchanges (id, name, fee_rate, market_type) VALUES ('bithumb', 'Bithumb', 0.0025, 'crypto')")

        # 2. trades
        await db.execute('''
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                exchange TEXT,
                market TEXT,
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
        await ensure_column(db, 'trades', 'market', 'TEXT')
        await ensure_column(db, 'trades', 'sequential_id', 'INTEGER')


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
                market TEXT,
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
        await ensure_column(db, 'orders_history', 'market', 'TEXT')

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
                        is_closed INTEGER DEFAULT 1,
                        PRIMARY KEY (exchange, symbol, interval, timestamp)
                    )
                ''')
                # 데이터 복원
                await db.execute('''
                    INSERT OR IGNORE INTO candles (exchange, symbol, interval, timestamp, open, high, low, close, volume, is_closed)
                    SELECT COALESCE(exchange, 'upbit'), symbol, interval, timestamp, open, high, low, close, volume, COALESCE(is_closed, 1)
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
                is_closed INTEGER DEFAULT 1,
                PRIMARY KEY (exchange, symbol, interval, timestamp)
            )
        ''')
        await ensure_column(db, 'candles', 'exchange', 'TEXT')
        await ensure_column(db, 'candles', 'is_closed', 'INTEGER DEFAULT 1')

        # 8. asset_master
        await db.execute('''
            CREATE TABLE IF NOT EXISTS asset_master (
                symbol TEXT PRIMARY KEY,
                korean_name TEXT NOT NULL,
                asset_type TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # 9. exchange_assets
        await db.execute('''
            CREATE TABLE IF NOT EXISTS exchange_assets (
                exchange TEXT,
                symbol TEXT,
                is_active INTEGER DEFAULT 1,
                is_delisted INTEGER DEFAULT 0,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (exchange, symbol),
                FOREIGN KEY (symbol) REFERENCES asset_master(symbol) ON UPDATE CASCADE
            )
        ''')

        # 10. real_orders (실계좌 거래 내역 영구 보관용)
        await db.execute('''
            CREATE TABLE IF NOT EXISTS real_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                exchange TEXT NOT NULL,
                uuid TEXT UNIQUE NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                price REAL DEFAULT 0.0,
                volume REAL DEFAULT 0.0,
                executed_volume REAL DEFAULT 0.0,
                fee REAL DEFAULT 0.0,
                state TEXT NOT NULL,
                created_at DATETIME,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # 11. system_events
        await db.execute('''
            CREATE TABLE IF NOT EXISTS system_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                target TEXT NOT NULL,
                message TEXT,
                timestamp INTEGER NOT NULL,
                context TEXT
            )
        ''')
        await ensure_column(db, 'system_events', 'context', 'TEXT')

        # 12. strategy_versions [NEW - V1]
        await db.execute('''
            CREATE TABLE IF NOT EXISTS strategy_versions (
                strategy_id TEXT PRIMARY KEY,
                current_version_id INTEGER NOT NULL,
                current_params TEXT NOT NULL,
                rollback_source_version INTEGER,
                applied_at INTEGER NOT NULL,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # 13. strategy_parameter_history [NEW - V1]
        await db.execute('''
            CREATE TABLE IF NOT EXISTS strategy_parameter_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy_id TEXT NOT NULL,
                version_id INTEGER NOT NULL,
                parent_version_id INTEGER,
                old_params TEXT,
                new_params TEXT,
                proposal_id INTEGER,
                is_current INTEGER DEFAULT 0,
                changed_by TEXT NOT NULL,
                change_reason TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # 14. strategy_performance_snapshots [NEW - V1]
        await db.execute('''
            CREATE TABLE IF NOT EXISTS strategy_performance_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy_id TEXT NOT NULL,
                version_id INTEGER NOT NULL,
                parameter_hash TEXT NOT NULL,
                snapshot_type TEXT NOT NULL,
                timestamp INTEGER NOT NULL,
                roi REAL,
                mdd REAL,
                profit_factor REAL,
                win_rate REAL,
                trade_count INTEGER,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # 15. market_regime_summaries [NEW - V2]
        await db.execute('''
            CREATE TABLE IF NOT EXISTS market_regime_summaries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                volatility REAL,
                rsi REAL,
                volume_ratio REAL,
                spread REAL,
                orderbook_imbalance REAL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # 16. strategy_insights [NEW - V2]
        await db.execute('''
            CREATE TABLE IF NOT EXISTS strategy_insights (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                portfolio_id TEXT,
                strategy_id TEXT,
                category TEXT NOT NULL,
                fact_summary TEXT NOT NULL,
                details_json TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # 17. strategy_proposals [NEW - V1.5 / V2 / V3.5]
        await db.execute('''
            CREATE TABLE IF NOT EXISTS strategy_proposals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                insight_id INTEGER,
                proposal_group_id TEXT,
                version INTEGER,
                portfolio_id TEXT,
                strategy_id TEXT,
                status TEXT NOT NULL,
                outcome TEXT NOT NULL,
                original_params TEXT,
                proposed_params TEXT,
                metrics TEXT,
                mutation_trace TEXT,
                confidence_score INTEGER,
                applied_at INTEGER,
                rolled_back_at INTEGER,
                decision_path_hash TEXT UNIQUE,
                audit_log_json TEXT,
                counterfactual_roi REAL DEFAULT 0.0,
                counterfactual_mdd REAL DEFAULT 0.0,
                is_counterfactual_tracked INTEGER DEFAULT 0,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (insight_id) REFERENCES strategy_insights(id) ON UPDATE CASCADE ON DELETE SET NULL
            )
        ''')

        # 18. proposal_evaluations [NEW - V3.5 1:N Horizon 구조]
        await db.execute('''
            CREATE TABLE IF NOT EXISTS proposal_evaluations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                proposal_id INTEGER NOT NULL,
                horizon_name TEXT NOT NULL,
                predicted_roi_7d REAL,
                actual_roi_7d REAL,
                roi_divergence REAL,
                predicted_trade_count_7d INTEGER,
                actual_trade_count_7d INTEGER,
                trade_count_divergence INTEGER,
                candidate_roi REAL,
                champion_roi REAL,
                roi_gap REAL,
                candidate_mdd REAL,
                champion_mdd REAL,
                virtual_rollback INTEGER DEFAULT 0,
                actual_label TEXT,
                actual_label_source TEXT,
                due_at INTEGER NOT NULL DEFAULT 0,
                evaluated_at INTEGER,
                locked_at INTEGER,
                retry_count INTEGER DEFAULT 0,
                last_error TEXT,
                evaluation_status TEXT NOT NULL DEFAULT 'PENDING',
                horizon_type TEXT,
                horizon_value INTEGER,
                policy_version TEXT,
                scorer_version TEXT,
                predicted_risk_score REAL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (proposal_id) REFERENCES strategy_proposals(id) ON UPDATE CASCADE ON DELETE CASCADE,
                UNIQUE (proposal_id, horizon_name)
            )
        ''')
        await ensure_column(db, 'proposal_evaluations', 'baseline_value', 'REAL')
        await ensure_column(db, 'proposal_evaluations', 'baseline_timestamp', 'INTEGER')
        await ensure_column(db, 'proposal_evaluations', 'baseline_volume', 'INTEGER')


        # 19. girs_shadow_metrics [NEW]
        await db.execute('''
            CREATE TABLE IF NOT EXISTS girs_shadow_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                proposal_id TEXT,
                strategy_id TEXT,
                model_risk_score REAL,
                fallback_risk_score REAL,
                final_promotion_score REAL,
                shadow_risk_score REAL,
                replay_drift REAL,
                correction_active INTEGER DEFAULT 0,
                operation_mode TEXT,
                model_version TEXT,
                scaler_version TEXT,
                strategy_version_id INTEGER,
                simulation_session_id TEXT,
                decision_type TEXT,
                blocked_reason TEXT,
                trade_age_ms INTEGER,
                orderbook_age_ms INTEGER,
                indicator_age_ms INTEGER,
                is_fresh INTEGER DEFAULT 1,
                stale_reason TEXT,
                snapshot_version TEXT,
                snapshot_hash TEXT,
                feature_vector_hash TEXT,
                orderbook_available INTEGER DEFAULT 0,
                market_type TEXT,
                session_state TEXT,
                volatility_regime TEXT,
                liquidity_regime TEXT,
                exchange TEXT,
                tps REAL,
                trade_count INTEGER,
                volume REAL,
                idle_time REAL
            )
        ''')

        # 20. promotion_event_log [NEW]
        await db.execute('''
            CREATE TABLE IF NOT EXISTS promotion_event_log (
                global_sequence_no INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT UNIQUE NOT NULL,
                proposal_id TEXT NOT NULL,
                sequence_no INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                payload TEXT,
                timestamp REAL NOT NULL,
                feature_snapshot TEXT,
                graph_embedding TEXT,
                model_version TEXT,
                scaler_version TEXT,
                UNIQUE(proposal_id, sequence_no)
            )
        ''')

        # 21. universe_guard_state [NEW]
        await db.execute('''
            CREATE TABLE IF NOT EXISTS universe_guard_state (
                exchange TEXT NOT NULL,
                market_type TEXT NOT NULL,
                symbol TEXT NOT NULL,
                status TEXT,
                blocked_reason TEXT,
                blocked_count INTEGER DEFAULT 0,
                last_blocked_at REAL,
                last_event_logged_reason TEXT,
                PRIMARY KEY (exchange, market_type, symbol)
            )
        ''')

        # [Migration] proposal_evaluations 테이블 1:N Horizon 구조 마이그레이션 감지 및 실행
        cursor = await db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='proposal_evaluations'")
        row = await cursor.fetchone()
        if row:
            sql = row[0]
            if "UNIQUE (proposal_id)" in sql or "proposal_id INTEGER UNIQUE" in sql or "horizon_name" not in sql:
                logger.info("[Migration] proposal_evaluations 테이블 1:N Horizon 구조 마이그레이션 시작")
                
                await db.execute("DROP TABLE IF EXISTS proposal_evaluations_backup")
                await db.execute("CREATE TABLE proposal_evaluations_backup AS SELECT * FROM proposal_evaluations")
                await db.execute("DROP TABLE proposal_evaluations")
                
                await db.execute('''
                    CREATE TABLE proposal_evaluations (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        proposal_id INTEGER NOT NULL,
                        horizon_name TEXT NOT NULL,
                        predicted_roi_7d REAL,
                        actual_roi_7d REAL,
                        roi_divergence REAL,
                        predicted_trade_count_7d INTEGER,
                        actual_trade_count_7d INTEGER,
                        trade_count_divergence INTEGER,
                        candidate_roi REAL,
                        champion_roi REAL,
                        roi_gap REAL,
                        candidate_mdd REAL,
                        champion_mdd REAL,
                        virtual_rollback INTEGER DEFAULT 0,
                        actual_label TEXT,
                        actual_label_source TEXT,
                        due_at INTEGER NOT NULL DEFAULT 0,
                        evaluated_at INTEGER,
                        locked_at INTEGER,
                        retry_count INTEGER DEFAULT 0,
                        last_error TEXT,
                        evaluation_status TEXT NOT NULL DEFAULT 'PENDING',
                        horizon_type TEXT,
                        horizon_value INTEGER,
                        policy_version TEXT,
                        scorer_version TEXT,
                        predicted_risk_score REAL,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (proposal_id) REFERENCES strategy_proposals(id) ON UPDATE CASCADE ON DELETE CASCADE,
                        UNIQUE (proposal_id, horizon_name)
                    )
                ''')
                
                try:
                    await db.execute('''
                        INSERT OR IGNORE INTO proposal_evaluations (
                            proposal_id, horizon_name, predicted_roi_7d, actual_roi_7d, roi_divergence,
                            predicted_trade_count_7d, actual_trade_count_7d, trade_count_divergence,
                            due_at, evaluation_status, horizon_type, horizon_value, policy_version, scorer_version
                        )
                        SELECT 
                            proposal_id, '7d', predicted_roi_7d, actual_roi_7d, roi_divergence,
                            predicted_trade_count_7d, actual_trade_count_7d, trade_count_divergence,
                            0, 'COMPLETED', 'elapsed', 604800, 'v1', 'mock_v1'
                        FROM proposal_evaluations_backup
                    ''')
                    logger.info("[Migration] proposal_evaluations 기존 데이터 이관 완료")
                except Exception as e:
                    logger.error(f"[Migration] proposal_evaluations 복원 중 예외: {e}")
                    
                await db.execute("DROP TABLE IF EXISTS proposal_evaluations_backup")
                logger.info("[Migration] proposal_evaluations 테이블 1:N Horizon 구조 마이그레이션 완수")

        # 인덱스
        await db.execute('CREATE INDEX IF NOT EXISTS idx_trades_exch_sym_time ON trades (exchange, symbol, trade_timestamp DESC)')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_candles_exch_sym_time ON candles (exchange, symbol, interval, timestamp DESC)')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_orders_history_portfolio_id ON orders_history (portfolio_id)')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_positions_portfolio_id ON positions (portfolio_id)')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_exchange_assets_active ON exchange_assets (exchange, is_active)')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_real_orders_exch_sym ON real_orders (exchange, symbol)')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_trades_timestamp ON trades (trade_timestamp)')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_candles_timestamp ON candles (timestamp)')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_system_events_timestamp ON system_events (timestamp DESC)')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_system_events_type ON system_events (event_type)')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_strategy_param_hist ON strategy_parameter_history (strategy_id, version_id)')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_strategy_perf_snap ON strategy_performance_snapshots (strategy_id, version_id)')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_market_regime_sum ON market_regime_summaries (symbol, timestamp DESC)')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_strategy_prop_group ON strategy_proposals (proposal_group_id)')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_prop_eval_status_due ON proposal_evaluations (evaluation_status, due_at)')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_prop_eval_id_horizon ON proposal_evaluations (proposal_id, horizon_name)')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_girs_shadow_metrics_time ON girs_shadow_metrics (timestamp DESC)')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_promotion_event_log_prop ON promotion_event_log (proposal_id)')
        await db.commit()
    
    await migrate_data(target_path)
    await seed_initial_assets(target_path)
    logger.info("Database initialization and migration complete.")

async def migrate_data(db_path: str = None):
    async with get_db_conn(db_path) as db:
        # universe_guard_state 테이블 생성 및 마이그레이션 보장
        cursor = await db.execute("PRAGMA table_info(universe_guard_state)")
        columns = await cursor.fetchall()
        if columns:
            has_exchange = any(col['name'] == 'exchange' for col in columns)
            if not has_exchange:
                logger.info("[Migration] universe_guard_state 테이블 복합 기본키 구조 마이그레이션 감지")
                
                # 1. 기존 데이터가 Upbit crypto 전용인지 검증
                cursor_old = await db.execute("SELECT symbol FROM universe_guard_state")
                old_rows = await cursor_old.fetchall()
                
                upbit_symbols = set()
                try:
                    async with db.execute("SELECT symbol FROM exchange_assets WHERE exchange = 'upbit'") as cur_ea:
                        ea_rows = await cur_ea.fetchall()
                        for r in ea_rows:
                            upbit_symbols.add(r['symbol'])
                except Exception:
                    pass
                
                crypto_symbols = {"BTC", "ETH", "XRP", "SOL", "DOGE", "ADA", "AVAX", "DOT", "TRX", "LINK"}
                
                for row in old_rows:
                    sym = row['symbol']
                    if sym.isdigit() and len(sym) == 6:
                        raise ValueError(f"CRITICAL Migration Failure: Existing universe_guard_state symbol '{sym}' looks like a stock code. Migration halted for safety.")
                    
                    is_valid_crypto = sym in crypto_symbols or sym in upbit_symbols
                    if not is_valid_crypto:
                        import re
                        if not re.match(r"^[A-Z0-9]+$", sym):
                            raise ValueError(f"CRITICAL Migration Failure: Existing universe_guard_state symbol '{sym}' is not a valid crypto symbol. Migration halted.")
                
                logger.info("[Migration] Existing universe_guard_state data validation passed (Upbit crypto confirmed).")
                
                # 2. RENAME 및 신규 생성 후 백필
                await db.execute("DROP TABLE IF EXISTS universe_guard_state_old")
                await db.execute("ALTER TABLE universe_guard_state RENAME TO universe_guard_state_old")
                
                await db.execute('''
                    CREATE TABLE universe_guard_state (
                        exchange TEXT NOT NULL,
                        market_type TEXT NOT NULL,
                        symbol TEXT NOT NULL,
                        status TEXT,
                        blocked_reason TEXT,
                        blocked_count INTEGER DEFAULT 0,
                        last_blocked_at REAL,
                        last_event_logged_reason TEXT,
                        PRIMARY KEY (exchange, market_type, symbol)
                    )
                ''')
                
                await db.execute('''
                    INSERT INTO universe_guard_state (exchange, market_type, symbol, status, blocked_reason, blocked_count, last_blocked_at, last_event_logged_reason)
                    SELECT 'upbit', 'crypto', symbol, status, blocked_reason, blocked_count, last_blocked_at, last_event_logged_reason
                    FROM universe_guard_state_old
                ''')
                
                await db.execute("DROP TABLE IF EXISTS universe_guard_state_old")
                logger.info("[Migration] universe_guard_state 복합 기본키 마이그레이션 및 backfill 완료")
        else:
            # 테이블이 아예 없는 경우 신규 생성
            await db.execute('''
                CREATE TABLE IF NOT EXISTS universe_guard_state (
                    exchange TEXT NOT NULL,
                    market_type TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    status TEXT,
                    blocked_reason TEXT,
                    blocked_count INTEGER DEFAULT 0,
                    last_blocked_at REAL,
                    last_event_logged_reason TEXT,
                    PRIMARY KEY (exchange, market_type, symbol)
                )
            ''')
        # 인덱스 추가 보장 (lookup 및 status 인덱스)
        await db.execute('CREATE INDEX IF NOT EXISTS idx_universe_guard_state_status ON universe_guard_state (status)')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_universe_guard_state_lookup ON universe_guard_state (exchange, market_type, status)')
        # exchange_assets 테이블에 is_delisted 컬럼이 없으면 마이그레이션 수행
        await ensure_column(db, 'exchange_assets', 'is_delisted', 'INTEGER DEFAULT 0')

        # strategy_proposals 신규 필드 마이그레이션
        await ensure_column(db, 'strategy_proposals', 'decision_path_hash', 'TEXT')
        await ensure_column(db, 'strategy_proposals', 'audit_log_json', 'TEXT')
        await ensure_column(db, 'strategy_proposals', 'counterfactual_roi', 'REAL DEFAULT 0.0')
        await ensure_column(db, 'strategy_proposals', 'counterfactual_mdd', 'REAL DEFAULT 0.0')
        await ensure_column(db, 'strategy_proposals', 'is_counterfactual_tracked', 'INTEGER DEFAULT 0')

        # girs_shadow_metrics 신규 필드 마이그레이션
        await ensure_column(db, 'girs_shadow_metrics', 'trade_age_ms', 'INTEGER')
        await ensure_column(db, 'girs_shadow_metrics', 'orderbook_age_ms', 'INTEGER')
        await ensure_column(db, 'girs_shadow_metrics', 'indicator_age_ms', 'INTEGER')
        await ensure_column(db, 'girs_shadow_metrics', 'is_fresh', 'INTEGER DEFAULT 1')
        await ensure_column(db, 'girs_shadow_metrics', 'stale_reason', 'TEXT')
        await ensure_column(db, 'girs_shadow_metrics', 'snapshot_version', 'TEXT')
        await ensure_column(db, 'girs_shadow_metrics', 'snapshot_hash', 'TEXT')
        await ensure_column(db, 'girs_shadow_metrics', 'feature_vector_hash', 'TEXT')
        await ensure_column(db, 'girs_shadow_metrics', 'orderbook_available', 'INTEGER DEFAULT 0')
        await ensure_column(db, 'girs_shadow_metrics', 'market_type', 'TEXT')
        await ensure_column(db, 'girs_shadow_metrics', 'session_state', 'TEXT')
        await ensure_column(db, 'girs_shadow_metrics', 'volatility_regime', 'TEXT')
        await ensure_column(db, 'girs_shadow_metrics', 'liquidity_regime', 'TEXT')
        await ensure_column(db, 'girs_shadow_metrics', 'exchange', 'TEXT')
        await ensure_column(db, 'girs_shadow_metrics', 'tps', 'REAL')
        await ensure_column(db, 'girs_shadow_metrics', 'trade_count', 'INTEGER')
        await ensure_column(db, 'girs_shadow_metrics', 'volume', 'REAL')
        await ensure_column(db, 'girs_shadow_metrics', 'idle_time', 'REAL')

        tables = ['trades', 'candles', 'positions', 'orders_history']
        for table in tables:
            try:
                await db.execute(f"UPDATE {table} SET exchange = 'upbit', symbol = REPLACE(symbol, 'UPB-', '') WHERE symbol LIKE 'UPB-%'")
                await db.execute(f"UPDATE {table} SET exchange = 'upbit', symbol = REPLACE(symbol, 'KRW-', '') WHERE symbol LIKE 'KRW-%'")
                await db.execute(f"UPDATE {table} SET exchange = 'kis', symbol = REPLACE(symbol, 'KIS-', '') WHERE symbol LIKE 'KIS-%'")
                await db.execute(f"UPDATE {table} SET exchange = 'upbit' WHERE exchange IS NULL")
            except Exception as e:
                logger.error(f"Migration error for {table}: {e}")

        # KIS is_active 오염 복구 (더 이상 신규 종목 추가를 방해하지 않도록 비활성화)
        # try:
        #     # candles 또는 trades 테이블에 실제 데이터(수집 이력)가 전혀 없는 KIS 종목은 is_active를 0으로 원복시킵니다.
        #     await db.execute('''
        #         UPDATE exchange_assets
        #         SET is_active = 0, updated_at = datetime('now')
        #         WHERE exchange = 'kis'
        #           AND is_active = 1
        #           AND symbol NOT IN (
        #               SELECT DISTINCT symbol FROM candles WHERE exchange = 'kis'
        #               UNION
        #               SELECT DISTINCT symbol FROM trades WHERE exchange = 'kis'
        #           )
        #     ''')
        #     logger.info("Successfully cleaned up contaminated KIS assets: restored is_active to 0 for uncollected symbols.")
        # except Exception as e:
        #     logger.error(f"Failed to restore KIS assets is_active: {e}")

        # 7. orders_history 중복 저장 이력 클리닝 (백테스트 체결 내역 2중 저장 버그 소거 대응)
        try:
            await db.execute('''
                DELETE FROM orders_history
                WHERE rowid NOT IN (
                    SELECT MIN(rowid)
                    FROM orders_history
                    GROUP BY portfolio_id, exchange, symbol, side, price, quantity, fee, timestamp, reason, context
                )
            ''')
            logger.info("Cleaned up duplicate orders_history records.")
        except Exception as e:
            logger.error(f"Failed to clean duplicate orders: {e}")

        await db.commit()

async def seed_initial_assets(db_path: str = None):
    """기존 stock_master.json 파일이 존재하면 DB의 asset_master 및 exchange_assets 테이블로 Seeding을 1회 수행합니다."""
    target_path = db_path if db_path is not None else DB_PATH
    import json
    
    # data/stock_master.json 경로 획득
    json_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'data', 'stock_master.json')
    if not os.path.exists(json_path):
        logger.info(f"Seed file not found at {json_path}. Skipping initial seeding.")
        return

    logger.info(f"Seeding initial assets from {json_path} to database...")
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        async with get_db_conn(target_path) as db:
            for exchange, symbols in data.items():
                asset_type = 'crypto' if exchange in ('upbit', 'bithumb') else 'stock'
                for symbol, name in symbols.items():
                    # 1. asset_master에 삽입
                    await db.execute('''
                        INSERT OR IGNORE INTO asset_master (symbol, korean_name, asset_type)
                        VALUES (?, ?, ?)
                    ''', (symbol, name, asset_type))

                    # 2. exchange_assets에 삽입 (기존 마스터 종목들은 모두 기본 활성 상태로 이식)
                    await db.execute('''
                        INSERT OR IGNORE INTO exchange_assets (exchange, symbol, is_active)
                        VALUES (?, ?, 1)
                    ''', (exchange, symbol))
            await db.commit()
            logger.info("Initial assets seeding successfully completed.")
    except Exception as e:
        logger.error(f"Failed to seed initial assets from JSON: {e}")

if __name__ == "__main__":
    asyncio.run(init_db())
