import asyncio
import os
import pytest
from typing import NamedTuple
from src.database.connection import get_db_conn
from src.database.writer import DatabaseWriter

# 테스트용 임시 DB 경로 설정
TEST_DB_PATH = "test_temp_database.db"

# 캔들 오브젝트를 흉내 내는 Mock 클래스 (결합 최소화)
class MockCandle:
    def __init__(self, exchange, symbol, interval, timestamp, open_val, high, low, close, volume):
        self.exchange = exchange
        self.symbol = symbol
        self.interval = interval
        self.timestamp = timestamp
        self.open = open_val
        self.high = high
        self.low = low
        self.close = close
        self.volume = volume

async def setup_test_db(db_path: str):
    """테스트용 임시 SQLite 테이블 구조를 세팅합니다."""
    # 기존 DB 잔재 완전 제거
    for ext in ["", "-wal", "-shm"]:
        path = db_path + ext
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass

    async with get_db_conn(db_path) as db:
        # 1. trades 테이블 생성
        await db.execute('''
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                exchange TEXT,
                market TEXT,
                symbol TEXT,
                trade_price REAL,
                trade_volume REAL,
                ask_bid TEXT,
                trade_timestamp INTEGER,
                sequential_id INTEGER,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        # 2. candles 테이블 생성
        await db.execute('''
            CREATE TABLE IF NOT EXISTS candles (
                exchange TEXT,
                symbol TEXT,
                interval INTEGER,
                timestamp INTEGER,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume REAL,
                PRIMARY KEY (exchange, symbol, interval, timestamp)
            )
        ''')
        await db.commit()

def teardown_test_db(db_path: str):
    """테스트 완료 후 임시 파일을 청소합니다."""
    for ext in ["", "-wal", "-shm"]:
        path = db_path + ext
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass

@pytest.mark.asyncio
async def test_database_writer_flow():
    """DatabaseWriter의 비동기 라이프사이클과 최종 플러시 기능의 무결성을 검증합니다."""
    # 1. 테스트 DB 환경 초기화
    await setup_test_db(TEST_DB_PATH)
    
    # 2. DatabaseWriter 인스턴스 기동
    writer = DatabaseWriter(db_path=TEST_DB_PATH)
    await writer.start()
    
    try:
        # 3. 모의 틱(Trades) 데이터 5개 삽입
        for i in range(5):
            writer.enqueue_tick({
                "exchange": "upbit",
                "code": "BTC",
                "trade_price": 100000.0 + i,
                "trade_volume": 0.1 * (i + 1),
                "ask_bid": "BID" if i % 2 == 0 else "ASK",
                "trade_timestamp": 1716000000000 + i * 1000
            })
            
        # 4. 모의 캔들(Candles) 데이터 3개 삽입
        for i in range(3):
            writer.enqueue_candle(MockCandle(
                exchange="kis",
                symbol="005930",
                interval=60,
                timestamp=1716000000 + i * 60,
                open_val=75000.0,
                high=75500.0,
                low=74900.0,
                close=75200.0 + i,
                volume=1000.0 + i * 10
            ))
            
        # 데이터가 큐에 정상적으로 담겨서 대기 중인지 체크
        assert writer.db_queue.qsize() == 5
        assert writer.candle_queue.qsize() == 3
        
        # 5. DB Writer 중단 호출 -> 🚨 이 시점에 강제 최종 플러시가 트리거되어야 함!
        await writer.stop()
        
        # 큐가 탈탈 털려 비어 있는지 검증
        assert writer.db_queue.qsize() == 0
        assert writer.candle_queue.qsize() == 0
        
        # 6. DB에서 실제 저장 레코드 수 검증
        async with get_db_conn(TEST_DB_PATH) as db:
            # 틱(trades) 개수 검증
            async with db.execute("SELECT COUNT(*) FROM trades") as cursor:
                trade_count = (await cursor.fetchone())[0]
                assert trade_count == 5

            # 상세 값 검증 (가장 첫 번째 틱 데이터)
            async with db.execute("SELECT trade_price, symbol FROM trades ORDER BY id ASC LIMIT 1") as cursor:
                row = await cursor.fetchone()
                assert row['trade_price'] == 100000.0
                assert row['symbol'] == "BTC"

            # 캔들(candles) 개수 검증
            async with db.execute("SELECT COUNT(*) FROM candles") as cursor:
                candle_count = (await cursor.fetchone())[0]
                assert candle_count == 3
                
    finally:
        # 7. 종료 처리 및 청소
        await writer.stop()
        teardown_test_db(TEST_DB_PATH)
