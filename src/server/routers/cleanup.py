# -*- coding: utf-8 -*-

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from src.engine.utils.telemetry import get_logger
from src.engine.command import UserCommand

logger = get_logger(__name__)
router = APIRouter(prefix="/api/cleanup", tags=["cleanup"])

# --- Pydantic 요청 스키마 정의 ---

class CleanupControlRequest(BaseModel):
    command_id: str



class CleanupPreviewRequest(BaseModel):
    date: str
    command_id: str


class CleanupRunRequest(BaseModel):
    date: str
    limit: int = 20000
    command_id: str


# --- REST API 엔드포인트 구현 ---

@router.get("/status")
async def get_cleanup_status(request: Request):
    """클린업 데몬의 실시간 상태 및 캐시된 이력을 조회합니다."""
    system = request.app.state.system
    status = system.cleanup_status
    if not status:
        # 데몬으로부터 텔레메트리 패킷을 아직 수신하지 못한 경우의 기본값
        return {
            "type": "market_cleanup_status",
            "cleanup_state": "ACTIVE",
            "is_running": False,
            "cleanup_interval": 3600,
            "trades_hours": 72,
            "candles_days": 30,
            "last_cleanup_time": 0,
            "next_cleanup_time": 0,
            "last_cleanup_duration_ms": 0,
            "last_cleanup_summary": {
                "trades_deleted": 0,
                "candles_deleted": 0,
                "candles_downsampled": 0
            },
            "last_error": None,
            "last_trades_cutoff": 0,
            "last_candles_cutoff": 0,
            "next_cleanup_target_trades": 0,
            "next_cleanup_target_candles": 0,
            "next_cleanup_target_downsample": 0,
            "next_cleanup_target_trades_cutoff": 0,
            "next_cleanup_target_candles_cutoff": 0,
            "pid": 0,
            "start_time": 0
        }
    return status


@router.post("/restart-daemon")
async def restart_cleanup_daemon(req: CleanupControlRequest, request: Request):
    """클린업 데몬 프로세스 자체를 자가 재기동시킵니다."""
    system = request.app.state.system
    payload = {"target": "market_cleanup_daemon", "command_id": req.command_id}
    try:
        await system.dispatcher.dispatch(
            UserCommand.CLEANUP_RESTART_DAEMON,
            payload
        )
        return {"status": "pending", "command_id": req.command_id}
    except Exception as e:
        logger.error(f"Failed to dispatch CLEANUP_RESTART_DAEMON: {e}")
        raise HTTPException(status_code=500, detail=str(e))





@router.post("/preview")
async def preview_cleanup_data(req: CleanupPreviewRequest, request: Request):
    """지정 날짜 이전의 삭제 대상 틱 건수를 데몬에 요청합니다."""
    system = request.app.state.system
    payload = {
        "date": req.date,
        "command_id": req.command_id
    }
    try:
        await system.dispatcher.dispatch(
            UserCommand.CLEANUP_PREVIEW,
            payload
        )
        return {"status": "pending", "command_id": req.command_id}
    except Exception as e:
        logger.error(f"Failed to dispatch CLEANUP_PREVIEW: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/run")
async def run_cleanup_data(req: CleanupRunRequest, request: Request):
    """지정 날짜 이전 데이터를 청크 단위로 영구 정리하도록 데몬에 수동 요청을 전송합니다."""
    system = request.app.state.system
    payload = {
        "date": req.date,
        "limit": req.limit,
        "command_id": req.command_id
    }
    try:
        await system.dispatcher.dispatch(
            UserCommand.CLEANUP_RUN_ONCE,
            payload
        )
        return {"status": "pending", "command_id": req.command_id}
    except Exception as e:
        logger.error(f"Failed to dispatch CLEANUP_RUN_ONCE: {e}")
        raise HTTPException(status_code=500, detail=str(e))
