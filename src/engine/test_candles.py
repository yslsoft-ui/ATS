import unittest
from src.engine.candles import CandleGenerator

class TestCandleGenerator(unittest.TestCase):
    def test_candle_generation(self):
        # 1초, 5초 인터벌 설정
        generator = CandleGenerator(intervals=[1, 5])
        symbol = "KRW-BTC"
        
        # T=0ms: 첫 틱
        closed = generator.process_tick(symbol, 100, 1.0, 0)
        self.assertEqual(len(closed), 0)
        
        # T=500ms: 같은 1초 캔들 내 틱
        closed = generator.process_tick(symbol, 110, 1.0, 500)
        self.assertEqual(len(closed), 0)
        
        # T=1200ms: 1초 경과 -> 1초 캔들 완성
        closed = generator.process_tick(symbol, 105, 1.0, 1200)
        self.assertEqual(len(closed), 1)
        self.assertEqual(closed[0].interval, 1)
        self.assertEqual(closed[0].open, 100)
        self.assertEqual(closed[0].high, 110)
        self.assertEqual(closed[0].low, 100)
        self.assertEqual(closed[0].close, 110) # 1200ms 틱 발생 전의 캔들이므로
        self.assertEqual(closed[0].volume, 2.0)
        
        # T=5500ms: 5초 경과 -> 5초 캔들 완성 (1초 캔들도 여러 개 완성되어야 함)
        closed = generator.process_tick(symbol, 120, 1.0, 5500)
        # 1.2s 틱 이후 5.5s 틱이 들어오면 1s, 2s, 3s, 4s, 5s 관련 캔들이 닫힘
        # 여기서는 중간 틱이 없으므로 직전 상태의 1s 캔들과 5s 캔들이 닫힘
        self.assertTrue(any(c.interval == 1 for c in closed))
        self.assertTrue(any(c.interval == 5 for c in closed))

if __name__ == "__main__":
    unittest.main()
