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
    
    # 1. DB 디렉토리 생성 보장
    db_dir = os.path.dirname(target_path)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir)
        
    # 2. 신규 스키마 구축 기동
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
