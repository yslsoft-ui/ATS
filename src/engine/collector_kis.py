import asyncio
import json
import aiohttp
import time
from typing import List, Dict, Optional, Any
from src.engine.utils.telemetry import get_logger
from src.engine.collector_base import BaseCollector, CollectorRegistry
from src.engine.credentials import CredentialProvider
from src.engine.utils.stock_mapper import stock_mapper
from src.engine.utils.market_hours import MarketHours
from src.engine.candles import Candle

logger = get_logger(__name__)

@CollectorRegistry.register('kis')
class KisCollector(BaseCollector):
    """
    한국투자증권(KIS) API로부터 국내 주식 실시간 체결 데이터를 수집합니다.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cred_provider = CredentialProvider()
        self.rank_task: Optional[asyncio.Task] = None

    @property
    def exchange(self) -> str:
        return 'kis'

    async def _fetch_symbols(self, config: Dict[str, Any]) -> List[str]:
        kis_symbols = config.get('exchanges', {}).get('kis', {}).get('symbols', [])
        if not kis_symbols:
            kis_symbols = list(stock_mapper._mapping.get('kis', {}).keys())
        return kis_symbols

    def _get_websocket_url(self, config: Dict[str, Any]) -> str:
        kis_config = config.get('exchanges', {}).get('kis', {})
        return kis_config.get('websocket_url', "ws://ops.koreainvestment.com:21000")

    async def _subscribe(self, ws, config: Dict[str, Any]):
        approval_key = await self.cred_provider.get_kis_approval_key()
        for symbol_code in self.available_symbols:
            subscribe_msg = {
                "header": {
                    "approval_key": approval_key,
                    "custtype": "P",
                    "tr_type": "1",
                    "content-type": "utf-8"
                },
                "body": {
                    "input": {
                        "tr_id": "H0STCNT0",
                        "tr_key": symbol_code
                    }
                }
            }
            await ws.send_json(subscribe_msg)
            await asyncio.sleep(0.1)

    def _parse_message(self, msg) -> Optional[Dict]:
        if msg.type == aiohttp.WSMsgType.TEXT:
            raw_data = msg.data
            if raw_data.startswith('0') or raw_data.startswith('1'):
                parts = raw_data.split('|')
                if len(parts) < 4: return None

                data_parts = parts[3].split('^')
                symbol_code = data_parts[0]
                price = float(data_parts[2])
                
                # KIS 국내주식 실시간 체결(H0STCNT0) 데이터 인덱스 명세 교정:
                # 8: 고가, 9: 저가, 12: 체결거래량(기존 7번 시가 맵핑 오류 수정), 14: 누적거래대금(원단위, 기존 22번 HTS 단축대금 오류 수정)
                volume = float(data_parts[12]) if len(data_parts) > 12 and data_parts[12] else 0.0
                high = float(data_parts[8]) if len(data_parts) > 8 and data_parts[8] else 0.0
                low = float(data_parts[9]) if len(data_parts) > 9 and data_parts[9] else 0.0
                acc_price = float(data_parts[14]) if len(data_parts) > 14 and data_parts[14] else 0.0
                
                tick_data = {
                    'type': 'tick',
                    'exchange': 'kis',
                    'code': symbol_code,
                    'trade_price': price,
                    'trade_volume': volume,
                    'signed_change_rate': float(data_parts[5]) / 100.0,
                    'ask_bid': 'BID', 
                    'trade_timestamp': int(time.time() * 1000),
                    'high_price': high,
                    'low_price': low,
                    'acc_trade_price_24h': acc_price
                }
                return tick_data
        return None

    async def _pre_connect_check(self) -> float:
        kis_config = self.config.get('exchanges', {}).get('kis', {}) if hasattr(self, 'config') and self.config else {}
        hours_config = kis_config.get('market_hours', {})
        start_time_str = hours_config.get('start_time', '08:30')
        end_time_str = hours_config.get('end_time', '18:10')

        if not MarketHours.is_krx_open(start_time_str=start_time_str, end_time_str=end_time_str):
            wait_sec = MarketHours.time_until_open('kis', start_time_str=start_time_str)
            logger.info(f"Market is closed. KisCollector waiting for {wait_sec/3600:.1f} hours...")
            return min(wait_sec, 3600.0)
        return 0.0

    async def _prepare_connection(self, config: Dict[str, Any]) -> bool:
        self.last_error = None
        approval_key = await self.cred_provider.get_kis_approval_key()
        if not approval_key:
            if self.cred_provider.last_status in [401, 403]:
                self.last_error = f"치명적 인증 오류 ({self.cred_provider.last_status}): 수집기가 중단되었습니다."
                await self.stop()
                return False
            logger.error("Failed to get approval key. Retrying in 10s...")
            return False
        return True

    async def _start_additional_tasks(self, config: Dict[str, Any]):
        logger.info("[KIS] 추가적인 백그라운드 태스크가 없습니다. (동적 랭킹 수집 루프 제거됨)")

    async def _handle_connection_error(self, error: Exception):
        if getattr(self.cred_provider, 'last_status', 0) not in [401, 403]:
            logger.error(f"[{self.exchange.upper()}] Collector Runtime Error: {error}. Reconnecting in 10s...")
            await asyncio.sleep(10)
        else:
            await self.stop()

    async def stop(self):
        await super().stop()
        if self.rank_task and not self.rank_task.done():
            self.rank_task.cancel()

    async def update_subscription(self, code: str, is_collected: bool):
        """ZMQ IPC 시그널 수신 시 동적으로 실시간 웹소켓 구독을 추가/해제합니다."""
        if is_collected:
            if code not in self.available_symbols:
                self.available_symbols.append(code)
                logger.info(f"[KIS] 동적 수집 종목 추가: {code}")
            
            # 전략 엔진 재구성
            if hasattr(self, 'config') and self.config:
                self._init_trade_engines(self.config)
            
            # 웹소켓 구독 등록
            if hasattr(self, 'ws') and self.ws and not self.ws.closed:
                try:
                    approval_key = await self.cred_provider.get_kis_approval_key()
                    subscribe_msg = {
                        "header": {
                            "approval_key": approval_key,
                            "custtype": "P",
                            "tr_type": "1",
                            "content-type": "utf-8"
                        },
                        "body": {
                            "input": {
                                "tr_id": "H0STCNT0",
                                "tr_key": code
                            }
                        }
                    }
                    await self.ws.send_json(subscribe_msg)
                    logger.info(f"[KIS] 웹소켓 실시간 구독 등록 송신 완료: {code}")
                except Exception as e:
                    logger.error(f"[KIS] 웹소켓 구독 등록 실패 ({code}): {e}")
        else:
            if code in self.available_symbols:
                self.available_symbols.remove(code)
                logger.info(f"[KIS] 동적 수집 종목 제거: {code}")
            
            # 전략 엔진 재구성 (제거된 종목 정리)
            if hasattr(self, 'config') and self.config:
                self._init_trade_engines(self.config)

            # 웹소켓 구독 해제
            if hasattr(self, 'ws') and self.ws and not self.ws.closed:
                try:
                    approval_key = await self.cred_provider.get_kis_approval_key()
                    unsubscribe_msg = {
                        "header": {
                            "approval_key": approval_key,
                            "custtype": "P",
                            "tr_type": "2",
                            "content-type": "utf-8"
                        },
                        "body": {
                            "input": {
                                "tr_id": "H0STCNT0",
                                "tr_key": code
                            }
                        }
                    }
                    await self.ws.send_json(unsubscribe_msg)
                    logger.info(f"[KIS] 웹소켓 실시간 구독 해제 송신 완료: {code}")
                except Exception as e:
                    logger.error(f"[KIS] 웹소켓 구독 해제 실패 ({code}): {e}")

    async def _fetch_historical_candles(self, symbol: str, start_time: int, end_time: int) -> List[Candle]:
        """한국투자증권(KIS) REST API를 사용하여 지정 구간 내의 1분봉 데이터를 조회합니다."""
        kis_config = self.config.get('exchanges', {}).get('kis', {}) if hasattr(self, 'config') and self.config else {}
        
        bf_config = self.config.get('collector', {}).get('backfill', {}) if hasattr(self, 'config') and self.config else {}
        delays = bf_config.get('delays', {})
        delay = delays.get('kis', 0.1)
        
        api_url = kis_config.get('api_url', 'https://openapi.koreainvestment.com:9443')
        url = f"{api_url}/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice"

        from datetime import datetime
        from zoneinfo import ZoneInfo
        kst = ZoneInfo('Asia/Seoul')

        candles: List[Candle] = []
        to_time = end_time

        if not self.session or self.session.closed:
            self.session = aiohttp.ClientSession()

        # 누락 시간 범위가 유효할 때까지 루프 (최신부터 과거로 역방향 페이지네이션)
        while to_time >= start_time:
            # KST 기준 시간 변환하여 FID_INPUT_HOUR_1 설정
            to_dt = datetime.fromtimestamp(to_time, tz=kst)
            hour_str = to_dt.strftime('%H%M%S')

            token = await self.cred_provider.get_kis_access_token()
            if not token:
                logger.error(f"[{self.exchange.upper()}] 백필을 위한 KIS 접근 토큰 발급 실패")
                break

            app_key = kis_config.get('app_key')
            app_secret = kis_config.get('app_secret')
            
            headers = {
                "Content-Type": "application/json",
                "authorization": f"Bearer {token}",
                "appkey": str(app_key) if app_key else "",
                "appsecret": str(app_secret) if app_secret else "",
                "tr_id": "FHKST03010200",
                "custtype": "P"
            }

            params = {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": symbol,
                "FID_INPUT_HOUR_1": hour_str,
                "FID_PW_DATA_INCU_YN": "Y"
            }

            try:
                async with self.session.get(url, headers=headers, params=params) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        logger.error(f"[{self.exchange.upper()}] {symbol} 과거 캔들 조회 실패 (HTTP {resp.status}): {body}")
                        break
                    
                    data = await resp.json()
                    output2 = data.get('output2', [])
                    if not output2:
                        break
                    
                    batch_candles = []
                    min_ts = to_time

                    for item in output2:
                        # 일자와 시간 필드 추출
                        date_str = item.get('stck_bsop_date')
                        time_str = item.get('stck_cntg_hour', '').zfill(6)
                        
                        if not date_str or not time_str:
                            continue

                        # KST 기준 파싱하여 타임스탬프 변환
                        dt_str = f"{date_str}{time_str}"
                        try:
                            dt = datetime.strptime(dt_str, '%Y%m%d%H%M%S').replace(tzinfo=kst)
                            ts = int(dt.timestamp())
                        except Exception as parse_err:
                            logger.error(f"[{self.exchange.upper()}] {symbol} 캔들 시간 파싱 에러 ({dt_str}): {parse_err}")
                            continue

                        min_ts = min(min_ts, ts)

                        # 요청한 시작 시간보다 이전 캔들이 유입된 경우 수집 중단 대상
                        if ts < start_time:
                            continue

                        # API가 보낸 시, 고, 저, 종, 거래량 정보 파싱
                        try:
                            open_p = float(item.get('stck_oprc', 0))
                            high_p = float(item.get('stck_hgpr', 0))
                            low_p = float(item.get('stck_lwpr', 0))
                            close_p = float(item.get('stck_prpr', 0))
                            vol = float(item.get('cntg_vol', 0))
                        except ValueError as val_err:
                            logger.error(f"[{self.exchange.upper()}] {symbol} 캔들 수치 변환 실패: {val_err}")
                            continue

                        candle = Candle(
                            exchange=self.exchange,
                            symbol=symbol,
                            interval=60,
                            timestamp=ts,
                            open=open_p,
                            high=high_p,
                            low=low_p,
                            close=close_p,
                            volume=vol,
                            is_closed=True
                        )
                        batch_candles.append(candle)
                    
                    candles.extend(batch_candles)
                    
                    # 더 이상 오래된 데이터가 유입되지 않거나 output2 크기가 너무 작으면 루프 종료
                    if len(output2) < 30 or min_ts >= to_time:
                        break
                        
                    # 다음 페이지네이션을 위해 to_time을 수집된 가장 오래된 캔들 시각의 1초 전으로 설정
                    to_time = min_ts - 60
                    
            except Exception as e:
                logger.error(f"[{self.exchange.upper()}] {symbol} 과거 캔들 API 호출 예외: {e}")
                break

            # Rate Limit 준수를 위한 딜레이 적용
            await asyncio.sleep(delay)
            
        return candles
