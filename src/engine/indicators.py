import collections
from typing import Optional, Dict, Any
import numpy as np

def calculate_sma(prices: np.ndarray, window: int) -> Optional[float]:
    """단순 이동평균(SMA)을 계산합니다."""
    if len(prices) < window:
        return None
    return float(np.mean(prices[-window:]))

def calculate_rsi(prices: np.ndarray, window: int) -> Optional[float]:
    """상대강도지수(RSI)를 계산합니다."""
    if len(prices) < window:
        return None
    diffs = np.diff(prices[-window-1:])
    gains = diffs[diffs > 0]
    losses = -diffs[diffs < 0]
    avg_gain = np.mean(gains) if len(gains) > 0 else 0.0
    avg_loss = np.mean(losses) if len(losses) > 0 else 0.0

    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return float(100.0 - (100.0 / (1 + rs)))

def calculate_bollinger_bands(prices: np.ndarray, window: int, num_std: float = 2.0) -> dict:
    """볼린저 밴드(Bollinger Bands)를 계산합니다."""
    if len(prices) < window:
        return {'upper': None, 'middle': None, 'lower': None}
    sma = np.mean(prices[-window:])
    std = np.std(prices[-window:])
    bb_upper = sma + (num_std * std)
    bb_lower = sma - (num_std * std)
    return {
        'upper': round(float(bb_upper), 4),
        'middle': round(float(sma), 4),
        'lower': round(float(bb_lower), 4)
    }

def calculate_macd(prices: np.ndarray, fast_period: int = 12, slow_period: int = 26, signal_period: int = 9) -> dict:
    """이동평균 수렴확산(MACD)을 계산합니다."""
    if len(prices) < max(fast_period, slow_period):
        return {'line': None, 'signal': None, 'hist': None}

    def get_ema(data, period):
        if len(data) == 0: return 0
        alpha = 2 / (period + 1)
        ema = data[0]
        for p in data[1:]:
            ema = (p * alpha) + (ema * (1 - alpha))
        return ema

    macd_history = []
    # 가격 히스토리를 훑어 macd_line의 시계열을 복원합니다. (Signal EMA 계산용)
    start_idx = max(0, len(prices) - 100)
    for i in range(start_idx, len(prices)):
        sub_prices = prices[:i+1]
        if len(sub_prices) < slow_period:
            continue
        sub_ema12 = get_ema(sub_prices[-fast_period:], fast_period)
        sub_ema26 = get_ema(sub_prices[-slow_period:], slow_period)
        sub_macd_line = sub_ema12 - sub_ema26
        macd_history.append(sub_macd_line)

    macd_line = macd_history[-1] if macd_history else (get_ema(prices[-fast_period:], fast_period) - get_ema(prices[-slow_period:], slow_period))
    
    if len(macd_history) < signal_period:
        macd_signal = macd_line
    else:
        macd_signal = get_ema(macd_history[-signal_period:], signal_period)

    macd_hist = macd_line - macd_signal

    return {
        'line': round(float(macd_line), 4),
        'signal': round(float(macd_signal), 4),
        'hist': round(float(macd_hist), 4)
    }

def calculate_ema(prices: np.ndarray, window: int) -> Optional[float]:
    """지수 이동평균(EMA)을 계산합니다."""
    if len(prices) < window:
        return None
    alpha = 2 / (window + 1)
    ema = prices[0]
    for p in prices[1:]:
        ema = (p * alpha) + (ema * (1 - alpha))
    return float(ema)

def calculate_atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, window: int) -> Optional[float]:
    """평균 실거래 범위(ATR)를 계산합니다."""
    if len(highs) < window + 1:
        return None
    
    tr_values = []
    for i in range(1, len(highs)):
        h = highs[i]
        l = lows[i]
        prev_c = closes[i - 1]
        tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
        tr_values.append(tr)
        
    if len(tr_values) < window:
        return None
        
    return float(np.mean(tr_values[-window:]))

class IndicatorCalculator:
    """
    틱(Tick) 단위로 유입되는 실시간 데이터를 효율적으로 처리하기 위해
    슬라이딩 윈도우(Sliding Window) 방식으로 지표를 계산합니다. (하위 호환용)
    """
    def __init__(self, window_size: int = 20):
        self.window_size = window_size
        self.prices = collections.deque(maxlen=window_size + 100)

    def update(self, price: float) -> dict:
        """
        새로운 틱 가격을 업데이트하고 현재 계산된 지표를 반환합니다.
        """
        self.prices.append(price)
        prices_arr = np.array(self.prices)

        if len(self.prices) < self.window_size:
            return {'sma': None, 'rsi': None, 'macd': None, 'bb': None, 'ema': None}

        sma = calculate_sma(prices_arr, self.window_size)
        rsi = calculate_rsi(prices_arr, self.window_size)
        bb = calculate_bollinger_bands(prices_arr, self.window_size)
        macd = calculate_macd(prices_arr)
        ema = calculate_ema(prices_arr, self.window_size)

        return {
            'sma': round(sma, 4) if sma is not None else None,
            'rsi': round(rsi, 4) if rsi is not None else None,
            'bb': bb if bb['upper'] is not None else None,
            'macd': macd if macd['line'] is not None else None,
            'ema': round(ema, 4) if ema is not None else None
        }

    @staticmethod
    def calculate_all_indicators(candles: list):
        """캔들 리스트를 받아 모든 기술 지표가 포함된 DataFrame을 반환합니다."""
        import pandas as pd
        if not candles:
            return pd.DataFrame()

        df = pd.DataFrame([vars(c) if hasattr(c, '__dict__') else c for c in candles])

        # 1. SMA (20) 및 Bollinger Bands (20, 2)
        df['sma'] = df['close'].rolling(window=20).mean()
        std = df['close'].rolling(window=20).std()
        df['bb_upper'] = df['sma'] + (2 * std)
        df['bb_lower'] = df['sma'] - (2 * std)

        # 2. RSI (14)
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        df['rsi'] = 100 - (100 / (1 + rs))

        # 3. EMA (20)
        df['ema'] = df['close'].ewm(span=20, adjust=False).mean()

        # 4. MACD (12, 26, 9)
        ema12 = df['close'].ewm(span=12, adjust=False).mean()
        ema26 = df['close'].ewm(span=26, adjust=False).mean()
        df['macd_line'] = ema12 - ema26
        df['macd_signal'] = df['macd_line'].ewm(span=9, adjust=False).mean()
        df['macd_hist'] = df['macd_line'] - df['macd_signal']

        # 5. ATR (14)
        high_low = df['high'] - df['low']
        high_prev_close = (df['high'] - df['close'].shift(1)).abs()
        low_prev_close = (df['low'] - df['close'].shift(1)).abs()
        tr = pd.concat([high_low, high_prev_close, low_prev_close], axis=1).max(axis=1)
        df['atr'] = tr.rolling(window=14).mean()

        return df
