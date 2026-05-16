from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, HTTPException
from fastapi.staticfiles import StaticFiles
from typing import Set, Optional, Dict, List
from pydantic import BaseModel
import shutil
import os
import asyncio
import json
import time
import aiohttp
from src.database.connection import get_db_conn
from collections import deque
from src.engine.candles import CandleGenerator
from src.engine.indicators import IndicatorCalculator
from src.engine.strategy import StrategyRegistry, BaseStrategy
from src.engine.trade_engine import TradeEngine, TradeSignal
from src.engine.loader import load_dynamic_strategies, unload_strategy
from src.engine.portfolio import PortfolioManager, Portfolio

from src.engine.system import TradingSystem

app = FastAPI()

# 전역 시스템 인스턴스 초기화
CONFIG_PATH = os.path.join(os.getcwd(), 'config', 'settings.yaml')
system = TradingSystem(CONFIG_PATH)

# 전역 상태 관리
class ConnectionManager:
    """WebSocket 연결 및 종목별 구독을 관리합니다."""
    def __init__(self):
        self.active_connections: Set[WebSocket] = set()
        self.subscriptions: Dict[WebSocket, str] = {}  # ws -> 구독 중인 종목

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.add(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.discard(websocket)
        self.subscriptions.pop(websocket, None)

    def subscribe(self, websocket: WebSocket, symbol: str):
        """클라이언트가 특정 종목을 구독합니다."""
        self.subscriptions[websocket] = symbol

    async def broadcast(self, message: dict):
        """해당 종목을 구독 중인 클라이언트에게만 전송합니다."""
        symbol = message.get('code', '')
        msg_str = json.dumps(message)
        targets = [ws for ws, sub in self.subscriptions.items() if sub == symbol]
        if targets:
            await asyncio.gather(
                *[ws.send_text(msg_str) for ws in targets],
                return_exceptions=True
            )

    async def broadcast_global(self, message: dict):
        """종목 구독 여부와 상관없이 모든 연결된 클라이언트에게 전송합니다."""
        msg_str = json.dumps(message)
        if self.active_connections:
            await asyncio.gather(
                *[ws.send_text(msg_str) for ws in self.active_connections],
                return_exceptions=True
            )

manager = ConnectionManager()
# 시스템 콜백 설정
system.broadcast_callback = manager.broadcast_global

@app.on_event("startup")
async def startup_event():
    # TradingSystem 기동 (내부에서 DB 초기화, 포트폴리오 로드, 전략 로드, 수집기 시작을 모두 수행)
    await system.boot()
    print("[INFO] 시스템 모든 구성 요소가 TradingSystem을 통해 시작되었습니다.")

@app.on_event("shutdown")
async def shutdown_event():
    await system.shutdown()
    print("[INFO] Shutdown complete.")

@app.get("/market")
async def get_market():
    """전체 KRW 마켓 종목 정보(한글명, 현재가, 변동률, 거래대금)를 반환합니다."""
    try:
        async with aiohttp.ClientSession() as session:
            # 1. 종목 기본 정보 (한글명 포함)
            async with session.get("https://api.upbit.com/v1/market/all?is_details=false") as resp:
                all_markets = await resp.json()
            krw_markets = [m for m in all_markets if m['market'].startswith('KRW-')]
            market_codes = [m['market'] for m in krw_markets]

            # 2. 현재가 조회 (100개씩 배치)
            tickers = []
            for i in range(0, len(market_codes), 100):
                batch = ','.join(market_codes[i:i+100])
                async with session.get(f"https://api.upbit.com/v1/ticker?markets={batch}") as resp:
                    tickers.extend(await resp.json())

            # 3. 데이터 병합 후 거래대금 내림차순 정렬
            ticker_map = {t['market']: t for t in tickers}
            result = []
            for m in krw_markets:
                code = m['market']
                t = ticker_map.get(code, {})
                result.append({
                    'market': code,
                    'korean_name': m.get('korean_name', ''),
                    'english_name': m.get('english_name', ''),
                    'trade_price': t.get('trade_price', 0),
                    'signed_change_rate': t.get('signed_change_rate', 0),
                    'signed_change_price': t.get('signed_change_price', 0),
                    'acc_trade_price_24h': t.get('acc_trade_price_24h', 0),
                    'high_price': t.get('high_price', 0),
                    'low_price': t.get('low_price', 0),
                })
            result.sort(key=lambda x: x['acc_trade_price_24h'], reverse=True)
            return result
    except Exception as e:
        return {"error": str(e)}

@app.get("/symbols")
async def get_symbols():
    """수집 가능한 전체 KRW 종목 목록을 반환합니다."""
    if system.collector and system.collector.available_symbols:
        return system.collector.available_symbols
    return ["KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL", "KRW-DOGE"]

@app.get("/collector/status")
async def get_status():
    is_running = system.collector.is_running if system.collector else False
    return {"is_running": is_running}

@app.post("/collector/start")
async def start_collector():
    if system.collector:
        await system.collector.start()
    return {"message": "Collector started"}

@app.post("/collector/stop")
async def stop_collector():
    if system.collector:
        await system.collector.stop()
    return {"message": "Collector stopped"}

@app.get("/api/system/queues")
async def get_queue_status():
    """각 작업 큐의 현재 적체량 및 누적 처리량을 반환합니다."""
    return {
        "processing": system.processing_queue.qsize(),
        "database": system.db_queue.qsize(),
        "candle": system.candle_queue.qsize(),
        "total": system.collector.total_processed_count if system.collector else 0
    }

@app.get("/api/strategies")
async def list_strategies():
    """사용 가능한 모든 전략 목록과 메타데이터를 반환합니다."""
    # 1. 레지스트리에서 코드 기반 메타데이터(설명, 파라미터 타입 등) 가져오기
    all_meta = StrategyRegistry.get_all_metadata()
    
    # 2. Config에서 현재 상태(enabled, current_params) 가져오기
    configs = system.strategy_configs
    
    results = []
    for meta in all_meta:
        s_id = meta['id']
        config = configs.get(s_id, {"enabled": False, "params": {}})
        
        # 메타데이터 구조에 현재 값 병합
        params_with_values = {}
        for p_name, p_info in meta['params'].items():
            current_val = config.get('params', {}).get(p_name, p_info.get('default'))
            params_with_values[p_name] = {
                **p_info,
                "current": current_val
            }
            
        results.append({
            "id": s_id,
            "name": meta['name'],
            "type": meta['type'],
            "description": meta['description'],
            "enabled": config.get('enabled', False),
            "params": params_with_values
        })
        
    return results

@app.put("/api/strategies/{strategy_id}")
async def update_strategy_params(strategy_id: str, params: Dict):
    """특정 전략의 파라미터를 업데이트하고 파일에 저장합니다."""
    s_id = strategy_id.lower()
    
    # 1. ConfigManager를 통해 파일 및 메모리 업데이트
    current_config = system.strategy_configs.get(s_id, {"enabled": False, "params": {}})
    current_config['params'].update(params)
    
    system.config_manager.update(f"strategies.{s_id}", current_config)
    
    # 2. 실시간 엔진 반영 강제 트리거 (Hot-reload를 기다려도 되지만 즉시 반영이 좋음)
    await system._on_config_changed(system.config_manager.config)
    
    return {"message": f"Strategy {strategy_id} updated and saved", "params": current_config['params']}

@app.delete("/api/strategies/{strategy_id}")
async def disable_strategy(strategy_id: str):
    """특정 전략을 비활성화하고 파일에 저장합니다."""
    s_id = strategy_id.lower()
    
    current_config = system.strategy_configs.get(s_id, {"enabled": False, "params": {}})
    current_config['enabled'] = False
    
    system.config_manager.update(f"strategies.{s_id}", current_config)
    await system._on_config_changed(system.config_manager.config)
    
    return {"message": f"Strategy {strategy_id} disabled and saved"}

@app.post("/api/strategies/{strategy_id}/enable")
async def enable_strategy(strategy_id: str):
    """특정 전략을 활성화하고 파일에 저장합니다."""
    s_id = strategy_id.lower()
    
    current_config = system.strategy_configs.get(s_id, {"enabled": False, "params": {}})
    current_config['enabled'] = True
    
    system.config_manager.update(f"strategies.{s_id}", current_config)
    await system._on_config_changed(system.config_manager.config)
    
    return {"message": f"Strategy {strategy_id} enabled and saved"}


@app.get("/candles")
async def get_candles(symbol: str = "KRW-BTC", interval: int = 60, limit: int = 500, start_ts: int = None, end_ts: int = None):
    """최적화된 고성능 캔들 데이터 반환"""
    async with get_db_conn() as db:
        all_candles = []
        
        # 1. 고분봉(60초 이상) 처리 최적화: 1분봉 기반 집계
        if interval >= 60 and interval % 60 == 0:
            query = "SELECT * FROM candles WHERE symbol = ? AND (interval = ? OR interval = 60)"
            params = [symbol, interval]
            if start_ts and end_ts:
                query += " AND timestamp BETWEEN ? AND ?"
                params.extend([start_ts, end_ts])
            query += " ORDER BY timestamp DESC LIMIT ?"
            params.append(limit * (interval // 60) + 100)
            
            async with db.execute(query, params) as cursor:
                rows = await cursor.fetchall()
                if rows:
                    raw_data = sorted([dict(r) for r in rows], key=lambda x: x['timestamp'])
                    generator = CandleGenerator(intervals=[interval])
                    for r in raw_data:
                        # 1분봉을 입력으로 상위 분봉 조립
                        closed = generator.process_tick(symbol, r['close'], r['volume'], 'BID', r['timestamp'] * 1000)
                        for c in closed:
                            all_candles.append({'timestamp': c.timestamp, 'open': c.open, 'high': c.high, 'low': c.low, 'close': c.close, 'volume': c.volume})
                    
                    current = generator.get_current_candle(symbol, interval)
                    if current and (not all_candles or all_candles[-1]['timestamp'] < current.timestamp):
                        all_candles.append({'timestamp': current.timestamp, 'open': current.open, 'high': current.high, 'low': current.low, 'close': current.close, 'volume': current.volume})
        
        # 2. 저분봉(60초 미만) 또는 데이터 부족 시 틱 데이터 활용
        if not all_candles:
            needed_ticks = limit * 2 if interval < 10 else limit * 10
            query = "SELECT * FROM trades WHERE symbol = ? "
            params = [symbol]
            if start_ts and end_ts:
                query += " AND trade_timestamp BETWEEN ? AND ?"
                params.extend([start_ts * 1000, end_ts * 1000])
            else:
                query += " AND trade_timestamp > ?"
                params.append(int((time.time() - (limit * interval * 1.5)) * 1000))
            
            query += " ORDER BY trade_timestamp DESC LIMIT ?"
            params.append(min(needed_ticks, 30000))
            
            async with db.execute(query, params) as cursor:
                rows = await cursor.fetchall()
                if rows:
                    ticks = sorted([dict(r) for r in rows], key=lambda x: x['trade_timestamp'])
                    generator = CandleGenerator(intervals=[interval])
                    for row in ticks:
                        closed = generator.process_tick(symbol, row['trade_price'], row['trade_volume'], row['ask_bid'], row['trade_timestamp'])
                        for c in closed:
                            all_candles.append({'timestamp': c.timestamp, 'open': c.open, 'high': c.high, 'low': c.low, 'close': c.close, 'volume': c.volume})
                    
                    current = generator.get_current_candle(symbol, interval)
                    if current and (not all_candles or all_candles[-1]['timestamp'] < current.timestamp):
                        all_candles.append({'timestamp': current.timestamp, 'open': current.open, 'high': current.high, 'low': current.low, 'close': current.close, 'volume': current.volume})

        # 3. 결과 정제 및 지표 계산
        if all_candles:
            seen = set()
            unique_candles = []
            for c in sorted(all_candles, key=lambda x: x['timestamp']):
                if c['timestamp'] not in seen:
                    unique_candles.append(c)
                    seen.add(c['timestamp'])
            
            unique_candles = unique_candles[-limit:]
            df = IndicatorCalculator.calculate_all_indicators(unique_candles)
            return df.replace({float('nan'): None}).to_dict(orient='records')
        
        return []

@app.get("/test-alert")
async def test_alert(symbol: str = "KRW-BTC"):
    """UI 확인용 테스트 알림을 강제로 발생시킵니다."""
    mock_alert = {
        "type": "alert",
        "code": symbol,
        "price": 100000000,
        "change": 5.23,
        "buy_ratio": 88.5,
        "msg": f"🚀 [TEST] 급등 포착: {symbol} (+5.23%)"
    }
    await manager.broadcast_global(mock_alert)
    await system.save_alert(mock_alert)
    return {"message": f"Test alert for {symbol} sent to all clients and saved"}

@app.get("/alerts")
async def get_alerts(limit: int = 50):
    """최근 알림 기록을 반환합니다."""
    async with get_db_conn() as db:
        async with db.execute("SELECT * FROM alerts ORDER BY timestamp DESC LIMIT ?", (limit,)) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

@app.delete("/api/alerts")
async def clear_alerts():
    """모든 알림 기록을 삭제합니다."""
    async with get_db_conn() as db:
        await db.execute("DELETE FROM alerts")
        await db.commit()
    return {"message": "모든 알림 기록이 삭제되었습니다."}

@app.get("/api/portfolios")
async def list_portfolios():
    """관리 중인 모든 포트폴리오 목록을 반환합니다."""
    return [
        {"id": p.id, "name": p.name, "cash": p.cash}
        for p in system.portfolio_manager.portfolios.values()
    ]

@app.get("/api/portfolio")
async def get_portfolio(portfolio_id: str = "default"):
    """포트폴리오의 현재 상태(잔고, 포지션, 수익률)를 반환합니다."""
    portfolio = system.portfolio_manager.portfolios.get(portfolio_id)
    if not portfolio:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    
    # 현재가 기준 평가액 계산을 위해 최신가 정보 수집
    # (실제로는 collector의 최신가 캐시를 사용하는 것이 좋음)
    current_prices = {}
    for symbol in portfolio.positions:
        # 간단히 각 포지션의 avg_price를 기본값으로 사용 (추후 최적화 가능)
        current_prices[symbol] = portfolio.positions[symbol].avg_price
    
    total_value = portfolio.get_total_value(current_prices)
    
    return {
        "id": portfolio.id,
        "name": portfolio.name,
        "initial_cash": portfolio.initial_cash, # 원금 정보 추가 [NEW]
        "cash": portfolio.cash,
        "total_value": total_value,
        "positions": [
            {
                "symbol": pos.symbol,
                "quantity": pos.quantity,
                "avg_price": pos.avg_price,
                "updated_at": pos.updated_at
            }
            for pos in portfolio.positions.values() if pos.quantity > 0
        ],
        "history": portfolio.history[-50:] # 최근 50건만 반환
    }

@app.get("/trades")
async def get_trades(symbol: str = "KRW-BTC", limit: int = 10):
    async with get_db_conn() as db:
        async with db.execute("SELECT trade_price, trade_volume, ask_bid, trade_timestamp FROM trades WHERE symbol = ? ORDER BY trade_timestamp DESC LIMIT ?", (symbol, limit)) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

@app.post("/data/cleanup")
async def cleanup_data(date: str):
    """지정된 날짜 이전의 데이터를 삭제합니다."""
    import datetime
    try:
        # ISO 날짜를 타임스탬프(ms)로 변환
        dt = datetime.datetime.fromisoformat(date)
        ts = int(dt.timestamp() * 1000)
        
        async with get_db_conn() as db:
            cursor = await db.execute("DELETE FROM trades WHERE trade_timestamp < ?", (ts,))
            deleted_count = cursor.rowcount
            await db.commit()
            return {"message": f"{deleted_count}개의 데이터가 삭제되었습니다.", "count": deleted_count}
    except Exception as e:
        return {"message": f"삭제 실패: {str(e)}", "count": 0}

@app.post("/api/portfolio/{portfolio_id}/panic")
async def panic_sell(portfolio_id: str):
    """모든 포지션을 즉시 시장가 청산하고 비상 정지합니다."""
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
                # 1. DB 거래 내역 저장 [NEW]
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

        # 4. 변경된 포트폴리오 상태 DB 영구 저장 [중요]
        await system.portfolio_manager.save_to_db(portfolio_id)

        return {"status": "success", "message": f"{len(results)}개 종목 청산 완료", "data": results}

    except Exception as e:
        print(f"[ERROR] Panic Sell Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


async def simulate_trade_from_engine(symbol: str, side: str, price: float, reason: str):
    """엔진에서 실시간 전략으로 발생한 모의 체결을 브로드캐스트합니다."""
    print(f"[ENGINE TRADE] {side} {symbol} at {price} (Reason: {reason})")
    trade_alert = {
        "type": "alert",
        "alert_type": "trade",
        "code": symbol,
        "price": price,
        "msg": f"🤖 [전략매매] {side} 주문: {symbol} ({reason})"
    }
    await manager.broadcast_global(trade_alert)
    await save_alert(trade_alert)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            text = await websocket.receive_text()
            try:
                msg = json.loads(text)
                # 클라이언트가 구독할 종목을 지정
                if 'subscribe' in msg:
                    manager.subscribe(websocket, msg['subscribe'])
            except json.JSONDecodeError:
                pass
    except WebSocketDisconnect:
        manager.disconnect(websocket)

if not os.path.exists("frontend"): os.makedirs("frontend")
app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
