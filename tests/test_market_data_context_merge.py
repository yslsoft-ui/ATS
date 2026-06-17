import unittest
from src.engine.candles import Candle
from src.engine.market_data_context import MarketDataContext

class TestMarketDataContextMerge(unittest.TestCase):
    def test_merge_backfilled_candles(self):
        # 1. MarketDataContext 생성 (max_len = 5)
        context = MarketDataContext(exchange_id="upbit", symbol="BTC", interval=60, max_len=5)

        # 2. 기존 캔들 추가 (타임스탬프: 100, 200, 300)
        c1 = Candle("upbit", "BTC", 60, 100, 10.0, 11.0, 9.0, 10.5, 1.0, is_closed=True)
        c2 = Candle("upbit", "BTC", 60, 200, 10.5, 12.0, 10.0, 11.0, 2.0, is_closed=True)
        c3 = Candle("upbit", "BTC", 60, 300, 11.0, 11.5, 10.5, 10.8, 1.5, is_closed=False) # 미마감

        context.add_candle(c1)
        context.add_candle(c2)
        context.add_candle(c3)

        # 지표 강제 캐싱 시뮬레이션
        context.indicator_cache["sma_window=2"] = 10.75

        # 3. 백필 캔들 병합 대상 생성 (타임스탬프: 150(과거 추가), 300(동일 타임스탬프 덮어쓰기))
        bc1 = Candle("upbit", "BTC", 60, 150, 10.2, 10.8, 10.1, 10.3, 1.2, is_closed=True)
        bc2 = Candle("upbit", "BTC", 60, 300, 11.0, 11.5, 10.5, 10.9, 1.5, is_closed=True) # 마감본으로 교체

        # 4. 병합 실행
        context.merge_backfilled_candles([bc1, bc2])

        # 5. 검증
        # - 정렬 여부 검증 (100 -> 150 -> 200 -> 300)
        self.assertEqual(len(context.candles), 4)
        self.assertEqual(context.candles[0].timestamp, 100)
        self.assertEqual(context.candles[1].timestamp, 150)
        self.assertEqual(context.candles[2].timestamp, 200)
        self.assertEqual(context.candles[3].timestamp, 300)

        # - 중복 제거 및 마감 캔들 교체 검증
        self.assertTrue(context.candles[3].is_closed)
        self.assertEqual(context.candles[3].close, 10.9) # bc2 값인 10.9로 덮어씌워져야 함

        # - 지표 캐시 초기화 검증
        self.assertNotIn("sma_window=2", context.indicator_cache)

        # 6. max_len 초과 캔들 슬라이싱 검증
        # 5개 제한 상태에서 총 6개가 되도록 2개 캔들 추가 백필
        bc3 = Candle("upbit", "BTC", 60, 50, 9.5, 10.0, 9.0, 9.8, 0.8, is_closed=True)
        bc4 = Candle("upbit", "BTC", 60, 400, 12.0, 12.5, 11.8, 12.2, 2.5, is_closed=True)
        context.merge_backfilled_candles([bc3, bc4])

        # max_len = 5 이므로 50, 100, 150, 200, 300, 400 중 가장 오래된 50은 유실되고 5개만 남아야 함
        self.assertEqual(len(context.candles), 5)
        self.assertEqual(context.candles[0].timestamp, 100)
        self.assertEqual(context.candles[-1].timestamp, 400)

if __name__ == "__main__":
    unittest.main()
