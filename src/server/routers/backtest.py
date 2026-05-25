from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from typing import Dict, Any, List, Union
from datetime import datetime
from src.engine.backtest import BacktestEngine
from src.server.routers.strategy import list_strategies
from src.database.connection import get_db_conn

router = APIRouter()

class BacktestRunRequest(BaseModel):
    exchange: str
    symbol: str
    start_date: str  # ISO8601 포맷 (예: 2026-05-23T00:00)
    end_date: str    # ISO8601 포맷 (예: 2026-05-25T00:00)
    initial_cash: Union[float, Dict[str, float]]
    strategies: Dict[str, Dict[str, Any]]  # { "macd_strategy": { "enabled": true, "params": { "interval": 60 } } }
    risk_limits_enabled: bool = True
    slippage_rate: float = 0.001

@router.post("/api/backtest/run")
async def run_backtest(req: BacktestRunRequest, request: Request):
    """틱 리플레이 기반 백테스트를 실행합니다 (단일 자금 풀 다중 종목 지원)."""
    import time
    start_time = time.time()
    system = request.app.state.system
    
    # ISO8601 문자열을 로컬 시간 기준 타임스탬프(ms)로 변환
    try:
        start_str = req.start_date.replace(" ", "T")
        end_str = req.end_date.replace(" ", "T")
        
        start_dt = datetime.fromisoformat(start_str)
        end_dt = datetime.fromisoformat(end_str)
        
        start_ts = int(start_dt.timestamp() * 1000)
        end_ts = int(end_dt.timestamp() * 1000)
    except ValueError as e:
        raise HTTPException(
            status_code=400, 
            detail=f"시작일 및 종료일의 날짜 포맷이 올바르지 않습니다 (ISO8601 필요). 에러: {str(e)}"
        )

    if start_ts >= end_ts:
        raise HTTPException(status_code=400, detail="시작일은 종료일보다 빨라야 합니다.")

    # 1. 대상 거래소 및 심볼 목록 결정
    target_exchange = req.exchange.strip().lower()
    raw_symbol = req.symbol.strip()
    
    requested_symbols = []
    if raw_symbol and raw_symbol.upper() != "ALL":
        requested_symbols = [s.strip().upper() for s in raw_symbol.split(",") if s.strip()]

    # 2. 다중 종목 자금 공유 백테스트 기동
    db_path = system.db_path
    engine = BacktestEngine(db_path=db_path)
    
    try:
        # 백테스트 실행 (run_multi)
        result = await engine.run_multi(
            exchange=target_exchange,
            symbols=requested_symbols,
            start_date=start_ts,
            end_date=end_ts,
            initial_cash=req.initial_cash,
            strategy_configs=req.strategies,
            risk_limits_enabled=req.risk_limits_enabled,
            slippage_rate=req.slippage_rate
        )
        
        # 새로 생성된 백테스트 포트폴리오를 메모리에도 동기화하기 위해 로드 호출
        await system.portfolio_manager.load_from_db()
        
        duration = round(time.time() - start_time, 2)
        result["duration"] = duration
        
        # 데이터베이스에 duration과 strategy_info 기록 (initial_cash 및 summary 포함)
        import json
        meta_info = {
            "applied_strategies": result.get("applied_strategies", []),
            "initial_cash": req.initial_cash,
            "risk_limits_enabled": req.risk_limits_enabled,
            "slippage_rate": req.slippage_rate,
            "summary": {
                "initial_cash": result["summary"]["initial_cash"],
                "final_value": result["summary"]["final_value"],
                "profit": result["summary"].get("profit", 0.0),
                "roi": result["summary"]["roi"],
                "fee": result["summary"]["fee"],
                "trade_count": result["summary"]["trade_count"]
            }
        }
        async with get_db_conn(db_path) as db:
            await db.execute(
                "UPDATE portfolios SET duration = ?, strategy_info = ? WHERE id = ?",
                (duration, json.dumps(meta_info), result["portfolio_id"])
            )
            await db.commit()
            
        return result
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"백테스트 실행 중 서버 에러가 발생했습니다: {str(e)}")

@router.get("/api/backtest/history")
async def get_backtest_history(request: Request):
    """누적된 과거 백테스트 세트(이력) 목록을 반환합니다."""
    system = request.app.state.system
    db_path = system.db_path
    
    query = """
        SELECT 
            p.id, 
            p.name, 
            p.initial_cash, 
            p.cash, 
            p.created_at,
            p.strategy_info,
            COALESCE(SUM(pos.quantity * pos.avg_price), 0) as total_position_value,
            (SELECT COUNT(*) FROM orders_history WHERE portfolio_id = p.id) as trade_count,
            (SELECT SUM(fee) FROM orders_history WHERE portfolio_id = p.id) as total_fee
        FROM portfolios p
        LEFT JOIN positions pos ON pos.portfolio_id = p.id
        WHERE p.type = 'simulationR'
        GROUP BY p.id
        ORDER BY p.created_at DESC
    """
    
    async with get_db_conn(db_path) as db:
        cursor = await db.execute(query)
        rows = await cursor.fetchall()
        
    history = []
    import json
    for r in rows:
        initial = r["initial_cash"]
        final_val = r["cash"] + r["total_position_value"]
        roi = ((final_val - initial) / initial * 100) if initial > 0 else 0.0
        trade_count = r["trade_count"]
        total_fee = r["total_fee"] or 0.0
        
        # strategy_info 내 캐시된 요약이 있으면 이를 우선하여 정합성 보장
        if r["strategy_info"]:
            try:
                meta = json.loads(r["strategy_info"])
                if isinstance(meta, dict) and "summary" in meta:
                    sum_data = meta["summary"]
                    initial = sum_data.get("initial_cash", initial)
                    final_val = sum_data.get("final_value", final_val)
                    roi = sum_data.get("roi", roi)
                    trade_count = sum_data.get("trade_count", trade_count)
                    total_fee = sum_data.get("fee", total_fee)
            except Exception:
                pass
                
        name = r["name"]
        if r["id"] == "default":
            name = "기본 모의투자"
            
        history.append({
            "portfolio_id": r["id"],
            "name": name,
            "initial_cash": initial,
            "final_value": round(final_val, 2),
            "roi": round(roi, 2),
            "trade_count": trade_count,
            "total_fee": round(total_fee or 0.0, 2),
            "created_at": r["created_at"]
        })
        
    return history

@router.get("/api/backtest/history/{portfolio_id}")
async def get_backtest_history_detail(portfolio_id: str, request: Request):
    """특정 백테스트 세트의 상세 정보를 복원하여 반환합니다."""
    system = request.app.state.system
    db_path = system.db_path
    
    async with get_db_conn(db_path) as db:
        # portfolios 조회
        cursor = await db.execute("SELECT * FROM portfolios WHERE id = ?", (portfolio_id,))
        port = await cursor.fetchone()
        if not port:
            raise HTTPException(status_code=404, detail="해당 백테스트 이력을 찾을 수 없습니다.")
            
        # positions 조회
        cursor = await db.execute("SELECT * FROM positions WHERE portfolio_id = ?", (portfolio_id,))
        positions = await cursor.fetchall()
        
        # orders_history 조회
        cursor = await db.execute("SELECT * FROM orders_history WHERE portfolio_id = ? ORDER BY timestamp ASC", (portfolio_id,))
        trades = await cursor.fetchall()
        
    # trades를 (exchange, symbol)로 그룹화하여 중복 심볼 격리
    trades_by_ex_sym = {}
    for t in trades:
        ex_lower = t["exchange"].lower()
        sym = t["symbol"]
        key = (ex_lower, sym)
        if key not in trades_by_ex_sym:
            trades_by_ex_sym[key] = []
        trades_by_ex_sym[key].append({
            "exchange": t["exchange"],
            "side": t["side"],
            "price": t["price"],
            "quantity": t["quantity"],
            "fee": t["fee"],
            "timestamp": t["timestamp"],
            "reason": t["reason"]
        })
        
    import json
    meta = None
    applied_strategies = []
    initial_cash_map = {}
    if "strategy_info" in port.keys() and port["strategy_info"]:
        try:
            meta = json.loads(port["strategy_info"])
            if isinstance(meta, dict) and "applied_strategies" in meta:
                applied_strategies = meta["applied_strategies"]
                initial_cash_map = meta.get("initial_cash", {})
            else:
                applied_strategies = meta
        except Exception:
            pass

    # exchange_initial_cash를 항상 딕셔너리로 전송
    ex_initial_cash_map = {}
    if isinstance(initial_cash_map, dict) and initial_cash_map:
        ex_initial_cash_map = {ex.lower(): float(val) for ex, val in initial_cash_map.items()}
    else:
        ex_set = set(p["exchange"].lower() for p in positions) if positions else set()
        if not ex_set and trades:
            ex_set = set(t["exchange"].lower() for t in trades)
        if ex_set:
            each_cash = port["initial_cash"] / len(ex_set)
            ex_initial_cash_map = {ex.lower(): each_cash for ex in ex_set}
        else:
            ex_id = port["exchange_id"] if "exchange_id" in port.keys() and port["exchange_id"] else "upbit"
            ex_initial_cash_map = {ex_id.lower(): port["initial_cash"]}

    results = []
    for (ex_lower, sym), sym_trades in trades_by_ex_sym.items():
        pos_info = next((p for p in positions if p["symbol"] == sym and p["exchange"].lower() == ex_lower), None)
        current_qty = pos_info["quantity"] if pos_info else 0.0
        avg_price = pos_info["avg_price"] if pos_info else 0.0
        
        # 캐시된 final_prices 맵 우선 조회 (마감된 모의투자 세션 대응)
        final_price = 0.0
        if isinstance(meta, dict) and "final_prices" in meta:
            final_price = meta["final_prices"].get(sym, 0.0)
            if final_price == 0.0:
                for fk, fv in meta["final_prices"].items():
                    if fk.replace("KIS-", "").replace("KRW-", "").replace("UPB-", "").lower() == sym.lower():
                        final_price = fv
                        break
        if final_price == 0.0:
            final_price = sym_trades[-1]["price"] if sym_trades else avg_price
            
        symbol_fee = sum(t["fee"] for t in sym_trades)
        
        # orders_history 테이블의 exchange 컬럼값을 우선 사용
        exchange_name = sym_trades[0]["exchange"] if sym_trades else (pos_info["exchange"] if (pos_info and "exchange" in pos_info.keys()) else ex_lower)
        
        # 저장해 두었던 거래소별 초기 자금 획득
        symbol_init_cash = ex_initial_cash_map.get(ex_lower, port["initial_cash"])

        # 종목별 profit 계산 (매도금액 + 평가액 - 매수금액 - 수수료)
        buy_sum = sum(t["price"] * t["quantity"] for t in sym_trades if t["side"] == "BUY")
        sell_sum = sum(t["price"] * t["quantity"] for t in sym_trades if t["side"] == "SELL")
        valuation = current_qty * final_price
        symbol_profit = sell_sum + valuation - buy_sum - symbol_fee
        
        # 매수 체결 건수
        buy_trades = [t for t in sym_trades if t["side"] == "BUY"]
        buy_count = len(buy_trades)
        
        symbol_roi = 0.0
        if buy_count > 0:
            avg_buy_val = buy_sum / buy_count
            symbol_roi = (symbol_profit / avg_buy_val * 100) if avg_buy_val > 0 else 0.0

        from src.engine.utils.stock_mapper import stock_mapper
        kor_name = stock_mapper.get_name(ex_lower, sym)

        results.append({
            "exchange": exchange_name,
            "symbol": sym,
            "korean_name": kor_name,
            "portfolio_id": portfolio_id,
            "portfolio_name": port["name"],
            "initial_cash": symbol_init_cash,
            "final_value": round(valuation, 2),
            "roi": round(symbol_roi, 4),
            "fee": round(symbol_fee, 2),
            "profit": round(symbol_profit, 2),
            "trade_count": len(sym_trades),
            "trades": sym_trades,
            "candle_history": [],  # 이력 로드 시 캔들 히스토리는 빈칸
            "quantity": current_qty,
            "avg_price": avg_price,
            "final_price": final_price
        })
        
    # 요약 정보 집계 ( results 및 ex_initial_cash_map 기준 )
    total_initial = sum(ex_initial_cash_map.values())
    ex_profit_sums = {ex.lower(): 0.0 for ex in ex_initial_cash_map.keys()}
    ex_fee_sums = {ex.lower(): 0.0 for ex in ex_initial_cash_map.keys()}
    ex_trade_counts = {ex.lower(): 0 for ex in ex_initial_cash_map.keys()}
    
    for r in results:
        ex_lower = r["exchange"].lower()
        if ex_lower not in ex_profit_sums:
            ex_profit_sums[ex_lower] = 0.0
            ex_fee_sums[ex_lower] = 0.0
            ex_trade_counts[ex_lower] = 0
        ex_profit_sums[ex_lower] += r["profit"]
        ex_fee_sums[ex_lower] += r["fee"]
        ex_trade_counts[ex_lower] += r["trade_count"]
        
    total_profit = sum(ex_profit_sums.values())
    total_fee = sum(ex_fee_sums.values())
    total_trade_count = sum(ex_trade_counts.values())
    total_final = total_initial + total_profit
    total_roi = (total_profit / total_initial * 100) if total_initial > 0 else 0.0
    
    duration = port["duration"] if ("duration" in port.keys() and port["duration"]) else 0.0
    
    return {
        "status": "success",
        "is_restore": True,
        "portfolio_id": portfolio_id,
        "portfolio_name": port["name"],
        "duration": duration,
        "applied_strategies": applied_strategies,
        "exchange_initial_cash": ex_initial_cash_map,
        "summary": {
            "initial_cash": round(total_initial, 2),
            "final_value": round(total_final, 2),
            "profit": round(total_profit, 2),
            "roi": round(total_roi, 2),
            "fee": round(total_fee, 2),
            "trade_count": total_trade_count,
        },
        "results": results
    }

@router.delete("/api/backtest/history/{portfolio_id}")
async def delete_backtest_history(portfolio_id: str, request: Request):
    """특정 백테스트 세트 또는 마감된 실시간 모의투자 세션의 상세 정보를 DB에서 영구 삭제합니다."""
    system = request.app.state.system
    db_path = system.db_path
    
    async with get_db_conn(db_path) as db:
        cursor = await db.execute("DELETE FROM portfolios WHERE id = ? AND type IN ('simulationR', 'simulation_ended')", (portfolio_id,))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=400, detail="삭제할 포트폴리오를 찾을 수 없습니다. (가동 중인 포트폴리오이거나 존재하지 않음)")
        await db.commit()
        
    # 메모리 캐시에서 직접 팝하여 동기화
    system.portfolio_manager.portfolios.pop(portfolio_id, None)
    
    return {"status": "success", "message": "이력이 정상적으로 삭제되었습니다."}

@router.delete("/api/backtest/history")
async def delete_all_backtest_history(request: Request):
    """누적된 모든 백테스트 및 종료된 실시간 모의투자 이력을 DB 및 메모리에서 일괄 영구 삭제합니다."""
    system = request.app.state.system
    db_path = system.db_path
    
    async with get_db_conn(db_path) as db:
        # portfolios에서 type이 'simulationR' 또는 'simulation_ended'인 레코드 삭제 (ON DELETE CASCADE로 하위 레코드 자동 정리됨)
        await db.execute("DELETE FROM portfolios WHERE type IN ('simulationR', 'simulation_ended')")
        await db.commit()
        
    # 메모리 캐시에서 simulationR 및 simulation_ended 타입의 포트폴리오 일괄 제거
    to_delete = [
        pid for pid, p in system.portfolio_manager.portfolios.items() 
        if getattr(p, 'portfolio_type', 'simulation') in ('simulationR', 'simulation_ended')
    ]
    for pid in to_delete:
        system.portfolio_manager.portfolios.pop(pid, None)
        
    return {"status": "success", "message": "모든 이력이 성공적으로 삭제되었습니다."}

@router.get("/api/backtest/default-configs")
async def get_backtest_default_configs(request: Request):
    """백테스트 화면에 표기할 디폴트 전략 구성을 반환합니다."""
    # 기존 strategy.py 라우터의 list_strategies 결과 재활용
    return await list_strategies(request)

