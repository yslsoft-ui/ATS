import numpy as np
from typing import List, Dict, Any
from src.engine.candles import Candle, CandleGenerator
from src.engine.exceptions import IndicatorNotReady, UnsupportedIndicatorError
from src.engine.indicators import (
    calculate_sma,
    calculate_rsi,
    calculate_bollinger_bands,
    calculate_macd
)

class MarketDataContext:
    """
    특정 자산 및 인터벌 단위로 시세(Candle)를 관리하고,
    기술 지표를 동적으로 연산하여 캐싱하는 데이터 관리 컨텍스트 모듈입니다.
    """
    def __init__(self, exchange_id: str, symbol: str, interval: int, max_len: int = 200):
        self.exchange_id = exchange_id
        self.symbol = symbol
        self.interval = interval
        self.max_len = max_len
        self.candles: List[Candle] = []
        self.indicator_cache: Dict[str, Any] = {}
        self.candle_gen = CandleGenerator(intervals=[interval])

    def add_candle(self, candle: Candle):
        """새로운 완성형 캔들을 추가하고 기존 캐시를 전부 무효화합니다."""
        self.candles.append(candle)
        if len(self.candles) > self.max_len:
            self.candles.pop(0)
        # 캔들이 새로 유입되면 지표 값이 변경되므로 캐시를 지웁니다.
        self.indicator_cache.clear()

    def add_tick(self, tick: Dict[str, Any]) -> List[Candle]:
        """실시간 틱 데이터를 입력받아 캔들을 조립하고, 마감된 캔들이 있으면 내부에 추가합니다."""
        closed_candles = self.candle_gen.process_tick(
            exchange_id=self.exchange_id,
            symbol=self.symbol,
            price=tick['trade_price'],
            volume=tick['trade_volume'],
            side=tick.get('ask_bid') or tick.get('side') or 'BID',
            timestamp_ms=tick['trade_timestamp']
        )
        for candle in closed_candles:
            self.add_candle(candle)
        return closed_candles

    @property
    def prices(self) -> np.ndarray:
        """현재까지 쌓인 캔들의 종가(Close) 배열을 반환합니다."""
        return np.array([c.close for c in self.candles])

    def get_indicator(self, name: str, **kwargs) -> Any:
        """
        요청한 지표를 동적으로 계산하여 반환합니다. 
        동일 캔들 상태(현재 타임스탬프)에서는 한 번 계산된 값을 캐시에서 꺼내 고속 반환합니다.
        """
        offset = kwargs.get('offset', 0)

        # 캐시 키 생성 (예: "sma_window=20_offset=0", "rsi_window=14_offset=1")
        kwargs_str = "_".join(f"{k}={v}" for k, v in sorted(kwargs.items()))
        cache_key = f"{name}_{kwargs_str}" if kwargs_str else name

        if cache_key in self.indicator_cache:
            return self.indicator_cache[cache_key]

        prices = self.prices
        if offset > 0:
            prices = prices[:-offset]

        result = None

        if name == 'sma':
            window = kwargs.get('window', kwargs.get('window_size', 20))
            if len(prices) < window:
                raise IndicatorNotReady(f"Insufficient candles for SMA. Required: {window}, Got: {len(prices)}")
            result = calculate_sma(prices, window)
        elif name == 'rsi':
            window = kwargs.get('window', kwargs.get('window_size', 14))
            required = window + 1
            if len(prices) < required:
                raise IndicatorNotReady(f"Insufficient candles for RSI. Required: {required}, Got: {len(prices)}")
            result = calculate_rsi(prices, window)
        elif name in ['bb', 'bb_upper', 'bb_lower', 'bb_middle']:
            window = kwargs.get('window', kwargs.get('window_size', 20))
            num_std = kwargs.get('num_std', 2.0)
            if len(prices) < window:
                raise IndicatorNotReady(f"Insufficient candles for Bollinger Bands. Required: {window}, Got: {len(prices)}")
            bb_res = calculate_bollinger_bands(prices, window, num_std)
            # 볼린저 밴드 개별 키 캐싱
            for k, v in bb_res.items():
                k_key = f"bb_{k}_{kwargs_str}" if kwargs_str else f"bb_{k}"
                self.indicator_cache[k_key] = v
            # bb 전체 맵 캐싱
            self.indicator_cache[f"bb_{kwargs_str}" if kwargs_str else "bb"] = bb_res
            
            suffix = name.split('_')[1] if '_' in name else None
            result = bb_res[suffix] if suffix else bb_res
        elif name in ['macd', 'macd_line', 'macd_signal', 'macd_hist']:
            fast = kwargs.get('fast_period', 12)
            slow = kwargs.get('slow_period', 26)
            signal = kwargs.get('signal_period', 9)
            required = max(fast, slow)
            if len(prices) < required:
                raise IndicatorNotReady(f"Insufficient candles for MACD. Required: {required}, Got: {len(prices)}")
            macd_res = calculate_macd(prices, fast, slow, signal)
            # MACD 개별 키 캐싱
            for k, v in macd_res.items():
                k_key = f"macd_{k}_{kwargs_str}" if kwargs_str else f"macd_{k}"
                self.indicator_cache[k_key] = v
            # macd 전체 맵 캐싱
            self.indicator_cache[f"macd_{kwargs_str}" if kwargs_str else "macd"] = macd_res
            
            suffix = name.split('_')[1] if '_' in name else None
            result = macd_res[suffix] if suffix else macd_res
        else:
            raise UnsupportedIndicatorError(f"Unsupported indicator: {name}")

        self.indicator_cache[cache_key] = result
        return result
