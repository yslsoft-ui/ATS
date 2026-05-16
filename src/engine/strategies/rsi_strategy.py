from typing import Dict, Optional
from src.engine.strategy import BaseStrategy, StrategyRegistry, StrategyResult
from src.engine.strategy_host import StrategyContext

@StrategyRegistry.register
class RSIStrategy(BaseStrategy):
    """
    RSI 지표를 기반으로 과매도(Buy) 및 과매수(Sell) 신호를 생성합니다.
    """
    default_params = {
        "rsi_window": {"type": "int", "default": 14, "description": "RSI 계산 기간"},
        "buy_threshold": {"type": "float", "default": 30.0, "description": "매수 임계값 (과매도)"},
        "sell_threshold": {"type": "float", "default": 70.0, "description": "매도 임계값 (과매수)"}
    }

    def __init__(self, strategy_id: str, params: Dict = None):
        super().__init__(strategy_id, params)
        # 필요한 지표 선언 (호스트가 계산 대행)
        self.required_indicators = ["rsi"]
        self.in_position = False
        self.buy_threshold = self.params.get('buy_threshold', 30.0)
        self.sell_threshold = self.params.get('sell_threshold', 70.0)

    def on_candle(self, candle) -> Optional[str]:
        # 하위 호환성을 위해 남겨두지만 사용되지 않음
        return None

    def on_update(self, context: StrategyContext) -> Optional[StrategyResult]:
        """
        StrategyHost로부터 전달받은 컨텍스트를 사용하여 판단을 내립니다.
        """
        rsi = context.indicators.get("rsi")
        if rsi is None:
            return None

        # 지표 스냅샷 생성
        trade_context = {"rsi": round(rsi, 2)}
        
        # 1. 매수 조건 (과매도 영역)
        if rsi <= self.buy_threshold and not self.in_position:
            self.in_position = True
            return StrategyResult(
                action="BUY", 
                price=context.current_price, 
                reason=f"RSI {rsi:.2f} <= {self.buy_threshold} (Oversold)",
                context=trade_context
            )

        # 2. 매도 조건 (과매수 영역)
        if rsi >= self.sell_threshold and self.in_position:
            self.in_position = False
            return StrategyResult(
                action="SELL", 
                price=context.current_price, 
                reason=f"RSI {rsi:.2f} >= {self.sell_threshold} (Overbought)",
                context=trade_context
            )

        return None
