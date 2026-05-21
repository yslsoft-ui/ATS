from fastapi import APIRouter, Request, HTTPException
from src.engine.utils.telemetry import get_logger

logger = get_logger(__name__)
router = APIRouter()

@router.get("/collector/status")
async def get_status(request: Request):
    """각 거래소별 수집기의 상세 작동 상태를 반환합니다."""
    system = request.app.state.system
    status_map = {}
    for c in system.collectors:
        exch = getattr(c, 'exchange', 'unknown')
        err = getattr(c, 'last_error', None)
        if not err and hasattr(c, 'cred_provider'):
            err = getattr(c.cred_provider, 'last_error', None)
            
        status_map[exch] = {
            "is_running": c.is_running,
            "error": err
        }
    return status_map

@router.post("/collector/start/{exchange}")
async def start_specific_collector(exchange: str, request: Request):
    """지정한 거래소의 수집기를 수동 시작합니다."""
    system = request.app.state.system
    for c in system.collectors:
        if getattr(c, 'exchange', '') == exchange:
            await c.start(system.config_manager.config)
            return {"message": f"{exchange} collector started"}
    raise HTTPException(status_code=404, detail=f"Collector for {exchange} not found")

@router.post("/collector/stop/{exchange}")
async def stop_specific_collector(exchange: str, request: Request):
    """지정한 거래소의 수집기를 수동 중단합니다."""
    system = request.app.state.system
    for c in system.collectors:
        if getattr(c, 'exchange', '') == exchange:
            await c.stop()
            return {"message": f"{exchange} collector stopped"}
    raise HTTPException(status_code=404, detail=f"Collector for {exchange} not found")

@router.post("/collector/start")
async def start_all_collectors(request: Request):
    """모든 수집기를 수동 기동합니다."""
    system = request.app.state.system
    for collector in system.collectors:
        await collector.start(system.config_manager.config)
    return {"message": "All collectors started"}

@router.post("/collector/stop")
async def stop_all_collectors(request: Request):
    """모든 수집기를 수동 중단합니다."""
    system = request.app.state.system
    for collector in system.collectors:
        await collector.stop()
    return {"message": "All collectors stopped"}
