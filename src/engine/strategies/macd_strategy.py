from typing import Dict, Optional
from src.engine.strategy import BaseStrategy, StrategyResult, StrategyRegistry
from src.engine.candles import Candle
from src.engine.indicators import IndicatorCalculator

@StrategyRegistry.register
class MACDStrategy(BaseStrategy):
    """
    MACD 골든크로스(매수) 및 데드크로스(매도) 신호를 생성합니다.
    """
    def __init__(self, fast_period: int = 12, slow_period: int = 26, signal_period: int = 9):
        self.calculator = IndicatorCalculator(window_size=slow_period)
        self.in_position = False
        self.prev_hist = 0

    @classmethod
    def get_metadata(cls) -> Dict:
        metadata = super().get_metadata()
        metadata["name"] = "MACD 골든크로스"
        metadata["params"] = {
            "fast_period": {"type": "int", "default": 12, "description": "단기 이동평균 기간"},
            "slow_period": {"type": "int", "default": 26, "description": "장기 이동평균 기간"},
            "signal_period": {"type": "int", "default": 9, "description": "신호선(Signal) 기간"}
        }
        return metadata

    def on_candle(self, candle: Candle) -> StrategyResult:
        indicators = self.calculator.update(candle.close)
        macd = indicators.get('macd')

        if macd is None or 'hist' not in macd:
            return StrategyResult("HOLD", reason="Waiting for MACD history")

        curr_hist = macd['hist']
        action = "HOLD"
        reason = ""

        if not self.in_position and self.prev_hist <= 0 and curr_hist > 0:
            self.in_position = True
            action = "BUY"
            reason = f"MACD Golden Cross (Hist: {curr_hist})"
        
        elif self.in_position and self.prev_hist >= 0 and curr_hist < 0:
            self.in_position = False
            action = "SELL"
            reason = f"MACD Dead Cross (Hist: {curr_hist})"

        self.prev_hist = curr_hist
        return StrategyResult(action, price=candle.close, reason=reason)
