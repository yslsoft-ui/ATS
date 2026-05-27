import asyncio
import json
import aiohttp
import time
from src.engine.utils.telemetry import get_logger
from src.engine.utils.stock_mapper import stock_mapper
from typing import List, Dict, Optional, Any
from src.engine.collector_base import BaseCollector, CollectorRegistry
from src.engine.candles import Candle

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
            
            if not self.session or self.session.closed:
                self.session = aiohttp.ClientSession()
            
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

    async def _fetch_historical_candles(self, symbol: str, start_time: int, end_time: int) -> List[Candle]:
        """빗썸 REST API를 사용하여 지정 구간 내의 1분봉 데이터를 조회합니다."""
        bithumb_config = self.config.get('exchanges', {}).get('bithumb', {}) if hasattr(self, 'config') and self.config else {}
        api_url = bithumb_config.get('api_url', 'https://api.bithumb.com/v1')
        url = f"{api_url}/candles/minutes/1"
        market = f"KRW-{symbol}"
        
        from datetime import datetime, timezone
        
        # settings.yaml 설정의 delays.bithumb 값을 안전하게 읽어옴 (하드코딩 제거)
        bf_config = self.config.get('collector', {}).get('backfill', {}) if hasattr(self, 'config') and self.config else {}
        delays = bf_config.get('delays', {})
        delay = delays.get('bithumb', 0.2)
        
        candles: List[Candle] = []
        to_time = end_time

        if not self.session or self.session.closed:
            self.session = aiohttp.ClientSession()

        # 누락 시간 범위가 유효할 때까지 루프
        while to_time >= start_time:
            # ISO 8601 포맷 (UTC)
            to_str = datetime.fromtimestamp(to_time, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
            params = {
                "market": market,
                "to": to_str,
                "count": 200
            }

            try:
                async with self.session.get(url, params=params) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        logger.error(f"[{self.exchange.upper()}] {symbol} 과거 캔들 조회 실패 (HTTP {resp.status}): {body}")
                        break
                    
                    data = await resp.json()
                    if not data or not isinstance(data, list):
                        if isinstance(data, dict) and "error" in data:
                            logger.error(f"[{self.exchange.upper()}] {symbol} 과거 캔들 API 오류: {data['error']}")
                        elif isinstance(data, dict) and "message" in data:
                            logger.error(f"[{self.exchange.upper()}] {symbol} 과거 캔들 API 오류 메시지: {data['message']}")
                        else:
                            logger.error(f"[{self.exchange.upper()}] {symbol} 과거 캔들 API 알 수 없는 응답 포맷: {data}")
                        break
                    
                    batch_candles = []
                    min_ts = to_time

                    for item in data:
                        # candle_date_time_utc 파싱하여 시작 타임스탬프(초) 계산
                        dt_str = item.get('candle_date_time_utc')
                        dt = datetime.strptime(dt_str, '%Y-%m-%dT%H:%M:%S')
                        ts = int(dt.replace(tzinfo=timezone.utc).timestamp())
                        
                        min_ts = min(min_ts, ts)
                        
                        # 요청한 시작 시간보다 이전 캔들이 유입된 경우 수집 중단 대상
                        if ts < start_time:
                            continue
                            
                        candle = Candle(
                            exchange=self.exchange,
                            symbol=symbol,
                            interval=60,
                            timestamp=ts,
                            open=float(item['opening_price']),
                            high=float(item['high_price']),
                            low=float(item['low_price']),
                            close=float(item['trade_price']),
                            volume=float(item['candle_acc_trade_volume']),
                            is_closed=True
                        )
                        batch_candles.append(candle)
                    
                    candles.extend(batch_candles)
                    
                    # 더 이상 오래된 데이터가 유입되지 않거나 count보다 적게 받았다면 루프 종료
                    if len(data) < 200 or min_ts >= to_time:
                        break
                        
                    # 다음 페이지네이션을 위해 to_time을 수집된 가장 오래된 캔들 시각의 1초 전으로 설정
                    to_time = min_ts - 60
                    
            except Exception as e:
                logger.error(f"[{self.exchange.upper()}] {symbol} 과거 캔들 API 호출 예외: {e}")
                break

            # 페이지네이션 간 안전 딜레이 적용 (설정 파일의 딜레이 연동)
            await asyncio.sleep(delay)
            
        return candles
