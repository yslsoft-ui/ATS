from typing import List, Dict, Any, Optional
from src.engine.candles import Candle
from src.engine.strategy import BaseStrategy, StrategyResult
from src.engine.exceptions import IndicatorNotReady

class StrategyContext:
    """전략 실행 시점에 전달되는 풍부한 데이터 컨텍스트입니다."""
    def __init__(self, exchange_id: str, symbol: str, interval: int, 
                 market_data_context: Any, params: Dict[str, Any], portfolio: Dict[str, Any]):
        self.exchange_id = exchange_id
        self.symbol = symbol
        self.interval = interval
        self.market_data_context = market_data_context
        self.params = params
        self.portfolio = portfolio

    @property
    def candles(self) -> List[Candle]:
        """컨텍스트가 속한 MarketDataContext로부터 캔들 리스트를 조회합니다."""
        return self.market_data_context.candles

    @property
    def last_candle(self) -> Optional[Candle]:
        return self.candles[-1] if self.candles else None

    @property
    def current_price(self) -> float:
        return self.last_candle.close if self.last_candle else 0.0

    def get_indicator(self, name: str, **kwargs) -> Any:
        """동적으로 지표를 계산하거나 캐시에서 즉시 반환합니다."""
        return self.market_data_context.get_indicator(name, **kwargs)

class StrategyHost:
    """
    각 전략(Strategy)을 래핑하여 실행하는 얇은 추상화 Runner 모듈입니다.
    데이터 보관, 로깅, 신호 가공 등 인프라스트럭처 책임을 배제한 순수 실행 단위입니다.
    """
    def __init__(self, strategy: BaseStrategy, exchange_id: str, symbol: str, interval: int):
        self.strategy = strategy
        self.exchange_id = exchange_id
        self.symbol = symbol
        self.interval = interval
        
        # 전략에 설정된 파라미터 가져오기
        self.params = getattr(strategy, 'params', {})

    async def execute(self, market_data_context: Any, portfolio_manager: Any = None, portfolio_id: Optional[str] = None) -> Optional[Any]:
        """
        주입된 MarketDataContext 기반으로 전략을 실행하고 원시 실행 결과(StrategyResult)를 반환합니다.
        """
        # 1. 포트폴리오 상태 구성 (exchange_id + symbol + portfolio_id 기반)
        portfolio_status = {}
        if portfolio_manager:
            portfolio_status = portfolio_manager.get_portfolio_summary(
                self.symbol, 
                portfolio_id=portfolio_id, 
                exchange_id=self.exchange_id
            )

        # 1.5. 전략 상태 동기화 (공통 청산 및 수동 청산 등 외부 요인 반영)
        if hasattr(self.strategy, 'in_position'):
            has_position = portfolio_status.get('quantity', 0.0) > 0
            self.strategy.in_position = has_position

        # 2. 컨텍스트 생성
        context = StrategyContext(
            exchange_id=self.exchange_id,
            symbol=self.symbol,
            interval=self.interval,
            market_data_context=market_data_context,
            params=self.params,
            portfolio=portfolio_status
        )

        # 3. 전략 실행 및 정상 준비 부족 상태(IndicatorNotReady) 핸들링
        try:
            action_result = self.strategy.on_update(context)
        except IndicatorNotReady as e:
            action_result = StrategyResult(
                action="HOLD",
                reason=f"IndicatorNotReady: {str(e)}"
            )

        return action_result
