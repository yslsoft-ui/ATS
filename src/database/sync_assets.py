import os
import io
import sys
import zipfile
import asyncio
import aiohttp
import json
import time
from typing import List, Tuple, Dict, Any

from src.database.connection import get_db_conn
from src.engine.utils.telemetry import get_logger

logger = get_logger("sync_assets")

KOSPI_URL = "https://new.real.download.dws.co.kr/common/master/kospi_code.mst.zip"
KOSDAQ_URL = "https://new.real.download.dws.co.kr/common/master/kosdaq_code.mst.zip"

async def download_and_parse_mst(url: str, filename: str, market_type: str) -> List[Tuple[str, str, str, str]]:
    logger.info(f"Downloading master file from {url}...")
    parsed_assets = []
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status != 200:
                    logger.error(f"Failed to download {filename}. HTTP Status: {response.status}")
                    return []
                content = await response.read()
                
        # Zip 파일 압축 해제 및 cp949 인코딩 파싱
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            with z.open(filename) as f:
                for line in f:
                    # 마스터 파일은 한 행당 약 280바이트 이상이므로 100바이트 이하인 행은 건너뜀
                    if len(line) < 100:
                        continue
                    try:
                        # 0~9 바이트: 단축코드 (예: 'A005930  ')
                        shrn_iscd = line[0:9].decode('cp949').strip()
                        if shrn_iscd.startswith('A') or shrn_iscd.startswith('B'):
                            symbol = shrn_iscd[1:]
                        else:
                            symbol = shrn_iscd
                        
                        # 21~61 바이트: 한글 종목명 (40바이트)
                        kor_name = line[21:61].decode('cp949', errors='ignore').strip()
                        
                        # 고정 바이트 파싱을 이용한 category 판별
                        # 61~63 바이트: 증권그룹구분코드 (scrt_grp_cls_code)
                        grp_code = line[61:63].decode('cp949', errors='ignore').strip()
                        
                        # ETP 상품구분코드 (etp_prod_cls_code) 오프셋: 코스피는 83~84 바이트, 코스닥은 79~80 바이트
                        if market_type == "KOSPI":
                            etp_code = line[83:84].decode('cp949', errors='ignore').strip()
                        else:
                            etp_code = line[79:80].decode('cp949', errors='ignore').strip()
                            
                        # 카테고리 매핑 규칙 적용
                        category = market_type
                        if grp_code in ("EF", "FE"):
                            category = "ETF"
                        elif grp_code == "EW":
                            category = "ELW"
                        elif etp_code in ("3", "4"):
                            category = "ETN"
                            
                        # KIS 모든 종목은 통합(UN) 채널로 수집하므로 default_market = "UN"으로 지정
                        default_market = "UN"
                        
                        if symbol and kor_name:
                            parsed_assets.append((symbol, kor_name, category, default_market))
                    except Exception as e:
                        logger.warning(f"Error parsing line {line}: {e}")
                        
        logger.info(f"Successfully parsed {len(parsed_assets)} symbols from {filename} with market_type={market_type}")
    except Exception as e:
        logger.error(f"Failed to download or parse {filename}: {e}")
        
    return parsed_assets

async def fetch_upbit_symbols() -> List[Tuple[str, str]]:
    logger.info("Fetching Upbit symbols from API...")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://api.upbit.com/v1/market/all") as response:
                if response.status == 200:
                    markets = await response.json()
                    symbols = [
                        (m['market'].replace('KRW-', ''), m.get('korean_name', ''))
                        for m in markets
                        if isinstance(m, dict) and m.get('market', '').startswith('KRW-')
                    ]
                    logger.info(f"Successfully fetched {len(symbols)} KRW symbols from Upbit")
                    return symbols
    except Exception as e:
        logger.error(f"Failed to fetch Upbit symbols: {e}")
    return []

async def fetch_bithumb_symbols() -> List[Tuple[str, str]]:
    logger.info("Fetching Bithumb symbols from API...")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://api.bithumb.com/v1/market/all") as response:
                if response.status == 200:
                    markets = await response.json()
                    symbols = [
                        (m['market'].replace('KRW-', ''), m.get('korean_name', ''))
                        for m in markets
                        if isinstance(m, dict) and m.get('market', '').startswith('KRW-')
                    ]
                    logger.info(f"Successfully fetched {len(symbols)} KRW symbols from Bithumb")
                    return symbols
    except Exception as e:
        logger.error(f"Failed to fetch Bithumb symbols: {e}")
    return []

async def sync_exchange_assets(db_path: str) -> Dict[str, Any]:
    """
    거래소 API 전체 종목 정보를 조회하여 DB(asset_master, exchange_assets)와 1회성 동기화를 진행합니다.
    - 신규 종목 추가
    - 상장 폐지 종목 비활성화 및 사용안함 마크 (is_active=0, is_delisted=1)
    - 결과 데이터(추가된 종목, 상폐된 종목, 거래소별 총 활성 개수)를 리턴합니다.
    """
    logger.info("=== 거래소 마스터 자산 동기화 시작 ===")
    
    # 동기화 결과 저장용 구조
    sync_results = {
        "upbit": {"total_active": 0, "total_registered": 0, "added": [], "delisted": []},
        "bithumb": {"total_active": 0, "total_registered": 0, "added": [], "delisted": []},
        "kis": {"total_active": 0, "total_registered": 0, "added": [], "delisted": []}
    }

    # 1. 최신 거래소 목록 조회 (API & 파일)
    upbit_symbols = await fetch_upbit_symbols()
    bithumb_symbols = await fetch_bithumb_symbols()
    
    kospi_raw = await download_and_parse_mst(KOSPI_URL, "kospi_code.mst", "KOSPI")
    kosdaq_raw = await download_and_parse_mst(KOSDAQ_URL, "kosdaq_code.mst", "KOSDAQ")
    
    if not kospi_raw or not kosdaq_raw:
        logger.error("[KIS] 코스피 또는 코스닥 마스터 파일 다운로드/파싱에 실패했습니다. KIS 자산 동기화를 건너뛰어 잘못된 상장폐지 및 비활성화 처리를 방지합니다.")
        kis_symbols = []
    else:
        # 이미 3튜플 구조이므로 그대로 병합
        kis_symbols = kospi_raw + kosdaq_raw

    if not upbit_symbols and not bithumb_symbols and not kis_symbols:
        logger.error("모든 거래소로부터 종목 정보를 읽어오는 데 실패했습니다. 동기화를 중단합니다.")
        return sync_results

    # 2. DB 연결 및 기존 매핑 로드
    async with get_db_conn(db_path) as db:
        # DB 무결성을 위해 PRAGMA foreign_keys 활성화
        await db.execute("PRAGMA foreign_keys = ON")

        # 2.1 asset_master 전체 종목 로드 (메모리 A)
        async with db.execute("SELECT symbol, korean_name FROM asset_master") as cursor:
            rows = await cursor.fetchall()
            db_asset_master = {r['symbol']: r['korean_name'] for r in rows}

        # 2.2 exchange_assets 전체 로드 (메모리 B)
        async with db.execute("SELECT exchange_id, symbol, is_active, is_delisted FROM exchange_assets") as cursor:
            rows = await cursor.fetchall()
            db_exchange_assets = {(r['exchange_id'], r['symbol']): (r['is_active'], r['is_delisted']) for r in rows}

        logger.info(f"DB 로드 완료: asset_master={len(db_asset_master)}개, exchange_assets={len(db_exchange_assets)}개")

        # 각 거래소별 동기화 대상 셋업
        targets = [
            ("upbit", [(sym, name, "Crypto", "KRW") for sym, name in upbit_symbols], "crypto", 1),
            ("bithumb", [(sym, name, "Crypto", "KRW") for sym, name in bithumb_symbols], "crypto", 1),
            ("kis", kis_symbols, "stock", 0)
        ]

        for exchange, api_list, asset_type, default_active in targets:
            if not api_list:
                logger.warning(f"[{exchange.upper()}] API 종목 목록이 비어있어 해당 거래소 동기화를 건너뜁니다.")
                continue

            api_symbols = set()
            new_master_count = 0
            new_exch_count = 0
            delisted_count = 0

            # 3. 신규 종목 추가 프로세스
            for sym, name, cat, default_market in api_list:
                api_symbols.add(sym)
                
                # 이미 download_and_parse_mst에서 정밀한 category가 설정되어 들어옴
                actual_category = cat
                
                # 3.1 asset_master에 신규 종목 추가 (category 포함)
                if sym not in db_asset_master:
                    await db.execute('''
                        INSERT OR IGNORE INTO asset_master (symbol, korean_name, asset_type, category)
                        VALUES (?, ?, ?, ?)
                    ''', (sym, name, asset_type, actual_category))
                    db_asset_master[sym] = name
                    new_master_count += 1
                else:
                    # 기존 종목의 category가 비어있거나 다르면 업데이트 지원
                    await db.execute('''
                        UPDATE asset_master
                        SET category = ?, updated_at = datetime('now')
                        WHERE symbol = ?
                    ''', (actual_category, sym))

                # 3.2 exchange_assets에 신규 매핑 추가 (market 및 market_updated_at 포함)
                key = (exchange, sym)
                if key not in db_exchange_assets:
                    await db.execute('''
                        INSERT OR IGNORE INTO exchange_assets (exchange_id, symbol, is_active, is_delisted, market, market_updated_at)
                        VALUES (?, ?, ?, 0, ?, NULL)
                    ''', (exchange, sym, default_active, default_market))
                    db_exchange_assets[key] = (default_active, 0)
                    new_exch_count += 1
                    # 결과 셋에 추가된 종목 기록
                    sync_results[exchange]["added"].append(f"{sym} ({name})")

                    # system_events 감사 로그 기록 (신규 상장)
                    ts_ms = int(time.time() * 1000)
                    ctx = json.dumps({
                        "exchange": exchange,
                        "symbol": sym,
                        "name": name,
                        "category": actual_category,
                        "relisted": False
                    }, ensure_ascii=False)
                    await db.execute('''
                        INSERT INTO system_events (event_type, target, message, timestamp, context)
                        VALUES (?, ?, ?, ?, ?)
                    ''', ("ASSET_LISTED", f"{exchange}:{sym}", f"[{exchange.upper()}] 신규 상장 감지: {sym} ({name})", ts_ms, ctx))
                else:
                    # 기존에 존재하나 상장폐지(is_delisted=1)로 마크되어 있던 종목이 다시 API 리스트에 나타난 경우 (재상장 등)
                    is_active, is_delisted = db_exchange_assets[key]
                    if is_delisted == 1:
                        await db.execute('''
                            UPDATE exchange_assets
                            SET is_delisted = 0, market = ?, market_updated_at = NULL, updated_at = datetime('now')
                            WHERE exchange_id = ? AND symbol = ?
                        ''', (default_market, exchange, sym))
                        db_exchange_assets[key] = (is_active, 0)
                        logger.info(f"[{exchange.upper()}] 재상장 감지: {sym} ({name}) 상장폐지 마크 해제")
                        # 재상장도 신규 추가 리스트에 표기하여 사용자에게 인지시킴
                        sync_results[exchange]["added"].append(f"{sym} ({name}) [재상장]")

                        # system_events 감사 로그 기록 (재상장)
                        ts_ms = int(time.time() * 1000)
                        ctx = json.dumps({
                            "exchange": exchange,
                            "symbol": sym,
                            "name": name,
                            "category": actual_category,
                            "relisted": True
                        }, ensure_ascii=False)
                        await db.execute('''
                            INSERT INTO system_events (event_type, target, message, timestamp, context)
                            VALUES (?, ?, ?, ?, ?)
                        ''', ("ASSET_LISTED", f"{exchange}:{sym}", f"[{exchange.upper()}] 재상장 감지: {sym} ({name})", ts_ms, ctx))
                    else:
                        # KIS 종목은 일괄적으로 market을 'UN'으로, market_updated_at을 NULL로 초기화합니다.
                        await db.execute('''
                            UPDATE exchange_assets
                            SET market = ?, market_updated_at = NULL, updated_at = datetime('now')
                            WHERE exchange_id = ? AND symbol = ?
                        ''', (default_market, exchange, sym))

            # 4. 상장 폐지 종목 마킹 프로세스 (API 리스트에는 없는데 DB 활성 상태인 것)
            for key, (is_active, is_delisted) in db_exchange_assets.items():
                exch_name, sym = key
                if exch_name != exchange:
                    continue
                
                # DB에서는 활성(is_active=1) 상태이거나 혹은 상장폐지 체크가 안되었는데 API에서는 유실된 경우
                if sym not in api_symbols and is_delisted == 0:
                    await db.execute('''
                        UPDATE exchange_assets
                        SET is_active = 0, is_delisted = 1, updated_at = datetime('now')
                        WHERE exchange_id = ? AND symbol = ?
                    ''', (exchange, sym))
                    db_exchange_assets[key] = (0, 1)
                    delisted_count += 1
                    
                    kor_name = db_asset_master.get(sym, sym)
                    sync_results[exchange]["delisted"].append(f"{sym} ({kor_name})")
                    logger.info(f"[{exchange.upper()}] 상장폐지(유실) 감지: {sym} -> 수집 비활성화(is_active=0) 및 사용안함(is_delisted=1) 마킹")

                    # system_events 감사 로그 기록 (상장폐지)
                    ts_ms = int(time.time() * 1000)
                    ctx = json.dumps({
                        "exchange": exchange,
                        "symbol": sym,
                        "name": kor_name,
                        "category": "Crypto" if exchange in ("upbit", "bithumb") else "Stock"
                    }, ensure_ascii=False)
                    await db.execute('''
                        INSERT INTO system_events (event_type, target, message, timestamp, context)
                        VALUES (?, ?, ?, ?, ?)
                    ''', ("ASSET_DELISTED", f"{exchange}:{sym}", f"[{exchange.upper()}] 상장폐지 감지: {sym} ({kor_name})", ts_ms, ctx))

            logger.info(f"[{exchange.upper()}] 동기화 결과: 마스터 신규 {new_master_count}개, 매핑 신규 {new_exch_count}개, 상장폐지 마크 {delisted_count}개")

        # 5. 대표 메이저 코인 강제 활성화 및 상장폐지 마크 방지 보장
        major_symbols = ["BTC", "ETH", "XRP", "SOL", "DOGE", "ADA"]
        for exchange in ["upbit", "bithumb"]:
            for sym in major_symbols:
                key = (exchange, sym)
                if key in db_exchange_assets:
                    await db.execute('''
                        UPDATE exchange_assets
                        SET is_active = 1, is_delisted = 0, updated_at = datetime('now')
                        WHERE exchange_id = ? AND symbol = ?
                    ''', (exchange, sym))
        
        await db.commit()

        # 6. 최종 총 활성 종목 수 및 전체 등록 종목 수 재쿼리하여 셋업
        for exchange in ["upbit", "bithumb", "kis"]:
            # 수신 종목 수 (is_active=1, is_delisted=0)
            async with db.execute('''
                SELECT count(*) FROM exchange_assets
                WHERE exchange_id = ? AND is_active = 1 AND is_delisted = 0
            ''', (exchange,)) as cursor:
                row = await cursor.fetchone()
                sync_results[exchange]["total_active"] = row[0] if row else 0

            # 전체 등록 종목 수 (is_delisted=0)
            async with db.execute('''
                SELECT count(*) FROM exchange_assets
                WHERE exchange_id = ? AND is_delisted = 0
            ''', (exchange,)) as cursor:
                row = await cursor.fetchone()
                sync_results[exchange]["total_registered"] = row[0] if row else 0
        
    logger.info("=== 거래소 마스터 자산 동기화 완료 ===")
    return sync_results
