from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
import os
import time
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
from src.server.routers.cleanup import router as cleanup_router
from src.server.routers.intelligence import router as intelligence_router
from src.server.routers.decision_console import router as decision_console_router

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
app.include_router(cleanup_router)
app.include_router(intelligence_router)
app.include_router(decision_console_router)


# ZeroMQ IPC 구독 태스크 정의
async def zmq_listener_loop():
    logger.info("[Web ZMQ Listener] Starting ZMQ listener loops...")
    market_sub = EventBusSubscriber("market_data")
    collector_sub = EventBusSubscriber("collector_signal")
    cleanup_sub = EventBusSubscriber("cleanup_signal")
    evaluation_sub = EventBusSubscriber("evaluation_signal")
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

    async def listen_collector():
        while True:
            try:
                topic, data = await collector_sub.receive()
                if not topic:
                    continue
                
                # [NEW] 실시간 수집기 종목 동기화 패킷 수신 시 캐시 및 브로드캐스트
                if data.get('type') == 'collector_symbols_sync':
                    exch = data.get('exchange')
                    if exch:
                        cached = system.collector_active_symbols.get(exch, {})
                        cached_ver = cached.get("symbols_version")
                        incoming_ver = data.get("symbols_version", 1)
                        if cached_ver == incoming_ver and cached.get("symbols"):
                            # 버전이 동일하고 이미 종목이 있으면, 타임스탬프만 갱신 (덮어쓰기 회피)
                            cached["synced_at"] = int(time.time() * 1000)
                            logger.info(f"[Web ZMQ Listener] {exch} 종목 목록 버전 동일 ({incoming_ver}) - 덮어쓰기 생략 및 시간 갱신")
                        else:
                            system.collector_active_symbols[exch] = {
                                "symbols": data.get("symbols", []),
                                "synced_at": int(time.time() * 1000),
                                "symbols_version": incoming_ver,
                                "source_pid": data.get("source_pid"),
                                "daemon_started_at": data.get("daemon_started_at")
                            }
                            logger.info(f"[Web ZMQ Listener] {exch} 종목 목록 동기화 캐시 갱신 완료 (버전: {incoming_ver})")
                
                elif data.get('type') == 'collector_daemon_detail':
                    data["synced_at"] = int(time.time() * 1000)
                    system.collector_daemon_detail = data.copy()
                    
                    # symbols_version 불일치 감지 및 10초 쿨다운 적용 자동 재동기화 트리거
                    daemon_versions = data.get("symbols_version", {})
                    for exch, daemon_ver in daemon_versions.items():
                        cached_ver = system.collector_active_symbols.get(exch, {}).get("symbols_version")
                        if cached_ver is not None and cached_ver != daemon_ver:
                            now_ms = int(time.time() * 1000)
                            # 동적으로 거래소 쿨다운 관리
                            last_req_time = getattr(app.state, 'last_request_symbols_sync_time', {}).get(exch, 0)
                            cooldown_ms = system.config_manager.get_monitoring_config()["request_symbols_sync_cooldown_ms"]
                            if now_ms - last_req_time > cooldown_ms:
                                if not hasattr(app.state, 'last_request_symbols_sync_time'):
                                    app.state.last_request_symbols_sync_time = {}
                                app.state.last_request_symbols_sync_time[exch] = now_ms
                                
                                # collector_control 토픽으로 직접 request_symbols_sync 퍼블리싱
                                if hasattr(app, 'state') and hasattr(app.state, 'control_publisher'):
                                    logger.warning(f"[Web ZMQ Listener] {exch} 종목 버전 불일치 감지 (로컬: {cached_ver} vs 데몬: {daemon_ver}). 재동기화 요청 송출.")
                                    await app.state.control_publisher.publish("collector_control", {
                                        "type": "request_symbols_sync",
                                        "exchange_id": exch
                                    })
                
                # 실시간 수집기 상태 패킷 수신 시 캐시 업데이트
                elif data.get('type') == 'collector_status':
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
                # [NEW] collector_command_result, collector_symbols_sync, collector_daemon_detail 타입 추가 브로드캐스트
                if data.get('type') in ['collector_status', 'queue_status', 'system_event', 'collector_command_result', 'collector_symbols_sync', 'collector_daemon_detail']:
                    from src.server.websocket import manager
                    await manager.broadcast_alert(data)
                elif system.broadcast_callback:
                    await system.broadcast_callback(data)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[Web ZMQ Collector Listener] Error: {e}")
                await asyncio.sleep(0.1)

    async def listen_cleanup():
        while True:
            try:
                topic, data = await cleanup_sub.receive()
                if not topic:
                    continue
                
                # 실시간 상태 패킷 수신 시 캐시 업데이트
                if data.get('type') == 'market_cleanup_status':
                    system.cleanup_status = data.copy()

                # 브로드캐스트 대상 이벤트 확장
                if data.get('type') in ['market_cleanup_status', 'system_event', 'cleanup_command_result']:
                    from src.server.websocket import manager
                    await manager.broadcast_alert(data)
                elif system.broadcast_callback:
                    await system.broadcast_callback(data)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[Web ZMQ Cleanup Listener] Error: {e}")
                await asyncio.sleep(0.1)

    async def listen_evaluation():
        while True:
            try:
                topic, data = await evaluation_sub.receive()
                if not topic:
                    continue
                if data.get('type') in ['shadow_eval_status', 'system_event']:
                    from src.server.websocket import manager
                    await manager.broadcast_alert(data)
                elif system.broadcast_callback:
                    await system.broadcast_callback(data)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[Web ZMQ Evaluation Listener] Error: {e}")
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
                elif data.get('type') == 'strategy_daemon_detail':
                    data["synced_at"] = int(time.time() * 1000)
                    system.strategy_daemon_detail = data.copy()

                # 실시간 전략 상태 및 주문 신호 발생 시 브로드캐스트 호출
                if data.get('type') in ['strategy_status', 'strategy_daemon_detail', 'strategy_command_result', 'system_event']:
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
    t2 = asyncio.create_task(listen_collector())
    t3 = asyncio.create_task(listen_cleanup())
    t4 = asyncio.create_task(listen_evaluation())
    t5 = asyncio.create_task(listen_strategy_signal())
    
    try:
        await asyncio.gather(t1, t2, t3, t4, t5)
    except asyncio.CancelledError:
        pass
    finally:
        t1.cancel()
        t2.cancel()
        t3.cancel()
        t4.cancel()
        t5.cancel()
        market_sub.close()
        collector_sub.close()
        cleanup_sub.close()
        evaluation_sub.close()
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
    # [NEW] 동적 재동기화 쿨다운 딕셔너리 초기화
    app.state.last_request_symbols_sync_time = {}
    # ZMQ 제어 Publisher 생성
    app.state.control_publisher = EventBusPublisher("collector_control")
    app.state.strategy_control_publisher = EventBusPublisher("strategy_control")
    app.state.cleanup_control_publisher = EventBusPublisher("market_cleanup_control")
    system.dispatcher.set_publishers(
        control_publisher=app.state.control_publisher,
        strategy_control_publisher=app.state.strategy_control_publisher,
        cleanup_control_publisher=app.state.cleanup_control_publisher
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
    if hasattr(app.state, 'cleanup_control_publisher'):
        app.state.cleanup_control_publisher.close()
        logger.info("[Web Server] ZMQ cleanup control publisher closed.")
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
                    # {"subscribe": "BTC", "exchange_id": "upbit"}
                    manager.subscribe(websocket, msg.get('exchange_id', msg.get('exchange', 'upbit')), msg['subscribe'])
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
