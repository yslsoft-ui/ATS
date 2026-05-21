import asyncio
import json
import aiohttp
import time
from src.engine.utils.telemetry import get_logger
from typing import List, Dict, Optional, Any
from src.engine.collector_base import BaseCollector, CollectorRegistry

logger = get_logger(__name__)

@CollectorRegistry.register('bithumb')
class BithumbCollector(BaseCollector):
    """
    빗썸 API로부터 실시간 체결 데이터를 수집하고 분석 엔진으로 배분합니다.
    """
    @property
    def exchange(self) -> str:
        return 'bithumb'

    async def _fetch_symbols(self, config: Dict[str, Any]) -> List[str]:
        try:
            bithumb_config = config.get('exchanges', {}).get('bithumb', {}) if config else {}
            api_url = bithumb_config.get('api_url', 'https://api.bithumb.com/v1')
            configured_symbols = bithumb_config.get('symbols', [])
            
            # --- 1. [NEW] 빗썸 공식 V1 API를 통해 한글명 동적 캐싱 로드 ---
            try:
                async with self.session.get(f"{api_url}/market/all") as resp:
                    v1_markets = await resp.json()
                    from src.engine.utils.stock_mapper import stock_mapper
                    for m in v1_markets:
                        if m['market'].startswith('KRW-'):
                            m_code = m['market'].replace('KRW-', '')
                            stock_mapper.add_mapping('bithumb', m_code, m['korean_name'])
                logger.info(f"[{self.exchange.upper()}] 공식 V1 API로부터 한글 코인명 매핑 로드 완료")
            except Exception as ex:
                logger.error(f"[{self.exchange.upper()}] 공식 V1 한글명 로드 실패 (Fallback 우회 작동): {ex}")
            
            # --- 2. 설정에 지정된 심볼 목록 로드 및 검증 ---
            if configured_symbols:
                logger.info(f"[{self.exchange.upper()}] 설정된 {len(configured_symbols)}개 종목 로드 완료: {configured_symbols}")
                # 초기 시세 정보 주입을 시도 (신형 V1 Ticker API 사용)
                try:
                    markets_query = ",".join([f"KRW-{s}" for s in configured_symbols])
                    async with self.session.get(f"{api_url}/ticker?markets={markets_query}") as resp:
                        tickers = await resp.json()
                        if isinstance(tickers, list) and self.on_data_callback:
                            for t in tickers:
                                symbol = t.get('market', '').replace('KRW-', '')
                                try:
                                    initial_data = {
                                        'type': 'tick',
                                        'exchange': 'bithumb',
                                        'code': symbol,
                                        'trade_price': float(t.get('trade_price', 0)),
                                        'signed_change_rate': float(t.get('signed_change_rate', 0)),
                                        'acc_trade_price_24h': float(t.get('acc_trade_price_24h', 0)),
                                        'high_price': float(t.get('high_price', 0)),
                                        'low_price': float(t.get('low_price', 0)),
                                        'trade_timestamp': int(t.get('timestamp', time.time() * 1000))
                                    }
                                    await self.on_data_callback(initial_data)
                                except Exception as e:
                                    logger.error(f"Error parsing ticker: {e}")
                                    continue
                except Exception as ex:
                    logger.warning(f"[{self.exchange.upper()}] 신형 V1 Ticker 로드 실패: {ex}")
                return configured_symbols
 
            # 설정된 심볼이 없을 경우, 458개 전체 종목 자동 로드 (Fallback - 신형 V1 API)
            all_krw_markets = []
            try:
                from src.engine.utils.stock_mapper import stock_mapper
                all_krw_markets = [f"KRW-{k}" for k in stock_mapper._mapping.get('bithumb', {}).keys()]
            except:
                pass
                
            if not all_krw_markets:
                return config.get('exchanges', {}).get('bithumb', {}).get('symbols', ["BTC", "ETH"])
                
            symbols = sorted([m.replace('KRW-', '') for m in all_krw_markets])
            logger.info(f"[{self.exchange.upper()}] {len(symbols)}개 전체 종목 신형 V1 로드 완료")
            
            # 100개씩 청크 단위로 나누어 병렬/순차 요청하여 초기 시세 캐시 채우기
            if self.on_data_callback:
                for i in range(0, len(all_krw_markets), 100):
                    batch = all_krw_markets[i:i+100]
                    markets_query = ",".join(batch)
                    try:
                        async with self.session.get(f"{api_url}/ticker?markets={markets_query}") as resp:
                            tickers = await resp.json()
                            if isinstance(tickers, list):
                                for t in tickers:
                                    symbol = t.get('market', '').replace('KRW-', '')
                                    try:
                                        initial_data = {
                                            'type': 'tick',
                                            'exchange': 'bithumb',
                                            'code': symbol,
                                            'trade_price': float(t.get('trade_price', 0)),
                                            'signed_change_rate': float(t.get('signed_change_rate', 0)),
                                            'acc_trade_price_24h': float(t.get('acc_trade_price_24h', 0)),
                                            'high_price': float(t.get('high_price', 0)),
                                            'low_price': float(t.get('low_price', 0)),
                                            'trade_timestamp': int(t.get('timestamp', time.time() * 1000))
                                        }
                                        await self.on_data_callback(initial_data)
                                    except:
                                        continue
                    except Exception as ex:
                        logger.warning(f"[{self.exchange.upper()}] 전체 종목 초기 Ticker 배치 로드 실패: {ex}")
            return symbols
        except Exception as e:
            logger.error(f"[{self.exchange.upper()}] 종목 조회 실패: {e}")
            return config.get('exchanges', {}).get('bithumb', {}).get('symbols', ["BTC", "ETH"])

    def _get_websocket_url(self, config: Dict[str, Any]) -> str:
        return config.get('exchanges', {}).get('bithumb', {}).get('websocket_url', "wss://ws-api.bithumb.com/websocket/v1")

    async def _subscribe(self, ws, config: Dict[str, Any]):
        subscribe_symbols = [f"KRW-{s}" for s in self.available_symbols]
        subscribe_data = [
            {"ticket": "collector"},
            {"type": "trade", "codes": subscribe_symbols},
            {"format": "DEFAULT"}
        ]
        await ws.send_json(subscribe_data)

    def _parse_message(self, msg) -> Optional[Dict]:
        raw_str = None
        if msg.type == aiohttp.WSMsgType.TEXT:
            raw_str = msg.data
        elif msg.type == aiohttp.WSMsgType.BINARY:
            raw_str = msg.data.decode('utf-8')
            
        if raw_str:
            try:
                data = json.loads(raw_str)
                # 빗썸 공식 신규 V1 체결(trade) 데이터 파싱
                if data.get('type') == 'trade':
                    code_raw = data.get('code', '')
                    symbol = code_raw.replace('KRW-', '')
                    
                    trade_price = float(data.get('trade_price', 0))
                    prev_close = float(data.get('prev_closing_price', 0))
                    
                    # 실시간 전일 대비 변동률 동적 연산
                    signed_change_rate = 0.0
                    if prev_close > 0:
                        signed_change_rate = (trade_price - prev_close) / prev_close
                    
                    tick_data = {
                        'type': 'tick',
                        'exchange': 'bithumb',
                        'code': symbol,
                        'trade_price': trade_price,
                        'trade_volume': float(data.get('trade_volume', 0)),
                        'ask_bid': data.get('ask_bid', 'BID'),
                        'trade_timestamp': int(data.get('trade_timestamp', time.time() * 1000)),
                        'change': data.get('change', 'EVEN'),
                        'signed_change_rate': signed_change_rate
                    }
                    return tick_data
            except Exception as e:
                logger.error(f"[{self.exchange.upper()}] Msg Parse Error: {e}")
        return None
