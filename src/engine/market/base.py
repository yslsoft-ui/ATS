from abc import ABC, abstractmethod
from typing import List
import aiohttp
from src.engine.market.dto import MarketTickerDTO

class MarketAdapter(ABC):
    """
    각 거래소별 시세 수집을 담당하는 어댑터 인터페이스 (Seam)
    """
    @abstractmethod
    async def fetch_market_data(self, session: aiohttp.ClientSession, system, mode: str = "parallel") -> List[MarketTickerDTO]:
        """
        해당 거래소의 REST API 시세를 조회하여 공통 규격인 MarketTickerDTO 리스트로 정제 후 반환합니다.
        """
        pass

