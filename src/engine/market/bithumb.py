import time
from typing import List
import aiohttp
from src.engine.market.base import MarketAdapter
from src.engine.market.dto import MarketTickerDTO
from src.engine.utils.stock_mapper import stock_mapper
from src.engine.utils.telemetry import get_logger

logger = get_logger(__name__)

class BithumbMarketAdapter(MarketAdapter):
    """
    빗썸 거래소 시세 조회 전용 어댑터
    """
    async def fetch_market_data(self, session: aiohttp.ClientSession, system, mode: str = "serial") -> List[MarketTickerDTO]:
        bithumb_config = system.config_manager.get('exchanges.bithumb', {})
        bithumb_api_url = bithumb_config.get('api_url', 'https://api.bithumb.com/v1')
        
        # 1. 빗썸 실시간 마켓 전체 목록 수집 (유의 종목 파악을 위해 isDetails=true 추가)
        url_all = f"{bithumb_api_url}/market/all?isDetails=true"
        async with session.get(url_all) as resp:
            if resp.status != 200:
                raise Exception(f"Failed to fetch Bithumb markets list: status {resp.status}")
            all_markets = await resp.json()
            
        krw_markets = [m for m in all_markets if m['market'].startswith('KRW-')]
        market_codes = [m['market'] for m in krw_markets]
        market_map = {m['market']: m['korean_name'] for m in krw_markets}
        
        # 유의종목 맵
        caution_map = {m['market']: (m.get('market_warning') == 'CAUTION') for m in krw_markets}

        # 1.5. 빗썸 주의 종목 / 경보제 상태 조회
        alert_markets = set()
        bithumb_warnings = {}
        warning_type_map = {
            "PRICE_SUDDEN_FLUCTUATION": "가격 급등락",
            "PRICE_DIFFERENCE_HIGH": "글로벌 시세 차이",
            "SPECIFIC_ACCOUNT_HIGH_TRANSACTION": "소수계정 거래 집중",
            "TRADING_VOLUME_SUDDEN_FLUCTUATION": "거래량 급등",
            "DEPOSIT_AMOUNT_SUDDEN_FLUCTUATION": "입금량 급등"
        }
        try:
            url_warning = f"{bithumb_api_url}/market/virtual_asset_warning"
            async with session.get(url_warning) as w_resp:
                if w_resp.status == 200:
                    warnings = await w_resp.json()
                    for item in warnings:
                        if isinstance(item, dict) and 'market' in item:
                            m_code = item['market']
                            alert_markets.add(m_code)
                            w_type = item.get('warning_type')
                            if w_type:
                                bithumb_warnings.setdefault(m_code, []).append(warning_type_map.get(w_type, "주의"))
        except Exception as e:
            logger.error(f"Failed to fetch Bithumb virtual asset warnings: {e}")

        # 전종목을 한 번에 단일 호출
        bithumb_tickers = []
        try:
            batch = ",".join(market_codes)
            async with session.get(f"{bithumb_api_url}/ticker?markets={batch}") as resp:
                if resp.status == 200:
                    bithumb_tickers = await resp.json()
        except Exception as e:
            logger.error(f"Failed to fetch Bithumb tickers: {e}")

        ticker_map = {t['market'].replace('KRW-', ''): t for t in bithumb_tickers if 'market' in t}

        dto_list = []
        active_symbols = stock_mapper.get_active_symbols('bithumb')
        for code in market_map.keys():
            s_code = code.replace('KRW-', '')
            if s_code in active_symbols:
                t = ticker_map.get(s_code, {})
                if t:
                    key = f"bithumb:{s_code}"
                    prev = system.latest_prices.get(key, {})
                    system.latest_prices[key] = {
                        'exchange': 'bithumb',
                        'market': s_code,
                        'trade_price': float(t.get('trade_price') if t.get('trade_price') is not None else prev.get('trade_price', 0.0)),
                        'signed_change_rate': float(t.get('signed_change_rate') if t.get('signed_change_rate') is not None else prev.get('signed_change_rate', 0.0)),
                        'change_price': float(t.get('signed_change_price') or t.get('change_price') or 0.0) if (t.get('signed_change_price') is not None or t.get('change_price') is not None) else prev.get('change_price', 0.0),
                        'timestamp': int(t.get('timestamp') if t.get('timestamp') is not None else prev.get('timestamp', time.time() * 1000)),
                        'high_price': float(t.get('high_price') if t.get('high_price') is not None else prev.get('high_price', 0.0)),
                        'low_price': float(t.get('low_price') if t.get('low_price') is not None else prev.get('low_price', 0.0)),
                        'acc_trade_price_24h': float(t.get('acc_trade_price_24h') if t.get('acc_trade_price_24h') is not None else prev.get('acc_trade_price_24h', 0.0))
                    }

                latest = system.get_latest_price('bithumb', s_code)
                korean_name = market_map.get(code, s_code)
                
                # stock_mapper에 이미 캐시된 한글명과 다르면 DB 영속화 (가드 조건)
                if stock_mapper.get_name('bithumb', s_code) != korean_name:
                    await stock_mapper.add_mapping_async('bithumb', s_code, korean_name, system.db_path)
                
                reasons = []
                is_caution = caution_map.get(code, False)
                if is_caution:
                    reasons.append("투자유의")
                if code in bithumb_warnings:
                    reasons.extend(bithumb_warnings[code])

                dto_list.append(MarketTickerDTO(
                    exchange="bithumb",
                    market=s_code,
                    korean_name=korean_name,
                    trade_price=latest.get('trade_price', 0.0),
                    signed_change_rate=latest.get('signed_change_rate', 0.0),
                    change_price=latest.get('change_price', 0.0),
                    acc_trade_price_24h=latest.get('acc_trade_price_24h', 0.0),
                    high_price=latest.get('high_price', 0.0),
                    low_price=latest.get('low_price', 0.0),
                    is_caution=is_caution,
                    is_alert=(code in alert_markets),
                    caution_reasons=reasons
                ))
        return dto_list
