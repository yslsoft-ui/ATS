from src.engine.strategy import BaseStrategy, StrategyResult, StrategyType, StrategyRegistry
from src.engine.strategy_host import StrategyContext
from src.engine.exceptions import IndicatorNotReady
from typing import Dict, Optional

@StrategyRegistry.register
class TrendBendStrategy(BaseStrategy):
    """
    거래량 다이버전스 기반 변곡점 탈출 전략.
    가격은 상승 또는 유지되나 거래량이 점진적으로 감소하며 동력이 고갈되는 시점의 첫 음봉에서 매도합니다.
    """
    type = StrategyType.EXIT

    def __init__(self, strategy_id: str, params: Dict = None):
        super().__init__(strategy_id, params)

    def on_update(self, context: StrategyContext) -> StrategyResult:
        candles = context.candles
        required = self.lookback + 1
        if len(candles) < required:
            raise IndicatorNotReady(f"Insufficient candles for TrendBendStrategy. Required: {required}, Got: {len(candles)}")

        hist = candles[-required:]
        current_candle = hist[-1]

        # 거래량 감소 추세 확인 (Volume Divergence)
        vol_decreasing = all(hist[i].volume > hist[i+1].volume for i in range(len(hist)-2))
        
        # 가격 상승 혹은 횡보 확인
        price_rising = hist[-2].close >= hist[0].open
        
        # 현재 캔들이 음봉인지 확인 (Trend Bend)
        is_bearish = current_candle.close < current_candle.open

        if vol_decreasing and price_rising and is_bearish:
            return StrategyResult("SELL", current_candle.close, "Trend Bend: Volume Divergence with Bearish Candle")

        return StrategyResult("HOLD")

    @classmethod
    def get_metadata(cls) -> Dict:
        meta = super().get_metadata()
        meta["params"] = {
            "lookback": {"default": 5, "type": "int", "description": "추세 확인을 위한 이전 캔들 개수"}
        }
        return meta
