import json
import os
from src.engine.utils.telemetry import get_logger
from typing import Dict, Optional

logger = get_logger(__name__)

class StockMapper:
    """
    종목 코드와 한글명을 매핑하는 싱글톤 유틸리티입니다.
    """
    _instance = None
    _mapping: Dict[str, Dict[str, str]] = {
        "upbit": {},
        "kis": {
            "005930": "삼성전자",
            "000660": "SK하이닉스",
            "035420": "NAVER",
            "005380": "현대차",
            "035720": "카카오",
            "000270": "기아",
            "005490": "POSCO홀딩스",
            "105560": "KB금융",
            "055550": "신한지주",
            "068270": "셀트리온"
        },
        "bithumb": {
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
    }
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(StockMapper, cls).__new__(cls)
            cls._instance._load_cache()
        return cls._instance

    def _load_cache(self):
        cache_path = 'data/stock_master.json'
        if os.path.exists(cache_path):
            try:
                with open(cache_path, 'r', encoding='utf-8') as f:
                    cached_data = json.load(f)
                    # 기존 하드코딩 데이터와 병합
                    for exch, symbols in cached_data.items():
                        if exch not in self._mapping:
                            self._mapping[exch] = {}
                        self._mapping[exch].update(symbols)
                logger.info(f"Loaded {sum(len(v) for v in self._mapping.values())} symbols from cache.")
            except Exception as e:
                logger.error(f"Failed to load stock master cache: {e}")

    def save_cache(self):
        os.makedirs('data', exist_ok=True)
        try:
            with open('data/stock_master.json', 'w', encoding='utf-8') as f:
                json.dump(self._mapping, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Failed to save stock master cache: {e}")

    def get_name(self, exchange: str, symbol: str) -> str:
        """거래소와 심볼을 받아 한글명을 반환합니다."""
        name = self._mapping.get(exchange, {}).get(symbol)
        if name:
            return name
            
        # 빗썸인 경우, 동일 심볼을 가진 업비트 한글명이 있다면 이를 스마트하게 연동 [NEW]
        if exchange == 'bithumb':
            upbit_name = self._mapping.get('upbit', {}).get(symbol)
            if upbit_name:
                self._mapping['bithumb'][symbol] = upbit_name
                return upbit_name
                
        return symbol

    def add_mapping(self, exchange: str, symbol: str, name: str):
        """새로운 매핑을 추가합니다."""
        if not exchange or not symbol:
            return
        if exchange not in self._mapping:
            self._mapping[exchange] = {}
        self._mapping[exchange][symbol] = str(name) if name is not None else ""

# 전역 인스턴스
stock_mapper = StockMapper()
