import pytest
import numpy as np
from src.engine.candles import Candle
from src.engine.market_data_context import MarketDataContext

def create_dummy_candle(close_price: float, timestamp: int) -> Candle:
    return Candle(
        exchange="upbit",
        symbol="BTC",
        interval=60,
        timestamp=timestamp,
        open=close_price,
        high=close_price,
        low=close_price,
        close=close_price,
        volume=1.0,
        is_closed=True
    )

def test_market_data_context_candle_accumulation():
    context = MarketDataContext("upbit", "BTC", 60, max_len=5)
    
    # 캔들 추가 테스트
    for i in range(10):
        context.add_candle(create_dummy_candle(100.0 + i, 1000 + i * 60))
        
    # 최대 길이를 넘지 않는지 검증
    assert len(context.candles) == 5
    # 최신 가격들이 보존되어 있는지 검증
    assert np.array_equal(context.prices, np.array([105.0, 106.0, 107.0, 108.0, 109.0]))

def test_market_data_context_caching():
    context = MarketDataContext("upbit", "BTC", 60, max_len=30)
    
    # 가격 데이터 채우기 (25개)
    prices = [10.0 + (i % 5) for i in range(25)]
    for i, p in enumerate(prices):
        context.add_candle(create_dummy_candle(p, 1000 + i * 60))
        
    # 지표 최초 계산
    sma_first = context.get_indicator("sma", window=20)
    assert sma_first is not None
    
    # 캐시 등록 여부 확인
    assert "sma_window=20" in context.indicator_cache
    assert context.indicator_cache["sma_window=20"] == sma_first
    
    # 두 번째 계산은 동일 캐시 값 반환
    sma_second = context.get_indicator("sma", window=20)
    assert sma_first == sma_second
    
    # 캔들 추가 시 캐시 클리어 검증
    context.add_candle(create_dummy_candle(15.0, 5000))
    assert len(context.indicator_cache) == 0
    
    # 재계산 검증
    sma_after_add = context.get_indicator("sma", window=20)
    assert sma_after_add is not None

def test_market_data_context_multiple_indicators():
    context = MarketDataContext("upbit", "BTC", 60, max_len=30)
    
    # 충분한 데이터 채우기
    for i in range(30):
        context.add_candle(create_dummy_candle(10.0 + i * 0.1, 1000 + i * 60))
        
    # RSI 및 볼린저 밴드 계산
    rsi = context.get_indicator("rsi", window=14)
    bb = context.get_indicator("bb", window=20)
    macd = context.get_indicator("macd", fast_period=12, slow_period=26, signal_period=9)
    
    assert rsi is not None
    assert bb['upper'] is not None
    assert macd['line'] is not None
    
    # 개별 볼린저 밴드 키 접근 시 캐시에서 조회되는지 검증
    bb_upper = context.get_indicator("bb_upper", window=20)
    assert bb_upper == bb['upper']
