import asyncio
from typing import List, Dict, Optional, Any
from src.database.connection import get_db_conn
from src.engine.candles import CandleGenerator, Candle
from src.engine.strategy import BaseStrategy, StrategyType, TradeSignal
from src.engine.strategy_host import StrategyHost

# 전략 레지스트리에 등록된 모든 전략을 불러오기 위해 임포트 (loader에 의해 로드됨)
from src.engine import strategies

class TradeEngine:
    """
    종목별로 캔들 생성과 전략 실행을 통합 관리하는 엔진입니다.
    """
    def __init__(self, symbol: str, strategies: List[BaseStrategy], on_status_callback: Optional[Any] = None):
        self.symbol = symbol
        self.strategies = strategies
        
        # 전략들을 호스트로 래핑
        self.hosts = [StrategyHost(s, symbol, s.params.get('interval', 60), on_status_callback=on_status_callback) for s in strategies]
        
        # 전략 분류 (호스트 기준)
        self.entry_hosts = [h for h in self.hosts if h.strategy.type in [StrategyType.ENTRY, StrategyType.BOTH]]
        self.exit_hosts = [h for h in self.hosts if h.strategy.type in [StrategyType.EXIT, StrategyType.BOTH]]
        
        # 전략들이 요구하는 모든 인터벌 추출
        self.intervals = list(set(s.params.get('interval', 60) for s in strategies))
        if not self.intervals:
            self.intervals = [60] # 기본값
        self.candle_gen = CandleGenerator(intervals=self.intervals)

    async def warm_up(self, db_path: Optional[str] = None, lookback_ticks: int = 1000, lookback_candles: int = 100):
        """DB에서 과거 캔들 또는 틱 데이터를 읽어와 전략의 상태를 복구합니다."""
        try:
            async with get_db_conn() as db:
                # 1. 먼저 캔들 테이블에서 데이터 시도
                has_candle_data = False
                for interval in self.intervals:
                    query = "SELECT * FROM candles WHERE symbol = ? AND interval = ? ORDER BY timestamp DESC LIMIT ?"
                    async with db.execute(query, (self.symbol, interval, lookback_candles)) as cursor:
                        rows = await cursor.fetchall()
                        if rows:
                            has_candle_data = True
                            # 시간순 처리를 위해 역순으로 전략에 주입
                            for j, row in enumerate(reversed(rows)):
                                candle = Candle(
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
                                # 워밍업 중이므로 신호 생성 없이 전략 상태만 업데이트
                                for host in self.hosts:
                                    if host.interval == interval:
                                        await host.on_candle(candle)
                                
                                # 20건마다 아주 짧게 쉬어줌 (UI 응답성 최우선)
                                if j % 20 == 0:
                                    await asyncio.sleep(0.001)
                    await asyncio.sleep(0.01) # 인터벌 사이 휴식
                
                # 2. 캔들 데이터가 없는 경우에만 틱 데이터로 워밍업 (Fallback)
                if not has_candle_data:
                    query = "SELECT trade_price, trade_volume, ask_bid, trade_timestamp FROM trades WHERE symbol = ? ORDER BY trade_timestamp DESC LIMIT ?"
                    async with db.execute(query, (self.symbol, lookback_ticks)) as cursor:
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
            
            print(f"[INFO] TradeEngine: {self.symbol} warmed up (Source: {'Candles' if has_candle_data else 'Ticks'}).")
        except Exception as e:
            print(f"[WARNING] TradeEngine: {self.symbol} warmup failed: {e}")

    async def process_tick(self, tick: Dict, portfolio_manager: Any, is_warmup: bool = False) -> tuple[List[TradeSignal], List[Candle]]:
        """실시간 틱을 처리하고, 완성된 캔들이 있을 경우 전략을 실행하여 신호와 캔들을 반환합니다."""
        closed_candles = self.candle_gen.process_tick(
            self.symbol, 
            tick['trade_price'], 
            tick['trade_volume'], 
            tick['ask_bid'], 
            tick['trade_timestamp']
        )
        
        signals = []
        for candle in closed_candles:
            # 1. 매수 전략 체크
            for host in self.entry_hosts:
                if host.interval == candle.interval:
                    # 호스트를 통해 지표 업데이트 및 전략 실행 (비동기 처리)
                    signal = await host.on_candle(candle, portfolio_manager)
                    if not is_warmup and signal:
                        signals.append(signal)
            
            # 2. 매도 전략 체크
            for host in self.exit_hosts:
                if host.interval == candle.interval:
                    signal = await host.on_candle(candle, portfolio_manager)
                    if not is_warmup and signal:
                        signals.append(signal)
        return signals, closed_candles

    def update_strategy_params(self, strategy_id: str, params: Dict):
        """등록된 특정 전략의 파라미터를 업데이트합니다."""
        for strategy in self.strategies:
            if strategy.__class__.__name__.lower() == strategy_id.lower():
                strategy.update_params(params)
