from typing import List
import aiohttp
from src.engine.market.base import MarketAdapter
from src.engine.market.dto import MarketTickerDTO
from src.engine.utils.stock_mapper import stock_mapper

class UpbitMarketAdapter(MarketAdapter):
    """
    업비트 거래소 시세 조회 전용 어댑터
    """
    async def fetch_market_data(self, session: aiohttp.ClientSession, system, mode: str = "serial") -> List[MarketTickerDTO]:
        url_all = "https://api.upbit.com/v1/market/all?is_details=true"
        async with session.get(url_all) as resp:
            if resp.status != 200:
                raise Exception(f"Failed to fetch Upbit markets list: status {resp.status}")
            all_markets = await resp.json()
            
        krw_markets = [m for m in all_markets if m['market'].startswith('KRW-')]
        market_codes = [m['market'] for m in krw_markets]
        market_map = {m['market']: m['korean_name'] for m in krw_markets}
        
        caution_map = {}
        alert_map = {}
        reasons_map = {}
        for m in krw_markets:
            market_event = m.get('market_event', {})
            is_caution = False
            is_alert = False
            reasons = []
            
            if market_event:
                if market_event.get('warning', False):
                    is_caution = True
                    reasons.append("투자유의")
                
                caution_obj = market_event.get('caution', {})
                if caution_obj:
                    if caution_obj.get('PRICE_FLUCTUATIONS'):
                        reasons.append("가격 급등락")
                    if caution_obj.get('TRADING_VOLUME_SOARING'):
                        reasons.append("거래량 급등")
                    if caution_obj.get('DEPOSIT_AMOUNT_SOARING'):
                        reasons.append("입금량 급등")
                    if caution_obj.get('GLOBAL_PRICE_DIFFERENCES'):
                        reasons.append("글로벌 시세 차이")
                    if caution_obj.get('CONCENTRATION_OF_SMALL_ACCOUNTS'):
                        reasons.append("소수계정 거래 집중")
                    
                    if any(caution_obj.values()):
                        is_alert = True
            
            caution_map[m['market']] = is_caution
            alert_map[m['market']] = is_alert
            reasons_map[m['market']] = reasons

        # 전종목을 한 번에 단일 호출
        tickers = []
        batch = ','.join(market_codes)
        url_ticker = f"https://api.upbit.com/v1/ticker?markets={batch}"
        async with session.get(url_ticker) as resp:
            if resp.status == 200:
                tickers = await resp.json()

        dto_list = []
        active_symbols = stock_mapper.get_active_symbols('upbit')
        for t in tickers:
            m_code = t['market'].replace('KRW-', '')
            if m_code in active_symbols:
                korean_name = market_map.get(t['market'], t['market'])
                dto_list.append(MarketTickerDTO(
                    exchange="upbit",
                    market=m_code,
                    korean_name=korean_name,
                    trade_price=float(t.get('trade_price') or 0.0),
                    signed_change_rate=float(t.get('signed_change_rate') or 0.0),
                    change_price=float(t.get('signed_change_price') or t.get('change_price') or 0.0),
                    acc_trade_price_24h=float(t.get('acc_trade_price_24h') or 0.0),
                    high_price=float(t.get('high_price') or 0.0),
                    low_price=float(t.get('low_price') or 0.0),
                    is_caution=caution_map.get(t['market'], False),
                    is_alert=alert_map.get(t['market'], False),
                    caution_reasons=reasons_map.get(t['market'], [])
                ))
        return dto_list
