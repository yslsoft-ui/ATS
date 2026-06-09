import pytest
import numpy as np
from src.engine.candles import Candle
from src.engine.market_data_context import MarketDataContext
from src.engine.strategy_host import StrategyHost, StrategyContext
from src.engine.strategy import StrategyRegistry, StrategyResult
from src.engine.indicators import calculate_sma, calculate_rsi, calculate_bollinger_bands

# 동적으로 로딩되지 않았을 경우를 위해 수동 임포트 시도 (TDD 단계)
try:
    from src.engine.strategies.short_term_momentum import ShortTermMomentumStrategy
except ImportError:
    # 아직 전략 파일이 작성되지 않은 상태이므로 테스트 로딩 시점에 에러가 날 수 있음.
    # pytest가 테스트를 컬렉션할 때 import error가 나지 않도록,
    # 실제 구현 파일이 생성되기 전에는 Mock 클래스 등으로 우회하거나
    # 바로 구현 파일을 생성한 뒤 테스트를 실행할 수도 있다.
    # 하지만 진정한 TDD를 위해서는 import 실패를 감수하고 적절히 선언한다.
    pass

def make_candle(close: float, timestamp: int, high: float = None, low: float = None, open_val: float = None, volume: float = 100.0) -> Candle:
    return Candle(
        exchange="upbit",
        symbol="BTC",
        interval=60,
        timestamp=timestamp,
        open=open_val if open_val is not None else close,
        high=high if high is not None else close,
        low=low if low is not None else close,
        close=close,
        volume=volume,
        buy_volume=volume / 2,
        sell_volume=volume / 2,
        count=10,
        is_closed=True
    )

def setup_context_with_candles(candles: list, strategy_params: dict) -> tuple[MarketDataContext, StrategyContext]:
    # 헬퍼 함수: MarketDataContext에 캔들을 밀어넣고 최신 캔들 상태의 Context를 생성합니다.
    mdc = MarketDataContext(exchange="upbit", symbol="BTC", interval=60, max_len=100)
    for c in candles:
        mdc.add_candle(c)
    
    context = StrategyContext(
        exchange="upbit",
        symbol="BTC",
        interval=60,
        market_data_context=mdc,
        params=strategy_params,
        portfolio={}
    )
    return mdc, context

# ─────────────────────────────────────────────────────────────────────
# 1. 웜업 기간 검증
# ─────────────────────────────────────────────────────────────────────
def test_short_term_momentum_warmup():
    from src.engine.strategies.short_term_momentum import ShortTermMomentumStrategy
    strat = ShortTermMomentumStrategy(strategy_id="short_term_momentum", params={})
    
    # 캔들 10개만 주입 (slow_window=20보다 작음)
    candles = [make_candle(100.0 + i, 1000 + i * 60) for i in range(10)]
    mdc, context = setup_context_with_candles(candles, strat.params)
    
    res = strat.on_update(context)
    assert res is None or res.action == "HOLD"

# ─────────────────────────────────────────────────────────────────────
# 2. 매수 진입(BUY) 신호 검증
# ─────────────────────────────────────────────────────────────────────
def test_short_term_momentum_buy_signal():
    from src.engine.strategies.short_term_momentum import ShortTermMomentumStrategy
    strat = ShortTermMomentumStrategy(strategy_id="short_term_momentum", params={
        "fast_window": 5,
        "slow_window": 20,
        "rsi_window": 14,
        "rsi_buy_threshold": 55.0,
        "bb_window": 20,
        "bb_std": 1.0  # 표준편차 승수를 1.0으로 낮춰 돌파 감도를 높임
    })
    
    # 이평 정배열, RSI 55 이상 + 상승(Slope > 0), 볼린저 밴드 상단(bb_upper * 0.98) 위 돌파 조건 유도
    # 초기 20개는 횡보 (100.0 근처)
    candles = [make_candle(100.0, 1000 + i * 60) for i in range(20)]
    
    # 그 뒤 캔들들은 급격히 우상향 돌파 (하락 98.0을 주어 RSI 100 고정 방지 및 점진적 상승으로 RSI Slope 확보)
    candles.append(make_candle(98.0, 1000 + 20 * 60))
    current_val = 98.0
    for i in range(5):
        current_val += 9.0 + i
        candles.append(make_candle(current_val, 1000 + (21 + i) * 60))
        
    mdc, context = setup_context_with_candles(candles, strat.params)
    
    res = strat.on_update(context)
    
    assert res is not None
    assert res.action == "BUY"
    assert strat.in_position is True
    assert strat.buy_price == 153.0
    assert strat.peak_price == 153.0

# ─────────────────────────────────────────────────────────────────────
# 3. RSI Slope 평탄/하락 시 매수 거부 검증
# ─────────────────────────────────────────────────────────────────────
def test_no_buy_when_rsi_flat():
    from src.engine.strategies.short_term_momentum import ShortTermMomentumStrategy
    strat = ShortTermMomentumStrategy(strategy_id="short_term_momentum", params={
        "fast_window": 5,
        "slow_window": 20,
        "rsi_window": 14,
        "rsi_buy_threshold": 55.0
    })
    
    # 20개 횡보
    candles = [make_candle(100.0, 1000 + i * 60) for i in range(20)]
    # 상승 후 마지막에 횡보하여 RSI 평탄 유도 (98.0 하락 섞기)
    candles.append(make_candle(98.0, 1000 + 20 * 60))
    candles.append(make_candle(105.0, 1000 + 21 * 60))
    candles.append(make_candle(110.0, 1000 + 22 * 60))
    candles.append(make_candle(110.0, 1000 + 23 * 60)) # 마지막 캔들 가격 보합 -> RSI slope <= 0
    
    mdc, context = setup_context_with_candles(candles, strat.params)
    res = strat.on_update(context)
    
    assert res is None or res.action == "HOLD"

# ─────────────────────────────────────────────────────────────────────
# 4. 이평선 역배열 시 매수 거부 검증
# ─────────────────────────────────────────────────────────────────────
def test_no_buy_when_fast_sma_crossing_down():
    from src.engine.strategies.short_term_momentum import ShortTermMomentumStrategy
    strat = ShortTermMomentumStrategy(strategy_id="short_term_momentum", params={
        "fast_window": 5,
        "slow_window": 20,
        "rsi_window": 14,
        "rsi_buy_threshold": 55.0
    })
    
    # 역배열 유도를 위해 과거 높은 가격에서 떨어지다가 급락 후 잠시 튀는 경우
    # 150에서 시작해 계속 하락
    candles = [make_candle(150.0 - i * 2, 1000 + i * 60) for i in range(20)]
    # 마지막 봉만 110에서 112로 소폭 반등하여 RSI는 오르나 역배열 상태 유지
    candles.append(make_candle(112.0, 1000 + 20 * 60))
    
    mdc, context = setup_context_with_candles(candles, strat.params)
    res = strat.on_update(context)
    
    assert res is None or res.action == "HOLD"

# ─────────────────────────────────────────────────────────────────────
# 5. 고정 손절선 (2.0%) 작동 검증
# ─────────────────────────────────────────────────────────────────────
def test_short_term_momentum_stop_loss():
    from src.engine.strategies.short_term_momentum import ShortTermMomentumStrategy
    strat = ShortTermMomentumStrategy(strategy_id="short_term_momentum", params={"stop_loss_pct": 2.0})
    
    # 억지로 포지션 진입 상태 모사
    strat.in_position = True
    strat.buy_price = 100.0
    strat.peak_price = 100.0
    strat.entry_time = 1000
    
    # 2.1% 하락한 캔들 주입 (97.9)
    candles = [make_candle(100.0, 1000 + i * 60) for i in range(20)]
    candles.append(make_candle(97.9, 1000 + 20 * 60))
    
    mdc, context = setup_context_with_candles(candles, strat.params)
    res = strat.on_update(context)
    
    assert res is not None
    assert res.action == "SELL"
    assert "Stop Loss" in res.reason
    assert strat.in_position is False

# ─────────────────────────────────────────────────────────────────────
# 6. 트레일링 스탑 (2.5%) 작동 검증
# ─────────────────────────────────────────────────────────────────────
def test_short_term_momentum_trailing_stop():
    from src.engine.strategies.short_term_momentum import ShortTermMomentumStrategy
    strat = ShortTermMomentumStrategy(strategy_id="short_term_momentum", params={"trailing_stop_pct": 2.5})
    
    # 포지션 진입 및 고점 상승 모사
    strat.in_position = True
    strat.buy_price = 100.0
    strat.peak_price = 120.0  # 최고점 120
    strat.entry_time = 1000
    
    # 최고점 120 대비 2.6% 하락한 116.8 주입
    # 120 * (1 - 0.026) = 116.88
    candles = [make_candle(100.0, 1000 + i * 60) for i in range(20)]
    # high가 116.8인 하락 봉
    candles.append(make_candle(116.8, 1000 + 20 * 60, high=116.8))
    
    mdc, context = setup_context_with_candles(candles, strat.params)
    res = strat.on_update(context)
    
    assert res is not None
    assert res.action == "SELL"
    assert "Trailing Stop" in res.reason
    assert strat.in_position is False

# ─────────────────────────────────────────────────────────────────────
# 7. 최고가 형성 중 트레일링 스탑 미작동 검증
# ─────────────────────────────────────────────────────────────────────
def test_trailing_does_not_trigger_before_peak_forms():
    from src.engine.strategies.short_term_momentum import ShortTermMomentumStrategy
    # rsi_sell_threshold를 105.0으로 넉넉히 설정하여 과매수 청산이 테스트를 방해하지 않게 가드함
    strat = ShortTermMomentumStrategy(strategy_id="short_term_momentum", params={
        "trailing_stop_pct": 2.5,
        "rsi_sell_threshold": 105.0
    })
    
    strat.in_position = True
    strat.buy_price = 100.0
    strat.peak_price = 110.0
    strat.entry_time = 1000
    
    # 가격이 최고가를 계속 갱신하는 상승 캔들 주입 (115.0)
    # 갱신 중에는 떨어지지 않았으므로 SELL이 발동하지 않고 peak_price가 115.0으로 갱신되어야 함
    candles = [make_candle(100.0, 1000 + i * 60) for i in range(20)]
    candles.append(make_candle(115.0, 1000 + 20 * 60, high=115.0))
    
    mdc, context = setup_context_with_candles(candles, strat.params)
    res = strat.on_update(context)
    
    assert res is None or res.action == "HOLD"
    assert strat.peak_price == 115.0
    assert strat.in_position is True

# ─────────────────────────────────────────────────────────────────────
# 8. 이평 데드 크로스 즉시 청산 검증
# ─────────────────────────────────────────────────────────────────────
def test_short_term_momentum_dead_cross():
    from src.engine.strategies.short_term_momentum import ShortTermMomentumStrategy
    # 손절선과 트레일링 스탑을 무력화하여 데드 크로스 단독 감지 테스트
    strat = ShortTermMomentumStrategy(strategy_id="short_term_momentum", params={
        "fast_window": 5,
        "slow_window": 20,
        "stop_loss_pct": 50.0,
        "trailing_stop_pct": 50.0
    })
    
    strat.in_position = True
    strat.buy_price = 100.0
    strat.peak_price = 100.0
    strat.entry_time = 1000
    
    # 횡보 후 데드크로스를 유도하기 위해 가격이 점진적으로 하락
    # 20개 횡보 (100.0)
    candles = [make_candle(100.0, 1000 + i * 60) for i in range(20)]
    # 그 뒤 급락 봉 5개 추가로 단기 이평이 장기 이평 아래로 꺾이게 유도
    for i in range(5):
        candles.append(make_candle(100.0 - (i + 1) * 3, 1000 + (20 + i) * 60))
        
    mdc, context = setup_context_with_candles(candles, strat.params)
    res = strat.on_update(context)
    
    assert res is not None
    assert res.action == "SELL"
    assert "Dead Cross" in res.reason
    assert strat.in_position is False

# ─────────────────────────────────────────────────────────────────────
# 9. RSI 80 이상 극단적 과매수 청산 검증
# ─────────────────────────────────────────────────────────────────────
def test_short_term_momentum_overbought_exit():
    from src.engine.strategies.short_term_momentum import ShortTermMomentumStrategy
    strat = ShortTermMomentumStrategy(strategy_id="short_term_momentum", params={
        "rsi_window": 14,
        "rsi_sell_threshold": 80.0
    })
    
    strat.in_position = True
    strat.buy_price = 100.0
    strat.peak_price = 100.0
    strat.entry_time = 1000
    
    # RSI 80 이상 도달하도록 폭등 유도
    candles = [make_candle(100.0, 1000 + i * 60) for i in range(20)]
    for i in range(10):
        candles.append(make_candle(100.0 + (i + 1) * 15, 1000 + (20 + i) * 60))
        
    mdc, context = setup_context_with_candles(candles, strat.params)
    res = strat.on_update(context)
    
    assert res is not None
    assert res.action == "SELL"
    assert "Overbought" in res.reason
    assert strat.in_position is False
