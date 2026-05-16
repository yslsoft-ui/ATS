from src.engine.strategy import BaseStrategy, StrategyResult, StrategyType, StrategyRegistry
from src.engine.candles import Candle
from typing import Dict, Optional

@StrategyRegistry.register
class PanicStrategy(BaseStrategy):
    """
    거래량 가중 변동성 기반 패닉 청산 전략.
    단시간 내에 압도적인 매도 거래량을 동반한 급락 감지 시 즉시 탈출합니다.
    """
    type = StrategyType.EXIT

    def __init__(self, vol_multiplier: float = 3.0, drop_threshold: float = 0.02, **kwargs):
        super().__init__(**kwargs)
        self.vol_multiplier = vol_multiplier  # 평균 거래량 대비 배수
        self.drop_threshold = drop_threshold    # 하락 폭 임계치 (2%)
        self.volumes = []

    def on_candle(self, candle: Candle) -> StrategyResult:
        self.volumes.append(candle.volume)
        if len(self.volumes) > 20:
            self.volumes.pop(0)

        if len(self.volumes) < 10:
            return StrategyResult("HOLD")

        avg_vol = sum(self.volumes[:-1]) / (len(self.volumes) - 1)
        
        # 1. 거래량이 평균보다 월등히 높고
        # 2. 가격이 시가 대비 임계치 이상 하락했을 때
        price_drop = (candle.close - candle.open) / candle.open
        
        if candle.volume > avg_vol * self.vol_multiplier and price_drop < -self.drop_threshold:
            return StrategyResult("SELL", candle.close, f"Panic Detected: Vol x{candle.volume/avg_vol:.1f}, Drop {price_drop*100:.1f}%")

        return StrategyResult("HOLD")

    @classmethod
    def get_metadata(cls) -> Dict:
        meta = super().get_metadata()
        meta["params"] = {
            "vol_multiplier": {"default": 3.0, "type": "float", "description": "평균 거래량 대비 패닉 판단 배수"},
            "drop_threshold": {"default": 0.02, "type": "float", "description": "패닉 판단 하락 폭 (0.02 = 2%)"}
        }
        return meta
