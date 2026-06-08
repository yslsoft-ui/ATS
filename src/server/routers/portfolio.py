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
    strategies: Dict[str, Any]

@router.get("/api/portfolios")
async def list_portfolios(request: Request):
    """관리 중인 모든 실시간 모의투자 포트폴리오 목록을 반환합니다."""
    system = request.app.state.system
    await system.portfolio_manager.load_from_db()
    
    ports = []
    for p in system.portfolio_manager.portfolios.values():
        if p.portfolio_type in ['simulation', 'simulation_ended']:
            ports.append({
                "id": p.id,
                "name": p.name,
                "cash": p.cash,
                "type": p.portfolio_type
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

@router.post("/api/portfolio/{portfolio_id}/panic")
async def panic_sell(portfolio_id: str, request: Request):
    """모든 포지션을 즉시 시장가 청산하고 비상 정지합니다."""
    system = request.app.state.system
    try:
        res = await system.dispatcher.dispatch(
            UserCommand.PORTFOLIO_PANIC,
            {"portfolio_id": portfolio_id}
        )
        return {"status": "success", "message": res.get("message", "청산 완료"), "data": res.get("data", [])}
    except Exception as e:
        logger.error(f"Panic Sell Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/trades")
async def get_trades(request: Request, exchange: str = "upbit", symbol: str = "BTC", limit: int = 10):
    """최근 체결 데이터를 DB에서 조회하여 반환합니다."""
    system = request.app.state.system
    async with get_db_conn(system.portfolio_manager.db_path) as db:
        async with db.execute("SELECT trade_price, trade_volume, ask_bid, trade_timestamp FROM trades WHERE exchange = ? AND symbol = ? ORDER BY trade_timestamp DESC LIMIT ?", (exchange, symbol, limit)) as cursor:
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
        async with db.execute("SELECT 1 FROM real_orders WHERE exchange = 'upbit' LIMIT 1") as cursor:
            row = await cursor.fetchone()
            if row:
                has_records = True
        async with db.execute("SELECT 1 FROM real_orders WHERE exchange = 'upbit' AND state = 'wait' LIMIT 1") as cursor:
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
                                    (exchange, uuid, symbol, side, price, volume, executed_volume, fee, state, created_at)
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
                        "SELECT DISTINCT symbol FROM real_orders WHERE exchange = 'upbit' AND (state = 'done' OR (state = 'cancel' AND executed_volume > 0))"
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
                            WHERE exchange = 'upbit' AND side = 'SELL' AND (state = 'done' OR (state = 'cancel' AND executed_volume > 0))
                            GROUP BY symbol
                        ) temp ON r.symbol = temp.symbol AND r.created_at = temp.max_created_at
                        WHERE r.exchange = 'upbit' AND r.side = 'SELL' AND (r.state = 'done' OR (r.state = 'cancel' AND r.executed_volume > 0))
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
                        "percent": 0.0
                    })
                
                return {
                    "total_eval_value": 0.0,
                    "formatted_total_value": "0",
                    "assets": asset_list
                }
            else:
                # [Active Mode] 보유 중인 자산 조회 (기존 로직)
                coins = [a for a in accounts if a['currency'] != 'KRW']
                coin_symbols = [f"KRW-{c['currency']}" for c in coins if f"KRW-{c['currency']}" in valid_krw_markets]
                
                prices = {}
                if coin_symbols:
                    for i in range(0, len(coin_symbols), 100):
                        batch = ','.join(coin_symbols[i:i+100])
                        async with session.get(f"{api_url}/ticker?markets={batch}") as resp:
                            if resp.status == 200:
                                tickers = await resp.json()
                                for t in tickers:
                                    prices[t['market']] = float(t['trade_price'])
                                    
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
                        eval_value = balance
                        korean_name = "원화"
                    else:
                        symbol = f"KRW-{currency}"
                        if symbol in valid_krw_markets:
                            current_price = prices.get(symbol, avg_buy_price)
                            eval_value = balance * current_price
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
                        "formatted_eval_value": f"{int(eval_value):,}" if eval_value >= 1.0 else f"{eval_value:.4f}"
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

