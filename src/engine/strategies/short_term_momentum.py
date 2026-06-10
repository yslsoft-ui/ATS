from typing import Dict, Optional, Any
from src.engine.strategy import BaseStrategy, StrategyResult, StrategyType, StrategyRegistry
from src.engine.strategy_host import StrategyContext
from src.engine.exceptions import IndicatorNotReady

@StrategyRegistry.register
class ShortTermMomentumStrategy(BaseStrategy):
    """
    단기상승흐름 (Short-Term Trend Momentum) 전략.
    돌파 이후 확인된 상승 흐름에 정배열과 RSI Slope 및 볼린저 밴드 상단 돌파로 늦게 탑승하고,
    짧은 추세를 트레일링 스탑, 고정 손절, 이평 데드 크로스 및 극단적 과매수 임계치로 신속히 회수하는 모멘텀 추종 전략입니다.
    """
    type = StrategyType.BOTH

    default_params = {
        "fast_window": {"type": "int", "default": 5, "description": "단기 이평선(SMA) 윈도우"},
        "slow_window": {"type": "int", "default": 20, "description": "장기 이평선(SMA) 윈도우"},
        "rsi_window": {"type": "int", "default": 14, "description": "RSI 계산 윈도우"},
        "rsi_buy_threshold": {"type": "float", "default": 55.0, "description": "모멘텀 진입 RSI 하한선"},
        "rsi_sell_threshold": {"type": "float", "default": 80.0, "description": "극단적 과매수 청산 RSI 상한선"},
        "bb_window": {"type": "int", "default": 20, "description": "볼린저 밴드 계산 윈도우"},
        "bb_std": {"type": "float", "default": 2.0, "description": "볼린저 밴드 표준편차 승수"},
        "stop_loss_pct": {"type": "float", "default": 2.0, "description": "고정 손절 비율 (%)"},
        "trailing_stop_pct": {"type": "float", "default": 2.5, "description": "최고점 대비 허용 하락 트레일링 비율 (%)"}
    }

    def __init__(self, strategy_id: str, params: Dict[str, Any] = None):
        super().__init__(strategy_id, params)
        # 1. 파라미터 멤버화
        self.fast_window = int(self.params.get("fast_window", 5))
        self.slow_window = int(self.params.get("slow_window", 20))
        self.rsi_window = int(self.params.get("rsi_window", 14))
        self.rsi_buy_threshold = float(self.params.get("rsi_buy_threshold", 55.0))
        self.rsi_sell_threshold = float(self.params.get("rsi_sell_threshold", 80.0))
        self.bb_window = int(self.params.get("bb_window", 20))
        self.bb_std = float(self.params.get("bb_std", 2.0))
        self.stop_loss_pct = float(self.params.get("stop_loss_pct", 2.0))
        self.trailing_stop_pct = float(self.params.get("trailing_stop_pct", 2.5))

        # 2. 인메모리 상태 필드 정의
        self.in_position: bool = False
        self.buy_price: Optional[float] = None
        self.peak_price: Optional[float] = None
        self.entry_time: Optional[int] = None

    def on_update(self, context: StrategyContext) -> Optional[StrategyResult]:
        """
        StrategyHost로부터 주기적 갱신 신호를 받아 의사결정을 내립니다.
        """
        candles = context.candles
        warmup_len = max(self.slow_window, self.bb_window, self.rsi_window) + 1
        if len(candles) < warmup_len:
            raise IndicatorNotReady(f"Insufficient candles for ShortTermMomentumStrategy. Required: {warmup_len}, Got: {len(candles)}")

        current_price = context.current_price
        last_candle = context.last_candle

        # 2. 실시간 지표 계산 (context.get_indicator 단일 경로로 통일)
        fast_sma = context.get_indicator("sma", window=self.fast_window)
        slow_sma = context.get_indicator("sma", window=self.slow_window)
        rsi = context.get_indicator("rsi", window=self.rsi_window)
        rsi_prev = context.get_indicator("rsi", window=self.rsi_window, offset=1)
        
        bb_upper = context.get_indicator("bb_upper", window=self.bb_window, num_std=self.bb_std)
        bb_middle = context.get_indicator("bb_middle", window=self.bb_window, num_std=self.bb_std)
        bb_lower = context.get_indicator("bb_lower", window=self.bb_window, num_std=self.bb_std)

        # 지표 스냅샷 생성 (감사용)
        trade_context = {
            "fast_sma": round(fast_sma, 2),
            "slow_sma": round(slow_sma, 2),
            "rsi": round(rsi, 2),
            "rsi_prev": round(rsi_prev, 2),
            "bb_upper": round(bb_upper, 2),
            "bb_middle": round(bb_middle, 2),
            "bb_lower": round(bb_lower, 2)
        }

        # ─────────────────────────────────────────────────────────────────────
        # [청산 로직] 포지션을 보유하고 있는 상태
        # ─────────────────────────────────────────────────────────────────────
        if self.in_position:
            # 진입 후 최고가 갱신
            self.peak_price = max(self.peak_price, last_candle.high)

            # A. 고정 손절선 검증
            if current_price <= self.buy_price * (1 - self.stop_loss_pct / 100):
                reason = (
                    f"Stop Loss: Entry {self.buy_price:,.0f} -> Current {current_price:,.0f} "
                    f"(-{(self.buy_price - current_price)/self.buy_price*100:.2f}%)"
                )
                self._reset_position_state()
                return StrategyResult("SELL", price=current_price, reason=reason, context=trade_context)

            # B. 트레일링 스탑 검증
            if current_price <= self.peak_price * (1 - self.trailing_stop_pct / 100):
                reason = (
                    f"Trailing Stop: Peak {self.peak_price:,.0f} -> Current {current_price:,.0f} "
                    f"(-{(self.peak_price - current_price)/self.peak_price*100:.2f}%)"
                )
                self._reset_position_state()
                return StrategyResult("SELL", price=current_price, reason=reason, context=trade_context)

            # C. 데드 크로스 추세 반전 검증
            if fast_sma < slow_sma:
                reason = f"Dead Cross: Fast SMA {fast_sma:.2f} < Slow SMA {slow_sma:.2f}"
                self._reset_position_state()
                return StrategyResult("SELL", price=current_price, reason=reason, context=trade_context)

            # D. 극단적 과매수 청산 검증
            if rsi >= self.rsi_sell_threshold:
                reason = f"Overbought Clean Out: RSI {rsi:.2f} >= {self.rsi_sell_threshold:.2f}"
                self._reset_position_state()
                return StrategyResult("SELL", price=current_price, reason=reason, context=trade_context)

            return StrategyResult("HOLD", context=trade_context)

        # ─────────────────────────────────────────────────────────────────────
        # [진입 로직] 포지션이 없는 상태
        # ─────────────────────────────────────────────────────────────────────
        else:
            # 1. 정배열 확인
            sma_trend_ok = fast_sma > slow_sma
            # 2. RSI 강세 및 상승 추세 (Slope > 0) 확인
            rsi_trend_ok = rsi >= self.rsi_buy_threshold and rsi > rsi_prev
            # 3. 볼린저 밴드 상단 98% 근처 돌파 여부 확인
            bb_ok = current_price >= (bb_upper * 0.98)
            # 4. 직전 봉 종가 대비 현재가 보합 혹은 상승 확인
            candle_trend_ok = len(candles) >= 2 and candles[-1].close >= candles[-2].close

            if sma_trend_ok and rsi_trend_ok and bb_ok and candle_trend_ok:
                self.in_position = True
                self.buy_price = current_price
                self.peak_price = last_candle.high
                self.entry_time = last_candle.timestamp
                
                reason = (
                    f"Momentum Breakout: Fast SMA {fast_sma:.2f} > Slow SMA {slow_sma:.2f} | "
                    f"RSI {rsi:.2f} (prev: {rsi_prev:.2f}) | "
                    f"Price {current_price:,.0f} >= BB Upper 98% {bb_upper*0.98:,.0f}"
                )
                return StrategyResult("BUY", price=current_price, reason=reason, context=trade_context)

            return StrategyResult("HOLD", context=trade_context)

    def _reset_position_state(self):
        """포지션 청산 시 상태를 즉시 리셋합니다."""
        self.in_position = False
        self.buy_price = None
        self.peak_price = None
        self.entry_time = None
