from typing import List
import aiohttp
from src.engine.market.base import MarketAdapter
from src.engine.market.dto import MarketTickerDTO

class UpbitMarketAdapter(MarketAdapter):
    """
    업비트 거래소 시세 조회 전용 어댑터
    """
    async def fetch_market_data(self, session: aiohttp.ClientSession, system, mode: str = "serial") -> List[MarketTickerDTO]:
        url_all = "https://api.upbit.com/v1/market/all?is_details=false"
        async with session.get(url_all) as resp:
            if resp.status != 200:
                raise Exception(f"Failed to fetch Upbit markets list: status {resp.status}")
            all_markets = await resp.json()
            
        krw_markets = [m for m in all_markets if m['market'].startswith('KRW-')]
        market_codes = [m['market'] for m in krw_markets]
        market_map = {m['market']: m['korean_name'] for m in krw_markets}

        # 전종목을 한 번에 단일 호출
        tickers = []
        batch = ','.join(market_codes)
        url_ticker = f"https://api.upbit.com/v1/ticker?markets={batch}"
        async with session.get(url_ticker) as resp:
            if resp.status == 200:
                tickers = await resp.json()

        dto_list = []
        for t in tickers:
            m_code = t['market'].replace('KRW-', '')
            korean_name = market_map.get(t['market'], t['market'])
            dto_list.append(MarketTickerDTO(
                exchange="upbit",
                market=m_code,
                korean_name=korean_name,
                trade_price=float(t.get('trade_price') or 0.0),
                signed_change_rate=float(t.get('signed_change_rate') or 0.0),
                acc_trade_price_24h=float(t.get('acc_trade_price_24h') or 0.0),
                high_price=float(t.get('high_price') or 0.0),
                low_price=float(t.get('low_price') or 0.0)
            ))
        return dto_list
