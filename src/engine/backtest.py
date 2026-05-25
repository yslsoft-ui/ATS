import asyncio
import time
from typing import Dict, List, Any, Optional
from src.database.connection import get_db_conn
from .matching import OrderbookMatchingEngine
from .candles import CandleGenerator, Candle
from .strategy import BaseStrategy, StrategyRegistry, TradeSignal
from .trade_engine import TradeEngine
from .portfolio import Portfolio, PortfolioManager, VirtualExecutor

from src.engine.pipeline import ExecutionPipeline

class BacktestPortfolioManagerProxy:
    """
    StrategyHost가 포트폴리오 요약을 조회할 때 
    특정 백테스트 임시 포트폴리오 ID를 바라보도록 우회해 주는 프록시 객체입니다.
    """
    def __init__(self, manager: PortfolioManager, portfolio_id: str):
        self.manager = manager
        self.portfolio_id = portfolio_id
        
    def get_portfolio_summary(self, symbol: str, exchange: Optional[str] = None) -> Dict[str, Any]:
        return self.manager.get_portfolio_summary(symbol, self.portfolio_id, exchange)

class BacktestEngine:
    def __init__(self, db_path: str = None):
        self.db_path = db_path
        self.portfolio_manager = PortfolioManager(db_path=self.db_path)
        self.virtual_executor = VirtualExecutor()
        self.portfolio_manager.executors['simulation'] = self.virtual_executor
        self.execution_pipeline = ExecutionPipeline(self.portfolio_manager)
        
    async def run(
        self, 
        exchange: str,
        symbol: str, 
        start_date: int,  # timestamp ms
        end_date: int,    # timestamp ms
        initial_cash: float, 
        strategy_configs: Dict[str, Dict[str, Any]],
        risk_limits_enabled: bool = True,
        slippage_rate: float = 0.001
    ) -> Dict[str, Any]:
        """
        저장된 틱 데이터를 기반으로 과거 리플레이 백테스트를 수행합니다.
        """
        # 1. 대상 과거 틱 데이터 로드
        async with get_db_conn(self.db_path) as db:
            cursor = await db.execute(
                "SELECT trade_timestamp, trade_price, trade_volume, ask_bid "
                "FROM trades "
                "WHERE exchange = ? AND symbol = ? AND trade_timestamp BETWEEN ? AND ? "
                "ORDER BY trade_timestamp ASC",
                (exchange, symbol, start_date, end_date)
            )
            rows = await cursor.fetchall()
            
            if not rows:
                return {"status": "error", "message": "해당 기간에 조회된 틱 데이터가 없습니다."}

        # 2. 거래소 수수료 설정 로드
        await self.portfolio_manager.load_exchange_configs()
        exchange_config = self.portfolio_manager.exchange_configs.get(exchange, {})
        fee_rate = exchange_config.get('fee_rate', 0.0005)
        self.virtual_executor.set_fee_rate(fee_rate)

        # 3. 백테스트용 임시 포트폴리오 생성
        # ID 예: backtest_upbit_BTC_1716382103
        timestamp_sec = int(time.time())
        portfolio_id = f"backtest_{exchange}_{symbol}_{timestamp_sec}"
        
        # 전략 메타데이터를 백테스트 포트폴리오 명칭에 포함
        strategies_used = [name for name, cfg in strategy_configs.items() if cfg.get('enabled', False)]
        strategies_str = ", ".join(strategies_used) if strategies_used else "No Active Strategy"
        portfolio_name = f"백테스트: {exchange}-{symbol} ({strategies_str})"
        
        portfolio = Portfolio(
            portfolio_id=portfolio_id, 
            name=portfolio_name, 
            initial_cash=initial_cash, 
            exchange_id=exchange,
            portfolio_type='simulationR'
        )
        self.portfolio_manager.add_portfolio(portfolio)
        # 백테스트 틱 리플레이 도중 발생하는 주문의 외래 키(orders_history -> portfolios) 제약 충족을 위해 사전 저장
        await self.portfolio_manager.save_to_db(portfolio_id)

        # 4. 전략 동적 구성
        active_strategies = []
        for strat_name, cfg in strategy_configs.items():
            if cfg.get('enabled', False):
                # 전략 인스턴스화
                strat_inst = StrategyRegistry.create_strategy(strat_name, cfg.get('params', {}))
                if strat_inst:
                    active_strategies.append(strat_inst)

        if not active_strategies:
            return {"status": "error", "message": "활성화된 전략이 설정되어 있지 않습니다."}

        # 5. TradeEngine 가동
        engine = TradeEngine(exchange, symbol, active_strategies)
        proxy_manager = BacktestPortfolioManagerProxy(self.portfolio_manager, portfolio_id)

        # 6. 리플레이 루프 구동
        candle_history: List[Dict[str, Any]] = []
        
        for row in rows:
            tick = {
                "trade_price": row["trade_price"],
                "trade_volume": row["trade_volume"],
                "ask_bid": row["ask_bid"],
                "trade_timestamp": row["trade_timestamp"]
            }
            
            # 엔진 주입
            signals, closed_candles = await engine.process_tick(tick, proxy_manager)
            
            # 차트 시각화용 캔들 히스토리 수집
            for c in closed_candles:
                candle_history.append({
                    "time": c.timestamp,
                    "open": c.open,
                    "high": c.high,
                    "low": c.low,
                    "close": c.close,
                    "volume": c.volume
                })
            
            # 발생한 전략 신호 가상 매칭 체결
            for sig in signals:
                await self.execution_pipeline.process_signal(
                    signal=sig,
                    price=tick["trade_price"],
                    portfolio_id=portfolio_id,
                    risk_limits_enabled=risk_limits_enabled,
                    slippage_rate=slippage_rate,
                    size_ratio=0.95  # 단일 백테스트는 수수료 여백 포함 95% 비율로 매수 적용
                )

        # 7. 백테스트 종료 시 최종 평가액 및 성과 계산
        final_price = rows[-1]["trade_price"]
        current_prices = {symbol: final_price}
        final_value = portfolio.get_total_value(current_prices)
        roi = ((final_value - initial_cash) / initial_cash) * 100
        
        total_fee = sum(h.get('fee', 0.0) for h in portfolio.history)

        # 8. 백테스트 결과를 portfolios, positions, orders_history에 영구 적재
        await self.portfolio_manager.save_to_db(portfolio_id)

        # 적용전략과 사용 파라미터 요약 생성
        applied_info = []
        for s in active_strategies:
            applied_info.append({
                "name": s.__class__.__name__,
                "params": s.params
            })

        return {
            "status": "success",
            "portfolio_id": portfolio_id,
            "portfolio_name": portfolio_name,
            "applied_strategies": applied_info,
            "summary": {
                "initial_cash": initial_cash,
                "final_value": round(final_value, 2),
                "roi": round(roi, 2),
                "trade_count": len(portfolio.history),
                "total_fee": round(total_fee, 2),
                "trades": [
                    {
                        "side": h["side"],
                        "price": h["price"],
                        "quantity": h["quantity"],
                        "fee": h["fee"],
                        "timestamp": h["timestamp"],
                        "reason": h.get("reason", "")
                    }
                    for h in portfolio.history
                ],
                "candle_history": candle_history
            }
        }

    async def run_multi(
        self,
        exchange: str,
        symbols: List[str],
        start_date: int,  # timestamp ms
        end_date: int,    # timestamp ms
        initial_cash: Any,  # Union[float, Dict[str, float]]
        strategy_configs: Dict[str, Dict[str, Any]],
        risk_limits_enabled: bool = True,
        slippage_rate: float = 0.001
    ) -> Dict[str, Any]:
        """
        다중 종목들의 틱 데이터를 시간 순서대로 융합 리플레이하며,
        거래소별 초기 예수금을 격리하여 백테스트를 수행합니다.
        """
        # 1. 대상 틱 데이터를 데이터베이스에서 일괄 조회하고 시간 순서대로 정렬
        query = (
            "SELECT exchange, symbol, trade_timestamp, trade_price, trade_volume, ask_bid "
            "FROM trades "
            "WHERE trade_timestamp BETWEEN ? AND ?"
        )
        params = [start_date, end_date]
        
        if exchange and exchange != "all":
            query += " AND exchange = ?"
            params.append(exchange)
            
        if symbols:
            placeholders = ",".join(["?"] * len(symbols))
            query += f" AND symbol IN ({placeholders})"
            params.extend(symbols)
            
        query += " ORDER BY trade_timestamp ASC"
        
        async with get_db_conn(self.db_path) as db:
            cursor = await db.execute(query, tuple(params))
            rows = await cursor.fetchall()
            
            if not rows:
                return {"status": "error", "message": "해당 기간에 조회된 틱 데이터가 없습니다."}

        # 2. 거래소 수수료 설정 로드
        await self.portfolio_manager.load_exchange_configs()

        # 3. 조회된 틱 데이터로부터 실제 데이터가 존재하는 종목 조합(pairs) 추출
        seen_pairs = {}
        for row in rows:
            seen_pairs[(row["exchange"], row["symbol"])] = True
        pairs = [{"exchange": ex, "symbol": sym} for ex, sym in seen_pairs.keys()]

        # 4. 백테스트용 통합 임시 포트폴리오 생성
        timestamp_sec = int(time.time())
        portfolio_id = f"backtest_multi_{timestamp_sec}"
        
        active_strat_names = [name for name, cfg in strategy_configs.items() if cfg.get('enabled', False)]
        strategies_str = ", ".join(active_strat_names) if active_strat_names else "No Active Strategy"
        portfolio_name = f"백테스트: 다중종목 ({strategies_str})"
        
        # 초기 투자금의 총합 계산
        if isinstance(initial_cash, dict):
            total_initial_cash = sum(initial_cash.values())
        else:
            total_initial_cash = initial_cash

        # 통합 포트폴리오 초기화
        portfolio = Portfolio(
            portfolio_id=portfolio_id,
            name=portfolio_name,
            initial_cash=total_initial_cash,
            exchange_id="all",
            portfolio_type='simulationR'
        )
        self.portfolio_manager.add_portfolio(portfolio)
        # 백테스트 틱 리플레이 도중 발생하는 주문의 외래 키 제약 충족을 위해 사전 저장
        await self.portfolio_manager.save_to_db(portfolio_id)

        # 거래소별 가상 가용 현금을 독립 추적할 딕셔너리 초기화 및 포트폴리오 바인딩
        exchange_cash = {}
        if isinstance(initial_cash, dict):
            exchange_cash = {ex.lower(): float(val) for ex, val in initial_cash.items()}
        else:
            ex_set = set(p["exchange"] for p in pairs)
            if ex_set:
                each_cash = initial_cash / len(ex_set)
                exchange_cash = {ex.lower(): each_cash for ex in ex_set}
            else:
                ex_name = exchange if exchange != "all" else "upbit"
                exchange_cash = {ex_name.lower(): initial_cash}
        
        portfolio.exchange_cash = exchange_cash.copy()

        # 5. 각 종목별 TradeEngine 구성
        engines = {}
        
        def get_or_create_engine(exchange: str, symbol: str):
            key = f"{exchange}_{symbol}"
            if key not in engines:
                # 활성 전략들을 이 종목 전용으로 독립 인스턴스화
                active_strategies = []
                for strat_name, cfg in strategy_configs.items():
                    if cfg.get('enabled', False):
                        strat_inst = StrategyRegistry.create_strategy(strat_name, cfg.get('params', {}))
                        if strat_inst:
                            active_strategies.append(strat_inst)
                engines[key] = TradeEngine(exchange, symbol, active_strategies)
            return engines[key]

        proxy_manager = BacktestPortfolioManagerProxy(self.portfolio_manager, portfolio_id)
        
        # 6. 종목별 캔들 히스토리 수집용 맵
        candle_histories = {f"{p['exchange']}_{p['symbol']}": [] for p in pairs}
        
        # 6. 리플레이 루프 구동 (시간 정렬된 단일 스트림)
        last_prices = {}
        
        for row in rows:
            ex = row["exchange"]
            sym = row["symbol"]
            last_prices[sym] = row["trade_price"]
            
            tick = {
                "trade_price": row["trade_price"],
                "trade_volume": row["trade_volume"],
                "ask_bid": row["ask_bid"],
                "trade_timestamp": row["trade_timestamp"]
            }
            
            # 종목 전용 TradeEngine 획득
            engine = get_or_create_engine(ex, sym)
            
            # 엔진 주입
            signals, closed_candles = await engine.process_tick(tick, proxy_manager)
            
            # 캔들 정보 수집
            key = f"{ex}_{sym}"
            for c in closed_candles:
                candle_histories[key].append({
                    "time": c.timestamp,
                    "open": c.open,
                    "high": c.high,
                    "low": c.low,
                    "close": c.close,
                    "volume": c.volume
                })
                
            # 발생한 전략 신호 가상 매칭 체결 (거래소별 자금 격리)
            for sig in signals:
                await self.execution_pipeline.process_signal(
                    signal=sig,
                    price=tick["trade_price"],
                    portfolio_id=portfolio_id,
                    risk_limits_enabled=risk_limits_enabled,
                    slippage_rate=slippage_rate,
                    size_ratio=0.19  # 다중 종목 백테스트 격리 자산의 20% * 수수료 margin 95% = 19% 반영
                )

        # 7. 백테스트 종료 시 최종 평가액 및 성과 계산
        final_value = portfolio.get_total_value(last_prices)
        roi = ((final_value - total_initial_cash) / total_initial_cash) * 100
        total_fee = sum(h.get('fee', 0.0) for h in portfolio.history)

        # 8. 백테스트 결과를 portfolios, positions, orders_history에 영구 적재
        await self.portfolio_manager.save_to_db(portfolio_id)

        # orders_history 저장은 process_signal 실행 중 자동으로 수행됩니다.

        # 적용전략과 사용 파라미터 요약 생성
        applied_info = []
        if engines:
            first_engine = list(engines.values())[0]
            for s in first_engine.strategies:
                applied_info.append({
                    "name": s.__class__.__name__,
                    "params": s.params
                })

        # 9. 각 종목별 개별 상세 결과 빌드
        results_by_symbol = []
        for p in pairs:
            ex = p["exchange"]
            sym = p["symbol"]
            key = f"{ex}_{sym}"
            
            # 거래소와 심볼을 동시에 대조하여 중복 격리
            symbol_trades = [
                {
                    "side": h["side"],
                    "price": h["price"],
                    "quantity": h["quantity"],
                    "fee": h["fee"],
                    "timestamp": h["timestamp"],
                    "reason": h.get("reason", "")
                }
                for h in portfolio.history if h["symbol"] == sym and h["exchange"].lower() == ex.lower()
            ]
            
            trade_count = len(symbol_trades)
            
            if trade_count > 0:
                current_qty = 0
                avg_price = 0
                total_cost = 0
                symbol_fee = 0.0
                
                # 손익 계산을 위해 매수/매도 합계 연산
                buy_sum = 0.0
                sell_sum = 0.0
                
                for t in symbol_trades:
                    symbol_fee += t["fee"]
                    if t["side"] == 'BUY':
                        total_cost += t["price"] * t["quantity"]
                        buy_sum += t["price"] * t["quantity"]
                        current_qty += t["quantity"]
                        if current_qty > 0:
                            avg_price = total_cost / current_qty
                    else:
                        sell_sum += t["price"] * t["quantity"]
                        current_qty -= t["quantity"]
                        if current_qty <= 0:
                            current_qty = 0; avg_price = 0; total_cost = 0
                            
                final_price = last_prices.get(sym, 0.0)
                # 미실현 보유 자산 평가액
                valuation = current_qty * final_price
                # 개별 종목 손익 = 매도금액 + 평가액 - 매수금액 - 수수료
                symbol_profit = sell_sum + valuation - buy_sum - symbol_fee
                
                # 매수 체결 건수
                buy_trades = [t for t in symbol_trades if t["side"] == 'BUY']
                buy_count = len(buy_trades)
                
                symbol_roi = 0.0
                if buy_count > 0:
                    avg_buy_val = buy_sum / buy_count
                    symbol_roi = (symbol_profit / avg_buy_val * 100) if avg_buy_val > 0 else 0.0
                
                # 해당 거래소에 배정되었던 초기 투자금 추출
                ex_lower = ex.lower()
                if isinstance(initial_cash, dict):
                    symbol_init_cash = initial_cash.get(ex_lower, 0.0)
                else:
                    ex_set = set(item["exchange"] for item in pairs)
                    symbol_init_cash = initial_cash / len(ex_set) if ex_set else initial_cash

                from src.engine.utils.stock_mapper import stock_mapper
                kor_name = stock_mapper.get_name(ex_lower, sym)

                results_by_symbol.append({
                    "exchange": ex,
                    "symbol": sym,
                    "korean_name": kor_name,
                    "portfolio_id": portfolio_id,
                    "portfolio_name": portfolio_name,
                    "initial_cash": symbol_init_cash,
                    "final_value": round(valuation, 2),
                    "roi": round(symbol_roi, 4),
                    "fee": round(symbol_fee, 2),
                    "profit": round(symbol_profit, 2),
                    "trade_count": trade_count,
                    "trades": symbol_trades,
                    "candle_history": candle_history_data if (candle_history_data := candle_histories.get(key, [])) else []
                })

        # exchange_initial_cash 구성
        ex_initial_cash_map = {}
        if isinstance(initial_cash, dict):
            ex_initial_cash_map = {ex.lower(): float(val) for ex, val in initial_cash.items()}
        else:
            ex_set = set(p["exchange"] for p in pairs)
            if ex_set:
                each_cash = initial_cash / len(ex_set)
                ex_initial_cash_map = {ex.lower(): each_cash for ex in ex_set}
            else:
                ex_name = exchange if exchange != "all" else "upbit"
                ex_initial_cash_map = {ex_name.lower(): initial_cash}

        # 10. 거래소별/전체 요약 지표 정밀 집계
        total_initial = sum(ex_initial_cash_map.values())
        ex_profit_sums = {ex.lower(): 0.0 for ex in ex_initial_cash_map.keys()}
        ex_fee_sums = {ex.lower(): 0.0 for ex in ex_initial_cash_map.keys()}
        ex_trade_counts = {ex.lower(): 0 for ex in ex_initial_cash_map.keys()}
        
        for r in results_by_symbol:
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

        return {
            "status": "success",
            "portfolio_id": portfolio_id,
            "portfolio_name": portfolio_name,
            "applied_strategies": applied_info,
            "exchange_initial_cash": ex_initial_cash_map,
            "summary": {
                "initial_cash": total_initial,
                "final_value": round(total_final, 2),
                "profit": round(total_profit, 2),
                "roi": round(total_roi, 2),
                "trade_count": total_trade_count,
                "total_fee": round(total_fee, 2),
                "fee": round(total_fee, 2),
                "trades": [
                    {
                        "symbol": h["symbol"],
                        "side": h["side"],
                        "price": h["price"],
                        "quantity": h["quantity"],
                        "fee": h["fee"],
                        "timestamp": h["timestamp"],
                        "reason": h.get("reason", "")
                    }
                    for h in portfolio.history
                ]
            },
            "results": results_by_symbol
        }


