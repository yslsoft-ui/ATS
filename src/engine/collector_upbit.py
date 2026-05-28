import asyncio
import json
import aiohttp
from src.engine.utils.telemetry import get_logger
from typing import List, Dict, Optional, Any
from src.engine.collector_base import BaseCollector, CollectorRegistry
from src.engine.candles import Candle

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
            # 1. DB에서 활성 종목 조회
            symbols = await self._fetch_active_symbols_from_db(config)
            
            # 2. DB에 활성 종목이 전혀 없을 경우 API를 통해 전체 KRW 마켓 종목 자동 로드 (Fallback)
            if not symbols:
                logger.warning(f"[{self.exchange.upper()}] DB에 활성화된 종목이 없습니다. API에서 전체 KRW 종목 로드를 시도합니다.")
                if not self.session or self.session.closed:
                    self.session = aiohttp.ClientSession()
                
                async with self.session.get("https://api.upbit.com/v1/market/all") as resp:
                    markets = await resp.json()
                    symbols = sorted([m['market'].replace('KRW-', '') for m in markets if m['market'].startswith('KRW-')])
                    
                if not symbols:
                    symbols = ["BTC", "ETH", "XRP"]
                    logger.info(f"[{self.exchange.upper()}] 최종 Fallback 기본 종목 적용: {symbols}")
            return symbols
        except Exception as e:
            logger.error(f"[{self.exchange.upper()}] 종목 조회 치명적 실패: {e}")
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

    async def _fetch_historical_candles(self, symbol: str, start_time: int, end_time: int) -> List[Candle]:
        """업비트 REST API를 사용하여 지정 구간 내의 1분봉 데이터를 조회합니다."""
        url = "https://api.upbit.com/v1/candles/minutes/1"
        market = f"KRW-{symbol}"
        
        from datetime import datetime, timezone
        
        # settings.yaml 설정의 delays.upbit 값을 안전하게 읽어옴 (하드코딩 제거)
        bf_config = self.config.get('collector', {}).get('backfill', {}) if hasattr(self, 'config') and self.config else {}
        delays = bf_config.get('delays', {})
        delay = delays.get('upbit', 0.2)
        
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
