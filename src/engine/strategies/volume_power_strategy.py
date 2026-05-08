from typing import Dict, Optional
from src.engine.strategy import BaseStrategy, StrategyResult, StrategyRegistry
from src.engine.candles import Candle

@StrategyRegistry.register
class VolumePowerStrategy(BaseStrategy):
    """
    체결강도(Volume Power)를 기반으로 매수/매도 신호를 생성합니다.
    체결강도 = (매수 체결량 / 매도 체결량) * 100
    """
    def __init__(self, buy_threshold: float = 120.0, sell_threshold: float = 80.0):
        self.buy_threshold = buy_threshold
        self.sell_threshold = sell_threshold
        self.in_position = False

    @classmethod
    def get_metadata(cls) -> Dict:
        metadata = super().get_metadata()
        metadata["name"] = "체결강도 돌파 전략"
        metadata["params"] = {
            "buy_threshold": {"type": "float", "default": 120.0, "description": "매수 진입 체결강도 (%)"},
            "sell_threshold": {"type": "float", "default": 80.0, "description": "매도 청산 체결강도 (%)"}
        }
        return metadata

    def on_candle(self, candle: Candle) -> StrategyResult:
        # 매도 거래량이 0인 경우에 대한 예외 처리
        if candle.sell_volume == 0:
            vol_power = 1000.0 if candle.buy_volume > 0 else 100.0 
        else:
            vol_power = (candle.buy_volume / candle.sell_volume) * 100.0

        action = "HOLD"
        reason = ""

        if not self.in_position and vol_power > self.buy_threshold:
            self.in_position = True
            action = "BUY"
            reason = f"Volume Power {vol_power:.1f}% > {self.buy_threshold}%"
        
        elif self.in_position and vol_power < self.sell_threshold:
            self.in_position = False
            action = "SELL"
            reason = f"Volume Power {vol_power:.1f}% < {self.sell_threshold}%"

        return StrategyResult(action, price=candle.close, reason=reason)
