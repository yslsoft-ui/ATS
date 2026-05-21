from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
import os
import json
from src.engine.system import TradingSystem
from src.server.websocket import manager
from src.engine.utils.telemetry import setup_logging, get_logger, update_broadcast_callback

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

# 전역 시스템 인스턴스 초기화
CONFIG_PATH = os.path.join(os.getcwd(), 'config', 'settings.yaml')
system = TradingSystem(CONFIG_PATH)

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

@app.on_event("startup")
async def startup_event():
    # TradingSystem 기동 (내부에서 DB 초기화, 포트폴리오 로드, 전략 로드, 수집기 시작을 모두 수행)
    await system.boot()
    logger.info("시스템 모든 구성 요소가 TradingSystem을 통해 시작되었습니다.")

@app.on_event("shutdown")
async def startup_event():
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
