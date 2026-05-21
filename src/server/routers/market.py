from fastapi import APIRouter, Request
from typing import Optional
from src.database.repository import SqliteMarketDataRepository
from src.engine.utils.stock_mapper import stock_mapper
from src.engine.utils.telemetry import get_logger

logger = get_logger(__name__)
router = APIRouter()
market_repo = SqliteMarketDataRepository()

@router.get("/market")
async def get_market(request: Request):
    """전체 마켓 종목 정보(한글명, 현재가, 변동률, 거래대금)를 반환합니다."""
    system = request.app.state.system
    results = await system.get_all_market_data()
    return results

@router.get("/symbols")
async def get_symbols(request: Request):
    """수집 가능한 전체 종목 목록을 반환합니다."""
    system = request.app.state.system
    all_symbols = []
    for collector in system.collectors:
        exch = getattr(collector, 'exchange', 'upbit')
        for s in getattr(collector, 'available_symbols', []):
            all_symbols.append({
                "exchange": exch, 
                "symbol": s,
                "name": stock_mapper.get_name(exch, s)
            })
    return all_symbols

@router.get("/candles")
async def get_candles(
    request: Request = None, 
    exchange: str = "upbit", 
    symbol: str = "BTC", 
    interval: int = 60, 
    limit: int = 500, 
    start_ts: int = None, 
    end_ts: int = None
):
    """최적화된 고성능 캔들 데이터 반환 (저장소 패턴 위임)"""
    system = request.app.state.system if request and hasattr(request.app.state, 'system') else None
    return await market_repo.get_candles(
        exchange=exchange,
        symbol=symbol,
        interval=interval,
        limit=limit,
        start_ts=start_ts,
        end_ts=end_ts,
        system_app_state_system=system
    )

@router.get("/restored-candles")
async def get_restored_candles(
    exchange: Optional[str] = None,
    symbol: Optional[str] = None,
    limit_minutes: int = 1440
):
    """DB에 누락되었으나 틱으로 복구된 캔들 목록 반환"""
    return await market_repo.get_restored_candles(
        exchange=exchange,
        symbol=symbol,
        limit_minutes=limit_minutes
    )


