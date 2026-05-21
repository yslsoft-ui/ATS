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
        return kis_symbols

    def _get_websocket_url(self, config: Dict[str, Any]) -> str:
        kis_config = config.get('exchanges', {}).get('kis', {})
        is_vts = kis_config.get('is_vts', True)
        default_url = "ws://ops.koreainvestment.com:31000" if is_vts else "ws://ops.koreainvestment.com:21000"
        return kis_config.get('websocket_url', default_url)

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
        if not MarketHours.is_krx_open():
            wait_sec = MarketHours.time_until_open('kis')
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
        # settings.yaml에 KIS 고정 수집 종목(symbols)이 기재되어 있다면 동적 랭킹 수집 루프를 기동하지 않습니다.
        # 이를 통해 사용자 고정 설정이 덮어쓰기 오염되는 것을 방지합니다.
        kis_symbols = config.get('exchanges', {}).get('kis', {}).get('symbols', [])
        if not kis_symbols:
            self.rank_task = asyncio.create_task(self._ranking_loop())
            logger.info("[KIS] 동적 랭킹 수집 루프가 기동되었습니다.")
        else:
            logger.info(f"[KIS] 고정 수집 종목 설정 감지 ({kis_symbols}). 동적 랭킹 수집 루프를 기동하지 않고 설정을 유지합니다.")

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

    async def _ranking_loop(self):
        await asyncio.sleep(5)
        while self.is_running:
            try:
                await self.fetch_market_rank()
                self.last_error = None
            except Exception as e:
                self.last_error = f"Ranking Fetch Error: {str(e)}"
                logger.error(f"Error in ranking loop: {e}")
            await asyncio.sleep(60)

    async def fetch_market_rank(self):
        logger.info("랭킹 수집 시도 중...")
        token = await self.cred_provider.get_kis_access_token()
        if not token:
            if self.cred_provider.last_status in [401, 403, 500]:
                self.last_error = f"인증 실패 ({self.cred_provider.last_status}): 토큰을 발급받을 수 없어 수집기를 중단합니다."
                await self.stop()
            return

        app_key = self.cred_provider.config.get('exchanges', {}).get('kis', {}).get('app_key')
        
        url = "https://openapivts.koreainvestment.com:29443/uapi/domestic-stock/v1/quotations/volume-rank"
        if not self.cred_provider.config.get('exchanges', {}).get('kis', {}).get('is_vts', True):
            url = "https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/quotations/volume-rank"

        headers = {
            "Content-Type": "application/json",
            "authorization": f"Bearer {token}",
            "appkey": app_key,
            "appsecret": self.cred_provider.config.get('exchanges', {}).get('kis', {}).get('app_secret'),
            "tr_id": "FHKST01010900",
            "custtype": "P"
        }
        
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_COND_SCR_DIV_CODE": "20173",
            "FID_INPUT_ISCD": "0000",
            "FID_DIV_CLS_CODE": "0",
            "FID_BLNG_CLS_CODE": "0",
            "FID_TRGT_CLS_CODE": "0",
            "FID_TRGT_EXLS_CLS_CODE": "0",
            "FID_INPUT_PRICE_1": "0",
            "FID_INPUT_PRICE_2": "0",
            "FID_VOL_CNT": "0",
            "FID_INPUT_DATE_1": ""
        }

        if not self.session or self.session.closed:
            self.session = aiohttp.ClientSession()

        async with self.session.get(url, headers=headers, params=params) as resp:
            if resp.status == 200:
                self.last_error = None
                data = await resp.json()
                output = data.get('output', [])
                
                if not output:
                    logger.warning("No ranking data (Market closed?). Using default major symbols as fallback.")
                    default_codes = ["005930", "000660", "035420", "005380", "035720", "000270", "005490", "105560", "055550", "068270"]
                    for code in default_codes:
                        name = stock_mapper.get_name('kis', code)
                        output.append({
                            'mksc_shrn_iscd': code,
                            'hts_kor_isnm': name,
                            'stck_prpr': '0',
                            'prdy_ctrt': '0',
                            'acml_tr_pbmn': '0'
                        })
                
                for item in output:
                    symbol = item.get('mksc_shrn_iscd')
                    name = item.get('hts_kor_isnm')
                    price = float(item.get('stck_prpr', 0))
                    change_rate = float(item.get('prdy_ctrt', 0)) / 100.0
                    volume_amt = float(item.get('acml_tr_pbmn', 0))

                    stock_mapper.add_mapping('kis', symbol, name)
                    
                    mock_tick = {
                        'type': 'tick',
                        'exchange': 'kis',
                        'code': symbol,
                        'trade_price': price,
                        'signed_change_rate': change_rate,
                        'acc_trade_price_24h': volume_amt,
                        'high_price': float(item.get('stck_hgpr', 0)),
                        'low_price': float(item.get('stck_lwpr', 0)),
                        'trade_timestamp': int(time.time() * 1000)
                    }
                    if self.on_data_callback:
                        await self.on_data_callback(mock_tick)
                
                self.available_symbols = [item.get('mksc_shrn_iscd') for item in output]
                stock_mapper.save_cache()
                
                logger.info(f"Updated {len(output)} ranking symbols via REST API")
            else:
                body = await resp.text()
                self.last_error = f"Ranking API Error: {resp.status} - {body}"
                logger.error(self.last_error)
                
                is_auth_error = resp.status in [401, 403] or (resp.status == 500 and ("유효하지" in body or "식별키" in body))
                if is_auth_error:
                    self.last_error = f"치명적 인증 오류 ({resp.status}): API 키를 확인하세요. 수집기를 중단합니다."
                    await self.stop()
