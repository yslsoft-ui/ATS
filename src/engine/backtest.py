import asyncio
from typing import Dict, List, Any
from src.database.connection import get_db_conn
from .matching import OrderbookMatchingEngine
from .candles import CandleGenerator
from .strategy import BaseStrategy
from .trade_engine import TradeEngine

class BacktestEngine:
    def __init__(self, db_path: str = None):
        self.db_path = db_path
        self.matching_engine = OrderbookMatchingEngine()
        
    async def run(self, symbol: str, initial_cash: float, strategy: Any, interval: int = 60) -> Dict[str, Any]:
        """
        틱 데이터를 기반으로 멀티 타임프레임 백테스트를 수행합니다.
        """
        async with get_db_conn() as db:
            cursor = await db.execute(
                "SELECT trade_timestamp, trade_price, trade_volume, ask_bid FROM trades WHERE symbol = ? ORDER BY trade_timestamp ASC",
                (symbol,)
            )
            rows = await cursor.fetchall()
            
            if not rows:
                return {"status": "error", "message": "No data found for backtest"}

            # 엔진 초기화
            engine = TradeEngine(symbol, [strategy])
            
            cash = initial_cash
            position = 0.0
            trades_executed = []
            candle_history = []
            
            for row in rows:
                price = row["trade_price"]
                timestamp = row["trade_timestamp"]
                
                # 1. 틱 데이터를 TradeEngine에 주입
                signals = engine.process_tick({
                    "trade_price": price,
                    "trade_volume": row["trade_volume"],
                    "ask_bid": row["ask_bid"],
                    "trade_timestamp": timestamp
                })
                
                # 2. 발생한 신호 처리
                for sig in signals:
                    if sig.action == "BUY" and cash > 0:
                        position = cash / price
                        cash = 0
                        trades_executed.append({
                            "type": "BUY",
                            "price": price,
                            "timestamp": timestamp,
                            "reason": sig.reason
                        })
                    
                    elif sig.action == "SELL" and position > 0:
                        cash = position * price
                        position = 0
                        trades_executed.append({
                            "type": "SELL",
                            "price": price,
                            "timestamp": timestamp,
                            "reason": sig.reason
                        })
                
                # 차트 표시용 캔들 히스토리 수집 (TradeEngine 내부 캔들 참조)
                # 여기서는 캔들 생성을 직접 하지 않고 엔진의 것을 가져오거나 별도 처리 필요
                # 일단 백테스트용 캔들 수집은 기존대로 유지하거나 엔진에서 노출하도록 수정 가능
                
            final_price = rows[-1]["trade_price"]
            final_value = cash + (position * final_price)
            roi = ((final_value - initial_cash) / initial_cash) * 100
            
            return {
                "status": "success",
                "summary": {
                    "initial_cash": initial_cash,
                    "final_value": round(final_value, 2),
                    "roi": round(roi, 2),
                    "trade_count": len(trades_executed),
                    "trades": trades_executed,
                    "candle_history": candle_history
                }
            }
