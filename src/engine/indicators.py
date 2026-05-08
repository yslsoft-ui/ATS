import collections
import numpy as np

class IndicatorCalculator:
    """
    틱(Tick) 단위로 유입되는 실시간 데이터를 효율적으로 처리하기 위해
    슬라이딩 윈도우(Sliding Window) 방식으로 지표를 계산합니다.
    """
    def __init__(self, window_size: int = 20):
        self.window_size = window_size
        self.prices = collections.deque(maxlen=window_size + 100) # 더 긴 역사를 위해 넉넉히 확보
        
    def update(self, price: float) -> dict:
        """
        새로운 틱 가격을 업데이트하고 현재 계산된 지표를 반환합니다.
        
        :param price: 현재 체결가
        :return: {'sma': float, 'rsi': float, 'macd': dict, 'bb': dict}
        """
        self.prices.append(price)
        prices_arr = np.array(self.prices)
        
        # 데이터가 충분히 쌓이기 전에는 초기값(None) 반환
        if len(self.prices) < self.window_size:
            return {'sma': None, 'rsi': None, 'macd': None, 'bb': None}
            
        # 1. SMA 계산
        sma = np.mean(prices_arr[-self.window_size:])
        
        # 2. RSI 계산
        diffs = np.diff(prices_arr[-self.window_size-1:])
        gains = diffs[diffs > 0]
        losses = -diffs[diffs < 0]
        avg_gain = np.mean(gains) if len(gains) > 0 else 0
        avg_loss = np.mean(losses) if len(losses) > 0 else 0
        
        if avg_loss == 0:
            rsi = 100.0 if avg_gain > 0 else 50.0
        else:
            rs = avg_gain / avg_loss
            rsi = 100.0 - (100.0 / (1 + rs))
            
        # 3. Bollinger Bands (20, 2)
        std = np.std(prices_arr[-self.window_size:])
        bb_upper = sma + (2 * std)
        bb_lower = sma - (2 * std)
        
        # 4. MACD 계산
        def get_ema(data, period):
            if len(data) == 0: return 0
            alpha = 2 / (period + 1)
            ema = data[0]
            for p in data[1:]:
                ema = (p * alpha) + (ema * (1 - alpha))
            return ema

        ema12 = get_ema(prices_arr[-12:], 12) if len(prices_arr) >= 12 else sma
        ema26 = get_ema(prices_arr[-26:], 26) if len(prices_arr) >= 26 else sma
        macd_line = ema12 - ema26
        
        # Signal 선 계산을 위해 MACD 이력 관리
        if not hasattr(self, 'macd_history'):
            self.macd_history = collections.deque(maxlen=100)
        self.macd_history.append(macd_line)
        
        macd_signal = get_ema(list(self.macd_history)[-9:], 9) if len(self.macd_history) >= 9 else macd_line
        macd_hist = macd_line - macd_signal
        
        return {
            'sma': round(sma, 4),
            'rsi': round(rsi, 4),
            'bb': {
                'upper': round(bb_upper, 4),
                'middle': round(sma, 4),
                'lower': round(bb_lower, 4)
            },
            'macd': {
                'line': round(macd_line, 4),
                'signal': round(macd_signal, 4),
                'hist': round(macd_hist, 4)
            }
        }

    @staticmethod
    def calculate_all_indicators(candles: list):
        """캔들 리스트를 받아 모든 기술 지표가 포함된 DataFrame을 반환합니다."""
        import pandas as pd
        if not candles:
            return pd.DataFrame()
            
        # 캔들 리스트를 데이터프레임으로 변환
        df = pd.DataFrame([vars(c) if hasattr(c, '__dict__') else c for c in candles])
        
        # 1. SMA (20)
        df['sma'] = df['close'].rolling(window=20).mean()
        
        # 2. Bollinger Bands
        std = df['close'].rolling(window=20).std()
        df['bb_upper'] = df['sma'] + (2 * std)
        df['bb_lower'] = df['sma'] - (2 * std)
        
        # 3. RSI (14)
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        df['rsi'] = 100 - (100 / (1 + rs))
        
        return df
