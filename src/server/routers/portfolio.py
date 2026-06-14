from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from typing import Dict, Any, List, Optional
from src.engine.command import UserCommand
from datetime import datetime
import aiohttp
import asyncio
import time
import json
from src.database.connection import get_db_conn
from src.server.websocket import manager
from src.engine.utils.telemetry import get_logger
from src.engine.utils.stock_mapper import stock_mapper

logger = get_logger(__name__)
router = APIRouter()

class StartPortfolioRequest(BaseModel):
    initial_cash: Any
    strategies: Optional[Dict[str, Any]] = None

@router.get("/api/portfolios")
async def list_portfolios(request: Request):
    """관리 중인 모든 실시간 모의투자 포트폴리오 목록을 반환합니다."""
    system = request.app.state.system
    await system.portfolio_manager.load_from_db()
    
    ports = []
    for p in system.portfolio_manager.portfolios.values():
        if p.portfolio_type == 'simulation':
            ports.append({
                "id": p.id,
                "name": p.name,
                "cash": p.cash,
                "type": p.portfolio_type,
                "created_at": p.created_at,
                "ended_at": p.ended_at
            })
            
    ports.sort(key=lambda x: x["id"], reverse=True)
    return ports

@router.get("/api/portfolio")
async def get_portfolio(request: Request, portfolio_id: str = "default"):
    """포트폴리오의 현재 상태(잔고, 포지션, 수익률) 및 분석 보고서 데이터를 반환합니다. (통합 리포트 빌더 적용)"""
    system = request.app.state.system
    await system.portfolio_manager.load_from_db()
    
    # default인 경우 활성 포트폴리오 탐색
    if portfolio_id == "default" or not portfolio_id:
        active_p = system.portfolio_manager.get_active_simulation_portfolio()
        if active_p:
            portfolio_id = active_p.id
        else:
            portfolio_id = ""

    return await system.portfolio_manager.get_portfolio_report_data(portfolio_id, system)


@router.post("/api/portfolio/start")
async def start_portfolio_session(req: StartPortfolioRequest, request: Request):
    """새로운 실시간 모의투자 세션을 시작합니다."""
    system = request.app.state.system
    try:
        res = await system.dispatcher.dispatch(
            UserCommand.PORTFOLIO_START,
            {
                "initial_cash": req.initial_cash,
                "strategies": req.strategies
            }
        )
        return {"status": "success", "portfolio_id": res["portfolio_id"], "name": res["name"]}
    except Exception as e:
        logger.error(f"Start portfolio session error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/api/portfolio/{portfolio_id}/end")
async def end_portfolio_session(portfolio_id: str, request: Request):
    """보유 포지션 청산 없이 실시간 모의투자 세션을 마감(동결) 처리합니다."""
    system = request.app.state.system
    try:
        await system.dispatcher.dispatch(
            UserCommand.PORTFOLIO_END,
            {"portfolio_id": portfolio_id}
        )
        return {"status": "success", "message": "모의투자 세션이 정상적으로 마감되었습니다."}
    except Exception as e:
        logger.error(f"End portfolio session error: {e}")
        raise HTTPException(status_code=500, detail=str(e))



@router.delete("/api/portfolio/history/{portfolio_id}")
async def delete_portfolio_history(portfolio_id: str, request: Request):
    """특정 마각된 모의투자 또는 과거 백테스트 이력 세션을 DB와 메모리에서 삭제합니다."""
    system = request.app.state.system
    db_path = system.portfolio_manager.db_path
    
    async with get_db_conn(db_path) as db:
        cursor = await db.execute("""
            DELETE FROM portfolios 
            WHERE id = ? AND (type = 'backtest' OR (type = 'simulation' AND ended_at IS NOT NULL))
        """, (portfolio_id,))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=400, detail="삭제할 포트폴리오를 찾을 수 없습니다. (가동 중이거나 존재하지 않음)")
        await db.commit()
        
    # 메모리 캐시에서 제거
    system.portfolio_manager.portfolios.pop(portfolio_id, None)
    return {"status": "success", "message": "이력이 정상적으로 삭제되었습니다."}

@router.delete("/api/portfolio/history")
async def delete_all_portfolio_history(request: Request):
    """모든 종료된 모의투자 및 과거 백테스트 이력을 DB와 메모리에서 일괄 영구 삭제합니다."""
    system = request.app.state.system
    db_path = system.portfolio_manager.db_path
    
    async with get_db_conn(db_path) as db:
        await db.execute("""
            DELETE FROM portfolios 
            WHERE type = 'backtest' OR (type = 'simulation' AND ended_at IS NOT NULL)
        """)
        await db.commit()
        
    to_delete = [
        pid for pid, p in system.portfolio_manager.portfolios.items()
        if getattr(p, 'portfolio_type', 'simulation') == 'backtest' or
           (getattr(p, 'portfolio_type', 'simulation') == 'simulation' and getattr(p, 'ended_at', None) is not None)
    ]
    for pid in to_delete:
        system.portfolio_manager.portfolios.pop(pid, None)
        
    return {"status": "success", "message": "모든 이력이 성공적으로 삭제되었습니다."}


@router.get("/trades")
async def get_trades(request: Request, exchange_id: str = "upbit", symbol: str = "BTC", limit: int = 10):
    """최근 체결 데이터를 DB에서 조회하여 반환합니다."""
    system = request.app.state.system
    async with get_db_conn(system.portfolio_manager.db_path) as db:
        async with db.execute("SELECT trade_price, trade_volume, ask_bid, trade_timestamp FROM trades WHERE exchange_id = ? AND symbol = ? ORDER BY trade_timestamp DESC LIMIT ?", (exchange_id, symbol, limit)) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


# --- 🔐 Upbit Real-Time Wallet Assets API Integration ---
import base64
import hmac
import hashlib
import json
import uuid
import os

def _create_upbit_jwt(access_key, secret_key, query_hash=None):
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "access_key": access_key,
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
        secret_key.encode('utf-8'),
        signing_input.encode('utf-8'),
        hashlib.sha256
    ).digest()
    return f"{signing_input}.{base64url(sig)}"

def _create_bithumb_jwt(access_key, secret_key, query_hash=None):
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "access_key": access_key,
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
        secret_key.encode('utf-8'),
        signing_input.encode('utf-8'),
        hashlib.sha256
    ).digest()
    return f"{signing_input}.{base64url(sig)}"

async def _sync_real_orders(access_key: str, secret_key: str, api_url: str, force_sync: bool = False):
    """
    업비트 거래소로부터 실제 완료된 거래 내역을 긁어와 로컬 DB real_orders에 누적 저장합니다.
    - 기록이 아예 없는 경우: page=1부터 루프를 돌아 전체 주문을 긁어오는 Full Backfill 수행.
    - 기록이 있고 force_sync=True인 경우: 최근 1페이지(최대 100건)만 수동 업데이트(Incremental Sync).
    - 기록이 있고 force_sync=False인 경우: 아무 것도 하지 않고 스킵(평시 페이지 진입용).
    """
    import hashlib
    import uuid
    import base64
    import hmac
    import os
    import urllib.parse

    # 1. 로컬 DB에 업비트 거래 기록이 이미 있는지 조회 및 미체결(wait) 주문 감지
    has_records = False
    has_pending = False
    async with get_db_conn() as db:
        async with db.execute("SELECT 1 FROM real_orders WHERE exchange_id = 'upbit' LIMIT 1") as cursor:
            row = await cursor.fetchone()
            if row:
                has_records = True
        async with db.execute("SELECT 1 FROM real_orders WHERE exchange_id = 'upbit' AND state = 'wait' LIMIT 1") as cursor:
            row = await cursor.fetchone()
            if row:
                has_pending = True

    # 평시 페이지 진입인데 이미 이력이 있고 대기 중인 주문도 없다면 스킵
    if has_records and not has_pending and not force_sync:
        return

    # 루프를 돌면서 주문 내역 수집
    page = 1
    limit = 100
    base_url = api_url.rstrip('/')
    upbit_v1_url = base_url if base_url.endswith('/v1') else f"{base_url}/v1"
    
    headers = {
        "Accept": "application/json"
    }

    async with aiohttp.ClientSession() as session:
        for state_val in ["done", "cancel"]:
            page = 1
            while True:
                params = [
                    ("state", state_val),
                    ("limit", str(limit)),
                    ("page", str(page))
                ]
                
                query_string = urllib.parse.urlencode(params).encode("utf-8")
                
                m = hashlib.sha512()
                m.update(query_string)
                query_hash = m.hexdigest()
                
                token = _create_upbit_jwt(access_key, secret_key, query_hash=query_hash)
                headers["Authorization"] = f"Bearer {token}"
                
                target_url = f"{upbit_v1_url}/orders?{query_string.decode('utf-8')}"
                
                try:
                    async with session.get(target_url, headers=headers) as resp:
                        if resp.status != 200:
                            err_txt = await resp.text()
                            logger.error(f"Upbit API error during order sync ({state_val}): {resp.status} - {err_txt}")
                            break
                        
                        orders = await resp.json()
                        if not orders:
                            break  # 더 이상 주문이 없으면 루프 종료
                        
                        # 로컬 DB에 적재
                        async with get_db_conn() as db:
                            for o in orders:
                                # 취소된 주문 중 체결 수량이 0인 완전 미체결 주문은 스킵
                                if o.get("state") == "cancel" and float(o.get("executed_volume") or 0.0) == 0.0:
                                    continue
                                    
                                market = o.get("market", "")
                                symbol = market.replace("KRW-", "").upper() if market.startswith("KRW-") else market
                                created_at = o.get("created_at")
                                
                                # SQLite의 ON CONFLICT DO UPDATE 문법을 사용해 이미 Ignore 된 가격이 0.0인 레코드도 올바르게 보정
                                await db.execute('''
                                    INSERT INTO real_orders 
                                    (exchange_id, uuid, symbol, side, price, volume, executed_volume, fee, state, created_at)
                                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                    ON CONFLICT(uuid) DO UPDATE SET
                                        price = excluded.price,
                                        volume = excluded.volume,
                                        executed_volume = excluded.executed_volume,
                                        fee = excluded.fee,
                                        state = excluded.state
                                ''', (
                                    'upbit',
                                    o.get("uuid"),
                                    symbol,
                                    "BUY" if o.get("side") == "bid" else "SELL",
                                    float(o.get("avg_price") or o.get("price") or 0.0),
                                    float(o.get("volume") or 0.0),
                                    float(o.get("executed_volume") or 0.0),
                                    float(o.get("paid_fee") or 0.0),
                                    o.get("state"),
                                    created_at
                                ))
                            await db.commit()
                            
                        # 만약 Full Backfill이 아니라 Incremental Sync (최근 1페이지) 라면 한 페이지만 받고 루프 강제 탈출
                        if has_records and force_sync:
                            break
                            
                        # 다음 페이지로
                        page += 1
                        await asyncio.sleep(0.05)
                except Exception as e:
                    logger.error(f"Failed to fetch order page {page} for state {state_val}: {e}")
                    break

@router.get("/api/exchanges/upbit/assets")
async def get_upbit_assets(request: Request, mode: str = "active", sync: bool = False):
    """업비트 실제 잔고를 조회하고 실시간 시세를 반영하여 평가금액이 높은 순서대로 정렬해 반환합니다. (처분 완료 자산 필터 지원)"""
    import os
    import hashlib
    # Real-time .env reloading helper to capture updates without uvicorn restart
    from pathlib import Path
    root_dir = Path(__file__).resolve().parents[3]
    env_path = root_dir / '.env'
    if env_path.exists():
        with open(env_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                if '=' in line:
                    k, v = line.split('=', 1)
                    os.environ[k.strip()] = v.strip()

    access_key = os.getenv("UPBIT_ACCESS_KEY")
    secret_key = os.getenv("UPBIT_SECRET_KEY")
    
    if not access_key or not secret_key or "your_access_key" in access_key:
        raise HTTPException(status_code=400, detail="업비트 API 키가 설정되지 않았습니다. .env 파일을 확인해 주세요.")
        
    system = request.app.state.system
    api_url = system.config_manager.get('exchanges.upbit.api_url', 'https://api.upbit.com')
    
    try:
        # 실거래 주문 이력 동기화 기동 (최초 풀 백필 또는 수동 갱신 요청 또는 처분 완료 자산 조회 시)
        try:
            await _sync_real_orders(access_key, secret_key, api_url, force_sync=(sync or mode == "liquidated"))
        except Exception as sync_err:
            logger.error(f"Failed to sync real orders: {sync_err}")

        # 1. 업비트 잔고 조회
        token = _create_upbit_jwt(access_key, secret_key)
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json"
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{api_url}/accounts", headers=headers) as resp:
                if resp.status != 200:
                    err_txt = await resp.text()
                    raise HTTPException(status_code=resp.status, detail=f"업비트 API 오류: {err_txt}")
                accounts = await resp.json()
                
            if not accounts:
                return {"total_eval_value": 0, "formatted_total_value": "0", "assets": []}
                
            valid_krw_markets = {f"KRW-{k}" for k in stock_mapper.get_active_symbols('upbit')}

            if mode == "liquidated":
                # [Liquidated Mode] 처분 완료 자산 (현재 잔고는 0 이나 과거 거래가 있었던 자산)
                # 보유 중인 코인 목록
                active_set = {a['currency'].upper() for a in accounts if float(a['balance']) + float(a['locked']) > 0}
                
                # 로컬 DB real_orders에서 거래 이력이 있는 코인 조회
                traded_coins = []
                sell_info = {}
                async with get_db_conn() as db:
                    async with db.execute(
                        "SELECT DISTINCT symbol FROM real_orders WHERE exchange_id = 'upbit' AND (state = 'done' OR (state = 'cancel' AND executed_volume > 0))"
                    ) as cursor:
                        rows = await cursor.fetchall()
                        traded_coins = [r['symbol'].upper() for r in rows]

                    # 각 코인별 가장 최근의 매도 완료/부분체결 취소 체결 데이터 조회
                    query = """
                        SELECT r.symbol, r.price, r.executed_volume
                        FROM real_orders r
                        INNER JOIN (
                            SELECT symbol, MAX(created_at) as max_created_at
                            FROM real_orders
                            WHERE exchange_id = 'upbit' AND side = 'SELL' AND (state = 'done' OR (state = 'cancel' AND executed_volume > 0))
                            GROUP BY symbol
                        ) temp ON r.symbol = temp.symbol AND r.created_at = temp.max_created_at
                        WHERE r.exchange_id = 'upbit' AND r.side = 'SELL' AND (r.state = 'done' OR (r.state = 'cancel' AND r.executed_volume > 0))
                    """
                    async with db.execute(query) as cursor:
                        rows = await cursor.fetchall()
                        for r in rows:
                            sym = r['symbol'].upper()
                            sell_info[sym] = {
                                "price": float(r['price'] or 0.0),
                                "volume": float(r['executed_volume'] or 0.0)
                            }

                # 처분된 코인 목록 도출 (KRW 제외)
                liquidated_coins = sorted(list((set(traded_coins) - active_set) - {"KRW"}))

                prices = {}
                coin_symbols = [f"KRW-{c}" for c in liquidated_coins if f"KRW-{c}" in valid_krw_markets]
                if coin_symbols:
                    for i in range(0, len(coin_symbols), 100):
                        batch = ','.join(coin_symbols[i:i+100])
                        async with session.get(f"{api_url}/ticker?markets={batch}") as resp:
                            if resp.status == 200:
                                tickers = await resp.json()
                                for t in tickers:
                                    prices[t['market']] = float(t['trade_price'])

                asset_list = []
                for currency in liquidated_coins:
                    symbol = f"KRW-{currency}"
                    if symbol in valid_krw_markets:
                        current_price = prices.get(symbol, 0.0)
                    else:
                        current_price = 0.0
                    
                    korean_name = stock_mapper.get_name('upbit', currency)
                    
                    # 최종 매각 정보 조회 (기본값은 0.0)
                    info = sell_info.get(currency.upper(), {"price": 0.0, "volume": 0.0})
                    sell_price = info["price"]
                    sell_volume = info["volume"]
                    sell_value = sell_price * sell_volume
                    
                    asset_list.append({
                        "currency": currency,
                        "korean_name": korean_name,
                        "balance": 0.0,
                        "avg_buy_price": sell_price,
                        "current_price": current_price,
                        "eval_value": sell_value,
                        "formatted_eval_value": f"{int(sell_value):,}" if sell_value >= 1.0 else f"{sell_value:.4f}",
                        "percent": 0.0,
                        "exchange_id": "upbit"
                    })
                
                return {
                    "total_eval_value": 0.0,
                    "formatted_total_value": "0",
                    "assets": asset_list
                }
            else:
                # [Active Mode] 보유 중인 자산 조회 (KRW/BTC 마켓 자동 환산)
                all_markets = []
                async with session.get(f"{api_url}/market/all") as m_resp:
                    if m_resp.status == 200:
                        all_markets = await m_resp.json()
                        
                krw_supported = {m['market'].replace("KRW-", "") for m in all_markets if m['market'].startswith("KRW-")}
                btc_supported = {m['market'].replace("BTC-", "") for m in all_markets if m['market'].startswith("BTC-")}

                # 보유 코인들에 대한 조회 대상 마켓 리스트 빌드 (BTC 마켓 종목 원화 환산용 KRW-BTC 강제 포함)
                query_markets = ["KRW-BTC"]
                for a in accounts:
                    currency = a['currency']
                    if currency == 'KRW':
                        continue
                    if currency in krw_supported:
                        query_markets.append(f"KRW-{currency}")
                    elif currency in btc_supported:
                        query_markets.append(f"BTC-{currency}")

                query_markets = list(set(query_markets))
                
                prices = {}
                if query_markets:
                    for i in range(0, len(query_markets), 100):
                        batch = ','.join(query_markets[i:i+100])
                        async with session.get(f"{api_url}/ticker?markets={batch}") as resp:
                            if resp.status == 200:
                                tickers = await resp.json()
                                for t in tickers:
                                    prices[t['market']] = float(t['trade_price'])
                                    
                btc_krw_price = prices.get("KRW-BTC", 0.0)
                asset_list = []
                total_eval_value = 0.0
                
                for a in accounts:
                    currency = a['currency']
                    balance = float(a['balance']) + float(a['locked'])
                    avg_buy_price = float(a['avg_buy_price'])
                    
                    if balance <= 0:
                        continue
                    
                    if currency == 'KRW':
                        current_price = 1.0
                        balance = int(balance)
                        eval_value = balance
                        korean_name = "원화"
                    else:
                        if currency in krw_supported:
                            symbol = f"KRW-{currency}"
                            current_price = prices.get(symbol, avg_buy_price)
                            eval_value = balance * current_price
                        elif currency in btc_supported:
                            symbol = f"BTC-{currency}"
                            btc_price = prices.get(symbol, 0.0)
                            current_price = btc_price * btc_krw_price
                            eval_value = balance * current_price
                            # 업비트 avg_buy_price는 이미 KRW 기준이므로 환산 생략
                        else:
                            current_price = 0.0
                            eval_value = 0.0
                        korean_name = stock_mapper.get_name('upbit', currency)
                            
                    total_eval_value += eval_value
                    
                    asset_list.append({
                        "currency": currency,
                        "korean_name": korean_name,
                        "balance": balance,
                        "avg_buy_price": avg_buy_price,
                        "current_price": current_price,
                        "eval_value": eval_value,
                        "formatted_eval_value": f"{int(eval_value):,}" if eval_value >= 1.0 else f"{eval_value:.4f}",
                        "exchange_id": "upbit"
                    })
                    
                for asset in asset_list:
                    asset["percent"] = round((asset["eval_value"] / total_eval_value * 100), 2) if total_eval_value > 0 else 0.0
                    
                asset_list.sort(key=lambda x: x["eval_value"], reverse=True)
                
                return {
                    "total_eval_value": total_eval_value,
                    "formatted_total_value": f"{int(total_eval_value):,}",
                    "assets": asset_list
                }
                
    except Exception as e:
        logger.error(f"Error fetching upbit assets: {e}")
        raise HTTPException(status_code=500, detail=str(e))


async def _sync_real_bithumb_orders(access_key: str, secret_key: str, api_url: str, force_sync: bool = False):
    """빗썸 거래소로부터 실제 완료된 거래 내역을 로컬 DB에 누적 저장합니다."""
    has_records = False
    has_pending = False
    async with get_db_conn() as db:
        async with db.execute("SELECT 1 FROM real_orders WHERE exchange_id = 'bithumb' LIMIT 1") as cursor:
            row = await cursor.fetchone()
            if row:
                has_records = True
        async with db.execute("SELECT 1 FROM real_orders WHERE exchange_id = 'bithumb' AND state = 'wait' LIMIT 1") as cursor:
            row = await cursor.fetchone()
            if row:
                has_pending = True

    if has_records and not has_pending and not force_sync:
        return

    page = 1
    limit = 100
    base_url = api_url.rstrip('/')
    bithumb_v1_url = base_url if base_url.endswith('/v1') else f"{base_url}/v1"
    
    headers = {
        "Accept": "application/json"
    }

    import urllib.parse
    async with aiohttp.ClientSession() as session:
        for state_val in ["done", "cancel"]:
            page = 1
            while True:
                params = [
                    ("state", state_val),
                    ("limit", str(limit)),
                    ("page", str(page))
                ]
                query_string = urllib.parse.urlencode(params).encode("utf-8")
                
                m = hashlib.sha512()
                m.update(query_string)
                query_hash = m.hexdigest()
                
                token = _create_bithumb_jwt(access_key, secret_key, query_hash=query_hash)
                headers["Authorization"] = f"Bearer {token}"
                
                target_url = f"{bithumb_v1_url}/orders?{query_string.decode('utf-8')}"
                
                try:
                    async with session.get(target_url, headers=headers) as resp:
                        if resp.status != 200:
                            err_txt = await resp.text()
                            logger.error(f"Bithumb API error during order sync ({state_val}): {resp.status} - {err_txt}")
                            break
                        
                        orders = await resp.json()
                        if not orders:
                            break
                        
                        async with get_db_conn() as db:
                            for o in orders:
                                if o.get("state") == "cancel" and float(o.get("executed_volume") or 0.0) == 0.0:
                                    continue
                                    
                                market = o.get("market", "")
                                symbol = market.replace("KRW-", "").upper() if market.startswith("KRW-") else market
                                created_at = o.get("created_at")
                                
                                await db.execute('''
                                    INSERT INTO real_orders 
                                    (exchange_id, uuid, symbol, side, price, volume, executed_volume, fee, state, created_at)
                                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                    ON CONFLICT(uuid) DO UPDATE SET
                                        price = excluded.price,
                                        volume = excluded.volume,
                                        executed_volume = excluded.executed_volume,
                                        fee = excluded.fee,
                                        state = excluded.state
                                ''', (
                                    'bithumb',
                                    o.get("uuid"),
                                    symbol,
                                    "BUY" if o.get("side") == "bid" else "SELL",
                                    float(o.get("avg_price") or o.get("price") or 0.0),
                                    float(o.get("volume") or 0.0),
                                    float(o.get("executed_volume") or 0.0),
                                    float(o.get("paid_fee") or 0.0),
                                    o.get("state"),
                                    created_at
                                ))
                            await db.commit()
                            
                        if has_records and force_sync:
                            break
                        page += 1
                        await asyncio.sleep(0.05)
                except Exception as e:
                    logger.error(f"Failed to fetch Bithumb order page {page} for state {state_val}: {e}")
                    break


async def _sync_real_kis_orders(system, force_sync: bool = False):
    """한국투자증권(KIS)으로부터 일별 주문 체결 내역을 조회해 로컬 DB에 누적 저장합니다."""
    kis_config = system.config_manager.get('exchanges.kis', {})
    kis_app_key = os.getenv("KIS_APP_KEY") or kis_config.get('app_key')
    kis_app_secret = os.getenv("KIS_APP_SECRET") or kis_config.get('app_secret')
    kis_account_no = os.getenv("KIS_ACCOUNT_NO") or kis_config.get('account_no')
    
    if not kis_app_key or not kis_app_secret or not kis_account_no:
        return

    has_records = False
    has_pending = False
    async with get_db_conn() as db:
        async with db.execute("SELECT 1 FROM real_orders WHERE exchange_id = 'kis' LIMIT 1") as cursor:
            row = await cursor.fetchone()
            if row:
                has_records = True
        async with db.execute("SELECT 1 FROM real_orders WHERE exchange_id = 'kis' AND state = 'wait' LIMIT 1") as cursor:
            row = await cursor.fetchone()
            if row:
                has_pending = True

    if has_records and not has_pending and not force_sync:
        return

    kis_account_no = str(kis_account_no).strip()
    if '-' in kis_account_no:
        cano, acnt_prdt_cd = kis_account_no.split('-', 1)
    else:
        cano = kis_account_no[:8]
        acnt_prdt_cd = kis_account_no[8:]
    if not acnt_prdt_cd:
        acnt_prdt_cd = "01"

    kis_api_url = kis_config.get('api_url', 'https://openapi.koreainvestment.com:9443').rstrip('/')
    is_vts = "openapivts" in kis_api_url
    tr_id = "VTTC0081R" if is_vts else "TTTC0081R"

    token = await system.cred_provider.get_kis_access_token()
    if not token:
        logger.error("_sync_real_kis_orders: KIS access token is missing.")
        return

    headers = {
        "content-type": "application/json",
        "authorization": f"Bearer {token}",
        "appkey": kis_app_key,
        "appsecret": kis_app_secret,
        "tr_id": tr_id,
        "custtype": "P"
    }

    today_str = datetime.now().strftime("%Y%m%d")
    start_dt = (datetime.now() - datetime.timedelta(days=7)).strftime("%Y%m%d")
    
    params = {
        "CANO": cano,
        "ACNT_PRDT_CD": acnt_prdt_cd,
        "INQR_STRT_DT": start_dt,
        "INQR_END_DT": today_str,
        "SLL_BUY_DVSN_CD": "00",
        "INQR_DVSN": "00",
        "PDNO": "",
        "CCLD_DVSN": "00",
        "ORD_GNO_BRNO": "",
        "ODNO": "",
        "INQR_DVSN_3": "00",
        "INQR_DVSN_1": "",
        "CTX_AREA_FK100": "",
        "CTX_AREA_NK100": ""
    }

    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(f"{kis_api_url}/uapi/domestic-stock/v1/trading/inquire-daily-ccld", headers=headers, params=params) as resp:
                if resp.status != 200:
                    err_txt = await resp.text()
                    logger.error(f"_sync_real_kis_orders API error: {resp.status} - {err_txt}")
                    return
                
                data = await resp.json()
                if data.get("rt_cd") != "0":
                    logger.error(f"_sync_real_kis_orders API failure: {data.get('msg1')}")
                    return
                
                output1 = data.get("output1", [])
                async with get_db_conn() as db:
                    for o in output1:
                        odno = o.get("odno")
                        if not odno:
                            continue
                        
                        pdno = o.get("pdno", "").strip().lstrip('A')
                        ord_qty = float(o.get("ord_qty") or 0.0)
                        ccld_qty = float(o.get("tot_ccld_qty") or 0.0)
                        cncl_cfrm_qty = float(o.get("cnc_cfrm_qty") or 0.0)
                        
                        if ccld_qty == ord_qty:
                            state = "done"
                        elif cncl_cfrm_qty > 0 or o.get("cncl_yn") == "Y":
                            state = "cancel"
                        else:
                            state = "wait"
                            
                        side = "BUY" if o.get("sll_buy_dvsn_cd") == "02" else "SELL"
                        price = float(o.get("avg_prvs") or o.get("ord_unpr") or 0.0)
                        
                        ord_dt = o.get("ord_dt")
                        ord_tmd = o.get("ord_tmd")
                        created_at = None
                        if ord_dt and ord_tmd:
                            created_at = f"{ord_dt[:4]}-{ord_dt[4:6]}-{ord_dt[6:8]} {ord_tmd[:2]}:{ord_tmd[2:4]}:{ord_tmd[4:6]}"
                        
                        await db.execute('''
                            INSERT INTO real_orders 
                            (exchange_id, uuid, symbol, side, price, volume, executed_volume, fee, state, created_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT(uuid) DO UPDATE SET
                                price = excluded.price,
                                volume = excluded.volume,
                                executed_volume = excluded.executed_volume,
                                fee = excluded.fee,
                                state = excluded.state
                        ''', (
                            'kis',
                            odno,
                            pdno,
                            side,
                            price,
                            ord_qty,
                            ccld_qty,
                            0.0,
                            state,
                            created_at
                        ))
                    await db.commit()
        except Exception as e:
            logger.error(f"Failed to sync KIS orders: {e}")


@router.get("/api/exchanges/bithumb/assets")
async def get_bithumb_assets(request: Request, mode: str = "active", sync: bool = False):
    """빗썸 실제 잔고 및 평가 자산 목록을 반환합니다."""
    access_key = os.getenv("BITHUMB_API_KEY")
    secret_key = os.getenv("BITHUMB_SECRET_KEY")
    
    if not access_key or not secret_key or "your_access_key" in access_key:
        raise HTTPException(status_code=400, detail="빗썸 API 키가 설정되지 않았습니다. .env 파일을 확인해 주세요.")
        
    system = request.app.state.system
    bithumb_config = system.config_manager.get('exchanges.bithumb', {})
    api_url = bithumb_config.get('api_url', 'https://api.bithumb.com').rstrip('/')
    bithumb_v1_url = api_url if api_url.endswith('/v1') else f"{api_url}/v1"
    
    try:
        try:
            await _sync_real_bithumb_orders(access_key, secret_key, bithumb_v1_url, force_sync=(sync or mode == "liquidated"))
        except Exception as sync_err:
            logger.error(f"Failed to sync real bithumb orders: {sync_err}")

        token = _create_bithumb_jwt(access_key, secret_key)
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json"
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{bithumb_v1_url}/accounts", headers=headers) as resp:
                if resp.status != 200:
                    err_txt = await resp.text()
                    raise HTTPException(status_code=resp.status, detail=f"빗썸 API 오류: {err_txt}")
                accounts = await resp.json()
                
            if not accounts:
                return {"total_eval_value": 0, "formatted_total_value": "0", "assets": []}
                
            valid_krw_markets = {f"KRW-{k}" for k in stock_mapper.get_active_symbols('bithumb')}

            if mode == "liquidated":
                active_set = {a['currency'].upper() for a in accounts if float(a['balance']) + float(a['locked']) > 0}
                traded_coins = []
                sell_info = {}
                async with get_db_conn() as db:
                    async with db.execute(
                        "SELECT DISTINCT symbol FROM real_orders WHERE exchange_id = 'bithumb' AND (state = 'done' OR (state = 'cancel' AND executed_volume > 0))"
                    ) as cursor:
                        rows = await cursor.fetchall()
                        traded_coins = [r['symbol'].upper() for r in rows]

                    query = """
                        SELECT r.symbol, r.price, r.executed_volume
                        FROM real_orders r
                        INNER JOIN (
                            SELECT symbol, MAX(created_at) as max_created_at
                            FROM real_orders
                            WHERE exchange_id = 'bithumb' AND side = 'SELL' AND (state = 'done' OR (state = 'cancel' AND executed_volume > 0))
                            GROUP BY symbol
                        ) temp ON r.symbol = temp.symbol AND r.created_at = temp.max_created_at
                        WHERE r.exchange_id = 'bithumb' AND r.side = 'SELL' AND (r.state = 'done' OR (r.state = 'cancel' AND r.executed_volume > 0))
                    """
                    async with db.execute(query) as cursor:
                        rows = await cursor.fetchall()
                        for r in rows:
                            sym = r['symbol'].upper()
                            sell_info[sym] = {
                                "price": float(r['price'] or 0.0),
                                "volume": float(r['executed_volume'] or 0.0)
                            }

                liquidated_coins = sorted(list((set(traded_coins) - active_set) - {"KRW"}))
                prices = {}
                coin_symbols = [f"KRW-{c}" for c in liquidated_coins if f"KRW-{c}" in valid_krw_markets]
                if coin_symbols:
                    for i in range(0, len(coin_symbols), 100):
                        batch = ','.join(coin_symbols[i:i+100])
                        async with session.get(f"{bithumb_v1_url}/ticker?markets={batch}") as resp:
                            if resp.status == 200:
                                tickers = await resp.json()
                                for t in tickers:
                                    prices[t['market']] = float(t['trade_price'])

                asset_list = []
                for currency in liquidated_coins:
                    symbol = f"KRW-{currency}"
                    if symbol in valid_krw_markets:
                        current_price = prices.get(symbol, 0.0)
                    else:
                        current_price = 0.0
                    
                    korean_name = stock_mapper.get_name('bithumb', currency)
                    info = sell_info.get(currency.upper(), {"price": 0.0, "volume": 0.0})
                    sell_price = info["price"]
                    sell_volume = info["volume"]
                    sell_value = sell_price * sell_volume
                    
                    asset_list.append({
                        "currency": currency,
                        "korean_name": korean_name,
                        "balance": 0.0,
                        "avg_buy_price": sell_price,
                        "current_price": current_price,
                        "eval_value": sell_value,
                        "formatted_eval_value": f"{int(sell_value):,}" if sell_value >= 1.0 else f"{sell_value:.4f}",
                        "percent": 0.0,
                        "exchange_id": "bithumb"
                    })
                
                return {
                    "total_eval_value": 0.0,
                    "formatted_total_value": "0",
                    "assets": asset_list
                }
            else:
                all_markets = []
                async with session.get(f"{bithumb_v1_url}/market/all") as m_resp:
                    if m_resp.status == 200:
                        all_markets = await m_resp.json()
                        
                krw_supported = {m['market'].replace("KRW-", "") for m in all_markets if m['market'].startswith("KRW-")}
                btc_supported = {m['market'].replace("BTC-", "") for m in all_markets if m['market'].startswith("BTC-")}

                query_markets = ["KRW-BTC"]
                for a in accounts:
                    currency = a['currency']
                    if currency == 'KRW':
                        continue
                    if currency in krw_supported:
                        query_markets.append(f"KRW-{currency}")
                    elif currency in btc_supported:
                        query_markets.append(f"BTC-{currency}")

                query_markets = list(set(query_markets))
                
                prices = {}
                if query_markets:
                    for i in range(0, len(query_markets), 100):
                        batch = ','.join(query_markets[i:i+100])
                        async with session.get(f"{bithumb_v1_url}/ticker?markets={batch}") as resp:
                            if resp.status == 200:
                                tickers = await resp.json()
                                for t in tickers:
                                    prices[t['market']] = float(t['trade_price'])
                                    
                btc_krw_price = prices.get("KRW-BTC", 0.0)
                asset_list = []
                total_eval_value = 0.0
                
                for a in accounts:
                    currency = a['currency']
                    balance = float(a['balance']) + float(a['locked'])
                    avg_buy_price = float(a['avg_buy_price'])
                    
                    if balance <= 0:
                        continue
                    
                    if currency == 'KRW':
                        current_price = 1.0
                        balance = int(balance)
                        eval_value = balance
                        korean_name = "원화"
                    else:
                        if currency in krw_supported:
                            symbol = f"KRW-{currency}"
                            current_price = prices.get(symbol, avg_buy_price)
                            eval_value = balance * current_price
                        elif currency in btc_supported:
                            symbol = f"BTC-{currency}"
                            btc_price = prices.get(symbol, 0.0)
                            current_price = btc_price * btc_krw_price
                            eval_value = balance * current_price
                        else:
                            current_price = 0.0
                            eval_value = 0.0
                        korean_name = stock_mapper.get_name('bithumb', currency)
                            
                    total_eval_value += eval_value
                    
                    asset_list.append({
                        "currency": currency,
                        "korean_name": korean_name,
                        "balance": balance,
                        "avg_buy_price": avg_buy_price,
                        "current_price": current_price,
                        "eval_value": eval_value,
                        "formatted_eval_value": f"{int(eval_value):,}" if eval_value >= 1.0 else f"{eval_value:.4f}",
                        "exchange_id": "bithumb"
                    })
                    
                for asset in asset_list:
                    asset["percent"] = round((asset["eval_value"] / total_eval_value * 100), 2) if total_eval_value > 0 else 0.0
                    
                asset_list.sort(key=lambda x: x["eval_value"], reverse=True)
                
                return {
                    "total_eval_value": total_eval_value,
                    "formatted_total_value": f"{int(total_eval_value):,}",
                    "assets": asset_list
                }
    except Exception as e:
        logger.error(f"Error fetching bithumb assets: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/exchanges/kis/assets")
async def get_kis_assets(request: Request, mode: str = "active", sync: bool = False):
    """한국투자증권(KIS) 실제 계좌 자산 잔고와 주식 목록을 반환합니다."""
    system = request.app.state.system
    kis_config = system.config_manager.get('exchanges.kis', {})
    kis_app_key = os.getenv("KIS_APP_KEY") or kis_config.get('app_key')
    kis_app_secret = os.getenv("KIS_APP_SECRET") or kis_config.get('app_secret')
    kis_account_no = os.getenv("KIS_ACCOUNT_NO") or kis_config.get('account_no')
    
    if not kis_app_key or not kis_app_secret or not kis_account_no:
        raise HTTPException(status_code=400, detail="KIS API 키 또는 계좌 정보가 설정되지 않았습니다.")
        
    kis_api_url = kis_config.get('api_url', 'https://openapi.koreainvestment.com:9443').rstrip('/')
    is_vts = "openapivts" in kis_api_url
    tr_id = "VTTC8434R" if is_vts else "TTTC8434R"

    try:
        try:
            await _sync_real_kis_orders(system, force_sync=(sync or mode == "liquidated"))
        except Exception as sync_err:
            logger.error(f"Failed to sync real KIS orders: {sync_err}")

        token = await system.cred_provider.get_kis_access_token()
        if not token:
            raise HTTPException(status_code=401, detail="KIS 토큰을 발급받을 수 없습니다.")

        kis_account_no = str(kis_account_no).strip()
        if '-' in kis_account_no:
            cano, acnt_prdt_cd = kis_account_no.split('-', 1)
        else:
            cano = kis_account_no[:8]
            acnt_prdt_cd = kis_account_no[8:]
        if not acnt_prdt_cd:
            acnt_prdt_cd = "01"

        headers = {
            "content-type": "application/json",
            "authorization": f"Bearer {token}",
            "appkey": kis_app_key,
            "appsecret": kis_app_secret,
            "tr_id": tr_id
        }
        
        params = {
            "CANO": cano,
            "ACNT_PRDT_CD": acnt_prdt_cd,
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
        
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{kis_api_url}/uapi/domestic-stock/v1/trading/inquire-balance", headers=headers, params=params) as resp:
                if resp.status != 200:
                    err_txt = await resp.text()
                    raise HTTPException(status_code=resp.status, detail=f"KIS API 오류: {err_txt}")
                data = await resp.json()
                
        if data.get('rt_cd') != '0':
            raise HTTPException(status_code=400, detail=f"KIS API 에러: {data.get('msg1')}")
            
        output1 = data.get('output1', [])
        output2 = data.get('output2', [])
        
        # 예수금 (총 예수금 금액)
        kis_cash = 0
        if output2:
            kis_cash = int(float(output2[0].get('dnca_tot_amt', 0)))

        asset_list = []
        total_eval_value = float(kis_cash)

        if mode == "liquidated":
            # 처분 완료 자산 조회
            active_set = {item.get('pdno', '').strip().lstrip('A') for item in output1 if float(item.get('hldg_qty', 0)) > 0}
            traded_stocks = []
            sell_info = {}
            async with get_db_conn() as db:
                async with db.execute(
                    "SELECT DISTINCT symbol FROM real_orders WHERE exchange_id = 'kis' AND (state = 'done' OR (state = 'cancel' AND executed_volume > 0))"
                ) as cursor:
                    rows = await cursor.fetchall()
                    traded_stocks = [r['symbol'].upper() for r in rows]

                query = """
                    SELECT r.symbol, r.price, r.executed_volume
                    FROM real_orders r
                    INNER JOIN (
                        SELECT symbol, MAX(created_at) as max_created_at
                        FROM real_orders
                        WHERE exchange_id = 'kis' AND side = 'SELL' AND (state = 'done' OR (state = 'cancel' AND executed_volume > 0))
                        GROUP BY symbol
                    ) temp ON r.symbol = temp.symbol AND r.created_at = temp.max_created_at
                    WHERE r.exchange_id = 'kis' AND r.side = 'SELL' AND (r.state = 'done' OR (r.state = 'cancel' AND r.executed_volume > 0))
                """
                async with db.execute(query) as cursor:
                    rows = await cursor.fetchall()
                    for r in rows:
                        sym = r['symbol'].upper()
                        sell_info[sym] = {
                            "price": float(r['price'] or 0.0),
                            "volume": float(r['executed_volume'] or 0.0)
                        }

            liquidated_stocks = sorted(list(set(traded_stocks) - active_set))
            for stock in liquidated_stocks:
                korean_name = stock_mapper.get_name('kis', stock)
                info = sell_info.get(stock, {"price": 0.0, "volume": 0.0})
                sell_price = info["price"]
                sell_volume = info["volume"]
                sell_value = sell_price * sell_volume
                
                asset_list.append({
                    "currency": stock,
                    "korean_name": korean_name,
                    "balance": 0.0,
                    "avg_buy_price": sell_price,
                    "current_price": 0.0,
                    "eval_value": sell_value,
                    "formatted_eval_value": f"{int(sell_value):,}" if sell_value >= 1.0 else f"{sell_value:.4f}",
                    "percent": 0.0,
                    "exchange_id": "kis"
                })
            return {
                "total_eval_value": 0.0,
                "formatted_total_value": "0",
                "assets": asset_list
            }
        else:
            # 원화 예수금 기본 추가 (원화 자산 시드)
            asset_list.append({
                "currency": "KRW",
                "korean_name": "원화 예수금",
                "balance": float(kis_cash),
                "avg_buy_price": 1.0,
                "current_price": 1.0,
                "eval_value": float(kis_cash),
                "formatted_eval_value": f"{kis_cash:,}",
                "exchange_id": "kis"
            })
            
            for item in output1:
                qty = float(item.get('hldg_qty', 0))
                if qty <= 0:
                    continue
                pdno = item.get('pdno', '').strip().lstrip('A')
                avg_price = float(item.get('pchs_avg_pric', 0))
                current_price = float(item.get('prpr', 0))
                eval_amt = float(item.get('evlu_amt', 0))
                prdt_name = item.get('prdt_name', '').strip() or stock_mapper.get_name('kis', pdno)
                
                total_eval_value += eval_amt
                
                asset_list.append({
                    "currency": pdno,
                    "korean_name": prdt_name,
                    "balance": qty,
                    "avg_buy_price": avg_price,
                    "current_price": current_price,
                    "eval_value": eval_amt,
                    "formatted_eval_value": f"{int(eval_amt):,}",
                    "exchange_id": "kis"
                })
                
            for asset in asset_list:
                asset["percent"] = round((asset["eval_value"] / total_eval_value * 100), 2) if total_eval_value > 0 else 0.0
                
            asset_list.sort(key=lambda x: x["eval_value"], reverse=True)
            
            return {
                "total_eval_value": total_eval_value,
                "formatted_total_value": f"{int(total_eval_value):,}",
                "assets": asset_list
            }
    except Exception as e:
        logger.error(f"Error fetching KIS assets: {e}")
        raise HTTPException(status_code=500, detail=str(e))

