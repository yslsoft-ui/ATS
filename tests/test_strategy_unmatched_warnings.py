import pytest
import asyncio
import logging
from src.services.strategy_service import StrategyService
from tests.test_command_dispatcher import FakeConfigManager
from src.database.repository import InMemoryTradingRepository
from src.engine.portfolio import Portfolio

def setup_module(module):
    logging.getLogger("src").propagate = True

class MockSubscriber:
    def __init__(self, data_list):
        self.data_list = data_list
        self.index = 0
    async def receive(self):
        if self.index < len(self.data_list):
            data = self.data_list[self.index]
            self.index += 1
            return "market_data", data
        else:
            raise asyncio.CancelledError()
    def close(self):
        pass

@pytest.mark.asyncio
async def test_strategy_service_no_warning_when_engines_empty(caplog):
    # Setup StrategyService
    config = FakeConfigManager({
        "system": {
            "db_path": ":memory:",
            "strategies_dir": "src/engine/strategies"
        }
    })
    repo = InMemoryTradingRepository()
    service = StrategyService(config_manager=config, event_bus=None, market_data_repository=repo)
    
    # Ensure trade_engines is empty
    assert len(service.trade_engines) == 0
    
    tick_data = {
        'type': 'tick',
        'exchange_id': 'bithumb',
        'code': 'META',
        'trade_price': 100.0,
        'trade_volume': 1.0,
        'ask_bid': 'BUY',
        'trade_timestamp': 123456789
    }
    
    # Mock the subscriber to deliver the tick data
    service.market_sub = MockSubscriber([tick_data])
    
    with caplog.at_level(logging.WARNING):
        await service._market_data_loop()
        
    # No warning should be captured because trade_engines is empty
    warnings = [r.message for r in caplog.records if "활성화된 전략 엔진에 매칭되지 않는 키 감지" in r.message]
    assert len(warnings) == 0


@pytest.mark.asyncio
async def test_strategy_service_warning_when_engines_exist_and_throttled(caplog):
    config = FakeConfigManager({
        "system": {
            "db_path": ":memory:",
            "strategies_dir": "src/engine/strategies"
        }
    })
    repo = InMemoryTradingRepository()
    service = StrategyService(config_manager=config, event_bus=None, market_data_repository=repo)
    
    # Populate trade_engines with a dummy engine to make it non-empty
    service.trade_engines = {"bithumb:BTC": object()}
    
    tick_data = {
        'type': 'tick',
        'exchange_id': 'bithumb',
        'code': 'META',
        'trade_price': 100.0,
        'trade_volume': 1.0,
        'ask_bid': 'BUY',
        'trade_timestamp': 123456789
    }
    
    # Send the tick once
    service.market_sub = MockSubscriber([tick_data])
    with caplog.at_level(logging.WARNING):
        await service._market_data_loop()
        
    # Warning should be captured
    warnings = [r.message for r in caplog.records if "활성화된 전략 엔진에 매칭되지 않는 키 감지" in r.message]
    assert len(warnings) == 1
    assert "bithumb:META" in warnings[0]
    
    caplog.clear()
    
    # Send the tick again
    service.market_sub = MockSubscriber([tick_data])
    with caplog.at_level(logging.WARNING):
        await service._market_data_loop()
        
    # Warning should NOT be captured again due to throttling
    warnings = [r.message for r in caplog.records if "활성화된 전략 엔진에 매칭되지 않는 키 감지" in r.message]
    assert len(warnings) == 0


@pytest.mark.asyncio
async def test_strategy_service_reload_engines_clears_unmatched_keys(caplog):
    config = FakeConfigManager({
        "system": {
            "db_path": ":memory:",
            "strategies_dir": "src/engine/strategies"
        }
    })
    repo = InMemoryTradingRepository()
    service = StrategyService(config_manager=config, event_bus=None, market_data_repository=repo)
    
    # Populate trade_engines
    service.trade_engines = {"bithumb:BTC": object()}
    
    tick_data = {
        'type': 'tick',
        'exchange_id': 'bithumb',
        'code': 'META',
        'trade_price': 100.0,
        'trade_volume': 1.0,
        'ask_bid': 'BUY',
        'trade_timestamp': 123456789
    }
    
    # Send tick to populate unmatched_keys
    service.market_sub = MockSubscriber([tick_data])
    await service._market_data_loop()
    assert "bithumb:META" in service._unmatched_keys
    
    # Reload engines (with None portfolio to keep it simple and trigger clear)
    await service.reload_trade_engines(None)
    
    # Check that unmatched_keys is cleared
    assert len(service._unmatched_keys) == 0
    
    # Send tick again
    caplog.clear()
    service.market_sub = MockSubscriber([tick_data])
    with caplog.at_level(logging.WARNING):
        await service._market_data_loop()
        
    # Warning should be captured again because unmatched_keys was cleared
    warnings = [r.message for r in caplog.records if "활성화된 전략 엔진에 매칭되지 않는 키 감지" in r.message]
    assert len(warnings) == 1
