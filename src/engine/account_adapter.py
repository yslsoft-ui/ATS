# -*- coding: utf-8 -*-
import os
import time
import base64
import hmac
import hashlib
import json
import uuid
import aiohttp
import urllib.parse
from dataclasses import dataclass, field
from typing import Dict, Tuple, Optional, Any, List
from abc import ABC, abstractmethod
from src.engine.utils.telemetry import get_logger

logger = get_logger(__name__)

# --- DTO Definitions ---

@dataclass(frozen=True)
class PositionBalanceDTO:
    symbol: str
    quantity: float
    avg_buy_price: float
    metadata: Dict[str, Any] = field(default_factory=dict)

@dataclass(frozen=True)
class OpenOrderDTO:
    order_id: str
    symbol: str
    side: str  # 'BUY' or 'SELL'
    price: float
    quantity: float
    remaining_quantity: float
    status: str  # e.g., 'open', 'partially_filled'
    ordered_at: float
    metadata: Dict[str, Any] = field(default_factory=dict)

@dataclass(frozen=True)
class AccountSnapshotDTO:
    exchange_id: str
    available_cash: float
    positions: Tuple[PositionBalanceDTO, ...]
    open_orders: Tuple[OpenOrderDTO, ...] = ()
    fetched_at_ms: int = 0
    t_plus_one_cash: Optional[float] = None
    t_plus_two_cash: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

# --- Seam (Interface) ---

class ExchangeAccountAdapter(ABC):
    """
    거래소로부터 계좌 잔고 및 미체결 주문 현황을 조회하고 정규화(DTO 규격화)하는 추상 어댑터입니다.
    """
    @abstractmethod
    async def fetch_snapshot(self) -> AccountSnapshotDTO:
        """
        거래소 API를 직접 호출하여 계좌의 최신 스냅샷 정보를 정규화된 DTO로 반환합니다.
        """
        pass

# --- Concrete Adapters ---

class UpbitAccountAdapter(ExchangeAccountAdapter):
    """
    업비트 거래소 계정의 잔고 및 미체결 주문 현황을 조회하여 DTO로 정규화하는 어댑터입니다.
    """
    def __init__(self, access_key: str, secret_key: str, api_url: str = 'https://api.upbit.com'):
        if not access_key or not secret_key:
            raise ValueError("[UpbitAccountAdapter] API keys are missing.")
        self.access_key = access_key
        self.secret_key = secret_key
        self.api_url = api_url.rstrip('/')
        self.upbit_v1_url = self.api_url if self.api_url.endswith('/v1') else f"{self.api_url}/v1"

    def _create_jwt(self, query_hash: Optional[str] = None) -> str:
        header = {"alg": "HS256", "typ": "JWT"}
        payload = {
            "access_key": self.access_key,
            "nonce": str(uuid.uuid4())
        }
        if query_hash:
            payload["query_hash"] = query_hash
            payload["query_hash_alg"] = "SHA512"
            
        def base64url(b):
            return base64.urlsafe_b64encode(b).decode('utf-8').replace('=', '')
            
        header_b64 = base64url(json.dumps(header, separators=(',', ':')).encode('utf-8'))
        payload_b64 = base64url(json.dumps(payload, separators=(',', ':')).encode('utf-8'))
        signing_input = f"{header_b64}.{payload_b64}"
        
        sig = hmac.new(
            self.secret_key.encode('utf-8'),
            signing_input.encode('utf-8'),
            hashlib.sha256
        ).digest()
        return f"{signing_input}.{base64url(sig)}"

    async def fetch_snapshot(self) -> AccountSnapshotDTO:
        # 1. 잔고 조회
        accounts_data = []
        token = self._create_jwt()
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json"
        }
        timeout = aiohttp.ClientTimeout(total=5.0)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(f"{self.upbit_v1_url}/accounts", headers=headers) as resp:
                if resp.status == 200:
                    accounts_data = await resp.json()
                else:
                    err_txt = await resp.text()
                    logger.error(f"[UpbitAccountAdapter] Accounts API error: {resp.status} - {err_txt}")
                    raise ValueError(f"Upbit Accounts API error: {err_txt}")

            # 2. 미체결 주문 조회 (wait)
            open_orders_data = []
            params = {"state": "wait"}
            query_string = urllib.parse.urlencode(params).encode("utf-8")
            m = hashlib.sha512()
            m.update(query_string)
            query_hash = m.hexdigest()
            
            wait_token = self._create_jwt(query_hash=query_hash)
            wait_headers = {
                "Authorization": f"Bearer {wait_token}",
                "Accept": "application/json"
            }
            async with session.get(f"{self.upbit_v1_url}/orders", params=params, headers=wait_headers) as resp:
                if resp.status == 200:
                    open_orders_data = await resp.json()
                else:
                    err_txt = await resp.text()
                    logger.error(f"[UpbitAccountAdapter] Open orders API error: {resp.status} - {err_txt}")
                    raise ValueError(f"Upbit Open Orders API error: {err_txt}")

        available_cash = 0.0
        positions = []
        for a in accounts_data:
            currency = a['currency']
            balance = float(a['balance']) + float(a['locked'])
            avg_buy_price = float(a['avg_buy_price'])
            if currency == 'KRW':
                available_cash = balance
            else:
                if balance > 0:
                    positions.append(PositionBalanceDTO(
                        symbol=currency.upper(),
                        quantity=balance,
                        avg_buy_price=avg_buy_price,
                        metadata=a
                    ))

        open_orders = []
        for o in open_orders_data:
            side = 'BUY' if o.get('side') == 'bid' else 'SELL'
            market = o.get('market', '')
            symbol = market.replace("KRW-", "").upper() if market.startswith("KRW-") else market
            vol = float(o.get('volume') or 0.0)
            exec_vol = float(o.get('executed_volume') or 0.0)
            rem_qty = vol - exec_vol
            
            ordered_at_ms = 0
            try:
                from datetime import datetime
                dt = datetime.fromisoformat(o.get('created_at'))
                ordered_at_ms = int(dt.timestamp() * 1000)
            except Exception:
                pass

            open_orders.append(OpenOrderDTO(
                order_id=o.get('uuid'),
                symbol=symbol,
                side=side,
                price=float(o.get('price') or 0.0),
                quantity=vol,
                remaining_quantity=rem_qty,
                status='open' if exec_vol == 0 else 'partially_filled',
                ordered_at=ordered_at_ms,
                metadata=o
            ))

        return AccountSnapshotDTO(
            exchange_id='upbit',
            available_cash=available_cash,
            positions=tuple(positions),
            open_orders=tuple(open_orders),
            fetched_at_ms=int(time.time() * 1000)
        )


class BithumbAccountAdapter(ExchangeAccountAdapter):
    """
    빗썸 거래소 계정의 잔고 및 미체결 주문 현황을 조회하여 DTO로 정규화하는 어댑터입니다.
    """
    def __init__(self, access_key: str, secret_key: str, api_url: str = 'https://api.bithumb.com'):
        if not access_key or not secret_key:
            raise ValueError("[BithumbAccountAdapter] API keys are missing.")
        self.access_key = access_key
        self.secret_key = secret_key
        self.api_url = api_url.rstrip('/')
        self.bithumb_v1_url = self.api_url if self.api_url.endswith('/v1') else f"{self.api_url}/v1"

    def _create_jwt(self, query_hash: Optional[str] = None) -> str:
        header = {"alg": "HS256", "typ": "JWT"}
        payload = {
            "access_key": self.access_key,
            "nonce": str(uuid.uuid4()),
            "timestamp": int(time.time() * 1000)
        }
        if query_hash:
            payload["query_hash"] = query_hash
            payload["query_hash_alg"] = "SHA512"
            
        def base64url(b):
            return base64.urlsafe_b64encode(b).decode('utf-8').replace('=', '')
            
        header_b64 = base64url(json.dumps(header, separators=(',', ':')).encode('utf-8'))
        payload_b64 = base64url(json.dumps(payload, separators=(',', ':')).encode('utf-8'))
        signing_input = f"{header_b64}.{payload_b64}"
        
        sig = hmac.new(
            self.secret_key.encode('utf-8'),
            signing_input.encode('utf-8'),
            hashlib.sha256
        ).digest()
        return f"{signing_input}.{base64url(sig)}"

    async def fetch_snapshot(self) -> AccountSnapshotDTO:
        # 1. 잔고 조회
        accounts_data = []
        token = self._create_jwt()
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json"
        }
        timeout = aiohttp.ClientTimeout(total=5.0)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(f"{self.bithumb_v1_url}/accounts", headers=headers) as resp:
                if resp.status == 200:
                    accounts_data = await resp.json()
                else:
                    err_txt = await resp.text()
                    logger.error(f"[BithumbAccountAdapter] Accounts API error: {resp.status} - {err_txt}")
                    raise ValueError(f"Bithumb Accounts API error: {err_txt}")

            # 2. 미체결 주문 조회 (wait)
            open_orders_data = []
            params = {"state": "wait"}
            query_string = urllib.parse.urlencode(params).encode("utf-8")
            m = hashlib.sha512()
            m.update(query_string)
            query_hash = m.hexdigest()
            
            wait_token = self._create_jwt(query_hash=query_hash)
            wait_headers = {
                "Authorization": f"Bearer {wait_token}",
                "Accept": "application/json"
            }
            async with session.get(f"{self.bithumb_v1_url}/orders", params=params, headers=wait_headers) as resp:
                if resp.status == 200:
                    open_orders_data = await resp.json()
                else:
                    err_txt = await resp.text()
                    logger.error(f"[BithumbAccountAdapter] Open orders API error: {resp.status} - {err_txt}")
                    raise ValueError(f"Bithumb Open Orders API error: {err_txt}")

        available_cash = 0.0
        positions = []
        for a in accounts_data:
            currency = a['currency']
            balance = float(a['balance']) + float(a['locked'])
            avg_buy_price = float(a['avg_buy_price'])
            if currency == 'KRW':
                available_cash = balance
            else:
                if balance > 0:
                    positions.append(PositionBalanceDTO(
                        symbol=currency.upper(),
                        quantity=balance,
                        avg_buy_price=avg_buy_price,
                        metadata=a
                    ))

        open_orders = []
        for o in open_orders_data:
            side = 'BUY' if o.get('side') == 'bid' else 'SELL'
            market = o.get('market', '')
            symbol = market.replace("KRW-", "").upper() if market.startswith("KRW-") else market
            vol = float(o.get('volume') or 0.0)
            exec_vol = float(o.get('executed_volume') or 0.0)
            rem_qty = vol - exec_vol
            
            ordered_at_ms = 0
            try:
                from datetime import datetime
                dt = datetime.fromisoformat(o.get('created_at'))
                ordered_at_ms = int(dt.timestamp() * 1000)
            except Exception:
                pass

            open_orders.append(OpenOrderDTO(
                order_id=o.get('uuid'),
                symbol=symbol,
                side=side,
                price=float(o.get('price') or 0.0),
                quantity=vol,
                remaining_quantity=rem_qty,
                status='open' if exec_vol == 0 else 'partially_filled',
                ordered_at=ordered_at_ms,
                metadata=o
            ))

        return AccountSnapshotDTO(
            exchange_id='bithumb',
            available_cash=available_cash,
            positions=tuple(positions),
            open_orders=tuple(open_orders),
            fetched_at_ms=int(time.time() * 1000)
        )


class KisAccountAdapter(ExchangeAccountAdapter):
    """
    한국투자증권(KIS) 국내주식 계정의 잔고 및 미체결 주문 현황을 조회하여 DTO로 정규화하는 어댑터입니다.
    """
    def __init__(self, app_key: str, app_secret: str, account_no: str, cred_provider, api_url: str = 'https://openapi.koreainvestment.com:9443'):
        if not app_key or not app_secret or not account_no:
            raise ValueError("[KisAccountAdapter] Credentials or account details are missing.")
        self.app_key = app_key
        self.app_secret = app_secret
        self.account_no = str(account_no).strip()
        self.cred_provider = cred_provider
        self.api_url = api_url.rstrip('/')
        
        # 계좌번호 분할 처리
        if '-' in self.account_no:
            self.cano, self.acnt_prdt_cd = self.account_no.split('-', 1)
        else:
            self.cano = self.account_no[:8]
            self.acnt_prdt_cd = self.account_no[8:]
        if not self.acnt_prdt_cd:
            self.acnt_prdt_cd = "01"

    async def fetch_snapshot(self) -> AccountSnapshotDTO:
        token = await self.cred_provider.get_kis_access_token()
        if not token:
            raise ValueError("[KisAccountAdapter] Failed to acquire KIS access token.")

        is_vts = "openapivts" in self.api_url
        
        # 1. KIS 잔고 조회 (inquire-balance)
        # 실전: TTTC8434R / 모의: VTTC8434R
        balance_tr_id = "VTTC8434R" if is_vts else "TTTC8434R"
        
        headers = {
            "content-type": "application/json",
            "authorization": f"Bearer {token}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "tr_id": balance_tr_id,
            "custtype": "P"
        }
        
        params = {
            "CANO": self.cano,
            "ACNT_PRDT_CD": self.acnt_prdt_cd,
            "AFHR_FLPR_YN": "N",
            "OFL_YN": "",
            "INQR_DVSN": "02",
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "01",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": ""
        }

        accounts_output1 = []
        accounts_output2 = {}

        timeout = aiohttp.ClientTimeout(total=5.0)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(f"{self.api_url}/uapi/domestic-stock/v1/trading/inquire-balance", headers=headers, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get('rt_cd') == '0':
                        accounts_output1 = data.get('output1', [])
                        output2_list = data.get('output2', [])
                        if output2_list:
                            accounts_output2 = output2_list[0]
                    else:
                        raise ValueError(f"KIS Balance API error: {data.get('msg1')}")
                else:
                    err_txt = await resp.text()
                    raise ValueError(f"KIS Balance API HTTP error: {resp.status} - {err_txt}")

            # 2. KIS 미체결 주문 내역 조회 (inquire-daily-ccld)
            # 실전: TTTC0081R / 모의: VTTC0081R
            # 3개월 이내 내역 조회를 통해 오늘 미체결 주문들을 파싱합니다.
            ccld_tr_id = "VTTC0081R" if is_vts else "TTTC0081R"
            ccld_headers = headers.copy()
            ccld_headers["tr_id"] = ccld_tr_id
            
            from datetime import datetime, timedelta
            today_str = datetime.now().strftime("%Y%m%d")
            start_dt = (datetime.now() - timedelta(days=2)).strftime("%Y%m%d") # 최근 2일 이내의 미체결 건 조회
            
            ccld_params = {
                "CANO": self.cano,
                "ACNT_PRDT_CD": self.acnt_prdt_cd,
                "INQR_STRT_DT": start_dt,
                "INQR_END_DT": today_str,
                "SLL_BUY_DVSN_CD": "00",
                "INQR_DVSN": "00",
                "PDNO": "",
                "CCLD_DVSN": "02",  # '02'는 미체결만 조회
                "ORD_GNO_BRNO": "",
                "ODNO": "",
                "INQR_DVSN_3": "00",
                "INQR_DVSN_1": "",
                "CTX_AREA_FK100": "",
                "CTX_AREA_NK100": ""
            }

            open_orders_output = []
            async with session.get(f"{self.api_url}/uapi/domestic-stock/v1/trading/inquire-daily-ccld", headers=ccld_headers, params=ccld_params) as resp:
                if resp.status == 200:
                    ccld_data = await resp.json()
                    if ccld_data.get('rt_cd') == '0':
                        open_orders_output = ccld_data.get('output1', [])
                    else:
                        logger.warning(f"[KisAccountAdapter] Failed to fetch open orders: {ccld_data.get('msg1')}")
                else:
                    err_txt = await resp.text()
                    logger.warning(f"[KisAccountAdapter] Open orders HTTP error: {resp.status} - {err_txt}")

        # 정규화
        # dnca_tot_amt: 예수금총액 (가용현금)
        available_cash = float(accounts_output2.get('dnca_tot_amt', 0))
        
        # d2_auto_tr_amt: D+2 외화/원화 자동이체 대상금액 등을 종합하여 KIS는 d2_auto_tr_amt 또는 prvs_rcvb_amt 등을 참고
        # t_plus_one_cash: D+1 정산금액, t_plus_two_cash: D+2 정산금액(sbst_amt_tot_amt 또는 prvs_rcvb_amt 바탕 산출)
        # KIS는 'output2'에 d1_settle_amt_brk, d2_settle_amt_brk 등의 값을 내려줍니다.
        t_plus_one = float(accounts_output2.get('d1_settle_amt_brk', 0)) if accounts_output2 else None
        t_plus_two = float(accounts_output2.get('d2_settle_amt_brk', 0)) if accounts_output2 else None

        positions = []
        for item in accounts_output1:
            qty = float(item.get('hldg_qty', 0))
            if qty <= 0:
                continue
            pdno = item.get('pdno', '').strip().lstrip('A')
            avg_price = float(item.get('pchs_avg_pric', 0))
            positions.append(PositionBalanceDTO(
                symbol=pdno,
                quantity=qty,
                avg_buy_price=avg_price,
                metadata=item
            ))

        open_orders = []
        for o in open_orders_output:
            ord_qty = float(o.get('ord_qty') or 0.0)
            ccld_qty = float(o.get('tot_ccld_qty') or 0.0)
            cncl_qty = float(o.get('cncl_cfrm_qty') or 0.0)
            rem_qty = ord_qty - ccld_qty - cncl_qty
            
            if rem_qty <= 0:
                continue

            odno = o.get('odno')
            pdno = o.get('pdno', '').strip().lstrip('A')
            # sll_buy_dvsn_cd: '01' 매도 / '02' 매수
            side = 'SELL' if o.get('sll_buy_dvsn_cd') == '01' else 'BUY'
            price = float(o.get('ord_unpr') or 0.0)
            
            # 주문시각 파싱 (ord_tmd: 'HHMMSS')
            ordered_at_ms = 0
            try:
                ord_dt = o.get('ord_dt') or today_str  # 'YYYYMMDD'
                ord_tmd = o.get('ord_tmd') or '000000' # 'HHMMSS'
                # struct_time 변환
                dt_str = f"{ord_dt} {ord_tmd}"
                dt_obj = datetime.strptime(dt_str, "%Y%m%d %H%M%S")
                ordered_at_ms = int(dt_obj.timestamp() * 1000)
            except Exception:
                pass

            open_orders.append(OpenOrderDTO(
                order_id=odno,
                symbol=pdno,
                side=side,
                price=price,
                quantity=ord_qty,
                remaining_quantity=rem_qty,
                status='open' if ccld_qty == 0 else 'partially_filled',
                ordered_at=ordered_at_ms,
                metadata=o
            ))

        return AccountSnapshotDTO(
            exchange_id='kis',
            available_cash=available_cash,
            positions=tuple(positions),
            open_orders=tuple(open_orders),
            fetched_at_ms=int(time.time() * 1000),
            t_plus_one_cash=t_plus_one,
            t_plus_two_cash=t_plus_two,
            metadata=accounts_output2 if accounts_output2 else {}
        )
