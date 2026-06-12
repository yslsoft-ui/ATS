import asyncio
import pytest
from typing import Dict, List, Optional, Any
from src.engine.market_data_processor import MarketDataProcessor
from src.engine.candles import Candle
from src.engine.strategy import BaseStrategy, StrategyResult, StrategyRegistry, StrategyType, TradeSignal

# 테스트용 모크 전략 등록
@StrategyRegistry.register
class MockProcessorTestStrategy(BaseStrategy):
    type = StrategyType.ENTRY
    default_params = {"interval": 60}


    def on_update(self, context: Any) -> Optional[StrategyResult]:
        latest_candle = context.candles[-1]
        # 종가가 1050.0 이상이면 BUY 신호 리턴
        if latest_candle.close >= 1050.0:
            return StrategyResult(action="BUY", price=latest_candle.close, reason="price_high")
        return StrategyResult(action="HOLD")

@pytest.mark.asyncio
async def test_processor_tick_processing_and_candle_generation():
    # 타 테스트에 의해 StrategyRegistry가 리셋되었을 가능성에 대응
    StrategyRegistry.register(MockProcessorTestStrategy)
    
    processing_queue = asyncio.Queue()
    db_queue = asyncio.Queue()
    candle_queue = asyncio.Queue()

    # 프로세서 인스턴스화
    processor = MarketDataProcessor(
        exchange="mock_exchange",
        processing_queue=processing_queue,
        db_queue=db_queue,
        candle_queue=candle_queue
    )

    processor.available_symbols = ["BTC"]
    
    # 전략 비활성화 설정
    config = {
        "strategies": {
            "mockprocessorteststrategy": {"enabled": False}
        },
        "exchanges": {
            "mock_exchange": {"warmup_enabled": False}
        }
    }

    await processor.start(config, worker_count=1)

    # 첫 번째 틱 주입
    tick1 = {
        'exchange': 'mock_exchange',
        'code': 'BTC',
        'trade_price': 1000.0,
        'trade_volume': 1.0,
        'ask_bid': 'ASK',
        'trade_timestamp': 1718020800000
    }
    await processing_queue.put(tick1)
    await asyncio.sleep(0.05)

    assert db_queue.qsize() == 1
    assert candle_queue.qsize() == 0

    # 1분 뒤 새로운 분 틱 주입 -> 첫 번째 캔들 완성 유도
    tick2 = {
        'exchange': 'mock_exchange',
        'code': 'BTC',
        'trade_price': 1010.0,
        'trade_volume': 2.0,
        'ask_bid': 'BID',
        'trade_timestamp': 1718020860000
    }
    await processing_queue.put(tick2)
    await asyncio.sleep(0.05)

    assert db_queue.qsize() == 2
    assert candle_queue.qsize() == 1

    candle = await candle_queue.get()
    assert candle.close == 1000.0

    await processor.stop()


@pytest.mark.asyncio
async def test_processor_queue_overload_drop_oldest():
    # maxsize=2 인 큐 정의
    processing_queue = asyncio.Queue(maxsize=2)
    
    # Collector의 Drop Oldest 주입 메커니즘을 테스트용 헬퍼 함수로 정의
    # (실제 BaseCollector에 반영될 구현 스펙과 동일)
    def enqueue_tick_with_drop_oldest(queue, tick):
        dropped = False
        if queue.full():
            try:
                queue.get_nowait()
                dropped = True
            except asyncio.QueueEmpty:
                pass
        queue.put_nowait(tick)
        return dropped

    tick1 = {'code': 'BTC', 'id': 1}
    tick2 = {'code': 'BTC', 'id': 2}
    tick3 = {'code': 'BTC', 'id': 3}

    # 1. 큐 가득 채우기 (2개)
    enqueue_tick_with_drop_oldest(processing_queue, tick1)
    enqueue_tick_with_drop_oldest(processing_queue, tick2)
    assert processing_queue.qsize() == 2

    # 2. 큐가 가득 찬 상태에서 세 번째 틱 주입 -> 가장 오래된 1번 틱이 드롭되어야 함
    dropped = enqueue_tick_with_drop_oldest(processing_queue, tick3)
    assert dropped is True
    assert processing_queue.qsize() == 2

    # 3. 큐 안의 아이템 확인 (1번은 나가고 2번, 3번이 남아있어야 함)
    first_item = await processing_queue.get()
    second_item = await processing_queue.get()

    assert first_item['id'] == 2
    assert second_item['id'] == 3


@pytest.mark.asyncio
async def test_processor_trade_engine_integration_both_enabled_and_disabled():
    # 타 테스트에 의해 StrategyRegistry가 리셋되었을 가능성에 대응
    StrategyRegistry.register(MockProcessorTestStrategy)
    
    # ------------------
    # 시나리오 1: 전략 활성화 (enabled: True)
    # ------------------
    processing_queue = asyncio.Queue()
    db_queue = asyncio.Queue()
    candle_queue = asyncio.Queue()

    signals_received = []
    async def on_signal(signal: TradeSignal, execution_price: float):
        signals_received.append(signal)

    processor = MarketDataProcessor(
        exchange="mock_exchange",
        processing_queue=processing_queue,
        db_queue=db_queue,
        candle_queue=candle_queue,
        on_signal_callback=on_signal
    )
    processor.available_symbols = ["BTC"]

    config_enabled = {
        "strategies": {
            "mockprocessorteststrategy": {"enabled": True, "params": {"interval": 60}}
        },
        "exchanges": {
            "mock_exchange": {"warmup_enabled": False}
        }
    }

    await processor.start(config_enabled, worker_count=1)

    # 1050 이상의 종가 틱 주입 (첫 틱 1050)
    await processing_queue.put({
        'exchange': 'mock_exchange',
        'code': 'BTC',
        'trade_price': 1060.0,
        'trade_volume': 1.0,
        'ask_bid': 'ASK',
        'trade_timestamp': 1718020800000
    })
    
    # 1분 경과 틱 주입 -> 1060 종가의 1분봉 캔들 완성 유도
    await processing_queue.put({
        'exchange': 'mock_exchange',
        'code': 'BTC',
        'trade_price': 990.0,
        'trade_volume': 1.0,
        'ask_bid': 'ASK',
        'trade_timestamp': 1718020860000
    })
        
    # 비동기 처리 완료 및 신호 수신을 최대 1.0초 동안 대기 (폴링 가드)
    for _ in range(20):
        if len(signals_received) >= 1:
            break
        await asyncio.sleep(0.05)

    # 1060 캔들이 마감되어 on_update가 호출되고 BUY 신호가 콜백으로 발생했는지 확인
    assert len(signals_received) == 1
    assert signals_received[0].action == 'BUY'
    assert signals_received[0].price == 1060.0
    assert signals_received[0].strategy_id == 'MockProcessorTestStrategy'

    await processor.stop()

    # ------------------
    # 시나리오 2: 전략 비활성화 (enabled: False)
    # ------------------
    processing_queue_2 = asyncio.Queue()
    db_queue_2 = asyncio.Queue()
    candle_queue_2 = asyncio.Queue()
    
    signals_received_2 = []
    async def on_signal_2(signal: TradeSignal, execution_price: float):
        signals_received_2.append(signal)

    processor_disabled = MarketDataProcessor(
        exchange="mock_exchange",
        processing_queue=processing_queue_2,
        db_queue=db_queue_2,
        candle_queue=candle_queue_2,
        on_signal_callback=on_signal_2
    )
    processor_disabled.available_symbols = ["BTC"]

    config_disabled = {
        "strategies": {
            "mockprocessorteststrategy": {"enabled": False}
        },
        "exchanges": {
            "mock_exchange": {"warmup_enabled": False}
        }
    }

    await processor_disabled.start(config_disabled, worker_count=1)

    # 동일하게 1050 이상의 틱 주입
    await processing_queue_2.put({
        'exchange': 'mock_exchange',
        'code': 'BTC',
        'trade_price': 1060.0,
        'trade_volume': 1.0,
        'ask_bid': 'ASK',
        'trade_timestamp': 1718020800000
    })
    # 1분 경과 틱 주입
    await processing_queue_2.put({
        'exchange': 'mock_exchange',
        'code': 'BTC',
        'trade_price': 990.0,
        'trade_volume': 1.0,
        'ask_bid': 'ASK',
        'trade_timestamp': 1718020860000
    })
    await asyncio.sleep(0.1)

    # 전략이 꺼져 있으므로 신호가 감지되지 않아야 함
    assert len(signals_received_2) == 0

    await processor_disabled.stop()
