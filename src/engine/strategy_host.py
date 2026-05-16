import logging
import asyncio
import time
from typing import List, Dict, Any, Optional, Callable
from src.engine.candles import Candle
from src.engine.strategy import BaseStrategy, TradeSignal
from src.engine.indicators import IndicatorCalculator

logger = logging.getLogger(__name__)

class StrategyContext:
    """
    전략이 판단을 내리는 데 필요한 모든 데이터를 제공하는 컨텍스트 객체입니다.
    """
    def __init__(self, symbol: str, interval: int, candles: List[Candle], indicators: Dict[str, Any], params: Dict[str, Any], portfolio: Dict[str, Any]):
        self.symbol = symbol
        self.interval = interval
        self.candles = candles
        self.indicators = indicators
        self.params = params
        self.portfolio = portfolio

    @property
    def last_candle(self) -> Optional[Candle]:
        return self.candles[-1] if self.candles else None

    @property
    def current_price(self) -> float:
        return self.last_candle.close if self.last_candle else 0.0

class StrategyHost:
    """
    전략을 감싸서(Wrapping) 데이터 공급 및 지표 계산을 관리하는 호스트입니다.
    """
    def __init__(self, strategy: BaseStrategy, symbol: str, interval: int, on_status_callback: Optional[Callable] = None):
        self.strategy = strategy
        self.symbol = symbol
        self.interval = interval
        self.candles: List[Candle] = []
        self.indicators: Dict[str, Any] = {}
        self.last_df = None  # 지표 계산 최적화를 위한 캐시
        self.on_status_callback = on_status_callback
        
        # 전략에 설정된 파라미터 가져오기
        self.params = getattr(strategy, 'params', {})

    async def on_candle(self, candle: Candle, portfolio_manager: Any = None) -> Optional[TradeSignal]:
        """새로운 캔들이 들어왔을 때 지표를 업데이트하고 전략을 실행합니다."""
        # 1. 캔들 추가
        self.candles.append(candle)
        
        # 최대 캔들 유지 (메모리 관리 - 200개 정도면 대부분의 지표 충분)
        if len(self.candles) > 200:
            self.candles.pop(0)

        # 2. 지표 업데이트 (증분 계산 논리 포함)
        await self._update_indicators()

        # 3. 포트폴리오 상태 구성 (필요 시)
        portfolio_status = {}
        if portfolio_manager:
            # 포트폴리오 매니저에서 현재 종목의 포지션 및 잔고 정보를 가져옴
            portfolio_status = portfolio_manager.get_portfolio_summary(self.symbol)

        # 4. 컨텍스트 생성
        context = StrategyContext(
            symbol=self.symbol,
            interval=self.interval,
            candles=self.candles,
            indicators=self.indicators,
            params=self.params,
            portfolio=portfolio_status
        )

        # 5. 전략 실행
        try:
            action_result = None
            if hasattr(self.strategy, 'on_update'):
                action_result = self.strategy.on_update(context)
            else:
                # 하위 호환성 유지 (점진적 리팩토링용)
                action_result = self.strategy.on_candle(candle)

            # --- [NEW] 실시간 상태 브로드캐스트 (Audit Log) ---
            if self.on_status_callback:
                status_info = {
                    "type": "strategy_status",
                    "strategy_id": self.strategy.id,
                    "symbol": self.symbol,
                    "indicators": self.indicators,
                    "last_action": action_result.action if hasattr(action_result, 'action') else str(action_result),
                    "timestamp": int(time.time() * 1000)
                }
                asyncio.create_task(self.on_status_callback(status_info))
            # -----------------------------------------------

            if not action_result:
                return None

            # StrategyResult 객체인 경우 처리
            from src.engine.strategy import StrategyResult
            if isinstance(action_result, StrategyResult):
                if action_result.action in ['BUY', 'SELL']:
                    return TradeSignal(
                        symbol=self.symbol,
                        action=action_result.action,
                        price=action_result.price or candle.close,
                        reason=action_result.reason or f"Strategy {self.strategy.id} signal",
                        interval=self.interval,
                        strategy_id=self.strategy.id,
                        context=action_result.context
                    )
            # 문자열인 경우 처리 (하위 호환)
            elif action_result in ['BUY', 'SELL']:
                return TradeSignal(
                    symbol=self.symbol,
                    action=action_result,
                    price=candle.close,
                    reason=f"Strategy {self.strategy.id} legacy signal",
                    interval=self.interval,
                    strategy_id=self.strategy.id
                )
        except Exception as e:
            logger.error(f"Error executing strategy {self.strategy.id}: {str(e)}")
            
        return None

    async def _update_indicators(self):
        """
        전략에서 선언한 지표들을 계산합니다.
        IndicatorCalculator 인스턴스를 유지하여 증분 계산을 수행합니다.
        """
        required = getattr(self.strategy, 'required_indicators', [])
        if not required:
            return

        if not hasattr(self, 'calculator'):
            # 전략이 요구하는 윈도우 사이즈를 파라미터에서 가져옴 (기본 20)
            window = self.params.get('rsi_window', self.params.get('sma_window', 20))
            self.calculator = IndicatorCalculator(window_size=window)

        try:
            # 마지막 캔들의 종가로 지표 업데이트 (증분 계산)
            if self.candles:
                results = self.calculator.update(self.candles[-1].close)
                # 계산된 결과 중 전략이 요구하는 지표만 필터링하여 저장
                for ind in required:
                    if ind in results:
                        self.indicators[ind] = results[ind]
                    elif isinstance(results.get('bb'), dict) and ind in ['bb_upper', 'bb_lower']:
                        # 볼린저 밴드 특수 처리
                        self.indicators['bb_upper'] = results['bb']['upper']
                        self.indicators['bb_lower'] = results['bb']['lower']
        except Exception as e:
            logger.error(f"Indicator incremental calculation error for {self.symbol}: {str(e)}")
