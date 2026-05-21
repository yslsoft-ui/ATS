import asyncio
import json
import aiohttp
from src.engine.utils.telemetry import get_logger
from typing import List, Dict, Optional, Any
from src.engine.collector_base import BaseCollector, CollectorRegistry

logger = get_logger(__name__)

@CollectorRegistry.register('upbit')
class UpbitCollector(BaseCollector):
    """
    업비트 API로부터 실시간 체결 데이터를 수집하고 분석 엔진으로 배분합니다.
    """
    @property
    def exchange(self) -> str:
        return 'upbit'

    async def _fetch_symbols(self, config: Dict[str, Any]) -> List[str]:
        try:
            if not self.session or self.session.closed:
                self.session = aiohttp.ClientSession()
            
            async with self.session.get("https://api.upbit.com/v1/market/all") as resp:
                markets = await resp.json()
                symbols = sorted([m['market'].replace('KRW-', '') for m in markets if m['market'].startswith('KRW-')])
                logger.info(f"[{self.exchange.upper()}] {len(symbols)}개 종목 로드 완료")
                return symbols
        except Exception as e:
            logger.error(f"[{self.exchange.upper()}] 종목 조회 실패: {e}")
            return ["BTC", "ETH", "XRP"]

    def _get_websocket_url(self, config: Dict[str, Any]) -> str:
        return config.get('exchanges', {}).get('upbit', {}).get('websocket_url', "wss://api.upbit.com/websocket/v1")

    async def _subscribe(self, ws, config: Dict[str, Any]):
        subscribe_codes = [f"KRW-{s}" for s in self.available_symbols]
        subscribe_data = [{"ticket": "collector"}, {"type": "trade", "codes": subscribe_codes}]
        await ws.send_json(subscribe_data)

    def _parse_message(self, msg) -> Optional[Dict]:
        if msg.type == aiohttp.WSMsgType.BINARY:
            try:
                data = json.loads(msg.data.decode('utf-8'))
                data['exchange'] = 'upbit'
                data['code'] = data['code'].replace('KRW-', '')
                return data
            except Exception as e:
                logger.error(f"[{self.exchange.upper()}] Msg Parse Error: {e}")
        return None
