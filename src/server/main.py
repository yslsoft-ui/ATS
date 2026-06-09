from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
import os
import json
import asyncio
from src.engine.system import TradingSystem
from src.server.websocket import manager
from src.engine.utils.telemetry import setup_logging, get_logger, update_broadcast_callback
from src.ipc.bus import EventBusSubscriber, EventBusPublisher

# 라우터 임포트
from src.server.routers.market import router as market_router
from src.server.routers.collector import router as collector_router
from src.server.routers.strategy import router as strategy_router
from src.server.routers.portfolio import router as portfolio_router
from src.server.routers.telemetry import router as telemetry_router
from src.server.routers.backtest import router as backtest_router
from src.server.routers.intelligence import router as intelligence_router

# 로깅 시스템 초기화 (초기 단계)
setup_logging()
logger = get_logger(__name__)

app = FastAPI()
logger.info("ATS Server is starting up...")

# 전역 시스템 인스턴스 초기화 (Web-only 모드로 설정)
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
app.include_router(backtest_router)
app.include_router(intelligence_router)


# ZeroMQ IPC 구독 태스크 정의
async def zmq_listener_loop():
    logger.info("[Web ZMQ Listener] Starting ZMQ listener loops...")
    market_sub = EventBusSubscriber("market_data")
    signal_sub = EventBusSubscriber("signal_data")
    strategy_sub = EventBusSubscriber("strategy_signal")
    
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
                
                # 실시간 수집기 상태 패킷 수신 시 캐시 업데이트
                if data.get('type') == 'collector_status':
                    exch = data.get('exchange')
                    if exch:
                        prev_status = system.collector_statuses.get(exch, {})
                        prev_running = prev_status.get('is_running')
                        current_running = data.get('is_running', False)
                        
                        if prev_running != current_running:
                            logger.info(f"[Web ZMQ Signal Listener] 수집기 상태 변경 감지: exch={exch}, is_running={prev_running} -> {current_running}")
                        else:
                            logger.debug(f"[Web ZMQ Signal Listener] 수집기 하트비트 수신: exch={exch}, is_running={current_running}")
                            
                        system.collector_statuses[exch] = {
                            "is_running": current_running,
                            "status": data.get('status', 'STOPPED'),
                            "status_reason": data.get('status_reason', None),
                            "error": data.get('error', None)
                        }
                elif data.get('type') == 'queue_status':
                    system.queue_status = {
                        "processing": data.get('processing', 0),
                        "database": data.get('database', 0),
                        "candle": data.get('candle', 0),
                        "total": data.get('total', 0)
                    }

                # 실시간 상태 패킷 브로드캐스트 호출
                if data.get('type') in ['collector_status', 'queue_status', 'system_event']:
                    from src.server.websocket import manager
                    await manager.broadcast_alert(data)
                elif system.broadcast_callback:
                    await system.broadcast_callback(data)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[Web ZMQ Signal Listener] Error: {e}")
                await asyncio.sleep(0.1)

    async def listen_strategy_signal():
        while True:
            try:
                topic, data = await strategy_sub.receive()
                if not topic:
                    continue
                
                # 실시간 제안 등록 수신 시 AutoScheduler에 알림 전송
                if data.get('type') == 'proposal_created' and hasattr(app.state, 'scheduler'):
                    proposal_id = data.get('proposal_id')
                    if proposal_id:
                        await app.state.scheduler.notify_proposal_created(proposal_id)

                # 실시간 전략 엔진 상태 패킷 수신 시 캐시 업데이트
                if data.get('type') == 'strategy_status' and 'strategy_id' not in data:
                    import time
                    prev_running = system.strategy_status.get('is_running')
                    prev_engines = system.strategy_status.get('active_engines')
                    current_running = data.get('is_running', False)
                    current_engines = data.get('active_engines', 0)
                    
                    if prev_running != current_running or prev_engines != current_engines:
                        logger.info(f"[Web ZMQ Strategy Listener] 전략 엔진 상태 변경 감지: is_running={prev_running} -> {current_running}, active_engines={prev_engines} -> {current_engines}")
                    else:
                        logger.debug(f"[Web ZMQ Strategy Listener] 전략 엔진 하트비트 수신: is_running={current_running}, active_engines={current_engines}")
                        
                    system.strategy_status = {
                        "is_running": current_running,
                        "active_engines": current_engines,
                        "last_heartbeat": time.time(),
                        "error": data.get('error', None)
                    }

                # 실시간 전략 상태 및 주문 신호 발생 시 브로드캐스트 호출
                if data.get('type') == 'strategy_status':
                    from src.server.websocket import manager
                    await manager.broadcast_alert(data)
                elif system.broadcast_callback:
                    await system.broadcast_callback(data)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[Web ZMQ Strategy Listener] Error: {e}")
                await asyncio.sleep(0.1)

    t1 = asyncio.create_task(listen_market())
    t2 = asyncio.create_task(listen_signal())
    t3 = asyncio.create_task(listen_strategy_signal())
    
    try:
        await asyncio.gather(t1, t2, t3)
    except asyncio.CancelledError:
        pass
    finally:
        t1.cancel()
        t2.cancel()
        t3.cancel()
        market_sub.close()
        signal_sub.close()
        strategy_sub.close()
        logger.info("[Web ZMQ Listener] ZMQ listener loops stopped and sockets closed.")

@app.on_event("startup")
async def startup_event():
    # TradingSystem 기동 (db_writer 및 수집기는 제외)
    await system.boot()
    # 웹서버 기동 이력 적재
    try:
        await system.repository.check_and_report_previous_crash('web_server')
        await system.repository.insert_system_event('DAEMON_START', 'web_server', '웹 API 서버 기동 완료')
    except Exception as e:
        logger.error(f"Failed to insert web server startup event: {e}")
    # ZMQ 제어 Publisher 생성
    app.state.control_publisher = EventBusPublisher("collector_control")
    app.state.strategy_control_publisher = EventBusPublisher("strategy_control")
    system.dispatcher.set_publishers(
        control_publisher=app.state.control_publisher,
        strategy_control_publisher=app.state.strategy_control_publisher
    )
    # ZMQ 구독 비동기 루프 기동
    app.state.zmq_loop_task = asyncio.create_task(zmq_listener_loop())
    
    # Hybrid Event-driven Scheduler 초기화 및 등록
    from src.engine.auto_scheduler import HybridAutoApplyScheduler
    app.state.scheduler = HybridAutoApplyScheduler(db_path=system.repository.db_path)
    enable_auto = system.config_manager.config.get("system", {}).get("enable_auto_proposal", False)
    app.state.scheduler.set_auto_proposal_enabled(enable_auto)
    
    # Counterfactual Sampling Tracker 초기화 및 기동
    from src.engine.counterfactual_tracker import CounterfactualSamplingTracker
    app.state.counterfactual_tracker = CounterfactualSamplingTracker(db_path=system.repository.db_path)
    await app.state.counterfactual_tracker.start()
    
    logger.info("시스템 모든 구성 요소가 TradingSystem 및 AutoScheduler와 함께 시작되었습니다. (Web-only + ZMQ Listener)")

@app.on_event("shutdown")
async def shutdown_event():
    # Counterfactual Tracker 종료
    if hasattr(app.state, 'counterfactual_tracker'):
        await app.state.counterfactual_tracker.stop()
        
    # AutoScheduler 종료
    if hasattr(app.state, 'scheduler'):
        await app.state.scheduler.close()
    # ZMQ 구독 중단
    if hasattr(app.state, 'zmq_loop_task'):
        app.state.zmq_loop_task.cancel()
        try:
            await app.state.zmq_loop_task
        except asyncio.CancelledError:
            pass
    # ZMQ 제어 Publisher 종료
    if hasattr(app.state, 'control_publisher'):
        app.state.control_publisher.close()
        logger.info("[Web Server] ZMQ control publisher closed.")
    if hasattr(app.state, 'strategy_control_publisher'):
        app.state.strategy_control_publisher.close()
        logger.info("[Web Server] ZMQ strategy control publisher closed.")
    # 웹서버 종료 이력 적재
    try:
        await system.repository.insert_system_event('DAEMON_STOP', 'web_server', '웹 API 서버 안전 종료 완료')
    except Exception as e:
        logger.error(f"Failed to insert web server shutdown event: {e}")
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
