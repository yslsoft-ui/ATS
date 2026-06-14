import asyncio
import pytest
from typing import Dict, List, Optional, Any
from src.engine.collector_base import BaseCollector, ConnectionMetadata
from src.engine.market_data_processor import MarketDataProcessor
from src.engine.candles import Candle

class MockCollector(BaseCollector):
    @property
    def exchange_id(self) -> str:
        return "mock_exchange"

    def get_connection_metadata(self, config: Dict[str, Any]) -> ConnectionMetadata:
        return {
            "operating_hours": "24시간 (연중무휴)",
            "websocket_url": "ws://mock",
            "api_url": "http://mock"
        }

    async def _fetch_symbols(self, config: Dict[str, Any]) -> List[str]:
        return ["BTC"]

    async def _fetch_historical_candles(self, symbol: str, start_time: int, end_time: int) -> List[Candle]:
        return []

    def _get_websocket_url(self, config: Dict[str, Any]) -> str:
        return "ws://mock"

    async def _subscribe(self, ws, config: Dict[str, Any]):
        pass

    def _parse_message(self, msg) -> Optional[Dict]:
        return msg

@pytest.mark.asyncio
async def test_legacy_collector_tick_processing_and_candle_generation():
    # 1. 테스트용 큐 정의
    processing_queue = asyncio.Queue(maxsize=5000)
    db_queue = asyncio.Queue()
    candle_queue = asyncio.Queue()

    # 2. MockCollector 및 MarketDataProcessor 인스턴스화
    collector = MockCollector(
        processing_queue=processing_queue,
        db_queue=db_queue,
        candle_queue=candle_queue
    )
    
    collector.available_symbols = ["BTC"]
    collector.is_running = True

    processor = MarketDataProcessor(
        exchange_id="mock_exchange",
        processing_queue=processing_queue,
        db_queue=db_queue,
        candle_queue=candle_queue
    )
    processor.available_symbols = ["BTC"]

    config = {
        "strategies": {},
        "exchanges": {
            "mock_exchange": {"warmup_enabled": False}
        }
    }

    # 3. 데이터 가공 프로세서 구동
    await processor.start(config, worker_count=1)

    # 4. 첫 번째 틱 주입
    tick1 = {
        'exchange_id': 'mock_exchange',
        'code': 'BTC',
        'trade_price': 1000.0,
        'trade_volume': 1.0,
        'ask_bid': 'ASK',
        'trade_timestamp': 1718020800000  # KST 2024-06-10 21:00:00.000 (ms)
    }
    
    await processing_queue.put(tick1)
    await asyncio.sleep(0.05) # 프로세서 처리 대기

    # db_queue에 틱이 정상 적재되었는지 검증
    assert db_queue.qsize() == 1
    enqueued_tick = await db_queue.get()
    assert enqueued_tick['trade_price'] == 1000.0
    assert enqueued_tick['code'] == 'BTC'
    
    # 아직 1분이 지나지 않아 캔들은 완성되지 않음
    assert candle_queue.qsize() == 0

    # 5. 두 번째 틱 주입 (1분 경과하여 새로운 분으로 넘어가 캔들 완성을 유도)
    tick2 = {
        'exchange_id': 'mock_exchange',
        'code': 'BTC',
        'trade_price': 1010.0,
        'trade_volume': 2.0,
        'ask_bid': 'BID',
        'trade_timestamp': 1718020860000  # 60초 뒤 (1분 경계)
    }
    
    await processing_queue.put(tick2)
    await asyncio.sleep(0.05)

    # db_queue에 두 번째 틱도 적재
    assert db_queue.qsize() == 1
    enqueued_tick2 = await db_queue.get()
    assert enqueued_tick2['trade_price'] == 1010.0

    # 1분이 지나 새로운 분이 개시되었으므로 첫 번째 분의 캔들이 완성되어 candle_queue에 들어와야 함
    assert candle_queue.qsize() == 1
    completed_candle = await candle_queue.get()
    
    # 캔들 속성 검증
    assert completed_candle.exchange_id == 'mock_exchange'
    assert completed_candle.symbol == 'BTC'
    assert completed_candle.open == 1000.0
    assert completed_candle.high == 1000.0
    assert completed_candle.low == 1000.0
    assert completed_candle.close == 1000.0
    assert completed_candle.volume == 1.0
    assert completed_candle.is_closed is True

    # 6. 자원 정리
    collector.is_running = False
    await processor.stop()
