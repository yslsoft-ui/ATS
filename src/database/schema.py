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

async def _check_migration_needed(db_path: str) -> bool:
    if not os.path.exists(db_path) or os.path.getsize(db_path) == 0:
        return False
    try:
        async with get_db_conn(db_path) as db:
            cursor = await db.execute("PRAGMA table_info(portfolios)")
            rows = await cursor.fetchall()
            is_id_text = False
            for r in rows:
                if r[1] == 'id' and 'TEXT' in str(r[2]).upper():
                    is_id_text = True
            if is_id_text:
                return True
    except Exception as e:
        logger.warning(f"Error checking migration necessity: {e}")
    return False

async def migrate_to_integer_keys(db_path: str):
    logger.info(f"[Migration] Migrating {db_path} to INTEGER portfolio keys...")
    
    tables_to_backup = [
        'portfolio_exchanges', 'positions', 'orders_history',
        'strategy_insights', 'strategy_proposals', 'portfolios'
    ]
    
    async with get_db_conn(db_path) as db:
        await db.execute("PRAGMA foreign_keys=OFF")
        
        existing_tables = []
        for t in tables_to_backup:
            cur = await db.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{t}'")
            if await cur.fetchone():
                existing_tables.append(t)
                logger.info(f"[Migration] Renaming {t} to {t}_old")
                await db.execute(f"ALTER TABLE {t} RENAME TO {t}_old")
        
        schema_sql_path = os.path.join(os.path.dirname(__file__), 'schema.sql')
        with open(schema_sql_path, 'r', encoding='utf-8') as f:
            schema_sql = f.read()
        
        await db.executescript(schema_sql)
        
        id_map = {}
        
        if 'portfolios' in existing_tables:
            cursor = await db.execute("PRAGMA table_info(portfolios_old)")
            cols = [col[1] for col in await cursor.fetchall()]
            has_ended_at = 'ended_at' in cols
            
            select_cols = "id, name, type, duration, strategy_info, created_at, updated_at"
            if has_ended_at:
                select_cols += ", ended_at"
                
            cursor = await db.execute(f"SELECT {select_cols} FROM portfolios_old")
            rows = await cursor.fetchall()
            
            seq_counter = 2
            for r in rows:
                old_id = r[0]
                if old_id == 'live' or old_id == '1' or old_id == 1:
                    new_id = 1
                else:
                    new_id = seq_counter
                    seq_counter += 1
                id_map[old_id] = new_id
                
                p_type = r[2]
                ended_at = r[7] if has_ended_at else None
                if p_type == 'simulation_ended':
                    p_type = 'simulation'
                    ended_at = r[6]  # updated_at
                
                await db.execute('''
                    INSERT OR REPLACE INTO portfolios (id, name, type, duration, strategy_info, ended_at, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (new_id, r[1], p_type, r[3], r[4], ended_at, r[5], r[6]))
                
        if 'portfolio_exchanges' in existing_tables:
            cursor = await db.execute("SELECT portfolio_id, exchange_id, initial_cash, cash, metrics, created_at, updated_at FROM portfolio_exchanges_old")
            rows = await cursor.fetchall()
            for r in rows:
                old_pid = r[0]
                new_pid = id_map.get(old_pid, 999)
                await db.execute('''
                    INSERT INTO portfolio_exchanges (portfolio_id, exchange_id, initial_cash, cash, metrics, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (new_pid, r[1], r[2], r[3], r[4], r[5], r[6]))
                    
        if 'positions' in existing_tables:
            cursor = await db.execute("SELECT portfolio_id, symbol, quantity, avg_price, entry_time, peak_price, updated_at, exchange_id FROM positions_old")
            rows = await cursor.fetchall()
            for r in rows:
                old_pid = r[0]
                new_pid = id_map.get(old_pid, 999)
                await db.execute('''
                    INSERT INTO positions (portfolio_id, symbol, quantity, avg_price, entry_time, peak_price, updated_at, exchange_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (new_pid, r[1], r[2], r[3], r[4], r[5], r[6], r[7]))
                    
        if 'orders_history' in existing_tables:
            cursor = await db.execute("SELECT portfolio_id, exchange_id, market, strategy_id, symbol, side, price, quantity, fee, timestamp, reason, context FROM orders_history_old")
            rows = await cursor.fetchall()
            for r in rows:
                old_pid = r[0]
                new_pid = id_map.get(old_pid, 999)
                await db.execute('''
                    INSERT INTO orders_history (portfolio_id, exchange_id, market, strategy_id, symbol, side, price, quantity, fee, timestamp, reason, context)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (new_pid, r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[9], r[10], r[11]))

        if 'strategy_insights' in existing_tables:
            cursor = await db.execute("SELECT id, portfolio_id, strategy_id, category, fact_summary, details_json, created_at FROM strategy_insights_old")
            rows = await cursor.fetchall()
            for r in rows:
                old_pid = r[1]
                new_pid = id_map.get(old_pid, 999) if old_pid else None
                await db.execute('''
                    INSERT OR REPLACE INTO strategy_insights (id, portfolio_id, strategy_id, category, fact_summary, details_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (r[0], new_pid, r[2], r[3], r[4], r[5], r[6]))

        if 'strategy_proposals' in existing_tables:
            cursor = await db.execute('''
                SELECT id, insight_id, proposal_group_id, version, portfolio_id, strategy_id, status, outcome, 
                       original_params, proposed_params, metrics, mutation_trace, confidence_score, 
                       applied_at, rolled_back_at, decision_path_hash, audit_log_json, 
                       counterfactual_roi, counterfactual_mdd, is_counterfactual_tracked, created_at, updated_at
                FROM strategy_proposals_old
            ''')
            rows = await cursor.fetchall()
            for r in rows:
                old_pid = r[4]
                new_pid = id_map.get(old_pid, 999) if old_pid else None
                await db.execute('''
                    INSERT OR REPLACE INTO strategy_proposals 
                    (id, insight_id, proposal_group_id, version, portfolio_id, strategy_id, status, outcome, 
                     original_params, proposed_params, metrics, mutation_trace, confidence_score, 
                     applied_at, rolled_back_at, decision_path_hash, audit_log_json, 
                     counterfactual_roi, counterfactual_mdd, is_counterfactual_tracked, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (r[0], r[1], r[2], r[3], new_pid, r[5], r[6], r[7], r[8], r[9], r[10], r[11], r[12], r[13], r[14], r[15], r[16], r[17], r[18], r[19], r[20], r[21]))

        for t in existing_tables:
            logger.info(f"[Migration] Dropping old backup table {t}_old")
            await db.execute(f"DROP TABLE {t}_old")
            
        await db.commit()
        await db.execute("PRAGMA foreign_keys=ON")
        
    logger.info("[Migration] Migration to integer keys completed successfully.")

async def init_db(db_path: str = None):
    import sqlite3
    target_path = db_path if db_path is not None else DB_PATH
    
    max_retries = 10
    retry_delay = 0.5
    
    db_dir = os.path.dirname(target_path)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir)
        
    if await _check_migration_needed(target_path):
        for attempt in range(max_retries):
            try:
                await migrate_to_integer_keys(target_path)
                return
            except sqlite3.OperationalError as e:
                if "locked" in str(e) and attempt < max_retries - 1:
                    logger.warning(f"Database is locked during migration. Retrying in {retry_delay}s... (Attempt {attempt + 1}/{max_retries})")
                    await asyncio.sleep(retry_delay)
                    retry_delay = min(retry_delay * 1.5, 5.0)
                else:
                    logger.error(f"Critical operational error during migration: {e}")
                    raise e
        return
        
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
    
    schema_sql_path = os.path.join(os.path.dirname(__file__), 'schema.sql')
    if not os.path.exists(schema_sql_path):
        raise FileNotFoundError(f"Database schema SQL file not found at {schema_sql_path}")

    with open(schema_sql_path, 'r', encoding='utf-8') as f:
        schema_sql = f.read()

    async with get_db_conn(target_path) as db:
        await db.executescript(schema_sql)
        await db.commit()
        
        # portfolios 테이블에 ended_at 컬럼 추가
        await ensure_column(db, 'portfolios', 'ended_at', 'DATETIME')
        
        # exchange_assets 테이블에 market 및 market_updated_at 컬럼 추가
        await ensure_column(db, 'exchange_assets', 'market', 'TEXT')
        await ensure_column(db, 'exchange_assets', 'market_updated_at', 'DATETIME')
        
        # asset_master 테이블에 category 컬럼 추가
        await ensure_column(db, 'asset_master', 'category', 'TEXT')

        # orders_history 및 real_orders 테이블에 tax 컬럼 추가
        await ensure_column(db, 'orders_history', 'tax', 'REAL DEFAULT 0.0')
        await ensure_column(db, 'real_orders', 'tax', 'REAL DEFAULT 0.0')
        
        # candles 테이블에 is_closed 및 is_backfill 컬럼 추가
        await ensure_column(db, 'candles', 'is_closed', 'INTEGER DEFAULT 1')
        await ensure_column(db, 'candles', 'is_backfill', 'INTEGER DEFAULT 0')
        await db.execute("CREATE INDEX IF NOT EXISTS idx_candles_lookup_latest ON candles (exchange_id, symbol, is_closed, timestamp DESC)")
        
        # exchanges 테이블에 korean_name 컬럼 추가 및 데이터 보정
        await ensure_column(db, 'exchanges', 'korean_name', 'TEXT')
        await db.execute("UPDATE exchanges SET korean_name = '업비트' WHERE id = 'upbit'")
        await db.execute("UPDATE exchanges SET korean_name = '한국투자증권' WHERE id = 'kis'")
        await db.execute("UPDATE exchanges SET korean_name = '빗썸' WHERE id = 'bithumb'")
        await db.commit()
        
        # kis_stock_info 테이블 생성
        await db.execute("""
            CREATE TABLE IF NOT EXISTS kis_stock_info (
                symbol TEXT PRIMARY KEY,
                prdt_name TEXT,
                prdt_abrv_name TEXT,
                mket_id_cd TEXT,
                scty_grp_id_cd TEXT,
                excg_dvsn_cd TEXT,
                lstg_stqt INTEGER,
                lstg_cptl_amt INTEGER,
                cpta INTEGER,
                papr REAL,
                issu_pric REAL,
                kospi200_item_yn TEXT,
                scts_mket_lstg_dt TEXT,
                kosdaq_mket_lstg_dt TEXT,
                lstg_abol_dt TEXT,
                std_pdno TEXT,
                prdt_eng_name TEXT,
                tr_stop_yn TEXT,
                admn_item_yn TEXT,
                thdt_clpr REAL,
                bfdy_clpr REAL,
                std_idst_clsf_cd_name TEXT,
                idx_bztp_lcls_cd_name TEXT,
                idx_bztp_mcls_cd_name TEXT,
                idx_bztp_scls_cd_name TEXT,
                cptt_trad_tr_psbl_yn TEXT,
                nxt_tr_stop_yn TEXT,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.commit()

        # planned_asset_events 테이블 및 인덱스 생성
        await db.execute("""
            CREATE TABLE IF NOT EXISTS planned_asset_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                exchange_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                event_type TEXT NOT NULL CHECK (event_type IN ('listing', 'delisting')),
                scheduled_at DATETIME NOT NULL,
                notice_url TEXT,
                status TEXT NOT NULL DEFAULT 'PLANNED' CHECK (status IN ('PLANNED', 'EXECUTED', 'CANCELLED')),
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_planned_events_lookup ON planned_asset_events (exchange_id, symbol, status)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_planned_events_schedule ON planned_asset_events (status, scheduled_at)")
        await db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_planned_events_unique ON planned_asset_events (exchange_id, symbol, event_type, scheduled_at)")
        await db.commit()
        
        # simulation_ended 데이터 보정 마이그레이션 실행
        await db.execute("""
            UPDATE portfolios 
            SET type = 'simulation', ended_at = updated_at 
            WHERE type = 'simulation_ended'
        """)
        await db.commit()

        # alerts 테이블 제거 마이그레이션 실행
        await db.execute("DROP TABLE IF EXISTS alerts")
        await db.commit()
        logger.info("[Migration] alerts 테이블이 삭제되었습니다.")

        # proposal_evaluations 시간 컬럼 초 단위 -> ms 단위 마이그레이션
        await db.execute("""
            UPDATE proposal_evaluations
            SET due_at = due_at * 1000
            WHERE due_at IS NOT NULL AND due_at < 1000000000000
        """)
        await db.execute("""
            UPDATE proposal_evaluations
            SET evaluated_at = evaluated_at * 1000
            WHERE evaluated_at IS NOT NULL AND evaluated_at < 1000000000000
        """)
        await db.execute("""
            UPDATE proposal_evaluations
            SET locked_at = locked_at * 1000
            WHERE locked_at IS NOT NULL AND locked_at < 1000000000000
        """)
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
