from fastapi import APIRouter, Request, HTTPException
from src.engine.utils.telemetry import get_logger
from src.engine.command import UserCommand

logger = get_logger(__name__)
router = APIRouter()

@router.get("/collector/status")
async def get_status(request: Request):
    """각 거래소별 수집기 및 전략 엔진의 상세 작동 상태를 반환합니다."""
    system = request.app.state.system
    
    status_map = {}
    # ZMQ를 통해 갱신된 전역 캐시 상태를 사용
    for exch, status in system.collector_statuses.items():
        status_map[exch] = status.copy()
            
    # 전략 엔진 상태 헬스체크 및 병합
    import time
    strategy_status = system.strategy_status
    is_strat_running = strategy_status.get("is_running", False)
    # 마지막 허트비트 수신 시각 기준으로 12초 이상 무반응 시 오프라인(STOPPED) 처리
    if is_strat_running and (time.time() - strategy_status.get("last_heartbeat", 0.0) > 12.0):
        is_strat_running = False
        
    status_map["strategy"] = {
        "is_running": is_strat_running,
        "active_engines": strategy_status.get("active_engines", 0) if is_strat_running else 0,
        "error": strategy_status.get("error", None) if is_strat_running else None
    }
    
    return status_map

@router.get("/collector/system-events")
async def get_system_events(request: Request, limit: int = 20):
    """최근의 시스템 운영 및 시장정지 이력 목록을 반환합니다."""
    system = request.app.state.system
    try:
        events = await system.repository.get_system_events(limit=limit)
        return events
    except Exception as e:
        logger.error(f"Failed to fetch system events: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch system events: {str(e)}")

@router.post("/collector/start/{exchange}")
async def start_specific_collector(exchange: str, request: Request, command_id: str = None):
    """지정한 거래소의 수집기를 수동 시작합니다."""
    system = request.app.state.system
    payload = {"exchange": exchange}
    if command_id:
        payload["command_id"] = command_id
    try:
        await system.dispatcher.dispatch(
            UserCommand.COLLECTOR_START,
            payload
        )
        return {"message": f"{exchange} collector start requested", "command_id": payload.get("command_id")}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/collector/stop/{exchange}")
async def stop_specific_collector(exchange: str, request: Request, command_id: str = None):
    """지정한 거래소의 수집기를 수동 중단합니다."""
    system = request.app.state.system
    payload = {"exchange": exchange}
    if command_id:
        payload["command_id"] = command_id
    try:
        await system.dispatcher.dispatch(
            UserCommand.COLLECTOR_STOP,
            payload
        )
        return {"message": f"{exchange} collector stop requested", "command_id": payload.get("command_id")}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/collector/start")
async def start_all_collectors(request: Request, command_id: str = None):
    """모든 수집기를 수동 기동합니다."""
    system = request.app.state.system
    payload = {"exchange": "all"}
    if command_id:
        payload["command_id"] = command_id
    try:
        await system.dispatcher.dispatch(
            UserCommand.COLLECTOR_START,
            payload
        )
        return {"message": "All collectors start requested", "command_id": payload.get("command_id")}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/collector/stop")
async def stop_all_collectors(request: Request, command_id: str = None):
    """모든 수집기를 수동 중단합니다."""
    system = request.app.state.system
    payload = {"exchange": "all"}
    if command_id:
        payload["command_id"] = command_id
    try:
        await system.dispatcher.dispatch(
            UserCommand.COLLECTOR_STOP,
            payload
        )
        return {"message": "All collectors stop requested", "command_id": payload.get("command_id")}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/collector/restart-daemon")
async def restart_collector_daemon(request: Request, command_id: str = None):
    """수집기 데몬 프로세스 자체를 자가 재기동시킵니다."""
    system = request.app.state.system
    payload = {"target": "collector_daemon"}
    if command_id:
        payload["command_id"] = command_id
    try:
        await system.dispatcher.dispatch(
            UserCommand.COLLECTOR_RESTART_DAEMON,
            payload
        )
        return {"message": "Collector daemon restart signal published successfully", "command_id": payload.get("command_id")}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/collector/daemon-detail")
async def get_daemon_detail(request: Request):
    """수집기 데몬의 실시간 큐, 거래소별 틱 수신 상태, 메모리 및 정합성 유효성을 진단하여 반환합니다."""
    system = request.app.state.system
    import time
    now_ms = int(time.time() * 1000)
    
    daemon_detail_stale = {}
    active_symbols_stale = {}
    symbols_version_mismatch = {}
    symbols_stale = {}
    
    # 활성 상태 수집기들 기준 루프 처리 (하드코딩 방지)
    exchanges = list(system.collector_statuses.keys())
    for exch in exchanges:
        # 1) daemon_detail_stale 검증 (하트비트 5초 주기, 15초 이상 수신 지연 시)
        detail_synced_at = system.collector_daemon_detail.get("synced_at", 0)
        daemon_detail_stale[exch] = (now_ms - detail_synced_at > 15000) if detail_synced_at > 0 else True
        
        # 2) active_symbols_stale 검증 (30초 정기/동적 동기화 주기, 75초 이상 수신 지연 시)
        sym_info = system.collector_active_symbols.get(exch, {})
        symbols_synced_at = sym_info.get("synced_at", 0)
        active_symbols_stale[exch] = (now_ms - symbols_synced_at > 75000) if symbols_synced_at > 0 else True
        
        # 3) symbols_version_mismatch 검증 (데몬 측과 웹서버 캐시 버전이 어긋날 시)
        daemon_ver = system.collector_daemon_detail.get("symbols_version", {}).get(exch)
        cached_ver = sym_info.get("symbols_version")
        symbols_version_mismatch[exch] = (daemon_ver is not None and cached_ver != daemon_ver)
        
        # 4) symbols_stale 종합 검증 (동기화 지연 또는 버전 불일치 발생 시)
        symbols_stale[exch] = active_symbols_stale[exch] or symbols_version_mismatch[exch]
        
    # defaultdict 방지용 딕셔너리 안전 정제 처리
    clean_daemon_detail = system.collector_daemon_detail.copy()
    if "symbols_version" in clean_daemon_detail:
        clean_daemon_detail["symbols_version"] = dict(clean_daemon_detail["symbols_version"])
        
    return {
        "daemon_detail": clean_daemon_detail,
        "active_symbols": {ex: info.get("symbols", []) for ex, info in system.collector_active_symbols.items()},
        "active_symbols_metadata": {ex: {
            "synced_at": info.get("synced_at"),
            "symbols_version": info.get("symbols_version"),
            "source_pid": info.get("source_pid"),
            "daemon_started_at": info.get("daemon_started_at"),
            "age_ms": (now_ms - info.get("synced_at", 0)) if info.get("synced_at") else None
        } for ex, info in system.collector_active_symbols.items()},
        "stale_status": {
            "daemon_detail_stale": daemon_detail_stale,
            "active_symbols_stale": active_symbols_stale,
            "symbols_version_mismatch": symbols_version_mismatch,
            "symbols_stale": symbols_stale
        }
    }
