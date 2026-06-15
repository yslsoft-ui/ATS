import time
from typing import List
import aiohttp
from src.engine.market.base import MarketAdapter
from src.engine.market.dto import MarketTickerDTO
from src.engine.utils.stock_mapper import stock_mapper
from src.engine.utils.telemetry import get_logger
from src.database.repository import SqliteMarketDataRepository

logger = get_logger(__name__)

class KisMarketAdapter(MarketAdapter):
    """
    국내 주식(KIS) 시세 조회 전용 어댑터
    """
    def __init__(self):
        self._db_repo = SqliteMarketDataRepository()

    async def fetch_market_data(self, session: aiohttp.ClientSession, system, mode: str = "parallel") -> List[MarketTickerDTO]:
        kis_symbols = set()
        kis_config_symbols = system.config_manager.get('exchanges.kis.symbols', [])
        if kis_config_symbols:
            kis_symbols.update(kis_config_symbols)
        kis_active_symbols = stock_mapper.get_active_symbols('kis')
        if kis_active_symbols:
            kis_symbols.update(kis_active_symbols)

        # 가격 캐시(latest_prices)에 존재하더라도, 실제로 활성화되었거나 설정된 종목만 노출
        for key in system.latest_prices.keys():
            if key.startswith('kis:'):
                s_code = key.split(':')[1]
                if s_code in kis_active_symbols or s_code in kis_config_symbols:
                    kis_symbols.add(s_code)

        if mode == "serial":
            # 순수 동기식(순차) 루프 DB 웜업으로 대기 시간 체감
            for s_code in kis_symbols:
                latest = system.get_latest_price('kis', s_code)
                if not latest or latest.get('trade_price', 0.0) == 0.0:
                    db_res = await self._db_repo.warm_up_kis_cache(s_code)
                    if db_res:
                        key = f"kis:{s_code}"
                        system.latest_prices[key] = {
                            'exchange': 'kis',
                            'market': s_code,
                            'trade_price': db_res.get('trade_price', 0.0),
                            'signed_change_rate': db_res.get('signed_change_rate', 0.0),
                            'timestamp': int(time.time() * 1000),
                            'high_price': db_res.get('high_price', 0.0),
                            'low_price': db_res.get('low_price', 0.0),
                            'acc_trade_price_24h': db_res.get('acc_trade_price_24h', 0.0)
                        }
        else:
            # 비동기 병렬 웜업
            import asyncio
            warmup_symbols = []
            warmup_tasks = []
            
            for s_code in kis_symbols:
                latest = system.get_latest_price('kis', s_code)
                if not latest or latest.get('trade_price', 0.0) == 0.0:
                    warmup_symbols.append(s_code)
                    warmup_tasks.append(self._db_repo.warm_up_kis_cache(s_code))
                    
            if warmup_tasks:
                warmup_results = await asyncio.gather(*warmup_tasks, return_exceptions=True)
                for s_code, db_res in zip(warmup_symbols, warmup_results):
                    if db_res and not isinstance(db_res, Exception):
                        key = f"kis:{s_code}"
                        system.latest_prices[key] = {
                            'exchange': 'kis',
                            'market': s_code,
                            'trade_price': db_res.get('trade_price', 0.0),
                            'signed_change_rate': db_res.get('signed_change_rate', 0.0),
                            'timestamp': int(time.time() * 1000),
                            'high_price': db_res.get('high_price', 0.0),
                            'low_price': db_res.get('low_price', 0.0),
                            'acc_trade_price_24h': db_res.get('acc_trade_price_24h', 0.0)
                        }


        dto_list = []
        for s_code in kis_symbols:
            latest = system.get_latest_price('kis', s_code)
            
            # 한글명: 이미 메모리에 존재하므로 동기식 조회를 1순위로 처리하여 비동기 오버헤드(0ms) 제거
            korean_name = stock_mapper.get_name('kis', s_code)
            if korean_name == s_code:
                korean_name = await stock_mapper.fetch_and_add_kis_symbol(s_code, system.db_path)

            
            trade_price = float(latest.get('trade_price') or 0.0) if latest else 0.0
            signed_change_rate = float(latest.get('signed_change_rate') or 0.0) if latest else 0.0
            change_price = float(latest.get('change_price') or 0.0) if latest else 0.0
            acc_trade_price_24h = float(latest.get('acc_trade_price_24h') or 0.0) if latest else 0.0
            high_price = float(latest.get('high_price') or 0.0) if latest else 0.0
            low_price = float(latest.get('low_price') or 0.0) if latest else 0.0

            dto_list.append(MarketTickerDTO(
                exchange="kis",
                market=s_code,
                korean_name=korean_name,
                trade_price=trade_price,
                signed_change_rate=signed_change_rate,
                change_price=change_price,
                acc_trade_price_24h=acc_trade_price_24h,
                high_price=high_price,
                low_price=low_price,
                is_collected=True
            ))
        return dto_list

