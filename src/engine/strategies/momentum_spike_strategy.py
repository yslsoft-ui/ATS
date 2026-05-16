from typing import Dict, Optional, List
from collections import deque
from src.engine.strategy import BaseStrategy, StrategyResult, StrategyRegistry
from src.engine.candles import Candle

@StrategyRegistry.register
class MomentumSpikeStrategy(BaseStrategy):
    """
    짧은 순간(1-10초)의 거래량 폭증과 가격 상승 모멘텀을 포착합니다.
    트레일링 스탑을 통해 수익을 보존하고 급락 시 탈출합니다.
    """
    def __init__(
        self, 
        lookback_periods: int = 20, 
        vol_multiplier: float = 3.0, 
        freq_multiplier: float = 2.0,
        buy_ratio_threshold: float = 0.7,
        price_change_threshold: float = 0.3,
        trailing_stop_pct: float = 1.5,
        **kwargs
    ):
        super().__init__(interval=kwargs.get('interval', 10))
        self.lookback_periods = lookback_periods
        self.vol_multiplier = vol_multiplier
        self.freq_multiplier = freq_multiplier
        self.buy_ratio_threshold = buy_ratio_threshold
        self.price_change_threshold = price_change_threshold
        self.trailing_stop_pct = trailing_stop_pct
        
        self.history = deque(maxlen=lookback_periods)
        self.in_position = False
        self.peak_price = 0.0
        self.buy_price = 0.0

    @classmethod
    def get_metadata(cls) -> Dict:
        metadata = super().get_metadata()
        metadata["name"] = "모멘텀 급등 돌파 전략"
        metadata["params"] = {
            "lookback_periods": {"type": "int", "default": 20, "description": "평균 계산을 위한 이전 봉 개수"},
            "vol_multiplier": {"type": "float", "default": 3.0, "description": "평균 대비 거래량 폭증 배수"},
            "freq_multiplier": {"type": "float", "default": 2.0, "description": "평균 대비 체결 빈도 증가 배수"},
            "buy_ratio_threshold": {"type": "float", "default": 0.7, "description": "최소 매수 체결 비중 (0.7 = 70%)"},
            "price_change_threshold": {"type": "float", "default": 0.3, "description": "최소 가격 상승률 (%)"},
            "trailing_stop_pct": {"type": "float", "default": 1.5, "description": "최고점 대비 허용 하락폭 (%)"}
        }
        return metadata

    def on_candle(self, candle: Candle) -> StrategyResult:
        # 이 전략은 10초 봉에서만 작동하도록 제한 (필요 시 수정 가능)
        if candle.interval != 10:
            return StrategyResult("HOLD")

        # 1. 매도 로직 (이미 포지션이 있는 경우)
        if self.in_position:
            self.peak_price = max(self.peak_price, candle.high)
            drop_from_peak = (self.peak_price - candle.close) / self.peak_price * 100
            
            if drop_from_peak >= self.trailing_stop_pct:
                self.in_position = False
                profit = (candle.close - self.buy_price) / self.buy_price * 100
                reason = f"Trailing Stop: Peak {self.peak_price:,.0f} -> Current {candle.close:,.0f} (-{drop_from_peak:.2f}%) | Profit: {profit:.2f}%"
                self.buy_price = 0.0
                self.peak_price = 0.0
                return StrategyResult("SELL", price=candle.close, reason=reason)
            
            return StrategyResult("HOLD")

        # 2. 매수 로직 (포지션이 없는 경우)
        if len(self.history) < self.lookback_periods:
            self.history.append(candle)
            return StrategyResult("HOLD", reason="Warming up history")

        # 과거 평균 계산
        avg_vol = sum(c.volume for c in self.history) / len(self.history)
        avg_freq = sum(c.count for c in self.history) / len(self.history)
        
        # 현재 상태 계산
        buy_ratio = candle.buy_volume / candle.volume if candle.volume > 0 else 0
        price_change = (candle.close - candle.open) / candle.open * 100
        
        # 조건 체크
        vol_spike = candle.volume >= (avg_vol * self.vol_multiplier)
        freq_spike = candle.count >= (avg_freq * self.freq_multiplier)
        strong_buy = buy_ratio >= self.buy_ratio_threshold
        price_up = price_change >= self.price_change_threshold
        
        if vol_spike and freq_spike and strong_buy and price_up:
            self.in_position = True
            self.buy_price = candle.close
            self.peak_price = candle.high
            reason = f"Spike Detected: Vol x{candle.volume/avg_vol:.1f}, Freq x{candle.count/avg_freq:.1f}, Buy {buy_ratio*100:.1f}%, Price +{price_change:.2f}%"
            return StrategyResult("BUY", price=candle.close, reason=reason)

        # 히스토리 업데이트
        self.history.append(candle)
        return StrategyResult("HOLD")
