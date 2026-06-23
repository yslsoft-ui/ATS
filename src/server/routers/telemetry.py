from fastapi import APIRouter, Request, HTTPException
import datetime
import time
import asyncio
from src.database.connection import get_db_conn
from src.server.websocket import manager
from src.engine.utils.telemetry import get_logger

logger = get_logger(__name__)
router = APIRouter()

@router.get("/api/system/queues")
async def get_queue_status(request: Request):
    """각 작업 큐의 현재 적체량 및 누적 처리량을 반환합니다."""
    system = request.app.state.system
    
    if not hasattr(system, "collectors"):
        return system.queue_status
        
    total_count = sum(getattr(c, 'total_processed_count', 0) for c in system.collectors)
    # 각 거래소 수집기가 격리 큐를 가짐에 따라, 큐 크기의 총합을 산출하여 UI에 누적 적체량을 리포트합니다.
    processing_size = sum(c.processing_queue.qsize() for c in system.collectors)
    return {
        "processing": processing_size,
        "database": system.db_writer.db_queue.qsize() if hasattr(system, 'db_writer') and hasattr(system.db_writer, 'db_queue') else 0,
        "candle": system.db_writer.candle_queue.qsize() if hasattr(system, 'db_writer') and hasattr(system.db_writer, 'candle_queue') else 0,
        "total": total_count
    }


@router.get("/test-notification")
async def test_notification(request: Request, symbol: str = "KRW-BTC"):
    """UI 확인용 테스트 알림을 강제로 발생시킵니다."""
    system = request.app.state.system
    exchange_id = "kis" if symbol.isdigit() or len(symbol) == 6 else "upbit"
    import time
    mock_notification = {
        "type": "alert",
        "notification_type": "trade",
        "exchange_id": exchange_id,
        "code": symbol,
        "price": 100000000,
        "change": 5.23,
        "buy_ratio": 88.5,
        "msg": f"🚀 [TEST] 급등 포착: {symbol} (+5.23%)",
        "timestamp": int(time.time() * 1000)
    }
    await manager.broadcast_alert(mock_notification)
    return {"message": f"Test notification for {symbol} sent to all clients"}

@router.get("/test-status")
async def test_strategy_status(strategy_id: str = "rsistrategy"):
    """UI 확인용 테스트 전략 상태 메시지를 강제로 발생시킵니다."""
    mock_status = {
        "type": "strategy_status",
        "strategy_id": strategy_id,
        "symbol": "KRW-BTC",
        "indicators": {
            "rsi": 42.5,
            "price": 95000000,
            "signal_gap": -7.5
        },
        "last_action": "WATCHING"
    }
    await manager.broadcast_alert(mock_status)
    return {"message": f"Test status for {strategy_id} broadcasted"}

from pydantic import BaseModel

class SettingUpdate(BaseModel):
    value: str

@router.get("/api/system/settings/{key}")
async def get_system_setting(key: str):
    """지정된 키의 시스템 설정을 조회합니다."""
    async with get_db_conn() as db:
        async with db.execute("SELECT value FROM system_settings WHERE key = ?", (key,)) as cursor:
            row = await cursor.fetchone()
            if row:
                return {"key": key, "value": row[0]}
            return {"key": key, "value": None}

@router.post("/api/system/settings/{key}")
async def set_system_setting(key: str, data: SettingUpdate):
    """지정된 키의 시스템 설정을 저장/업데이트합니다."""
    async with get_db_conn() as db:
        await db.execute(
            "INSERT OR REPLACE INTO system_settings (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
            (key, data.value)
        )
        await db.commit()
    return {"key": key, "value": data.value}



