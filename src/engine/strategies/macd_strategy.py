from typing import Dict, Optional, Any
from src.engine.strategy import BaseStrategy, StrategyResult, StrategyRegistry
from src.engine.strategy_host import StrategyContext

@StrategyRegistry.register
class MACDStrategy(BaseStrategy):
    """
    MACD 골든크로스(매수) 및 데드크로스(매도) 신호를 생성합니다.
    """
    def __init__(self, strategy_id: str, params: Dict = None):
        super().__init__(strategy_id, params)
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

    def on_update(self, context: StrategyContext) -> Optional[StrategyResult]:
        fast = self.params.get('fast_period', 12)
        slow = self.params.get('slow_period', 26)
        signal = self.params.get('signal_period', 9)

        macd = context.get_indicator(
            'macd',
            fast_period=fast,
            slow_period=slow,
            signal_period=signal
        )

        curr_hist = macd['hist']
        action = "HOLD"
        reason = ""

        if not self.in_position and self.prev_hist <= 0 and curr_hist > 0:
            self.in_position = True
            action = "BUY"
            reason = f"MACD Golden Cross (Hist: {curr_hist:.4f})"
        
        elif self.in_position and self.prev_hist >= 0 and curr_hist < 0:
            self.in_position = False
            action = "SELL"
            reason = f"MACD Dead Cross (Hist: {curr_hist:.4f})"

        self.prev_hist = curr_hist
        if action != "HOLD":
            return StrategyResult(action, price=context.current_price, reason=reason, context={"macd_hist": curr_hist})
        return None
