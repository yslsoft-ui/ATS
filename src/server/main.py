from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
import os
import json
import asyncio
from src.engine.system import TradingSystem
from src.server.websocket import manager
from src.engine.utils.telemetry import setup_logging, get_logger, update_broadcast_callback
from src.ipc.bus import EventBusSubscriber

# 라우터 임포트
from src.server.routers.market import router as market_router
from src.server.routers.collector import router as collector_router
from src.server.routers.strategy import router as strategy_router
from src.server.routers.portfolio import router as portfolio_router
from src.server.routers.telemetry import router as telemetry_router

# 로깅 시스템 초기화 (초기 단계)
setup_logging()
logger = get_logger(__name__)

app = FastAPI()
logger.info("ATS Server is starting up...")

# 전역 시스템 인스턴스 초기화 (Web-only 모드로 설정)
CONFIG_PATH = os.path.join(os.getcwd(), 'config', 'settings.yaml')
system = TradingSystem(CONFIG_PATH, is_web_only=True)

# FastAPI state에 싱글톤 보관 (APIRouter 연동용)
app.state.system = system

# 시스템 및 로깅 웹소켓 콜백 설정
system.broadcast_callback = manager.broadcast
update_broadcast_callback(manager.broadcast_alert)

# 라우터 등록
app.include_router(market_router)
app.include_router(collector_router)
app.include_router(strategy_router)
app.include_router(portfolio_router)
app.include_router(telemetry_router)

# ZeroMQ IPC 구독 태스크 정의
async def zmq_listener_loop():
    logger.info("[Web ZMQ Listener] Starting ZMQ listener loops...")
    market_sub = EventBusSubscriber("market_data")
    signal_sub = EventBusSubscriber("signal_data")
    
    async def listen_market():
        while True:
            try:
                topic, data = await market_sub.receive()
                if not topic:
                    continue
                # 실시간 마켓 데이터를 TradingSystem에 캐싱 및 웹 브로드캐스트 위임
                await system._handle_market_data(data)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[Web ZMQ Market Listener] Error: {e}")
                await asyncio.sleep(0.1)

    async def listen_signal():
        while True:
            try:
                topic, data = await signal_sub.receive()
                if not topic:
                    continue
                # 실시간 전략 신호/체결 알림 발생 시 브로드캐스트 콜백 호출
                if system.broadcast_callback:
                    await system.broadcast_callback(data)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[Web ZMQ Signal Listener] Error: {e}")
                await asyncio.sleep(0.1)

    t1 = asyncio.create_task(listen_market())
    t2 = asyncio.create_task(listen_signal())
    
    try:
        await asyncio.gather(t1, t2)
    except asyncio.CancelledError:
        pass
    finally:
        t1.cancel()
        t2.cancel()
        market_sub.close()
        signal_sub.close()
        logger.info("[Web ZMQ Listener] ZMQ listener loops stopped and sockets closed.")

@app.on_event("startup")
async def startup_event():
    # TradingSystem 기동 (db_writer 및 수집기는 제외)
    await system.boot()
    # ZMQ 구독 비동기 루프 기동
    app.state.zmq_loop_task = asyncio.create_task(zmq_listener_loop())
    logger.info("시스템 모든 구성 요소가 TradingSystem을 통해 시작되었습니다. (Web-only + ZMQ Listener)")

@app.on_event("shutdown")
async def shutdown_event():
    # ZMQ 구독 중단
    if hasattr(app.state, 'zmq_loop_task'):
        app.state.zmq_loop_task.cancel()
        try:
            await app.state.zmq_loop_task
        except asyncio.CancelledError:
            pass
    await system.shutdown()
    logger.info("Shutdown complete.")

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
                    # {"subscribe": "BTC", "exchange": "upbit"}
                    manager.subscribe(websocket, msg.get('exchange', 'upbit'), msg['subscribe'])
            except json.JSONDecodeError:
                pass
    except WebSocketDisconnect:
        manager.disconnect(websocket)

if not os.path.exists("frontend"):
    os.makedirs("frontend")
app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
