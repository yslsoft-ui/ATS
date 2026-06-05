from fastapi import APIRouter, Request, HTTPException
from src.engine.utils.telemetry import get_logger

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

@router.post("/collector/start/{exchange}")
async def start_specific_collector(exchange: str, request: Request):
    """지정한 거래소의 수집기를 수동 시작합니다."""
    system = request.app.state.system
    
    # 설정 파일의 exchanges.<exch>.enabled 값을 True로 변경 및 저장 (수집 데몬이 파일 감시로 기동하게 됨)
    config_key = f"exchanges.{exchange}"
    exch_config = system.config_manager.get(config_key)
    if exch_config is not None:
        system.config_manager.update(f"{config_key}.enabled", True)
        return {"message": f"{exchange} collector start requested via configuration update"}
    raise HTTPException(status_code=404, detail=f"Configuration for exchange '{exchange}' not found")

@router.post("/collector/stop/{exchange}")
async def stop_specific_collector(exchange: str, request: Request):
    """지정한 거래소의 수집기를 수동 중단합니다."""
    system = request.app.state.system
    
    # 설정 파일의 exchanges.<exch>.enabled 값을 False로 변경 및 저장 (수집 데몬이 파일 감시로 중단하게 됨)
    config_key = f"exchanges.{exchange}"
    exch_config = system.config_manager.get(config_key)
    if exch_config is not None:
        system.config_manager.update(f"{config_key}.enabled", False)
        return {"message": f"{exchange} collector stop requested via configuration update"}
    raise HTTPException(status_code=404, detail=f"Configuration for exchange '{exchange}' not found")

@router.post("/collector/start")
async def start_all_collectors(request: Request):
    """모든 수집기를 수동 기동합니다."""
    system = request.app.state.system
    
    exchanges_config = system.config_manager.get('exchanges', {})
    for exch in exchanges_config.keys():
        system.config_manager.update(f"exchanges.{exch}.enabled", True)
    return {"message": "All collectors start requested via configuration update"}

@router.post("/collector/stop")
async def stop_all_collectors(request: Request):
    """모든 수집기를 수동 중단합니다."""
    system = request.app.state.system
    
    exchanges_config = system.config_manager.get('exchanges', {})
    for exch in exchanges_config.keys():
        system.config_manager.update(f"exchanges.{exch}.enabled", False)
    return {"message": "All collectors stop requested via configuration update"}

@router.post("/collector/restart-daemon")
async def restart_collector_daemon(request: Request):
    """수집기 데몬 프로세스 자체를 자가 재기동시킵니다."""
    publisher = request.app.state.control_publisher
    await publisher.publish("collector_control", {"type": "restart_daemon"})
    return {"message": "Collector daemon restart signal published successfully"}


