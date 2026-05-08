from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import aiosqlite
import os
import asyncio
from src.collector.upbit_ws import UpbitCollector, DBWriter
from src.engine.backtest import BacktestEngine

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'data', 'backtest.db')

# 전역 변수로 수집기 상태 관리
collector_task = None
stop_event = None

app = FastAPI(title="Trading System API", version="1.0.0")

# 프론트엔드(React) 연동을 위한 CORS 설정
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 실제 운영 시에는 로컬호스트 등 명시적 도메인으로 한정
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class BacktestRequest(BaseModel):
    symbol: str
    start_date: str
    end_date: str
    initial_cash: float

@app.get("/api/status")
async def get_status():
    """시스템의 현재 연결 상태와 버전 정보를 반환합니다."""
    return {
        "status": "online",
        "database": "connected",
        "collector": "running" if collector_task and not collector_task.done() else "stopped",
        "version": "1.0.0"
    }

@app.post("/api/collector/start")
async def start_collector():
    global collector_task, stop_event
    if collector_task and not collector_task.done():
        return {"status": "error", "message": "Collector is already running"}
    
    stop_event = asyncio.Event()
    queue = asyncio.Queue()
    
    async def run_collector_logic():
        collector = UpbitCollector(queue)
        writer = DBWriter(queue, DB_PATH)
        
        # 가동
        tasks = [
            asyncio.create_task(collector.connect_and_listen(["KRW-BTC"])),
            asyncio.create_task(writer.run())
        ]
        
        # 중단 이벤트 대기
        await stop_event.wait()
        
        # 정리
        for t in tasks: t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    collector_task = asyncio.create_task(run_collector_logic())
    return {"status": "success", "message": "Collector started"}

@app.post("/api/collector/stop")
async def stop_collector():
    global stop_event
    if stop_event:
        stop_event.set()
        return {"status": "success", "message": "Collector stop signal sent"}
    return {"status": "error", "message": "Collector is not running"}

@app.get("/api/trades/{symbol}")
async def get_recent_trades(symbol: str, limit: int = 50):
    """지정된 종목의 최근 체결 데이터를 반환합니다."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute('''
                SELECT trade_timestamp, trade_price, trade_volume, ask_bid
                FROM trades
                WHERE symbol = ?
                ORDER BY trade_timestamp DESC
                LIMIT ?
            ''', (symbol, limit))
            rows = await cursor.fetchall()
            
            # Recharts 표시를 위해 과거 시간부터 오름차순으로 뒤집기
            results = []
            for r in reversed(rows):
                results.append({
                    "timestamp": r["trade_timestamp"],
                    "price": r["trade_price"],
                    "volume": r["trade_volume"],
                    "ask_bid": r["ask_bid"]
                })
            return {"status": "success", "data": results}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/api/candles/{symbol}")
async def get_candles(symbol: str, interval: int = 60):
    """
    틱 데이터를 기반으로 캔들(OHLC) 데이터를 생성하고 기술 지표를 추가하여 반환합니다.
    """
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            query = f'''
                SELECT 
                    (trade_timestamp / (1000 * {interval})) * {interval} as candle_time,
                    MIN(trade_price) as low,
                    MAX(trade_price) as high,
                    CAST(SUBSTR(MIN(PRINTF('%015d', trade_timestamp) || trade_price), 16) AS REAL) as open,
                    CAST(SUBSTR(MAX(PRINTF('%015d', trade_timestamp) || trade_price), 16) AS REAL) as close,
                    SUM(trade_volume) as volume
                FROM trades
                WHERE symbol = ?
                GROUP BY candle_time
                ORDER BY candle_time ASC
            '''
            cursor = await db.execute(query, (symbol,))
            rows = await cursor.fetchall()
            
            candles = []
            prices = []
            for r in rows:
                candles.append({
                    "time": r["candle_time"],
                    "open": r["open"],
                    "high": r["high"],
                    "low": r["low"],
                    "close": r["close"]
                })
                prices.append(r["close"])
            
            # 기술 지표 계산 (이동평균, 볼린저 밴드 등)
            if len(candles) >= 20:
                for i in range(len(candles)):
                    if i < 19: continue
                    window = prices[i-19:i+1]
                    sma = sum(window) / 20
                    std = (sum((x - sma) ** 2 for x in window) / 20) ** 0.5
                    candles[i]["sma"] = round(sma, 2)
                    candles[i]["bb_upper"] = round(sma + (2 * std), 2)
                    candles[i]["bb_lower"] = round(sma - (2 * std), 2)

            return {"status": "success", "data": candles}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.post("/api/backtest/run")
async def run_backtest(req: BacktestRequest):
    """지정된 파라미터로 백테스트 엔진을 실행합니다."""
    engine = BacktestEngine(DB_PATH)
    result = await engine.run(req.symbol, req.initial_cash)
    return result

if __name__ == "__main__":
    import uvicorn
    # 로컬 테스트용 서버 실행 (8000 포트)
    uvicorn.run("src.api.main:app", host="127.0.0.1", port=8000, reload=True)
