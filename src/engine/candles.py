from dataclasses import dataclass
from typing import Dict, List, Optional
import time

@dataclass
class Candle:
    symbol: str
    interval: int  # in seconds
    timestamp: int  # start timestamp of the candle
    open: float
    high: float
    low: float
    close: float
    volume: float
    buy_volume: float = 0.0
    sell_volume: float = 0.0
    count: int = 0
    is_closed: bool = False

class CandleGenerator:
    """
    틱 데이터를 실시간으로 수집하여 여러 타임프레임의 캔들을 생성합니다.
    """
    def __init__(self, intervals: List[int]):
        self.intervals = intervals
        # timeframe -> symbol -> current_candle
        self.current_candles: Dict[int, Dict[str, Candle]] = {interval: {} for interval in intervals}

    def process_tick(self, symbol: str, price: float, volume: float, side: str, timestamp_ms: int) -> List[Candle]:
        """
        새로운 틱 데이터를 처리하고, 완성된(Closed) 캔들이 있다면 반환합니다.
        
        :param side: 'BID' (매수) 또는 'ASK' (매도)
        :param timestamp_ms: 밀리초 단위 타임스탬프
        """
        timestamp_s = timestamp_ms // 1000
        closed_candles = []

        for interval in self.intervals:
            # 해당 인터벌의 시작 시간 계산
            candle_start_time = (timestamp_s // interval) * interval
            
            if symbol not in self.current_candles[interval]:
                # 새 캔들 시작
                self.current_candles[interval][symbol] = Candle(
                    symbol=symbol,
                    interval=interval,
                    timestamp=candle_start_time,
                    open=price,
                    high=price,
                    low=price,
                    close=price,
                    volume=volume,
                    buy_volume=volume if side == 'BID' else 0.0,
                    sell_volume=volume if side == 'ASK' else 0.0,
                    count=1
                )
            else:
                current = self.current_candles[interval][symbol]
                
                # 시간이 경과하여 새로운 캔들이 시작되어야 하는 경우
                if candle_start_time > current.timestamp:
                    current.is_closed = True
                    closed_candles.append(current)
                    
                    # 새로운 캔들 생성
                    self.current_candles[interval][symbol] = Candle(
                        symbol=symbol,
                        interval=interval,
                        timestamp=candle_start_time,
                        open=price,
                        high=price,
                        low=price,
                        close=price,
                        volume=volume,
                        buy_volume=volume if side == 'BID' else 0.0,
                        sell_volume=volume if side == 'ASK' else 0.0,
                        count=1
                    )
                else:
                    # 기존 캔들 업데이트
                    current.high = max(current.high, price)
                    current.low = min(current.low, price)
                    current.close = price
                    current.volume += volume
                    if side == 'BID':
                        current.buy_volume += volume
                    else:
                        current.sell_volume += volume
                    current.count += 1
                    
        return closed_candles

    def get_current_candle(self, symbol: str, interval: int) -> Optional[Candle]:
        """현재 진행 중인 캔들을 가져옵니다."""
        return self.current_candles.get(interval, {}).get(symbol)
