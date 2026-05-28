from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from typing import Dict, Any, List, Optional
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
    
    # 1. 기존 활성 모의투자 세션이 있다면 자동 종료 처리
    active_p = system.portfolio_manager.get_active_simulation_portfolio()
    if active_p:
        try:
            logger.info(f"기존 활성화된 모의투자 세션 종료 처리 중: {active_p.id}")
            await _end_portfolio_session_internal(active_p.id, system)
        except Exception as e:
            logger.error(f"기존 활성 세션 자동 종료 중 에러: {e}")
            
    # 2. 신규 포트폴리오 생성 및 거래소별 자금 분배
    portfolio_id = f"simulation_{int(time.time())}"
    p_name = f"실시간 모의투자 ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')})"
    initial_cash_input = req.initial_cash
    exchange_cash_map = {}
    total_cash = 0.0

    enabled_exchanges = []
    exchanges_config = system.config_manager.get('exchanges', {})
    for ex_id, exch_config in exchanges_config.items():
        if exch_config.get('enabled', True):
            enabled_exchanges.append(ex_id.lower())
            
    if not enabled_exchanges:
        enabled_exchanges = ['upbit']

    if isinstance(initial_cash_input, dict):
        for ex, cash_val in initial_cash_input.items():
            ex_lower = ex.lower()
            if ex_lower in enabled_exchanges:
                val = float(cash_val)
                exchange_cash_map[ex_lower] = val
                total_cash += val
        
        if not exchange_cash_map:
            total_cash = 30000000.0
            each_cash = total_cash / len(enabled_exchanges)
            exchange_cash_map = {ex: each_cash for ex in enabled_exchanges}
    else:
        total_cash = float(initial_cash_input)
        each_cash = total_cash / len(enabled_exchanges)
        exchange_cash_map = {ex: each_cash for ex in enabled_exchanges}

    from src.engine.portfolio import Portfolio
    p = Portfolio(
        portfolio_id=portfolio_id,
        name=p_name,
        initial_cash=total_cash,
        exchange_id='all',
        portfolio_type='simulation'
    )
    p.cash = total_cash
    p.exchange_cash = exchange_cash_map
    
    # 4. 선택 전략 메타 정보 기재
    meta_info = {
        "applied_strategies": req.strategies,
        "initial_cash": req.initial_cash
    }
    p.strategy_info = json.dumps(meta_info)
    
    # 5. 메모리 등록 및 DB 영구 저장
    system.portfolio_manager.add_portfolio(p)
    await system.portfolio_manager.save_to_db(portfolio_id)
    
    # ZMQ IPC 메시지 발행
    strategy_pub = getattr(request.app.state, 'strategy_control_publisher', None)
    if strategy_pub:
        try:
            msg = {
                "type": "update_portfolio",
                "portfolio_id": portfolio_id
            }
            await strategy_pub.publish("strategy_control", msg)
            logger.info(f"[Web Portfolio Router] ZMQ strategy control message published: {msg}")
        except Exception as e:
            logger.error(f"[Web Portfolio Router] Failed to publish ZMQ message: {e}")

    logger.info(f"새 실시간 모의투자 세션이 성공적으로 시작되었습니다: {portfolio_id}")
    return {"status": "success", "portfolio_id": portfolio_id, "name": p_name}

@router.post("/api/portfolio/{portfolio_id}/end")
async def end_portfolio_session(portfolio_id: str, request: Request):
    """보유 포지션 청산 없이 실시간 모의투자 세션을 마감(동결) 처리합니다."""
    system = request.app.state.system
    try:
        await _end_portfolio_session_internal(portfolio_id, system)
        
        # ZMQ IPC 메시지 발행
        strategy_pub = getattr(request.app.state, 'strategy_control_publisher', None)
        if strategy_pub:
            try:
                msg = {
                    "type": "update_portfolio",
                    "portfolio_id": portfolio_id
                }
                await strategy_pub.publish("strategy_control", msg)
                logger.info(f"[Web Portfolio Router] ZMQ strategy control message published: {msg}")
            except Exception as e:
                logger.error(f"[Web Portfolio Router] Failed to publish ZMQ message: {e}")

        return {"status": "success", "message": "모의투자 세션이 정상적으로 마감되었습니다."}
    except Exception as e:
        logger.error(f"End portfolio session error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

async def _end_portfolio_session_internal(portfolio_id: str, system):
    """모의투자 마감 내부 공통 처리 메서드 (미실현 평가가 박제)"""
    portfolio = system.portfolio_manager.portfolios.get(portfolio_id)
    if not portfolio:
        raise Exception("Portfolio not found")
        
    # 1. 각 종목별 최종 평가가(현재 실시간 시세) 산출
    current_prices = await system.portfolio_manager.get_portfolio_current_prices(portfolio_id, system)

    # 2. 누적 수수료 및 거래 건수 집계
    async with get_db_conn() as db:
        async with db.execute("SELECT COUNT(*), SUM(fee) FROM orders_history WHERE portfolio_id = ?", (portfolio_id,)) as cursor:
            row = await cursor.fetchone()
            trade_count = row[0] if row else 0
            total_fee = row[1] if row and row[1] is not None else 0.0

    # 3. 최종 평가 금액 및 메타데이터 구성
    total_value = portfolio.get_total_value(current_prices)
    
    meta = {}
    if portfolio.strategy_info:
        try:
            meta = json.loads(portfolio.strategy_info)
        except Exception:
            pass
            
    meta["final_prices"] = current_prices
    meta["summary"] = {
        "initial_cash": portfolio.initial_cash,
        "final_value": total_value,
        "profit": total_value - portfolio.initial_cash,
        "roi": round(((total_value - portfolio.initial_cash) / portfolio.initial_cash * 100), 2) if portfolio.initial_cash > 0 else 0.0,
        "fee": round(total_fee, 2),
        "trade_count": trade_count
    }
    
    # 4. 타입 변경 및 저장
    portfolio.strategy_info = json.dumps(meta)
    portfolio.portfolio_type = 'simulation_ended'
    
    # DB 영구 저장
    await system.portfolio_manager.save_to_db(portfolio_id)

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
        positions_to_sell = [(pos.exchange, pos.symbol, pos.quantity) 
                             for pos in portfolio.positions.values() if pos.quantity > 0]
        if not positions_to_sell:
            return {"status": "success", "message": "청산할 포지션이 없습니다.", "data": []}

        # 2. 실시간 가격 구성 (Upbit는 API 호출, KIS 등은 최신 캔들 종가 사용)
        prices = {}
        upbit_symbols = [sym for ex, sym, qty in positions_to_sell if ex.lower() == 'upbit']
        
        # Upbit 가격 조회
        if upbit_symbols:
            try:
                formatted = [f"KRW-{s}" if not s.startswith("KRW-") else s for s in upbit_symbols]
                async with aiohttp.ClientSession() as session:
                    async with session.get(f"https://api.upbit.com/v1/ticker?markets={','.join(formatted)}") as resp:
                        if resp.status == 200:
                            tickers = await resp.json()
                            for t in tickers:
                                clean_sym = t['market'].replace("KRW-", "")
                                prices[('upbit', clean_sym)] = float(t['trade_price'])
            except Exception as e:
                logger.error(f"Failed to fetch upbit tickers for panic sell: {e}")

        # KIS 및 기타 가격 조회 (DB 캔들 조회)
        async with get_db_conn() as db:
            for ex, sym, qty in positions_to_sell:
                ex_key = ex.lower()
                if ex_key != 'upbit':
                    try:
                        async with db.execute(
                            "SELECT close FROM candles WHERE exchange = ? AND symbol = ? ORDER BY timestamp DESC LIMIT 1",
                            (ex_key, sym)
                        ) as cursor:
                            row = await cursor.fetchone()
                            if row:
                                prices[(ex_key, sym)] = row['close']
                            else:
                                pos_key = (ex_key, sym)
                                prices[(ex_key, sym)] = portfolio.positions[pos_key].avg_price
                    except Exception as e:
                        logger.error(f"Failed to query panic sell price for {ex_key}:{sym}: {e}")
                        pos_key = (ex_key, sym)
                        prices[(ex_key, sym)] = portfolio.positions[pos_key].avg_price
                else:
                    if ('upbit', sym) not in prices:
                        prices[('upbit', sym)] = portfolio.positions[('upbit', sym)].avg_price

        # 3. 각 종목별 청산 실행
        results = []
        executor = system.portfolio_manager.executors.get('simulation')
        for ex, symbol, qty in positions_to_sell:
            ex_key = ex.lower()
            price = prices.get((ex_key, symbol), 0)
            if price == 0:
                continue
            
            res = await executor.execute_order(
                exchange=ex,
                symbol=symbol,
                side='SELL',
                quantity=qty,
                trade_price=price
            )
            if res:
                results.append(res)
                # 1. 포트폴리오 상태 갱신
                portfolio.update_position(
                    exchange=res['exchange'],
                    symbol=res['symbol'],
                    side=res['side'],
                    price=res['price'],
                    quantity=res['quantity'],
                    fee=res['fee'],
                    strategy_id="panic_sell",
                    reason="긴급 손절 (Panic Sell)"
                )
                
                # 2. DB 거래 내역 저장
                async with get_db_conn() as db:
                    await db.execute('''
                        INSERT INTO orders_history (portfolio_id, exchange, strategy_id, symbol, side, price, quantity, fee, timestamp, reason, context)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        portfolio_id, 
                        res['exchange'],
                        "panic_sell", 
                        res['symbol'], 
                        res['side'], 
                        res['price'], 
                        res['quantity'], 
                        res['fee'], 
                        int(time.time()), 
                        "긴급 손절 (Panic Sell)", 
                        "{}"
                    ))
                    await db.commit()

                # 3. 긴급 알림 브로드캐스트
                alert = {
                    "type": "alert",
                    "alert_type": "panic",
                    "exchange": ex,
                    "code": symbol,
                    "price": price,
                    "msg": f"🚨 [긴급손절] {symbol} ({ex}) 전량 매도 완료"
                }
                await manager.broadcast_global(alert)
                asyncio.create_task(system.save_alert(alert))

        # 4. 변경된 포트폴리오 상태 DB 영구 저장
        await system.portfolio_manager.save_to_db(portfolio_id)

        # ZMQ IPC 메시지 발행
        strategy_pub = getattr(request.app.state, 'strategy_control_publisher', None)
        if strategy_pub:
            try:
                msg = {
                    "type": "update_portfolio",
                    "portfolio_id": portfolio_id
                }
                await strategy_pub.publish("strategy_control", msg)
                logger.info(f"[Web Portfolio Router] ZMQ strategy control message published: {msg}")
            except Exception as e:
                logger.error(f"[Web Portfolio Router] Failed to publish ZMQ message: {e}")

        return {"status": "success", "message": f"{len(results)}개 종목 청산 완료", "data": results}

    except Exception as e:
        logger.error(f"Panic Sell Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

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
            valid_krw_markets = {f"KRW-{k}" for k in stock_mapper.get_active_symbols('upbit')}
            
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

