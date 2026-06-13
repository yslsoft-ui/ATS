import asyncio
import json
import aiohttp
import time
from typing import List, Dict, Optional, Any
from src.engine.utils.telemetry import get_logger
from src.engine.collector_base import BaseCollector, CollectorRegistry, ConnectionMetadata
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

    def get_connection_metadata(self, config: Dict[str, Any]) -> ConnectionMetadata:
        kis_config = config.get('exchanges', {}).get('kis', {})
        hours = kis_config.get('market_hours', {})
        op_hours = f"{hours.get('start_time', '08:30')} ~ {hours.get('end_time', '18:00')}"
        return {
            "operating_hours": op_hours,
            "websocket_url": self._get_websocket_url(config),
            "api_url": kis_config.get('api_url', "https://openapi.koreainvestment.com:9443")
        }

    async def _fetch_symbols(self, config: Dict[str, Any]) -> List[str]:
        # DB에서 활성 종목 조회
        symbols = await self._fetch_active_symbols_from_db(config)
        if not symbols:
            # DB 조회 실패 혹은 비어있을 때 기본 폴백 종목 지정 (예: 삼성전자 '005930')
            symbols = ["005930"]
            logger.info(f"[{self.exchange.upper()}] DB에 활성 종목이 없어 기본 종목으로 폴백합니다: {symbols}")
        return symbols

    def _get_websocket_url(self, config: Dict[str, Any]) -> str:
        kis_config = config.get('exchanges', {}).get('kis', {})
        return kis_config.get('websocket_url', "ws://ops.koreainvestment.com:21000")

    async def _subscribe(self, ws, config: Dict[str, Any]):
        approval_key = await self.cred_provider.get_kis_approval_key()
        for symbol_code in self.available_symbols:
            # 1. 통합 실시간 체결 구독
            subscribe_msg_cnt = {
                "header": {
                    "approval_key": approval_key,
                    "custtype": "P",
                    "tr_type": "1",
                    "content-type": "utf-8"
                },
                "body": {
                    "input": {
                        "tr_id": "H0UNCNT0",  # 실시간 주식 체결가 통합
                        "tr_key": symbol_code
                    }
                }
            }
            await ws.send_json(subscribe_msg_cnt)
            await asyncio.sleep(0.05)

            # 2. 통합 실시간 장운영정보 구독
            subscribe_msg_mko = {
                "header": {
                    "approval_key": approval_key,
                    "custtype": "P",
                    "tr_type": "1",
                    "content-type": "utf-8"
                },
                "body": {
                    "input": {
                        "tr_id": "H0UNMKO0",  # 국내주식 장운영정보 통합
                        "tr_key": symbol_code
                    }
                }
            }
            await ws.send_json(subscribe_msg_mko)
            await asyncio.sleep(0.05)

    def _parse_message(self, msg) -> Optional[Dict]:
        if msg.type != aiohttp.WSMsgType.TEXT:
            return None

        if msg.type == aiohttp.WSMsgType.TEXT:
            raw_data = msg.data

            # KIS 서버는 약 110초 간격으로 JSON PINGPONG 메시지를 보내 세션 유지를 확인한다.
            # 실제 포맷: {"header":{"tr_id":"PINGPONG","datetime":"YYYYMMDDHHMMSS"}}
            # 응답하지 않으면 서버가 연결을 끊으므로, 수신한 JSON을 그대로 echo 해야 한다.
            if '"PINGPONG"' in raw_data:
                logger.debug(f"[KIS] PINGPONG 수신 → echo 응답")
                if self.ws and not self.ws.closed:
                    asyncio.ensure_future(self.ws.send_str(raw_data))
                return None

            # KIS 구독 요청/해제에 대한 JSON 응답 처리
            # 포맷: {"header":{"tr_id":"H0STCNT0",...},"body":{"rt_cd":"0","msg1":"SUBSCRIBE SUCCESS",...}}
            if raw_data.startswith('{'):
                try:
                    json_msg = json.loads(raw_data)
                    body = json_msg.get('body', {})
                    rt_cd = body.get('rt_cd', '')
                    msg1 = body.get('msg1', '')
                    tr_id_resp = json_msg.get('header', {}).get('tr_id', '')
                    if rt_cd == '0':
                        logger.info(f"[KIS] 구독 응답 OK: tr_id={tr_id_resp}, msg={msg1}")
                    else:
                        logger.warning(f"[KIS] 구독 응답 에러: tr_id={tr_id_resp}, rt_cd={rt_cd}, msg={msg1}")
                except Exception:
                    pass
                return None

            if raw_data.startswith('0') or raw_data.startswith('1'):
                parts = raw_data.split('|')
                if len(parts) < 4: return None

                tr_id = parts[1]
                
                # 장운영정보 통합 (H0UNMKO0) 실시간 파싱 및 감지
                if tr_id == 'H0UNMKO0':
                    try:
                        all_fields = parts[3].split('^')
                        if len(all_fields) >= 4:
                            symbol_code = all_fields[0]
                            trht_yn = all_fields[1]
                            susp_reason = all_fields[2].strip()
                            mkop_cls_code = all_fields[3]
                            vi_cls_code = all_fields[8] if len(all_fields) > 8 else 'N'
                            
                            if trht_yn == 'Y':
                                self.status = "SUSPENDED"
                                self.status_reason = f"[{symbol_code}] 거래정지: {susp_reason}"
                                logger.warning(f"[KIS] {symbol_code} 거래정지(SUSPENDED) 감지: {susp_reason} (장운영구분: {mkop_cls_code})")
                            elif vi_cls_code not in ('N', ''):
                                self.status = "SUSPENDED"
                                self.status_reason = f"[{symbol_code}] VI 발동 (VI구분: {vi_cls_code})"
                                logger.warning(f"[KIS] {symbol_code} 변동성완화장치(VI) 발동 감지 (장운영구분: {mkop_cls_code})")
                            else:
                                # 정상 복구
                                if self.status == "SUSPENDED":
                                    self.status = "RUNNING"
                                    self.status_reason = None
                                    logger.info(f"[KIS] {symbol_code} 거래 정지/VI 해제. RUNNING 복구.")
                    except Exception as e:
                        logger.error(f"[KIS] 장운영정보 통합 파싱 에러: {e}")
                    return None



                market = 'UN' if tr_id == 'H0UNCNT0' else ('NXT' if tr_id == 'H0NXCNT0' else 'KRX')

                try:
                    data_cnt = int(parts[2])
                except ValueError:
                    data_cnt = 1

                all_fields = parts[3].split('^')
                FIELD_COUNT = 46
                
                tick_list = []
                for i in range(data_cnt):
                    start_idx = i * FIELD_COUNT
                    if start_idx + FIELD_COUNT > len(all_fields):
                        break

                    symbol_code = all_fields[start_idx]
                    time_str = all_fields[start_idx + 1]
                    try:
                        price = float(all_fields[start_idx + 2]) if all_fields[start_idx + 2] else 0.0
                        volume = float(all_fields[start_idx + 12]) if all_fields[start_idx + 12] else 0.0
                        high = float(all_fields[start_idx + 8]) if all_fields[start_idx + 8] else 0.0
                        low = float(all_fields[start_idx + 9]) if all_fields[start_idx + 9] else 0.0
                        acc_price = float(all_fields[start_idx + 14]) if all_fields[start_idx + 14] else 0.0
                        signed_change_rate = (float(all_fields[start_idx + 5]) / 100.0) if all_fields[start_idx + 5] else 0.0
                    except ValueError:
                        continue

                    sign = all_fields[start_idx + 3]
                    raw_change = float(all_fields[start_idx + 4]) if all_fields[start_idx + 4] else 0.0
                    change_price = -raw_change if sign in ('4', '5') else raw_change

                    # 체결구분(CCLD_DVSN) 매핑: 1은 매수(BID), 5는 매도(ASK)
                    raw_ask_bid = all_fields[start_idx + 21]
                    ask_bid = 'ASK' if raw_ask_bid == '5' else 'BID'

                    # KIS 체결시각(HHMMSS)을 오늘 날짜와 조합하여 Unix Timestamp(ms) 계산
                    from zoneinfo import ZoneInfo
                    from datetime import datetime
                    kst = ZoneInfo('Asia/Seoul')
                    now = datetime.now(kst)
                    dt_str = f"{now.strftime('%Y%m%d')}{time_str}"
                    try:
                        dt = datetime.strptime(dt_str, '%Y%m%d%H%M%S').replace(tzinfo=kst)
                        trade_timestamp = int(dt.timestamp() * 1000)
                    except Exception:
                        trade_timestamp = int(time.time() * 1000)

                    tick_data = {
                        'type': 'tick',
                        'exchange': 'kis',
                        'market': market,
                        'code': symbol_code,
                        'trade_price': price,
                        'trade_volume': volume,
                        'signed_change_rate': signed_change_rate,
                        'change_price': change_price,
                        'ask_bid': ask_bid, 
                        'trade_timestamp': trade_timestamp,
                        'high_price': high,
                        'low_price': low,
                        'acc_trade_price_24h': acc_price
                    }
                    tick_list.append(tick_data)
                
                return tick_list if tick_list else None
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


    async def _handle_connection_error(self, error: Exception):
        if isinstance(error, ValueError):
            self.last_error = f"설정 오류: {error}"
            logger.critical(f"[{self.exchange.upper()}] 치명적 설정 오류 감지. 수집기를 정지합니다. Error: {error}")
            await self.stop()
        elif getattr(self.cred_provider, 'last_status', 0) not in [401, 403]:
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
                                "tr_id": "H0UNCNT0",
                                "tr_key": code
                            }
                        }
                    }
                    await self.ws.send_json(subscribe_msg)
                    logger.info(f"[KIS] 웹소켓 실시간 통합 구독 등록 송신 완료: {code}")
                except Exception as e:
                    logger.error(f"[KIS] 웹소켓 구독 등록 실패 ({code}): {e}")
        else:
            if code in self.available_symbols:
                self.available_symbols.remove(code)
                logger.info(f"[KIS] 동적 수집 종목 제거: {code}")
            
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
                                "tr_id": "H0UNCNT0",
                                "tr_key": code
                            }
                        }
                    }
                    await self.ws.send_json(unsubscribe_msg)
                    logger.info(f"[KIS] 웹소켓 실시간 통합 구독 해제 송신 완료: {code}")
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
                "FID_COND_MRKT_DIV_CODE": "UN",
                "FID_INPUT_ISCD": symbol,
                "FID_INPUT_HOUR_1": hour_str,
                "FID_PW_DATA_INCU_YN": "Y",
                "FID_ETC_CLS_CODE": ""
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

                        # KIS 장외 시간대 가짜/오류 캔들 유입 차단 (KST 20:00 ~ 08:30)
                        try:
                            time_val = int(time_str)
                            if time_val >= 200000 or time_val < 83000:
                                continue
                        except ValueError:
                            pass

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

                        # 거래량이 0인 캔들은 백필 수집 대상에서 제외
                        if vol == 0.0:
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
