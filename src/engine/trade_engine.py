import asyncio
import time
import hashlib
import json
import math
from typing import List, Dict, Optional, Any
from src.engine.utils.telemetry import get_logger

logger = get_logger(__name__)
from src.engine.candles import Candle
from src.engine.strategy import BaseStrategy, StrategyType, TradeSignal, StrategyResult
from src.engine.strategy_host import StrategyHost
from src.engine.market_data_context import MarketDataContext
from src.database.repository import BaseMarketDataRepository, SqliteMarketDataRepository
from src.engine.girs_types import FeatureSnapshot
from src.config.manager import ConfigManager
from src.engine.feature_builder import FeatureBuilder, FeatureBuildRequest

# 전략 레지스트리에 등록된 모든 전략을 불러오기 위해 임포트 (loader에 의해 로드됨)
from src.engine import strategies

class TradeEngine:
    def __init__(self, exchange_id: str, symbol: str, strategies: List[BaseStrategy], on_status_callback: Optional[Any] = None, market_data_repo: Optional[BaseMarketDataRepository] = None):
        self.exchange_id = exchange_id
        self.symbol = symbol
        self.strategies = strategies
        self.on_status_callback = on_status_callback
        self.market_data_repo = market_data_repo or SqliteMarketDataRepository()
        self.last_tick = None
        self.last_tick_received_at = 0.0
        self.config_manager = ConfigManager("config/settings.yaml")
        
        # 전략들을 호스트로 래핑
        self.hosts = [StrategyHost(s, exchange_id, symbol, s.params.get('interval', 60)) for s in strategies]
        
        # 공통 청산 평가기 초기화
        from src.engine.exit_evaluator import CommonExitEvaluator
        self.exit_evaluator = CommonExitEvaluator(self.config_manager.config)
        
        # 전략들이 요구하는 모든 인터벌 추출 및 Context 모듈 초기화
        self.intervals = list(set(s.params.get('interval', 60) for s in strategies))
        if not self.intervals:
            self.intervals = [60] # 기본값
            
        self.contexts: Dict[int, MarketDataContext] = {
            interval: MarketDataContext(exchange_id, symbol, interval) for interval in self.intervals
        }
        self.feature_builder = FeatureBuilder(self.market_data_repo, self.config_manager)

    async def warm_up(self, db_path: Optional[str] = None, lookback_ticks: int = 1000, lookback_candles: int = 100):
        """리포지토리를 사용해 과거 캔들 또는 틱 데이터를 읽어와 전략의 상태를 복구합니다."""
        try:
            # 1. 먼저 캔들 테이블에서 데이터 시도
            has_candle_data = False
            for interval in self.intervals:
                # get_candles 메서드를 사용해 이미 지표 연산까지 처리된 캔들 데이터를 획득
                candles_data = await self.market_data_repo.get_candles(
                    exchange_id=self.exchange_id,
                    symbol=self.symbol,
                    interval=interval,
                    limit=lookback_candles
                )
                if candles_data:
                    has_candle_data = True
                    context = self.contexts[interval]
                    # 시간순(과거 -> 최신) 주입
                    for j, row in enumerate(candles_data):
                        # DB에서 반환한 딕셔너리를 Candle 객체로 변환
                        candle = Candle(
                            exchange_id=self.exchange_id,
                            symbol=self.symbol,
                            interval=interval,
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
                trades_data = await self.market_data_repo.get_recent_trades(
                    exchange_id=self.exchange_id,
                    symbol=self.symbol,
                    limit=lookback_ticks
                )
                if trades_data:
                    # get_recent_trades는 최신 -> 과거 순이므로 시간순 처리를 위해 reversed 사용
                    for i, row in enumerate(reversed(trades_data)):
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

    def sync_position_state(self, portfolio_manager: Any):
        """실제 포트폴리오 잔고를 조회하여 전략의 인메모리 포지션 상태를 강제 동기화합니다."""
        if not portfolio_manager:
            return

        # portfolio_manager 타입 유연성 대응 (실거래용 PortfolioManager vs 백테스트용 프록시 객체)
        target_portfolios = {}
        actual_pm = portfolio_manager
        p_id = None
        if hasattr(portfolio_manager, 'manager') and hasattr(portfolio_manager, 'portfolio_id'):
            actual_pm = portfolio_manager.manager
            p_id = portfolio_manager.portfolio_id
            if p_id in actual_pm.portfolios:
                target_portfolios = {p_id: actual_pm.portfolios[p_id]}
        elif hasattr(portfolio_manager, 'portfolios'):
            target_portfolios = portfolio_manager.portfolios
            if len(target_portfolios) == 1:
                p_id = list(target_portfolios.keys())[0]
            elif hasattr(portfolio_manager, 'get_active_simulation_portfolio'):
                active_sim = portfolio_manager.get_active_simulation_portfolio()
                if active_sim:
                    p_id = active_sim.id

        has_position = False
        matching_pos = None
        for portfolio in target_portfolios.values():
            pos_key = (self.exchange_id.lower(), self.symbol)
            if pos_key in portfolio.positions and portfolio.positions[pos_key].quantity > 0:
                has_position = True
                matching_pos = portfolio.positions[pos_key]
                break

        for host in self.hosts:
            if hasattr(host.strategy, 'in_position'):
                if host.strategy.in_position != has_position:
                    logger.info(
                        f"[TradeEngine] [sync_position_state] 전략 {host.strategy.id}의 인메모리 포지션 상태 동기화: "
                        f"{host.strategy.in_position} -> {has_position} ({self.symbol})"
                    )
                    host.strategy.in_position = has_position
                    if has_position and matching_pos:
                        host.strategy.buy_price = matching_pos.avg_price
                        host.strategy.peak_price = matching_pos.peak_price or matching_pos.avg_price
                        host.strategy.entry_time = matching_pos.entry_time
                    else:
                        if hasattr(host.strategy, '_reset_position_state'):
                            host.strategy._reset_position_state()
                        else:
                            host.strategy.buy_price = None
                            host.strategy.peak_price = None
                            host.strategy.entry_time = None

    async def process_tick(self, tick: Dict, portfolio_manager: Any, is_warmup: bool = False) -> tuple[List[TradeSignal], List[Candle]]:
        """실시간 틱을 처리하고, 완성된 캔들이 있을 경우 전략을 실행하여 신호와 캔들을 반환합니다."""
        self.last_tick = tick
        if not is_warmup:
            self.last_tick_received_at = time.time()
        tick_price = tick['trade_price']
        signals = []
        common_exit_triggered = False

        # portfolio_manager 타입 유연성 대응 (실거래용 PortfolioManager vs 백테스트용 프록시 객체)
        target_portfolios = {}
        actual_pm = portfolio_manager
        p_id = None
        if portfolio_manager:
            if hasattr(portfolio_manager, 'manager') and hasattr(portfolio_manager, 'portfolio_id'):
                actual_pm = portfolio_manager.manager
                p_id = portfolio_manager.portfolio_id
                if p_id in actual_pm.portfolios:
                    target_portfolios = {p_id: actual_pm.portfolios[p_id]}
            elif hasattr(portfolio_manager, 'portfolios'):
                target_portfolios = portfolio_manager.portfolios
                if len(target_portfolios) == 1:
                    p_id = list(target_portfolios.keys())[0]
                elif hasattr(portfolio_manager, 'get_active_simulation_portfolio'):
                    active_sim = portfolio_manager.get_active_simulation_portfolio()
                    if active_sim:
                        p_id = active_sim.id

        # 1. 틱 가격 기준으로 포지션의 peak_price 갱신 및 공통 청산 규칙 평가 (웜업 단계 제외)
        if portfolio_manager and not is_warmup:
            for pid, portfolio in target_portfolios.items():
                pos_key = (self.exchange_id.lower(), self.symbol)
                if pos_key in portfolio.positions:
                    pos = portfolio.positions[pos_key]
                    if pos.quantity > 0:
                        # peak_price 실시간 갱신
                        old_peak = pos.peak_price
                        pos.peak_price = max(pos.peak_price, tick_price)
                        
                        # peak_price가 상승하여 갱신되었을 때만 DB 저장
                        if pos.peak_price > old_peak:
                            await actual_pm.repository.save_portfolio(portfolio)
                            
                        # 공통 청산 규칙 평가
                        tick_ts = tick['trade_timestamp'] / 1000.0 if 'trade_timestamp' in tick else None
                        exit_triggered, exit_reason = self.exit_evaluator.evaluate(pos, tick_price, tick_ts)
                        if exit_triggered:
                            common_exit_triggered = True
                            # 즉시 시장가 SELL 신호 생성 (공통 청산)
                            signals.append(TradeSignal(
                                exchange_id=self.exchange_id,
                                symbol=self.symbol,
                                action="SELL",
                                price=tick_price,
                                reason=f"Common Exit: {exit_reason}",
                                interval=self.intervals[0] if self.intervals else 60,
                                strategy_id="COMMON_EXIT",
                                context={"exit_type": exit_reason, "avg_price": pos.avg_price, "peak_price": pos.peak_price}
                            ))

        closed_candles = []
        for interval in self.intervals:
            context = self.contexts[interval]
            candles_for_interval = context.add_tick(tick)
            closed_candles.extend(candles_for_interval)
        
        for candle in closed_candles:
            if is_warmup:
                continue
                
            # 단일화된 hosts 루프
            for host in self.hosts:
                if host.interval != candle.interval:
                    continue
                
                # 즉시 재진입 방지 가드: 이번 틱/캔들에서 공통 청산이 발생한 경우 신규 진입 차단
                if common_exit_triggered:
                    logger.info(f"[TradeEngine] Common Exit 발생으로 인해 {self.symbol} 전략 실행 생략 (즉시 재진입 방지)")
                    continue
                
                # 포지션 보유 상태 확인
                has_position = False
                for portfolio in target_portfolios.values():
                    pos_key = (self.exchange_id.lower(), self.symbol)
                    if pos_key in portfolio.positions and portfolio.positions[pos_key].quantity > 0:
                        has_position = True
                        break
                
                # 전략의 인메모리 포지션 상태를 실제 포트폴리오 상태와 동기화 (재기동/핫리로드 대응)
                if hasattr(host.strategy, 'in_position'):
                    if host.strategy.in_position != has_position:
                        logger.info(
                            f"[TradeEngine] 전략 {host.strategy.id}의 인메모리 포지션 상태 동기화: "
                            f"{host.strategy.in_position} -> {has_position} ({self.symbol})"
                        )
                        host.strategy.in_position = has_position
                        if has_position:
                            for portfolio in target_portfolios.values():
                                pos_key = (self.exchange_id.lower(), self.symbol)
                                if pos_key in portfolio.positions and portfolio.positions[pos_key].quantity > 0:
                                    pos = portfolio.positions[pos_key]
                                    host.strategy.buy_price = pos.avg_price
                                    host.strategy.peak_price = pos.peak_price or pos.avg_price
                                    host.strategy.entry_time = pos.entry_time
                                    break
                        else:
                            if hasattr(host.strategy, '_reset_position_state'):
                                host.strategy._reset_position_state()
                            else:
                                host.strategy.buy_price = None
                                host.strategy.peak_price = None
                                host.strategy.entry_time = None
                
                # 1) 전략이 비활성화(enabled=False)되었고, 보유 포지션도 없다면 신규 진입 방지를 위해 실행 건너뜀
                # 2) 만약 포지션을 보유하고 있다면 전략이 꺼져 있어도 청산 신호를 내보내야 하므로 실행함
                strategy_enabled = getattr(host.strategy, 'enabled', True)
                if not strategy_enabled and not has_position:
                    continue
                
                # 전략 실행 (portfolio_id 명시성 반영)
                action_result = await host.execute(context, portfolio_manager, portfolio_id=p_id)
                if action_result:
                    action = action_result.action if hasattr(action_result, 'action') else str(action_result)
                    # 비활성화된 전략의 오작동 BUY 신호 강제 차단 또는 포지션 보유 중인 전략의 중복 BUY 신호 차단
                    if action == 'BUY' and (not strategy_enabled or has_position):
                        logger.warning(
                            f"[TradeEngine] {self.symbol} BUY 신호 차단: "
                            f"전략활성화={strategy_enabled}, 포지션보유={has_position}"
                        )
                        continue
                        
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
                "exchange_id": self.exchange_id,
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
                    exchange_id=self.exchange_id,
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
                exchange_id=self.exchange_id,
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

    async def capture_feature_snapshot(self, proposal_id: str, strategy_id: str, exchange_id: str, symbol: str, proposal_type: str) -> FeatureSnapshot:
        """
        현재 시점의 실시간 시세 데이터, 지표 데이터 및 거래소 시장 특성을 취합하여 FeatureSnapshot DTO를 생성합니다.
        """
        req = FeatureBuildRequest(
            hosts=self.hosts,
            contexts=self.contexts,
            last_tick=self.last_tick
        )
        return await self.feature_builder.capture_feature_snapshot(
            proposal_id=proposal_id,
            strategy_id=strategy_id,
            exchange_id=exchange_id,
            symbol=symbol,
            proposal_type=proposal_type,
            request=req
        )
