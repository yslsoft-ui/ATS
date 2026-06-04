from fastapi import APIRouter, Request, HTTPException
import datetime
import time
import asyncio
from src.database.connection import get_db_conn
from src.server.websocket import manager
from src.engine.utils.telemetry import get_logger

logger = get_logger(__name__)
router = APIRouter()

@router.get("/alerts")
async def get_alerts(limit: int = 50):
    """최근 알림 기록을 반환합니다."""
    async with get_db_conn() as db:
        async with db.execute("SELECT * FROM alerts ORDER BY timestamp DESC LIMIT ?", (limit,)) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

@router.delete("/api/alerts")
async def clear_alerts():
    """모든 알림 기록을 삭제합니다."""
    async with get_db_conn() as db:
        await db.execute("DELETE FROM alerts")
        await db.commit()
    return {"message": "모든 알림 기록이 삭제되었습니다."}

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


@router.get("/test-alert")
async def test_alert(request: Request, symbol: str = "KRW-BTC"):
    """UI 확인용 테스트 알림을 강제로 발생시킵니다."""
    system = request.app.state.system
    mock_alert = {
        "type": "alert",
        "code": symbol,
        "price": 100000000,
        "change": 5.23,
        "buy_ratio": 88.5,
        "msg": f"🚀 [TEST] 급등 포착: {symbol} (+5.23%)"
    }
    await manager.broadcast_global(mock_alert)
    await system.save_alert(mock_alert)
    return {"message": f"Test alert for {symbol} sent to all clients and saved"}

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
    await manager.broadcast_global(mock_status)
    return {"message": f"Test status for {strategy_id} broadcasted"}

@router.get("/data/cleanup/preview")
async def cleanup_data_preview(date: str):
    """지정된 날짜 이전의 삭제 대상 데이터(체결 및 캔들) 건수를 미리 조회합니다."""
    try:
        dt = datetime.datetime.fromisoformat(date)
        ts = int(dt.timestamp() * 1000)
        ts_sec = ts // 1000
        
        async with get_db_conn() as db:
            # 1. 체결 데이터 카운트 (trade_timestamp는 밀리초 단위)
            async with db.execute("SELECT COUNT(*) FROM trades WHERE trade_timestamp < ?", (ts,)) as cursor:
                trades_count = (await cursor.fetchone())[0]
            
            # 2. 캔들 데이터 카운트 (timestamp는 초 단위)
            async with db.execute("SELECT COUNT(*) FROM candles WHERE timestamp < ?", (ts_sec,)) as cursor:
                candles_count = (await cursor.fetchone())[0]
                
            return {
                "trades_count": trades_count,
                "candles_count": candles_count,
                "total_count": trades_count + candles_count,
                "date": date
            }
    except Exception as e:
        logger.error(f"Cleanup preview failed: {e}")
        raise HTTPException(status_code=500, detail=f"조회 실패: {str(e)}")

@router.post("/data/cleanup")
async def cleanup_data(date: str, limit: int = 20000):
    """지정된 날짜 이전의 체결 데이터 및 캔들 데이터를 지정된 한도(limit) 내에서 삭제합니다. (청크 분할 및 DB 락 방지)"""
    try:
        dt = datetime.datetime.fromisoformat(date)
        ts = int(dt.timestamp() * 1000)
        ts_sec = ts // 1000
        
        async with get_db_conn() as db:
            # 1. trades 테이블 분할 삭제
            cursor_trades = await db.execute("""
                DELETE FROM trades 
                WHERE rowid IN (
                    SELECT rowid FROM trades 
                    WHERE trade_timestamp < ? 
                    LIMIT ?
                )
            """, (ts, limit))
            deleted_trades = cursor_trades.rowcount
            await db.commit()
            
            # 2. trades 삭제 완료 후 남은 한도가 있다면 candles 테이블 분할 삭제
            remaining_limit = limit - deleted_trades
            deleted_candles = 0
            
            if remaining_limit > 0:
                cursor_candles = await db.execute("""
                    DELETE FROM candles 
                    WHERE rowid IN (
                        SELECT rowid FROM candles 
                        WHERE timestamp < ? 
                        LIMIT ?
                    )
                """, (ts_sec, remaining_limit))
                deleted_candles = cursor_candles.rowcount
                await db.commit()
            
            return {
                "message": f"성공적으로 정리되었습니다. (체결: {deleted_trades}건, 캔들: {deleted_candles}건)",
                "deleted_trades": deleted_trades,
                "deleted_candles": deleted_candles,
                "total_deleted": deleted_trades + deleted_candles
            }
    except Exception as e:
        logger.error(f"Cleanup failed: {e}")
        raise HTTPException(status_code=500, detail=f"삭제 실패: {str(e)}")
