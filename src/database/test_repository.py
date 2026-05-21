import unittest
import asyncio
from src.database.repository import SqliteMarketDataRepository, InMemoryMarketDataRepository

class TestMarketDataRepository(unittest.IsolatedAsyncioTestCase):
    """
    시장 데이터 저장소 모듈(MarketDataRepository)에 대한 정밀 단위 테스트 클래스입니다.
    """
    async def test_in_memory_repository_candles(self):
        """
        인메모리 저장소의 캔들 적재, 조회 및 보조지표 자동 계산 기능을 테스트합니다.
        """
        repo = InMemoryMarketDataRepository()
        exchange = "upbit"
        symbol = "BTC"
        
        # 25개의 가상 캔들 데이터 생성 및 주입 (SMA 20과 RSI 14를 계산하기에 충분한 개수)
        for i in range(25):
            repo.add_candle(exchange, symbol, {
                'timestamp': 1700000000 + (i * 60),
                'open': 100.0 + i,
                'high': 105.0 + i,
                'low': 95.0 + i,
                'close': 102.0 + i,
                'volume': 1.0 + i
            })
            
        candles = await repo.get_candles(exchange, symbol, interval=60, limit=100)
        
        # 검증
        self.assertEqual(len(candles), 25)
        # 마지막 캔들의 기술 지표(SMA, RSI) 계산 값 검증 (null이 아니고 실수 형태의 값이어야 함)
        last_candle = candles[-1]
        self.assertIsNotNone(last_candle.get('sma'))
        self.assertIsNotNone(last_candle.get('rsi'))
        self.assertIsNotNone(last_candle.get('bb_upper'))
        self.assertTrue(isinstance(last_candle['sma'], float))
        self.assertTrue(isinstance(last_candle['rsi'], float))

    async def test_in_memory_repository_trades(self):
        """
        인메모리 저장소의 최근 틱 데이터 조회 및 정렬 기능을 테스트합니다.
        """
        repo = InMemoryMarketDataRepository()
        exchange = "bithumb"
        symbol = "ETH"
        
        # 3개의 모의 틱 데이터 주입
        repo.add_trade(exchange, symbol, {
            'trade_timestamp': 1000, 'trade_price': 500.0, 'trade_volume': 0.1, 'ask_bid': 'BID'
        })
        repo.add_trade(exchange, symbol, {
            'trade_timestamp': 3000, 'trade_price': 502.0, 'trade_volume': 0.2, 'ask_bid': 'ASK'
        })
        repo.add_trade(exchange, symbol, {
            'trade_timestamp': 2000, 'trade_price': 501.0, 'trade_volume': 0.3, 'ask_bid': 'BID'
        })
        
        trades = await repo.get_recent_trades(exchange, symbol, limit=2)
        
        # 검증: limit 작동 확인 및 최신 시간순(내림차순) 정렬 확인
        self.assertEqual(len(trades), 2)
        self.assertEqual(trades[0]['trade_timestamp'], 3000)
        self.assertEqual(trades[1]['trade_timestamp'], 2000)

    async def test_sqlite_repository_basic_query(self):
        """
        실제 SQLite 데이터베이스 어댑터의 캔들 쿼리 동작 안정성을 테스트합니다.
        """
        repo = SqliteMarketDataRepository()
        
        # 실거래 DB가 존재하는 상황에서 예외 없이 안전하게 데이터를 Fetch해 오는지 구조 검증
        try:
            candles = await repo.get_candles("upbit", "BTC", interval=60, limit=5)
            # 만약 DB에 데이터가 전혀 없더라도 에러 없이 빈 리스트를 조화롭게 리턴해야 함
            self.assertTrue(isinstance(candles, list))
        except Exception as e:
            self.fail(f"SqliteMarketDataRepository.get_candles()가 예외를 발생시켰습니다: {e}")

if __name__ == "__main__":
    unittest.main()
