import os
import io
import sys
import zipfile
import asyncio
import aiohttp
from typing import List, Tuple

# 프로젝트 루트 경로 추가
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.database.connection import get_db_conn
from src.engine.utils.telemetry import setup_logging, get_logger

setup_logging(log_file="seed_kis_master.log")
logger = get_logger("seed_kis_master")

KOSPI_URL = "https://new.real.download.dws.co.kr/common/master/kospi_code.mst.zip"
KOSDAQ_URL = "https://new.real.download.dws.co.kr/common/master/kosdaq_code.mst.zip"

async def download_and_parse_mst(url: str, filename: str) -> List[Tuple[str, str]]:
    logger.info(f"Downloading master file from {url}...")
    parsed_assets = []
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status != 200:
                    logger.error(f"Failed to download {filename}. HTTP Status: {response.status}")
                    return []
                content = await response.read()
                
        # Zip 파일 압축 해제 및 파싱
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            with z.open(filename) as f:
                # mst 파일은 한 라인씩 고정폭 바이트 구조로 되어 있음
                for line in f:
                    if len(line) < 61:
                        continue
                    try:
                        # 0~9 바이트: 단축코드 (예: 'A005930  ')
                        shrn_iscd = line[0:9].decode('cp949').strip()
                        # 보통 첫 글자 알파벳(A)을 제거한 6자리 코드를 사용
                        if shrn_iscd.startswith('A') or shrn_iscd.startswith('B'):
                            symbol = shrn_iscd[1:]
                        else:
                            symbol = shrn_iscd
                        
                        # 21~61 바이트: 한글 종목명 (40바이트)
                        kor_name = line[21:61].decode('cp949', errors='ignore').strip()
                        
                        if symbol and kor_name:
                            parsed_assets.append((symbol, kor_name))
                    except Exception as e:
                        logger.warning(f"Error parsing line {line}: {e}")
                        
        logger.info(f"Successfully parsed {len(parsed_assets)} symbols from {filename}")
    except Exception as e:
        logger.error(f"Failed to download or parse {filename}: {e}")
        
    return parsed_assets

async def seed_master_db():
    # settings.yaml을 읽어서 DB 경로 조회
    from src.config.manager import ConfigManager
    config_manager = ConfigManager("config/settings.yaml")
    db_path = config_manager.get('system.db_path', 'data/backtest.db')
    
    logger.info(f"Seeding KIS master to database at {db_path}...")
    
    kospi_assets = await download_and_parse_mst(KOSPI_URL, "kospi_code.mst")
    kosdaq_assets = await download_and_parse_mst(KOSDAQ_URL, "kosdaq_code.mst")
    
    all_assets = kospi_assets + kosdaq_assets
    if not all_assets:
        logger.error("No assets parsed. Seeding aborted.")
        return
        
    logger.info(f"Total {len(all_assets)} symbols parsed. Injecting to DB...")
    
    success_count = 0
    try:
        async with get_db_conn(db_path) as db:
            # 트랜잭션 시작
            await db.execute("BEGIN TRANSACTION")
            for symbol, name in all_assets:
                # 1. asset_master 에 저장
                await db.execute('''
                    INSERT OR IGNORE INTO asset_master (symbol, korean_name, asset_type)
                    VALUES (?, ?, 'stock')
                ''', (symbol, name))
                
                # 2. exchange_assets 에 저장 (기본적으로 비활성 is_active=0 상태)
                await db.execute('''
                    INSERT OR IGNORE INTO exchange_assets (exchange_id, symbol, is_active)
                    VALUES ('kis', ?, 0)
                ''', (symbol,))
                success_count += 1
            await db.commit()
        logger.info(f"Successfully seeded {success_count} assets to database.")
    except Exception as e:
        logger.error(f"Database insertion failed: {e}")

if __name__ == "__main__":
    asyncio.run(seed_master_db())
