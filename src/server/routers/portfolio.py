from fastapi import APIRouter, Request, HTTPException
import aiohttp
import asyncio
import time
from src.database.connection import get_db_conn
from src.server.websocket import manager
from src.engine.utils.telemetry import get_logger
from src.engine.utils.stock_mapper import stock_mapper

logger = get_logger(__name__)
router = APIRouter()

@router.get("/api/portfolios")
async def list_portfolios(request: Request):
    """관리 중인 모든 포트폴리오 목록을 반환합니다."""
    system = request.app.state.system
    return [
        {"id": p.id, "name": p.name, "cash": p.cash}
        for p in system.portfolio_manager.portfolios.values()
    ]

@router.get("/api/portfolio")
async def get_portfolio(request: Request, portfolio_id: str = "default"):
    """포트폴리오의 현재 상태(잔고, 포지션, 수익률)를 반환합니다."""
    system = request.app.state.system
    portfolio = system.portfolio_manager.portfolios.get(portfolio_id)
    if not portfolio:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    
    current_prices = {}
    for symbol in portfolio.positions:
        current_prices[symbol] = portfolio.positions[symbol].avg_price
    
    total_value = portfolio.get_total_value(current_prices)
    
    return {
        "id": portfolio.id,
        "name": portfolio.name,
        "initial_cash": portfolio.initial_cash,
        "cash": portfolio.cash,
        "total_value": total_value,
        "positions": [
            {
                "exchange": pos.exchange,
                "symbol": pos.symbol,
                "quantity": pos.quantity,
                "avg_price": pos.avg_price,
                "updated_at": pos.updated_at
            }
            for pos in portfolio.positions.values() if pos.quantity > 0
        ],
        "history": portfolio.history[-50:]
    }

@router.get("/trades")
async def get_trades(exchange: str = "upbit", symbol: str = "BTC", limit: int = 10):
    """최근 체결 데이터를 DB에서 조회하여 반환합니다."""
    async with get_db_conn() as db:
        async with db.execute("SELECT trade_price, trade_volume, ask_bid, trade_timestamp FROM trades WHERE exchange = ? AND symbol = ? ORDER BY trade_timestamp DESC LIMIT ?", (exchange, symbol, limit)) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

@router.post("/api/portfolio/{portfolio_id}/panic")
async def panic_sell(portfolio_id: str, request: Request):
    """모든 포지션을 즉시 시장가 청산하고 비상 정지합니다."""
    system = request.app.state.system
    try:
        portfolio = system.portfolio_manager.portfolios.get(portfolio_id)
        if not portfolio:
            raise HTTPException(status_code=404, detail="Portfolio not found")

        # 1. 청산할 종목들 추출
        symbols = [s for s, pos in portfolio.positions.items() if pos.quantity > 0]
        if not symbols:
            return {"status": "success", "message": "청산할 포지션이 없습니다.", "data": []}

        # 2. 실시간 가격 조회 (Upbit API)
        prices = {}
        async with aiohttp.ClientSession() as session:
            for i in range(0, len(symbols), 100):
                batch = ','.join(symbols[i:i+100])
                async with session.get(f"https://api.upbit.com/v1/ticker?markets={batch}") as resp:
                    tickers = await resp.json()
                    for t in tickers:
                        prices[t['market']] = t['trade_price']

        # 3. 각 종목별 청산 실행
        results = []
        executor = system.portfolio_manager.executors.get('simulation')
        for symbol in symbols:
            price = prices.get(symbol, 0)
            if price == 0: continue
            
            qty = portfolio.positions[symbol].quantity
            res = await executor.execute_order(
                portfolio=portfolio,
                symbol=symbol,
                side='SELL',
                quantity=qty,
                trade_price=price,
                reason="긴급 손절 (Panic Sell)"
            )
            if res:
                results.append(res)
                # 1. DB 거래 내역 저장
                async with get_db_conn() as db:
                    await db.execute('''
                        INSERT INTO orders_history (portfolio_id, strategy_id, symbol, side, price, quantity, fee, timestamp, reason, context)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (portfolio_id, "panic_sell", res['symbol'], res['side'], res['price'], res['quantity'], res['fee'], int(time.time()), "긴급 손절 (Panic Sell)", "{}"))
                    await db.commit()

                # 2. 긴급 알림 브로드캐스트
                alert = {
                    "type": "alert",
                    "alert_type": "panic",
                    "code": symbol,
                    "price": price,
                    "msg": f"🚨 [긴급손절] {symbol} 전량 매도 완료"
                }
                await manager.broadcast_global(alert)
                asyncio.create_task(system.save_alert(alert))

        # 4. 변경된 포트폴리오 상태 DB 영구 저장
        await system.portfolio_manager.save_to_db(portfolio_id)

        return {"status": "success", "message": f"{len(results)}개 종목 청산 완료", "data": results}

    except Exception as e:
        logger.error(f"Panic Sell Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


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

@router.get("/api/exchanges/upbit/assets")
async def get_upbit_assets(request: Request):
    """업비트 실제 잔고를 조회하고 실시간 시세를 반영하여 평가금액이 높은 순서대로 정렬해 반환합니다."""
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
                
            # 2. [OPTIMIZED] 매번 REST API로 마켓 리스트를 호출하는 대신, 메모리에 대량 적재된 stock_mapper 캐시 사용
            valid_krw_markets = {f"KRW-{k}" for k in stock_mapper._mapping.get('upbit', {}).keys()}
            
            # 실시간 시세가 존재하는 실제 코인만 추려서 Ticker 일괄 요청 (에러 방지)
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
                                
            # 3. 자산 리스트 재구성 및 평가액 연산
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
                    # 실존 마켓에 존재하면 실시간 현재가, 없으면 0.0 처리 (상장폐지/에어드랍 찌꺼기 방어)
                    if symbol in valid_krw_markets:
                        current_price = prices.get(symbol, avg_buy_price)
                        eval_value = balance * current_price
                    else:
                        current_price = 0.0
                        eval_value = 0.0
                    
                    # [OPTIMIZED] 메모리 캐시(stock_mapper)에서 번개처럼 한글명 조회
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
                
            # 4. 비중(percent) 산정 및 평가금액 많은 순 정렬
            for asset in asset_list:
                asset["percent"] = round((asset["eval_value"] / total_eval_value * 100), 2) if total_eval_value > 0 else 0.0
                
            # 평가금액 기준 내림차순 정렬
            asset_list.sort(key=lambda x: x["eval_value"], reverse=True)
            
            return {
                "total_eval_value": total_eval_value,
                "formatted_total_value": f"{int(total_eval_value):,}",
                "assets": asset_list
            }
            
    except Exception as e:
        logger.error(f"Error fetching upbit assets: {e}")
        raise HTTPException(status_code=500, detail=str(e))

