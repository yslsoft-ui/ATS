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
        self.symbol_market_map: Dict[str, str] = {}
        self.last_event_symbol: Optional[str] = None
        self.vi_active_symbols = set()
        self.suspended_symbols = set()

    @property
    def exchange_id(self) -> str:
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
            logger.info(f"[{self.exchange_id.upper()}] DB에 활성 종목이 없어 기본 종목으로 폴백합니다: {symbols}")
            return symbols
            
        return symbols

    def _get_websocket_url(self, config: Dict[str, Any]) -> str:
        kis_config = config.get('exchanges', {}).get('kis', {})
        return kis_config.get('websocket_url', "ws://ops.koreainvestment.com:21000")

    async def _subscribe(self, ws, config: Dict[str, Any]):
        approval_key = await self.cred_provider.get_kis_approval_key()
        for symbol_code in self.available_symbols:
            market = self.symbol_market_map.get(symbol_code, "J")
            tr_id_cnt = 'H0UNCNT0' if market == "UN" else 'H0STCNT0'
            tr_id_mko = 'H0UNMKO0' if market == "UN" else 'H0STMKO0'
            
            # 1. 실시간 체결 구독
            subscribe_msg_cnt = {
                "header": {
                    "approval_key": approval_key,
                    "custtype": "P",
                    "tr_type": "1",
                    "content-type": "utf-8"
                },
                "body": {
                    "input": {
                        "tr_id": tr_id_cnt,
                        "tr_key": symbol_code
                    }
                }
            }
            await ws.send_json(subscribe_msg_cnt)
            await asyncio.sleep(0.05)
            logger.info(f"[KIS] 웹소켓 실시간 체결 구독 등록 송신 완료: {symbol_code} (tr_id={tr_id_cnt})")

            # 2. 실시간 장운영정보 구독
            subscribe_msg_mko = {
                "header": {
                    "approval_key": approval_key,
                    "custtype": "P",
                    "tr_type": "1",
                    "content-type": "utf-8"
                },
                "body": {
                    "input": {
                        "tr_id": tr_id_mko,
                        "tr_key": symbol_code
                    }
                }
            }
            await ws.send_json(subscribe_msg_mko)
            await asyncio.sleep(0.05)
            logger.info(f"[KIS] 웹소켓 실시간 장운영정보 구독 등록 송신 완료: {symbol_code} (tr_id={tr_id_mko})")

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
                
                # 장운영정보 통합 (H0UNMKO0) 및 KRX 전용 (H0STMKO0) 실시간 파싱 및 감지
                if tr_id in ('H0UNMKO0', 'H0STMKO0'):
                    try:
                        all_fields = parts[3].split('^')
                        if len(all_fields) >= 4:
                            symbol_code = all_fields[0]
                            trht_yn = all_fields[1]
                            susp_reason = all_fields[2].strip()
                            mkop_cls_code = all_fields[3]
                            vi_cls_code = all_fields[8] if len(all_fields) > 8 else 'N'
                            
                            korean_name = stock_mapper.get_name('kis', symbol_code)
                            self.last_event_symbol = symbol_code
                            
                            # 1. 시장 전체 거래정지 / 서킷브레이크 판단 (수집기 전역 상태 제어)
                            if mkop_cls_code in ('164', '174', '184'):
                                if self.status != "SUSPENDED":
                                    self.status = "SUSPENDED"
                                    self.status_reason = f"시장 전체 정지 / 서킷브레이커 발동 (코드: {mkop_cls_code})"
                                    logger.warning(f"[KIS] 시장 전체 정지(SUSPENDED) 감지! 장운영구분: {mkop_cls_code}")
                            else:
                                if self.status == "SUSPENDED":
                                    self.status = "RUNNING"
                                    self.status_reason = None
                                    logger.info(f"[KIS] 시장 전체 정지 해제. RUNNING 복구.")
                                    
                            # 2. 개별 종목별 거래정지 감지 및 이벤트 기록
                            if trht_yn == 'Y':
                                if symbol_code not in self.suspended_symbols:
                                    self.suspended_symbols.add(symbol_code)
                                    msg = f"[{symbol_code}] {korean_name} 거래정지: {susp_reason}"
                                    logger.warning(f"[KIS] 개별 종목 거래정지 감지: {msg}")
                                    asyncio.create_task(self._record_stock_event('STOCK_SUSPENDED', symbol_code, msg))
                            else:
                                if symbol_code in self.suspended_symbols:
                                    self.suspended_symbols.remove(symbol_code)
                                    msg = f"[{symbol_code}] {korean_name} 거래정지 해제"
                                    logger.info(f"[KIS] 개별 종목 거래정지 해제 감지: {msg}")
                                    asyncio.create_task(self._record_stock_event('STOCK_RESUMED', symbol_code, msg))
                                    
                            # 3. 개별 종목별 VI 발동 감지 및 이벤트 기록
                            if vi_cls_code not in ('N', ''):
                                if symbol_code not in self.vi_active_symbols:
                                    self.vi_active_symbols.add(symbol_code)
                                    msg = f"[{symbol_code}] {korean_name} VI 발동 (구분코드: {vi_cls_code})"
                                    logger.warning(f"[KIS] 개별 종목 VI 발동 감지: {msg}")
                                    asyncio.create_task(self._record_stock_event('STOCK_VI_ACTIVATED', symbol_code, msg))
                            else:
                                if symbol_code in self.vi_active_symbols:
                                    self.vi_active_symbols.remove(symbol_code)
                                    msg = f"[{symbol_code}] {korean_name} VI 해제"
                                    logger.info(f"[KIS] 개별 종목 VI 해제 감지: {msg}")
                                    asyncio.create_task(self._record_stock_event('STOCK_VI_RELEASED', symbol_code, msg))
                                    
                    except Exception as e:
                        logger.error(f"[KIS] 장운영정보 파싱 에러 (tr_id={tr_id}): {e}")
                    return None

                if tr_id not in ('H0UNCNT0', 'H0STCNT0', 'H0NXCNT0'):
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
                        'exchange_id': 'kis',
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
                        'acc_trade_price_24h': acc_price,
                        'is_vi': symbol_code in self.vi_active_symbols,
                        'is_suspended': symbol_code in self.suspended_symbols
                    }
                    tick_list.append(tick_data)
                
                return tick_list if tick_list else None
        return None

    async def _record_stock_event(self, event_type: str, symbol_code: str, message: str):
        # 1. DB 기록
        if self.repository:
            try:
                await self.repository.insert_system_event(
                    event_type=event_type,
                    target=symbol_code,
                    message=message
                )
            except Exception as e:
                logger.error(f"[KIS] Failed to insert stock system event: {e}")
        
        # 2. ZMQ/Websocket 발행 (on_signal_callback 호출)
        if self.on_signal_callback:
            try:
                payload = {
                    "type": "stock_event",
                    "event_type": event_type,
                    "target": symbol_code,
                    "message": message,
                    "timestamp": int(time.time() * 1000)
                }
                self.on_signal_callback(payload)
            except Exception as e:
                logger.error(f"[KIS] Failed to trigger stock event signal callback: {e}")

    async def _pre_connect_check(self) -> float:
        kis_config = self.config.get('exchanges', {}).get('kis', {}) if hasattr(self, 'config') and self.config else {}
        hours_config = kis_config.get('market_hours', {})
        start_time_str = hours_config.get('start_time', '08:30')
        end_time_str = hours_config.get('end_time', '18:10')

        # 1. 주말 및 영업시간 기본 체크
        if not MarketHours.is_krx_open(start_time_str=start_time_str, end_time_str=end_time_str):
            wait_sec = MarketHours.time_until_open('kis', start_time_str=start_time_str)
            logger.info(f"Market is closed. KisCollector waiting for {wait_sec/3600:.1f} hours...")
            return min(wait_sec, 3600.0)

        # 2. 공휴일/휴장일 실시간 API 체크 (CredentialProvider 활용)
        from datetime import datetime, timedelta, timezone
        kst = timezone(timedelta(hours=9))
        now_kst = datetime.now(kst)
        today_str = now_kst.strftime("%Y%m%d")

        try:
            is_open = await self.cred_provider.check_kis_open_day(today_str)
            if not is_open:
                # 오늘 남은 시간(익일 00:00 KST)까지 연결을 유예합니다.
                tomorrow = (now_kst + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
                wait_sec = (tomorrow - now_kst).total_seconds()
                logger.info(f"[KisCollector] 오늘({now_kst.strftime('%Y-%m-%d')})은 KIS 휴장일입니다. 익일 00:00시까지 연결을 유예합니다 ({wait_sec/3600:.1f}시간 대기).")
                return wait_sec
        except Exception as e:
            # 수집기 데몬 루프는 API 호출 실패로 중단(폭사)되지 않고 60초 대기 후 안전하게 재시도합니다.
            logger.error(f"[KisCollector] KIS 휴장일 확인 실패로 60초 동안 수집 연결을 일시 유예합니다: {e}")
            return 60.0

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
        
        # 종목별 Nextrade 정보 사전 조회 및 동기화
        await self._detect_symbol_markets(self.available_symbols)
        return True

    async def _detect_symbol_markets(self, symbols: List[str]):
        """종목별 Nextrade 거래 가능 여부를 판단하여 self.symbol_market_map에 매핑하고 DB에 캐싱합니다."""
        db_path = self.config.get('db_path', 'data/backtest.db') if hasattr(self, 'config') and self.config else 'data/backtest.db'
        from src.database.connection import get_db_conn
        
        # 1. 먼저 DB에서 존재하는 정보 조회
        symbols_to_fetch = []
        try:
            async with get_db_conn(db_path) as db:
                for symbol in symbols:
                    async with db.execute(
                        "SELECT cptt_trad_tr_psbl_yn, nxt_tr_stop_yn FROM kis_stock_info WHERE symbol = ?", 
                        (symbol,)
                    ) as cursor:
                        row = await cursor.fetchone()
                        if row:
                            psbl = row['cptt_trad_tr_psbl_yn']
                            stop = row['nxt_tr_stop_yn']
                            self.symbol_market_map[symbol] = "UN" if (psbl == "Y" and stop != "Y") else "J"
                        else:
                            symbols_to_fetch.append(symbol)
        except Exception as e:
            logger.error(f"[KIS] DB에서 stock_info 조회 실패: {e}")
            symbols_to_fetch = list(symbols)

        if not symbols_to_fetch:
            return

        # 2. DB에 없는 종목은 KIS OpenAPI CTPF1002R 호출
        kis_config = self.config.get('exchanges', {}).get('kis', {}) if hasattr(self, 'config') and self.config else {}
        api_url = kis_config.get('api_url', 'https://openapi.koreainvestment.com:9443').rstrip('/')
        url = f"{api_url}/uapi/domestic-stock/v1/quotations/search-stock-info"

        if not self.session or self.session.closed:
            self.session = aiohttp.ClientSession()

        for symbol in symbols_to_fetch:
            token = await self.cred_provider.get_kis_access_token()
            if not token:
                logger.error(f"[KIS] {symbol} 정보 조회를 위한 KIS access token 발급 실패")
                self.symbol_market_map[symbol] = "J" # fallback
                continue

            app_key = kis_config.get('app_key')
            app_secret = kis_config.get('app_secret')

            headers = {
                "Content-Type": "application/json",
                "authorization": f"Bearer {token}",
                "appkey": str(app_key) if app_key else "",
                "appsecret": str(app_secret) if app_secret else "",
                "tr_id": "CTPF1002R",
                "custtype": "P"
            }
            params = {
                "PRDT_TYPE_CD": "300",
                "PDNO": symbol
            }

            try:
                async with self.session.get(url, headers=headers, params=params) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        logger.error(f"[KIS] {symbol} 주식기본조회 실패 (HTTP {resp.status}): {body}")
                        self.symbol_market_map[symbol] = "J"
                        continue
                    
                    data = await resp.json()
                    if data.get('rt_cd') != '0':
                        logger.error(f"[KIS] {symbol} 주식기본조회 API 오류: {data.get('msg1')}")
                        self.symbol_market_map[symbol] = "J"
                        continue

                    output = data.get('output', {})
                    psbl = output.get('cptt_trad_tr_psbl_yn', 'N')
                    stop = output.get('nxt_tr_stop_yn', 'N')

                    self.symbol_market_map[symbol] = "UN" if (psbl == "Y" and stop != "Y") else "J"

                    # DB에 캐싱 저장
                    async with get_db_conn(db_path) as db:
                        await db.execute("""
                            INSERT OR REPLACE INTO kis_stock_info (
                                symbol, prdt_name, prdt_abrv_name, mket_id_cd, scty_grp_id_cd, excg_dvsn_cd,
                                lstg_stqt, lstg_cptl_amt, cpta, papr, issu_pric, kospi200_item_yn,
                                scts_mket_lstg_dt, kosdaq_mket_lstg_dt, lstg_abol_dt, std_pdno, prdt_eng_name,
                                tr_stop_yn, admn_item_yn, thdt_clpr, bfdy_clpr, std_idst_clsf_cd_name,
                                idx_bztp_lcls_cd_name, idx_bztp_mcls_cd_name, idx_bztp_scls_cd_name,
                                cptt_trad_tr_psbl_yn, nxt_tr_stop_yn, updated_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                        """, (
                            symbol,
                            output.get('prdt_name'),
                            output.get('prdt_abrv_name'),
                            output.get('mket_id_cd'),
                            output.get('scty_grp_id_cd'),
                            output.get('excg_dvsn_cd'),
                            int(output.get('lstg_stqt') or 0) if output.get('lstg_stqt') else None,
                            int(output.get('lstg_cptl_amt') or 0) if output.get('lstg_cptl_amt') else None,
                            int(output.get('cpta') or 0) if output.get('cpta') else None,
                            float(output.get('papr') or 0) if output.get('papr') else None,
                            float(output.get('issu_pric') or 0) if output.get('issu_pric') else None,
                            output.get('kospi200_item_yn'),
                            output.get('scts_mket_lstg_dt') or output.get('kosdaq_mket_lstg_dt'),
                            output.get('kosdaq_mket_lstg_dt'),
                            output.get('lstg_abol_dt'),
                            output.get('std_pdno'),
                            output.get('prdt_eng_name'),
                            output.get('tr_stop_yn'),
                            output.get('admn_item_yn'),
                            float(output.get('thdt_clpr') or 0) if output.get('thdt_clpr') else None,
                            float(output.get('bfdy_clpr') or 0) if output.get('bfdy_clpr') else None,
                            output.get('std_idst_clsf_cd_name'),
                            output.get('idx_bztp_lcls_cd_name'),
                            output.get('idx_bztp_mcls_cd_name'),
                            output.get('idx_bztp_scls_cd_name'),
                            psbl,
                            stop
                        ))
                        await db.commit()
                    
                    logger.info(f"[KIS] {symbol} 기본정보 동기화 완료: Nextrade 여부(가능={psbl}, 정지={stop}) -> Market {self.symbol_market_map[symbol]}")
            except Exception as e:
                logger.error(f"[KIS] {symbol} 기본정보 동기화 실패: {e}")
                self.symbol_market_map[symbol] = "J"

            # 과도한 API 호출 방지를 위해 딜레이 부여
            await asyncio.sleep(0.2)


    async def _handle_connection_error(self, error: Exception):
        if isinstance(error, ValueError):
            self.last_error = f"설정 오류: {error}"
            logger.critical(f"[{self.exchange_id.upper()}] 치명적 설정 오류 감지. 수집기를 정지합니다. Error: {error}")
            await self.stop()
        elif getattr(self.cred_provider, 'last_status', 0) not in [401, 403]:
            logger.error(f"[{self.exchange_id.upper()}] Collector Runtime Error: {error}. Reconnecting in 10s...")
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
                # 신규 등록 종목에 대한 즉시 백필 기동 (백그라운드 비동기 태스크)
                asyncio.create_task(self.backfill_symbol(code, self.config))
            
            # 동적으로 추가된 종목도 Nextrade 여부 판별 수행
            if code not in self.symbol_market_map:
                await self._detect_symbol_markets([code])
            
            market = self.symbol_market_map.get(code, "J")
            tr_id_cnt = 'H0UNCNT0' if market == "UN" else 'H0STCNT0'
            
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
                                "tr_id": tr_id_cnt,
                                "tr_key": code
                             }
                        }
                    }
                    await self.ws.send_json(subscribe_msg)
                    logger.info(f"[KIS] 웹소켓 실시간 구독 등록 송신 완료: {code} (tr_id={tr_id_cnt})")
                except Exception as e:
                    logger.error(f"[KIS] 웹소켓 구독 등록 실패 ({code}): {e}")
        else:
            if code in self.available_symbols:
                self.available_symbols.remove(code)
                logger.info(f"[KIS] 동적 수집 종목 제거: {code}")
            
            market = self.symbol_market_map.get(code, "J")
            tr_id_cnt = 'H0UNCNT0' if market == "UN" else 'H0STCNT0'
            
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
                                "tr_id": tr_id_cnt,
                                "tr_key": code
                            }
                        }
                    }
                    await self.ws.send_json(unsubscribe_msg)
                    logger.info(f"[KIS] 웹소켓 실시간 구독 해제 송신 완료: {code} (tr_id={tr_id_cnt})")
                except Exception as e:
                    logger.error(f"[KIS] 웹소켓 구독 해제 실패 ({code}): {e}")

    async def _fetch_historical_candles(self, symbol: str, start_time: int, end_time: int) -> List[Candle]:
        """한국투자증권(KIS) REST API를 사용하여 지정 구간 내의 1분봉 데이터를 조회합니다."""
        kis_config = self.config.get('exchanges', {}).get('kis', {}) if hasattr(self, 'config') and self.config else {}
        
        bf_config = self.config.get('collector', {}).get('backfill', {}) if hasattr(self, 'config') and self.config else {}
        delays = bf_config.get('delays', {})
        delay = delays.get('kis', 0.2)
        
        api_url = kis_config.get('api_url', 'https://openapi.koreainvestment.com:9443')
        url = f"{api_url}/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice"

        from datetime import datetime
        from zoneinfo import ZoneInfo
        kst = ZoneInfo('Asia/Seoul')

        candles: List[Candle] = []
        to_time = end_time
        prev_accum_val = None  # 페이지네이션 전체에 걸쳐 누적 거래대금을 추적하여 가짜 캔들 차단
        active_date = None     # 첫 호출 시 확인된 영업일을 기준 영업일로 고정 (장외 시간/미래 조회 오동작 방지)

        if not self.session or self.session.closed:
            self.session = aiohttp.ClientSession()

        # 누락 시간 범위가 유효할 때까지 루프 (최신부터 과거로 역방향 페이지네이션)
        while to_time >= start_time:
            # KST 기준 시간 변환하여 FID_INPUT_HOUR_1 설정
            to_dt = datetime.fromtimestamp(to_time, tz=kst)
            
            # 미래 조회 보간 버그 방지를 위해 최대 저녁 8시(20:00:00)로 제한
            if to_dt.hour >= 20:
                to_dt = to_dt.replace(hour=20, minute=0, second=0, microsecond=0)
                to_time = int(to_dt.timestamp())
                if to_time < start_time:
                    break

            hour_str = to_dt.strftime('%H%M%S')

            token = await self.cred_provider.get_kis_access_token()
            if not token:
                logger.error(f"[{self.exchange_id.upper()}] 백필을 위한 KIS 접근 토큰 발급 실패")
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

            market = self.symbol_market_map.get(symbol, "J")
            params = {
                "FID_COND_MRKT_DIV_CODE": market,  # Nextrade 지원 상태에 따른 동적 시장 설정
                "FID_INPUT_ISCD": symbol,
                "FID_INPUT_HOUR_1": hour_str,
                "FID_PW_DATA_INCU_YN": "Y",
                "FID_ETC_CLS_CODE": ""
            }

            try:
                async with self.session.get(url, headers=headers, params=params) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        logger.error(f"[{self.exchange_id.upper()}] {symbol} 과거 캔들 조회 실패 (HTTP {resp.status}): {body}")
                        break
                    
                    data = await resp.json()
                    output2 = data.get('output2', [])
                    if not output2:
                        break
                    
                    # 1. output2의 캔들 목록을 영업일 및 체결시간 기준으로 시간 오름차순(과거->최신) 정렬
                    output2_sorted = sorted(
                        output2, 
                        key=lambda x: (x.get('stck_bsop_date', ''), x.get('stck_cntg_hour', ''))
                    )

                    # 첫 호출 시 가장 최신 영업일을 기준 영업일로 고정 (장외 시간/미래 조회 오동작 방지)
                    if active_date is None and output2_sorted:
                        active_date = output2_sorted[-1].get('stck_bsop_date')
                    
                    batch_candles = []
                    min_ts = to_time

                    for item in output2_sorted:
                        # 일자와 시간 필드 추출
                        date_str = item.get('stck_bsop_date')
                        time_str = item.get('stck_cntg_hour', '').zfill(6)
                        
                        if not date_str or not time_str:
                            continue

                        # 기준 영업일과 일치하지 않는 다른 영업일(미래의 오늘 날짜) 데이터 배제
                        if active_date and date_str != active_date:
                            continue

                        # 누적 거래대금 파싱
                        try:
                            accum_val = float(item.get('acml_tr_pbmn', 0))
                        except ValueError:
                            accum_val = 0.0

                        # 누적 거래대금의 변동이 전혀 없다면, 실제 체결이 발생하지 않은 빈 캔들로 보고 필터링
                        if prev_accum_val is not None and accum_val == prev_accum_val:
                            continue
                        
                        prev_accum_val = accum_val

                        # KST 기준 파싱하여 타임스탬프 변환
                        dt_str = f"{date_str}{time_str}"
                        try:
                            dt = datetime.strptime(dt_str, '%Y%m%d%H%M%S').replace(tzinfo=kst)
                            ts = int(dt.timestamp())
                        except Exception as parse_err:
                            logger.error(f"[{self.exchange_id.upper()}] {symbol} 캔들 시간 파싱 에러 ({dt_str}): {parse_err}")
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
                            logger.error(f"[{self.exchange_id.upper()}] {symbol} 캔들 수치 변환 실패: {val_err}")
                            continue

                        # 거래량이 0인 캔들은 백필 수집 대상에서 제외
                        if vol == 0.0:
                            continue

                        candle = Candle(
                            exchange_id=self.exchange_id,
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
                logger.error(f"[{self.exchange_id.upper()}] {symbol} 과거 캔들 API 호출 예외: {e}")
                break

            # Rate Limit 준수를 위한 딜레이 적용
            await asyncio.sleep(delay)
            
        return candles
