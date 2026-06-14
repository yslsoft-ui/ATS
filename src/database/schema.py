import asyncio
import os
import shutil
import time
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
    target_path = db_path if db_path is not None else DB_PATH
    
    max_retries = 10
    retry_delay = 0.5
    
    # 1. 구형 스키마 감지 및 파괴적 리셋 (Destructive Reset)
    try:
        db_dir = os.path.dirname(target_path)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir)
            
        if os.path.exists(target_path):
            conn = sqlite3.connect(target_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            try:
                cursor.execute("PRAGMA table_info(portfolios)")
                p_cols = [r[1] for r in cursor.fetchall()]
                
                cursor.execute("PRAGMA table_info(orders_history)")
                oh_cols = [r[1] for r in cursor.fetchall()]

                cursor.execute("PRAGMA table_info(exchange_assets)")
                ea_cols = [r[1] for r in cursor.fetchall()]
                
                # 구형 스키마 조건: portfolios에 exchange_id가 있거나 orders_history에 exchange가 있는 경우, 또는 exchange_assets에 exchange가 있는 경우
                has_old_p_schema = p_cols and ("exchange_id" in p_cols or "cash" in p_cols or "initial_cash" in p_cols)
                has_old_oh_schema = oh_cols and "exchange" in oh_cols
                has_old_ea_schema = ea_cols and "exchange" in ea_cols
                
                if has_old_p_schema or has_old_oh_schema or has_old_ea_schema:
                    logger.warning("[Destructive Reset] 구형 오염된 DB 스키마가 감지되었습니다. 파괴적 스키마 리셋을 개시합니다.")
                    conn.close()
                    
                    # 1) 안전 복사 백업
                    backup_filename = f"trading_backup_{int(time.time())}.db"
                    backup_path = os.path.join(db_dir or ".", backup_filename)
                    shutil.copy2(target_path, backup_path)
                    logger.warning(f"[Destructive Reset] 기존 DB 파일을 {backup_path} 경로에 안전 백업하였습니다.")
                    
                    # 2) 관련 모든 테이블 Drop
                    conn = sqlite3.connect(target_path)
                    cursor = conn.cursor()
                    tables_to_drop = [
                        "portfolios", "portfolio_exchanges", "positions", 
                        "orders_history", "real_orders", "girs_shadow_metrics", 
                        "universe_guard_state", "proposal_evaluations", "proposal_evaluation_runs",
                        "proposal_reevaluation_jobs", "promotion_event_log", "strategy_versions", 
                        "strategy_parameter_history", "strategy_performance_snapshots", "alerts", "candles", "trades",
                        "exchange_assets"
                    ]
                    for tbl in tables_to_drop:
                        cursor.execute(f"DROP TABLE IF EXISTS {tbl}")
                    cursor.execute("VACUUM")
                    conn.commit()
                    logger.warning("[Destructive Reset] 구형 오염 테이블들의 DROP 처리를 성공적으로 완료했습니다.")
            except Exception as e:
                logger.error(f"[Destructive Reset] 스키마 검사 중 오류 발생: {e}")
            finally:
                conn.close()
    except Exception as e:
        logger.error(f"[Destructive Reset] 외부 백업 및 초기화 루프 예외: {e}")

    # 2. 신규 정규 스키마 구축 기동
    for attempt in range(max_retries):
        try:
            await _init_db_core(target_path)
            return
        except sqlite3.OperationalError as e:
            if "locked" in str(e) and attempt < max_retries - 1:
                logger.warning(f"Database is locked during init_db. Retrying in {retry_delay}s... (Attempt {attempt + 1}/{max_retries})")
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 1.5, 5.0)
            else:
                logger.error(f"Critical operational error during init_db: {e}")
                raise e

async def _init_db_core(target_path: str):
    logger.info(f"Initializing database at {target_path}")
    
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
                exchange_id TEXT,
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

        # 3. portfolios (정규화 완료)
        await db.execute('''
            CREATE TABLE IF NOT EXISTS portfolios (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                type TEXT NOT NULL,
                duration REAL DEFAULT 0.0,
                strategy_info TEXT DEFAULT '',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # 3.5. portfolio_exchanges
        await db.execute('''
            CREATE TABLE IF NOT EXISTS portfolio_exchanges (
                portfolio_id TEXT,
                exchange_id TEXT,
                initial_cash REAL DEFAULT 0.0,
                cash REAL DEFAULT 0.0,
                is_primary INTEGER DEFAULT 0,
                metrics TEXT DEFAULT '{}',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (portfolio_id, exchange_id),
                FOREIGN KEY (portfolio_id) REFERENCES portfolios(id) ON UPDATE CASCADE ON DELETE CASCADE
            )
        ''')

        # 4. positions (명칭 통일 및 FK 제약)
        await db.execute('''
            CREATE TABLE IF NOT EXISTS positions (
                portfolio_id TEXT,
                symbol TEXT,
                quantity REAL DEFAULT 0,
                avg_price REAL DEFAULT 0,
                entry_time REAL DEFAULT 0.0,
                peak_price REAL DEFAULT 0.0,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                exchange_id TEXT,
                PRIMARY KEY (portfolio_id, exchange_id, symbol),
                FOREIGN KEY (portfolio_id, exchange_id) REFERENCES portfolio_exchanges(portfolio_id, exchange_id) ON UPDATE CASCADE ON DELETE CASCADE
            )
        ''')

        # 5. orders_history (exchange_id 명칭 통일)
        await db.execute('''
            CREATE TABLE IF NOT EXISTS orders_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                portfolio_id TEXT,
                exchange_id TEXT,
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

        # 6. alerts
        await db.execute('''
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                exchange_id TEXT,
                symbol TEXT,
                price REAL,
                msg TEXT,
                timestamp INTEGER
            )
        ''')

        # 7. candles
        await db.execute('''
            CREATE TABLE IF NOT EXISTS candles (
                exchange_id TEXT,
                symbol TEXT,
                interval INTEGER,
                timestamp INTEGER,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume REAL,
                is_closed INTEGER DEFAULT 1,
                PRIMARY KEY (exchange_id, symbol, interval, timestamp)
            )
        ''')

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
                exchange_id TEXT,
                symbol TEXT,
                is_active INTEGER DEFAULT 1,
                is_delisted INTEGER DEFAULT 0,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (exchange_id, symbol),
                FOREIGN KEY (symbol) REFERENCES asset_master(symbol) ON UPDATE CASCADE
            )
        ''')

        # 10. real_orders
        await db.execute('''
            CREATE TABLE IF NOT EXISTS real_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                exchange_id TEXT NOT NULL,
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

        # 12. strategy_versions
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

        # 13. strategy_parameter_history
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

        # 14. strategy_performance_snapshots
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

        # 15. market_regime_summaries
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

        # 16. strategy_insights
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

        # 17. strategy_proposals
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

        # 18. proposal_evaluations
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
                baseline_value REAL,
                baseline_timestamp INTEGER,
                baseline_volume INTEGER,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (proposal_id) REFERENCES strategy_proposals(id) ON UPDATE CASCADE ON DELETE CASCADE,
                UNIQUE (proposal_id, horizon_name)
            )
        ''')

        # 19. girs_shadow_metrics
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
                exchange_id TEXT,
                tps REAL,
                trade_count INTEGER,
                volume REAL,
                idle_time REAL
            )
        ''')

        # 20. promotion_event_log
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

        # 21. universe_guard_state (exchange_id 명칭 통일)
        await db.execute('''
            CREATE TABLE IF NOT EXISTS universe_guard_state (
                exchange_id TEXT NOT NULL,
                market_type TEXT NOT NULL,
                symbol TEXT NOT NULL,
                status TEXT,
                blocked_reason TEXT,
                blocked_count INTEGER DEFAULT 0,
                last_blocked_at REAL,
                last_event_logged_reason TEXT,
                PRIMARY KEY (exchange_id, market_type, symbol)
            )
        ''')

        # 22. proposal_reevaluation_jobs
        await db.execute('''
            CREATE TABLE IF NOT EXISTS proposal_reevaluation_jobs (
                job_id INTEGER PRIMARY KEY AUTOINCREMENT,
                proposal_id INTEGER NOT NULL,
                status TEXT NOT NULL,
                requested_at INTEGER NOT NULL,
                started_at INTEGER,
                finished_at INTEGER,
                requested_by TEXT NOT NULL,
                mode TEXT NOT NULL,
                input_snapshot_id INTEGER,
                error_message TEXT,
                worker_id TEXT,
                FOREIGN KEY (proposal_id) REFERENCES strategy_proposals(id) ON UPDATE CASCADE ON DELETE CASCADE
            )
        ''')

        # 23. proposal_evaluation_runs
        await db.execute('''
            CREATE TABLE IF NOT EXISTS proposal_evaluation_runs (
                evaluation_run_id INTEGER PRIMARY KEY AUTOINCREMENT,
                proposal_id INTEGER NOT NULL,
                job_id INTEGER,
                girs_score REAL,
                promotion_score REAL,
                stability_score REAL,
                rollback_probability REAL,
                data_quality_blocked INTEGER DEFAULT 0,
                counterfactual_result_id INTEGER,
                model_version TEXT,
                scorer_version TEXT,
                simulator_version TEXT,
                created_at INTEGER NOT NULL,
                FOREIGN KEY (proposal_id) REFERENCES strategy_proposals(id) ON UPDATE CASCADE ON DELETE CASCADE,
                FOREIGN KEY (job_id) REFERENCES proposal_reevaluation_jobs(job_id) ON UPDATE CASCADE ON DELETE SET NULL
            )
        ''')

        # 인덱스 생성
        await db.execute('CREATE INDEX IF NOT EXISTS idx_trades_exch_sym_time ON trades (exchange_id, symbol, trade_timestamp DESC)')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_candles_exch_sym_time ON candles (exchange_id, symbol, interval, timestamp DESC)')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_orders_history_portfolio_id ON orders_history (portfolio_id)')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_positions_portfolio_id ON positions (portfolio_id)')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_exchange_assets_active ON exchange_assets (exchange_id, is_active)')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_real_orders_exch_sym ON real_orders (exchange_id, symbol)')
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
        await db.execute('CREATE INDEX IF NOT EXISTS idx_proposal_reeval_jobs_prop ON proposal_reevaluation_jobs (proposal_id, status)')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_proposal_eval_runs_prop ON proposal_evaluation_runs (proposal_id)')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_universe_guard_state_status ON universe_guard_state (status)')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_universe_guard_state_lookup ON universe_guard_state (exchange_id, market_type, status)')
        
        await db.commit()
    
    await seed_initial_assets(target_path)
    logger.info("Database initialization and schema reset complete.")

async def seed_initial_assets(db_path: str = None):
    target_path = db_path if db_path is not None else DB_PATH
    import json
    
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
                    await db.execute('''
                        INSERT OR IGNORE INTO asset_master (symbol, korean_name, asset_type)
                        VALUES (?, ?, ?)
                    ''', (symbol, name, asset_type))

                    await db.execute('''
                        INSERT OR IGNORE INTO exchange_assets (exchange_id, symbol, is_active)
                        VALUES (?, ?, 1)
                    ''', (exchange, symbol))
            await db.commit()
            logger.info("Initial assets seeding successfully completed.")
    except Exception as e:
        logger.error(f"Failed to seed initial assets from JSON: {e}")

if __name__ == "__main__":
    asyncio.run(init_db())
