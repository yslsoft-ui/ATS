from typing import Dict, Optional
from src.engine.strategy import BaseStrategy, StrategyResult, StrategyRegistry
from src.engine.candles import Candle
from src.engine.indicators import IndicatorCalculator

@StrategyRegistry.register
class RSIStrategy(BaseStrategy):
    """
    RSI 지표를 기반으로 과매도(Buy) 및 과매수(Sell) 신호를 생성합니다.
    """
    def __init__(self, rsi_window: int = 14, buy_threshold: float = 30.0, sell_threshold: float = 70.0):
        self.calculator = IndicatorCalculator(window_size=rsi_window)
        self.buy_threshold = buy_threshold
        self.sell_threshold = sell_threshold
        self.in_position = False

    @classmethod
    def get_metadata(cls) -> Dict:
        metadata = super().get_metadata()
        metadata["name"] = "RSI 역추세 전략"
        metadata["params"] = {
            "rsi_window": {"type": "int", "default": 14, "description": "RSI 계산 기간"},
            "buy_threshold": {"type": "float", "default": 30.0, "description": "매수 임계값 (과매도)"},
            "sell_threshold": {"type": "float", "default": 70.0, "description": "매도 임계값 (과매수)"}
        }
        return metadata

    def on_candle(self, candle: Candle) -> StrategyResult:
        indicators = self.calculator.update(candle.close)
        rsi = indicators.get('rsi')

        if rsi is None:
            return StrategyResult("HOLD", reason="Waiting for indicators to warm up")

        if not self.in_position and rsi < self.buy_threshold:
            self.in_position = True
            return StrategyResult("BUY", price=candle.close, reason=f"RSI {rsi} < {self.buy_threshold}")
        
        elif self.in_position and rsi > self.sell_threshold:
            self.in_position = False
            return StrategyResult("SELL", price=candle.close, reason=f"RSI {rsi} > {self.sell_threshold}")

        return StrategyResult("HOLD")
