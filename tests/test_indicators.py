import pytest
import numpy as np
import pandas as pd
from src.engine.indicators import IndicatorCalculator, calculate_ema, calculate_atr
from src.engine.candles import Candle

def make_candle(close: float, high: float, low: float, open_val: float, timestamp: int) -> Candle:
    return Candle(
        exchange_id="upbit",
        symbol="BTC",
        interval=60,
        timestamp=timestamp,
        open=open_val,
        high=high,
        low=low,
        close=close,
        volume=100.0,
        buy_volume=50.0,
        sell_volume=50.0,
        count=10,
        is_closed=True
    )

def test_calculate_ema():
    prices = np.array([10.0, 11.0, 12.0, 13.0, 14.0])
    # window = 3
    ema_val = calculate_ema(prices, 3)
    assert ema_val is not None
    # alpha = 2 / (3 + 1) = 0.5
    # ema_0 = 10.0
    # ema_1 = 11.0 * 0.5 + 10.0 * 0.5 = 10.5
    # ema_2 = 12.0 * 0.5 + 10.5 * 0.5 = 11.25
    # ema_3 = 13.0 * 0.5 + 11.25 * 0.5 = 12.125
    # ema_4 = 14.0 * 0.5 + 12.125 * 0.5 = 13.0625
    assert abs(ema_val - 13.0625) < 1e-4

def test_calculate_atr():
    highs = np.array([10.0, 12.0, 15.0, 14.0, 16.0])
    lows = np.array([9.0, 11.0, 13.0, 12.0, 14.0])
    closes = np.array([9.5, 11.5, 14.0, 13.0, 15.0])
    # TR_1: max(12-11, |12-9.5|, |11-9.5|) = max(1, 2.5, 1.5) = 2.5
    # TR_2: max(15-13, |15-11.5|, |13-11.5|) = max(2, 3.5, 1.5) = 3.5
    # TR_3: max(14-12, |14-14|, |12-14|) = max(2, 0, 2) = 2.0
    # TR_4: max(16-14, |16-13|, |14-13|) = max(2, 3, 1) = 3.0
    # window = 3
    atr_val = calculate_atr(highs, lows, closes, 3)
    assert atr_val is not None
    # Average of last 3 TRs: (3.5 + 2.0 + 3.0) / 3 = 8.5 / 3 = 2.8333
    assert abs(atr_val - 2.8333) < 1e-3

def test_calculate_all_indicators():
    # 30개의 캔들 생성
    candles = []
    for i in range(30):
        candles.append(make_candle(
            close=100.0 + i,
            high=102.0 + i,
            low=98.0 + i,
            open_val=99.0 + i,
            timestamp=1000 + i * 60
        ))
    
    df = IndicatorCalculator.calculate_all_indicators(candles)
    assert not df.empty
    # 신규 지표 필드 존재 여부 확인
    assert 'ema' in df.columns
    assert 'macd_line' in df.columns
    assert 'macd_signal' in df.columns
    assert 'macd_hist' in df.columns
    assert 'atr' in df.columns
    
    # 마지막 지표 값들이 유효한 숫자인지 확인
    assert not pd.isna(df['ema'].iloc[-1])
    assert not pd.isna(df['macd_line'].iloc[-1])
    assert not pd.isna(df['macd_signal'].iloc[-1])
    assert not pd.isna(df['macd_hist'].iloc[-1])
    assert not pd.isna(df['atr'].iloc[-1])
