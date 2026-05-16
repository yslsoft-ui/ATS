from src.engine.strategy import BaseStrategy, StrategyResult, StrategyType, StrategyRegistry
from src.engine.candles import Candle
from typing import Dict, Optional

@StrategyRegistry.register
class TrendBendStrategy(BaseStrategy):
    """
    거래량 다이버전스 기반 변곡점 탈출 전략.
    가격은 상승 또는 유지되나 거래량이 점진적으로 감소하며 동력이 고갈되는 시점의 첫 음봉에서 매도합니다.
    """
    type = StrategyType.EXIT

    def __init__(self, lookback: int = 5, **kwargs):
        super().__init__(**kwargs)
        self.lookback = lookback
        self.candles = []

    def on_candle(self, candle: Candle) -> StrategyResult:
        self.candles.append(candle)
        if len(self.candles) > self.lookback + 1:
            self.candles.pop(0)

        if len(self.candles) < self.lookback + 1:
            return StrategyResult("HOLD")

        # 거래량 감소 추세 확인 (Volume Divergence)
        vol_decreasing = all(self.candles[i].volume > self.candles[i+1].volume for i in range(len(self.candles)-2))
        
        # 가격 상승 혹은 횡보 확인
        price_rising = self.candles[-2].close >= self.candles[0].open
        
        # 현재 캔들이 음봉인지 확인 (Trend Bend)
        is_bearish = candle.close < candle.open

        if vol_decreasing and price_rising and is_bearish:
            return StrategyResult("SELL", candle.close, "Trend Bend: Volume Divergence with Bearish Candle")

        return StrategyResult("HOLD")

    @classmethod
    def get_metadata(cls) -> Dict:
        meta = super().get_metadata()
        meta["params"] = {
            "lookback": {"default": 5, "type": "int", "description": "추세 확인을 위한 이전 캔들 개수"}
        }
        return meta
