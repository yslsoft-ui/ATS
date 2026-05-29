import asyncio
import time
from typing import List, Dict, Optional, Any
from src.engine.utils.telemetry import get_logger

logger = get_logger(__name__)
from src.database.connection import get_db_conn
from src.engine.candles import CandleGenerator, Candle
from src.engine.strategy import BaseStrategy, StrategyType, TradeSignal, StrategyResult
from src.engine.strategy_host import StrategyHost
from src.engine.market_data_context import MarketDataContext

# 전략 레지스트리에 등록된 모든 전략을 불러오기 위해 임포트 (loader에 의해 로드됨)
from src.engine import strategies

class TradeEngine:
    def __init__(self, exchange: str, symbol: str, strategies: List[BaseStrategy], on_status_callback: Optional[Any] = None):
        self.exchange = exchange
        self.symbol = symbol
        self.strategies = strategies
        self.on_status_callback = on_status_callback
        
        # 전략들을 호스트로 래핑
        self.hosts = [StrategyHost(s, exchange, symbol, s.params.get('interval', 60)) for s in strategies]
        
        # 전략 분류 (호스트 기준)
        self.entry_hosts = [h for h in self.hosts if h.strategy.type in [StrategyType.ENTRY, StrategyType.BOTH]]
        self.exit_hosts = [h for h in self.hosts if h.strategy.type in [StrategyType.EXIT, StrategyType.BOTH]]
        
        # 전략들이 요구하는 모든 인터벌 추출 및 Context 모듈 초기화
        self.intervals = list(set(s.params.get('interval', 60) for s in strategies))
        if not self.intervals:
            self.intervals = [60] # 기본값
            
        self.contexts: Dict[int, MarketDataContext] = {
            interval: MarketDataContext(exchange, symbol, interval) for interval in self.intervals
        }
        self.candle_gen = CandleGenerator(intervals=self.intervals)

    async def warm_up(self, db_path: Optional[str] = None, lookback_ticks: int = 1000, lookback_candles: int = 100):
        """DB에서 과거 캔들 또는 틱 데이터를 읽어와 전략의 상태를 복구합니다."""
        try:
            async with get_db_conn() as db:
                # 1. 먼저 캔들 테이블에서 데이터 시도
                has_candle_data = False
                for interval in self.intervals:
                    query = "SELECT * FROM candles WHERE exchange = ? AND symbol = ? AND interval = ? ORDER BY timestamp DESC LIMIT ?"
                    async with db.execute(query, (self.exchange, self.symbol, interval, lookback_candles)) as cursor:
                        rows = await cursor.fetchall()
                        if rows:
                            has_candle_data = True
                            context = self.contexts[interval]
                            # 시간순 처리를 위해 역순으로 데이터 컨텍스트에 주입
                            for j, row in enumerate(reversed(rows)):
                                candle = Candle(
                                    exchange=row['exchange'],
                                    symbol=row['symbol'],
                                    interval=row['interval'],
                                    timestamp=row['timestamp'],
                                    open=row['open'],
                                    high=row['high'],
                                    low=row['low'],
                                    close=row['close'],
                                    volume=row['volume'],
                                    is_closed=True
                                )
                                context.add_candle(candle)
                                
                                # 20건마다 아주 짧게 쉬어줌 (UI 응답성 최우선)
                                if j % 20 == 0:
                                    await asyncio.sleep(0.001)
                    await asyncio.sleep(0.01) # 인터벌 사이 휴식
                
                # 2. 캔들 데이터가 없는 경우에만 틱 데이터로 워밍업 (Fallback)
                if not has_candle_data:
                    query = "SELECT trade_price, trade_volume, ask_bid, trade_timestamp FROM trades WHERE exchange = ? AND symbol = ? ORDER BY trade_timestamp DESC LIMIT ?"
                    async with db.execute(query, (self.exchange, self.symbol, lookback_ticks)) as cursor:
                        rows = await cursor.fetchall()
                        for i, row in enumerate(reversed(rows)):
                            await self.process_tick({
                                'trade_price': row['trade_price'],
                                'trade_volume': row['trade_volume'],
                                'ask_bid': row['ask_bid'],
                                'trade_timestamp': row['trade_timestamp']
                            }, None, is_warmup=True)
                            
                            # 20건마다 제어권 양보
                            if i % 20 == 0:
                                await asyncio.sleep(0.001)
            
            logger.debug(f"{self.symbol} warmed up (Source: {'Candles' if has_candle_data else 'Ticks'}).")
        except Exception as e:
            logger.warning(f"{self.symbol} warmup failed: {e}")

    async def process_tick(self, tick: Dict, portfolio_manager: Any, is_warmup: bool = False) -> tuple[List[TradeSignal], List[Candle]]:
        """실시간 틱을 처리하고, 완성된 캔들이 있을 경우 전략을 실행하여 신호와 캔들을 반환합니다."""
        closed_candles = self.candle_gen.process_tick(
            self.exchange,
            self.symbol, 
            tick['trade_price'], 
            tick['trade_volume'], 
            tick['ask_bid'], 
            tick['trade_timestamp']
        )
        
        signals = []
        for candle in closed_candles:
            # 완성된 캔들을 컨텍스트에 갱신하여 지표 계산 캐시 무효화 및 데이터 누적
            context = self.contexts[candle.interval]
            context.add_candle(candle)
            
            # 1. 매수 전략 체크
            for host in self.entry_hosts:
                if host.interval == candle.interval:
                    action_result = await host.execute(context, portfolio_manager)
                    if not is_warmup and action_result:
                        # 브로드캐스트 및 시그널 가공 처리
                        await self._handle_strategy_result(host, candle, action_result, signals)
            
            # 2. 매도 전략 체크
            for host in self.exit_hosts:
                if host.interval == candle.interval:
                    action_result = await host.execute(context, portfolio_manager)
                    if not is_warmup and action_result:
                        await self._handle_strategy_result(host, candle, action_result, signals)
                        
        return signals, closed_candles

    async def _handle_strategy_result(self, host: StrategyHost, candle: Candle, action_result: Any, signals: List[TradeSignal]):
        """전략 판단 결과물로부터 신호를 빌드하고 UI로 실시간 브로드캐스트합니다."""
        context_data = self.contexts[candle.interval]
        
        # --- 실시간 상태 브로드캐스트 (Audit Log) ---
        if self.on_status_callback:
            # 하위 호환성을 위해 사전 선언한 지표 딕셔너리를 포함
            indicators_data = {}
            required = getattr(host.strategy, 'required_indicators', [])
            for ind in required:
                window = host.params.get('rsi_window', host.params.get('sma_window', 20))
                indicators_data[ind] = context_data.get_indicator(ind, window=window)
                
            status_info = {
                "type": "strategy_status",
                "strategy_id": host.strategy.id,
                "exchange": self.exchange,
                "symbol": self.symbol,
                "indicators": indicators_data,
                "last_action": action_result.action if hasattr(action_result, 'action') else str(action_result),
                "timestamp": int(time.time() * 1000)
            }
            asyncio.create_task(self.on_status_callback(status_info))

        # --- 신호 가공 및 패킹 ---
        if isinstance(action_result, StrategyResult):
            if action_result.action in ['BUY', 'SELL']:
                signals.append(TradeSignal(
                    exchange=self.exchange,
                    symbol=self.symbol,
                    action=action_result.action,
                    price=action_result.price or candle.close,
                    reason=action_result.reason or f"Strategy {host.strategy.id} signal",
                    interval=candle.interval,
                    strategy_id=host.strategy.id,
                    context=action_result.context
                ))
        elif action_result in ['BUY', 'SELL']:
            signals.append(TradeSignal(
                exchange=self.exchange,
                symbol=self.symbol,
                action=action_result,
                price=candle.close,
                reason=f"Strategy {host.strategy.id} legacy signal",
                interval=candle.interval,
                strategy_id=host.strategy.id
            ))

    def update_strategy_params(self, strategy_id: str, params: Dict):
        """등록된 특정 전략의 파라미터를 업데이트합니다."""
        for strategy in self.strategies:
            if strategy.__class__.__name__.lower() == strategy_id.lower():
                strategy.update_params(params)

