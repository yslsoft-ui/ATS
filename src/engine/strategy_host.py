from typing import List, Dict, Any, Optional
from src.engine.candles import Candle
from src.engine.strategy import BaseStrategy

class StrategyContext:
    """전략 실행 시점에 전달되는 풍부한 데이터 컨텍스트입니다."""
    def __init__(self, exchange: str, symbol: str, interval: int, 
                 market_data_context: Any, params: Dict[str, Any], portfolio: Dict[str, Any]):
        self.exchange = exchange
        self.symbol = symbol
        self.interval = interval
        self.market_data_context = market_data_context
        self.params = params
        self.portfolio = portfolio
        
        # 하위 호환성을 위해 기존 required_indicators 사전 연산값을 채워둘 딕셔너리
        self._indicators_dict = {}

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

    @property
    def indicators(self) -> Dict[str, Any]:
        """하위 호환성 지원용 indicators 딕셔너리 프로퍼티"""
        return self._indicators_dict

class StrategyHost:
    """
    각 전략(Strategy)을 래핑하여 실행하는 얇은 추상화 Runner 모듈입니다.
    데이터 보관, 로깅, 신호 가공 등 인프라스트럭처 책임을 배제한 순수 실행 단위입니다.
    """
    def __init__(self, strategy: BaseStrategy, exchange: str, symbol: str, interval: int):
        self.strategy = strategy
        self.exchange = exchange
        self.symbol = symbol
        self.interval = interval
        
        # 전략에 설정된 파라미터 가져오기
        self.params = getattr(strategy, 'params', {})

    async def execute(self, market_data_context: Any, portfolio_manager: Any = None) -> Optional[Any]:
        """
        주입된 MarketDataContext 기반으로 전략을 실행하고 원시 실행 결과(StrategyResult 또는 str)를 반환합니다.
        """
        # 1. 포트폴리오 상태 구성
        portfolio_status = {}
        if portfolio_manager:
            portfolio_status = portfolio_manager.get_portfolio_summary(self.symbol, exchange=self.exchange)

        # 2. 컨텍스트 생성
        context = StrategyContext(
            exchange=self.exchange,
            symbol=self.symbol,
            interval=self.interval,
            market_data_context=market_data_context,
            params=self.params,
            portfolio=portfolio_status
        )

        # 3. 하위 호환성: 전략에 등록된 required_indicators 사전 계산
        required = getattr(self.strategy, 'required_indicators', [])
        for ind in required:
            window = self.params.get('rsi_window', self.params.get('sma_window', 20))
            val = context.get_indicator(ind, window=window)
            context._indicators_dict[ind] = val

        # 4. 전략 실행
        action_result = None
        if hasattr(self.strategy, 'on_update'):
            action_result = self.strategy.on_update(context)
        else:
            # 하위 호환성
            action_result = self.strategy.on_candle(context.last_candle)

        return action_result

