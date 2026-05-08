import asyncio
import aiosqlite
import pandas as pd
from typing import Dict, List, Any
from .matching import OrderbookMatchingEngine
from .candles import CandleGenerator
from .strategy import RSIStrategy

class BacktestEngine:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.matching_engine = OrderbookMatchingEngine()
        
    async def run(self, symbol: str, initial_cash: float, strategy: Any, interval: int = 60) -> Dict[str, Any]:
        """
        틱 데이터를 기반으로 멀티 타임프레임 백테스트를 수행합니다.
        """
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT trade_timestamp, trade_price, trade_volume, ask_bid FROM trades WHERE symbol = ? ORDER BY trade_timestamp ASC",
                (symbol,)
            )
            rows = await cursor.fetchall()
            
            if not rows:
                return {"status": "error", "message": "No data found for backtest"}

            # 엔진 초기화
            candle_gen = CandleGenerator(intervals=[interval])
            
            cash = initial_cash
            position = 0.0
            trades_executed = []
            candle_history = []
            
            for row in rows:
                price = row["trade_price"]
                volume = row["trade_volume"]
                timestamp = row["trade_timestamp"]
                
                # 1. 틱 데이터를 캔들 생성기에 주입
                closed_candles = candle_gen.process_tick(symbol, price, volume, timestamp)
                
                # 2. 완성된 캔들이 있으면 전략 실행
                for candle in closed_candles:
                    candle_history.append(candle)
                    result = strategy.on_candle(candle)
                    
                    if result.action == "BUY" and cash > 0:
                        # 전량 매수 시뮬레이션
                        position = cash / price
                        cash = 0
                        trades_executed.append({
                            "type": "BUY",
                            "price": price,
                            "timestamp": timestamp,
                            "reason": result.reason
                        })
                    
                    elif result.action == "SELL" and position > 0:
                        # 전량 매도 시뮬레이션
                        cash = position * price
                        position = 0
                        trades_executed.append({
                            "type": "SELL",
                            "price": price,
                            "timestamp": timestamp,
                            "reason": result.reason
                        })
                
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
