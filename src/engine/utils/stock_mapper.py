import json
import os
from src.engine.utils.telemetry import get_logger
from typing import Dict, Optional

logger = get_logger(__name__)

class StockMapper:
    """
    종목 코드와 한글명을 매핑하는 싱글톤 유틸
    """
    _instance = None
    _mapping: Dict[str, str] = {
        # 기본 탑재 하드코딩 매핑 (DB 로드 실패 시 폴백용)
        "005930": "삼성전자",
        "000660": "SK하이닉스",
        "035420": "NAVER",
        "005380": "현대차",
        "035720": "카카오",
        "000270": "기아",
        "005490": "POSCO홀딩스",
        "105560": "KB금융",
        "055550": "신한지주",
        "068270": "셀트리온",
        "BTC": "비트코인",
        "ETH": "이더리움",
        "XRP": "리플",
        "SOL": "솔라나",
        "DOGE": "도지코인",
        "ADA": "에이다",
        "AVAX": "아발란체",
        "DOT": "폴카닷",
        "TRX": "트론",
        "LINK": "체인링크"
    }
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(StockMapper, cls).__new__(cls)
        return cls._instance

    def _load_cache(self):
        """[DEPRECATED] 하위 호환용 빈 래퍼 함수입니다."""
        pass

    def save_cache(self):
        """[DEPRECATED] 하위 호환용 빈 래퍼 함수입니다."""
        pass

    async def load_from_db(self, db_path: Optional[str] = None):
        """
        데이터베이스의 asset_master 테이블의 모든 종목 한글명을 한번에 로드하여
        단일 플랫 메모리 딕셔너리 캐시를 최신화합니다.
        """
        from src.database.connection import get_db_conn
        logger.info("Loading stock master mapping from database...")
        try:
            async with get_db_conn(db_path) as db:
                # asset_master 테이블의 모든 심볼과 한글명 로드
                async with db.execute('SELECT symbol, korean_name FROM asset_master') as cursor:
                    rows = await cursor.fetchall()
                
                # 메모리 캐시 초기화
                self._mapping = {}
                count = 0
                for row in rows:
                    symbol = row['symbol']
                    name = row['korean_name']
                    self._mapping[symbol] = name
                    count += 1
                
                logger.info(f"Loaded {count} symbols from database to StockMapper memory cache.")
        except Exception as e:
            logger.error(f"Failed to load stock master mapping from database: {e}")

    async def add_mapping_async(self, exchange: str, symbol: str, name: str, db_path: Optional[str] = None):
        """
        새로운 종목의 한글명을 asset_master 테이블에만 영속화하고 메모리 캐시를 업데이트합니다.
        exchange_assets(수집 구독 관리)는 건드리지 않아 is_active 오염을 원천 방지합니다.
        """
        if not symbol:
            return
        
        from src.database.connection import get_db_conn
        asset_type = 'crypto' if exchange in ('upbit', 'bithumb') else 'stock'
        name_str = str(name) if name is not None else symbol

        try:
            async with get_db_conn(db_path) as db:
                # asset_master에만 한글명 영속화 (exchange_assets는 수집 구독 관리이므로 건드리지 않음)
                await db.execute('''
                    INSERT OR REPLACE INTO asset_master (symbol, korean_name, asset_type, updated_at)
                    VALUES (?, ?, ?, datetime('now'))
                ''', (symbol, name_str, asset_type))
                await db.commit()
            
            # 단일 메모리 캐시 업데이트
            self._mapping[symbol] = name_str
            logger.debug(f"[StockMapper] Successfully added symbol to DB and memory: {symbol} ({name_str})")
        except Exception as e:
            logger.error(f"[StockMapper] Failed to add mapping dynamically: {exchange}:{symbol} - {e}")

    async def fetch_and_add_kis_symbol(self, symbol: str, db_path: Optional[str] = None) -> str:
        """
        로컬 캐시에 없는 KIS 종목코드가 발견되면 KIS REST API 주식현재가 시세조회를 통해
        한글 종목명을 실시간 질의하여 DB 및 메모리 캐시에 적재합니다.
        """
        if not symbol:
            return ""
            
        # 이미 메모리 캐시에 존재하면 즉시 반환
        if symbol in self._mapping:
            return self._mapping[symbol]

        from src.engine.credentials import CredentialProvider
        from src.config.manager import ConfigManager
        import aiohttp

        # 1. 설정 획득
        config_path = "config/settings.yaml"
        config_manager = ConfigManager(config_path)
        kis_config = config_manager.get('exchanges.kis', {})
        
        app_key = kis_config.get('app_key')
        app_secret = kis_config.get('app_secret')
        api_url = kis_config.get('api_url', 'https://openapi.koreainvestment.com:9443')
        
        # 2. 자격 증명 공급자를 통해 접근 토큰 획득
        cred_provider = CredentialProvider()
        token = await cred_provider.get_kis_access_token()
        if not token:
            logger.error(f"[StockMapper] Failed to get KIS access token for symbol fetch: {symbol}")
            self._mapping[symbol] = symbol
            return symbol

        url = f"{api_url}/uapi/domestic-stock/v1/quotations/inquire-price"
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "authorization": f"Bearer {token}",
            "appkey": str(app_key) if app_key is not None else "",
            "appsecret": str(app_secret) if app_secret is not None else "",
            "tr_id": "FHKST01010100",
            "custtype": "P"
        }
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": symbol
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, params=params) as resp:
                    if resp.status == 200:
                        res_data = await resp.json()
                        if res_data.get('rt_cd') == '0':
                            output1 = res_data.get('output1', {})
                            korean_name = output1.get('hts_kor_isnm', '').strip()
                            if korean_name:
                                logger.info(f"[StockMapper] KIS REST API on-demand fetch success: {symbol} -> {korean_name}")
                                # DB 및 메모리 캐시 적재
                                await self.add_mapping_async('kis', symbol, korean_name, db_path)
                                return korean_name
                        else:
                            logger.error(f"[StockMapper] KIS API Response Error for {symbol}: {res_data.get('msg1')}")
                    else:
                        text = await resp.text()
                        logger.error(f"[StockMapper] KIS API HTTP Error {resp.status} for {symbol}: {text}")
        except Exception as e:
            logger.error(f"[StockMapper] KIS REST API call exception for {symbol}: {e}")
            
        # 조회 실패 시 중복 네트워크 재호출 방지를 위해 메모리 캐시 키에 종목코드로 임시 등록 후 반환
        self._mapping[symbol] = symbol
        return symbol

    async def fetch_and_add_bithumb_symbol(self, symbol: str, db_path: Optional[str] = None) -> str:
        """
        빗썸 단독 상장 등의 이유로 한글명이 없는 경우, 빗썸 공식 V1 API(/market/all)를 통해
        해당 심볼의 한글명을 찾아 DB 및 메모리 캐시에 등록합니다.
        """
        if not symbol:
            return ""
            
        if symbol in self._mapping:
            return self._mapping[symbol]

        # 빗썸 API에서 조회
        import aiohttp
        url = "https://api.bithumb.com/v1/market/all"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        markets = await resp.json()
                        for m in markets:
                            if m['market'] == f"KRW-{symbol}":
                                korean_name = m.get('korean_name', '').strip()
                                if korean_name:
                                    logger.info(f"[StockMapper] Bithumb V1 API on-demand fetch success: {symbol} -> {korean_name}")
                                    await self.add_mapping_async('bithumb', symbol, korean_name, db_path)
                                    return korean_name
        except Exception as e:
            logger.error(f"[StockMapper] Bithumb API fetch exception for {symbol}: {e}")
            
        self._mapping[symbol] = symbol
        return symbol

    def get_name(self, exchange: str, symbol: str) -> str:
        """거래소와 심볼을 받아 한글명을 반환합니다. 단일 캐시에서 조회합니다."""
        name = self._mapping.get(symbol)
        if name:
            return name
        return symbol

    def add_mapping(self, exchange: str, symbol: str, name: str):
        """[DEPRECATED] 동기식 매핑 추가는 비동기 add_mapping_async()를 권장합니다."""
        if not symbol:
            return
        self._mapping[symbol] = str(name) if name is not None else ""

# 전역 인스턴스
stock_mapper = StockMapper()
