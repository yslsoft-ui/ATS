import pytest
import asyncio
import time
from typing import Optional, Dict, List, Any
from unittest.mock import AsyncMock, Mock, patch
from src.services.strategy_service import StrategyService
from tests.test_command_dispatcher import FakeConfigManager
from src.engine.portfolio import Portfolio, Position

# KIS 개장 여부를 검증하기 위한 날짜 픽스처
# 주말은 2026-06-21 (일요일), 평일은 2026-06-24 (수요일)
# check_kis_open_day 가 모킹되어 사용됩니다.

_DEFAULT = object()

class MockMarketDataRepository:
    def __init__(self, closed_candles, query_exception=None, latest_candle_ts_val=_DEFAULT):
        self.closed_candles = closed_candles
        self.query_exception = query_exception
        self.latest_candle_ts_val = latest_candle_ts_val

    async def get_latest_closed_candles_batch(self, keys):
        # 입력받은 keys 중에서 매칭되는 것만 반환
        return {k: self.closed_candles[k] for k in keys if k in self.closed_candles}

    async def get_latest_candle_timestamp(self, exchange_id: str) -> Optional[int]:
        if self.query_exception:
            raise self.query_exception
        if self.latest_candle_ts_val is not _DEFAULT:
            return self.latest_candle_ts_val
        # 기본적으로 closed_candles 중 exchange_id 가 일치하는 것들의 timestamp 중 최댓값 반환
        timestamps = [
            c["timestamp"] for k, c in self.closed_candles.items()
            if k[0].lower() == exchange_id.lower()
        ]
        return max(timestamps) if timestamps else None

@pytest.mark.asyncio
@patch("src.services.strategy_service.PortfolioManager")
async def test_upbit_stale_under_threshold(mock_pm_class):
    """upbit 가격이 threshold(7200초) 이내일 때 정상적으로 hydrate 되는지 검증"""
    config = FakeConfigManager({
        "system": {
            "price_hydrate_stale_threshold_seconds": 3600,
            "price_hydrate_stale_threshold_seconds_upbit": 7200,
            "db_path": ":memory:",
            "strategies_dir": "src/engine/strategies"
        }
    })
    
    current_time = time.time()
    # 5000초 전 타임스탬프 (7200초 이내)
    ts = int(current_time) - 5000
    
    candles = {
        ("upbit", "KRW-BTC"): {
            "close": 50000000.0,
            "timestamp": ts
        }
    }
    
    repo = MockMarketDataRepository(candles)
    event_bus = AsyncMock()
    service = StrategyService(config_manager=config, event_bus=event_bus, market_data_repository=repo)
    
    # PortfolioManager 클래스 모킹
    mock_pm = AsyncMock()
    mock_pm.load_from_db = AsyncMock()
    
    portfolio = Portfolio("port_test", "Test Portfolio", "simulation")
    portfolio.positions[("upbit", "KRW-BTC")] = Position("upbit", "KRW-BTC", quantity=0.1, avg_price=50000000.0)
    mock_pm.get_active_simulation_portfolio = Mock(return_value=portfolio)
    mock_pm_class.return_value = mock_pm
    
    service.reload_trade_engines = AsyncMock(return_value={})
    
    await service.start()
    
    assert service.latest_prices[("upbit", "KRW-BTC")] == 50000000.0


@pytest.mark.asyncio
@patch("src.services.strategy_service.PortfolioManager")
async def test_upbit_stale_over_threshold_fail_stop(mock_pm_class):
    """upbit 가격이 threshold(7200초)를 초과해 stale하면 KeyError가 발생하며 Fail-Stop하는지 검증"""
    config = FakeConfigManager({
        "system": {
            "price_hydrate_stale_threshold_seconds": 3600,
            "price_hydrate_stale_threshold_seconds_upbit": 7200,
            "db_path": ":memory:",
            "strategies_dir": "src/engine/strategies"
        }
    })
    
    current_time = time.time()
    # 8000초 전 타임스탬프 (7200초 초과)
    ts = int(current_time) - 8000
    
    candles = {
        ("upbit", "KRW-BTC"): {
            "close": 50000000.0,
            "timestamp": ts
        }
    }
    
    repo = MockMarketDataRepository(candles)
    event_bus = AsyncMock()
    service = StrategyService(config_manager=config, event_bus=event_bus, market_data_repository=repo)
    
    mock_pm = AsyncMock()
    mock_pm.load_from_db = AsyncMock()
    
    portfolio = Portfolio("port_test", "Test Portfolio", "simulation")
    portfolio.positions[("upbit", "KRW-BTC")] = Position("upbit", "KRW-BTC", quantity=0.1, avg_price=50000000.0)
    mock_pm.get_active_simulation_portfolio = Mock(return_value=portfolio)
    mock_pm_class.return_value = mock_pm
    
    service.reload_trade_engines = AsyncMock(return_value={})
    
    with pytest.raises(KeyError) as exc_info:
        await service.start()
    assert "is stale" in str(exc_info.value)


@pytest.mark.asyncio
@patch("src.services.strategy_service.PortfolioManager")
@patch("src.engine.credentials.CredentialProvider.check_kis_open_day", new_callable=AsyncMock)
async def test_kis_open_market_stale_fail_stop(mock_check_open_day, mock_pm_class):
    """KIS 개장일 장중 상태일 때, 가격이 kis_open threshold(3600초)를 초과하면 Fail-Stop하는지 검증"""
    mock_check_open_day.return_value = True # 개장일로 모킹
    
    config = FakeConfigManager({
        "system": {
            "price_hydrate_stale_threshold_seconds": 3600,
            "price_hydrate_stale_threshold_seconds_kis_open": 3600,
            "price_hydrate_stale_threshold_seconds_kis_closed": 345600,
            "db_path": ":memory:",
            "strategies_dir": "src/engine/strategies"
        }
    })
    
    current_time = time.time()
    # 5000초 전 타임스탬프 (3600초 초과)
    ts = int(current_time) - 5000
    
    candles = {
        ("kis", "009150"): {
            "close": 2000000.0,
            "timestamp": ts
        }
    }
    
    repo = MockMarketDataRepository(candles)
    event_bus = AsyncMock()
    service = StrategyService(config_manager=config, event_bus=event_bus, market_data_repository=repo)
    
    mock_pm = AsyncMock()
    mock_pm.load_from_db = AsyncMock()
    
    portfolio = Portfolio("port_test", "Test Portfolio", "simulation")
    portfolio.positions[("kis", "009150")] = Position("kis", "009150", quantity=1.0, avg_price=2000000.0)
    mock_pm.get_active_simulation_portfolio = Mock(return_value=portfolio)
    mock_pm_class.return_value = mock_pm
    
    service.reload_trade_engines = AsyncMock(return_value={})
    
    # 시간대를 장중(10:00 KST)으로 강제 모킹하기 위해 _is_kis_market_open_now를 감쌉니다.
    async def mock_is_open_now():
        return True # 장중 상태로 강제 분기
    service._is_kis_market_open_now = mock_is_open_now
    
    with pytest.raises(KeyError) as exc_info:
        await service.start()
    assert "is stale" in str(exc_info.value)


@pytest.mark.asyncio
@patch("src.services.strategy_service.PortfolioManager")
@patch("src.engine.credentials.CredentialProvider.check_kis_open_day", new_callable=AsyncMock)
async def test_kis_closed_market_stale_passed(mock_check_open_day, mock_pm_class):
    """KIS 비개장/야간/휴장 상태일 때, 가격이 kis_closed threshold(4일) 이내이면 통과하는지 검증"""
    mock_check_open_day.return_value = True # 개장일이지만 야간으로 시뮬레이션할 것임
    
    config = FakeConfigManager({
        "system": {
            "price_hydrate_stale_threshold_seconds": 3600,
            "price_hydrate_stale_threshold_seconds_kis_open": 3600,
            "db_path": ":memory:",
            "strategies_dir": "src/engine/strategies"
        }
    })
    
    current_time = time.time()
    # 500000초 전 타임스탬프 (비개장 상태이므로 가격 경과 시간 제한 없이 바이패스되어 기동 성공해야 함)
    ts = int(current_time) - 500000
    
    candles = {
        ("kis", "009150"): {
            "close": 2000000.0,
            "timestamp": ts
        }
    }
    
    repo = MockMarketDataRepository(candles)
    event_bus = AsyncMock()
    service = StrategyService(config_manager=config, event_bus=event_bus, market_data_repository=repo)
    
    mock_pm = AsyncMock()
    mock_pm.load_from_db = AsyncMock()
    
    portfolio = Portfolio("port_test", "Test Portfolio", "simulation")
    portfolio.positions[("kis", "009150")] = Position("kis", "009150", quantity=1.0, avg_price=2000000.0)
    mock_pm.get_active_simulation_portfolio = Mock(return_value=portfolio)
    mock_pm_class.return_value = mock_pm
    
    service.reload_trade_engines = AsyncMock(return_value={})
    
    # 시간대를 야간(22:00 KST) 상태로 강제 모킹
    async def mock_is_open_now():
        return False
    service._is_kis_market_open_now = mock_is_open_now
    
    # 에러 없이 정상 기동되어야 함
    await service.start()
    assert service.latest_prices[("kis", "009150")] == 2000000.0


@pytest.mark.asyncio
@patch("src.services.strategy_service.PortfolioManager")
@patch("src.engine.credentials.CredentialProvider.check_kis_open_day", new_callable=AsyncMock)
async def test_kis_state_check_failed_fail_stop(mock_check_open_day, mock_pm_class):
    """KIS 시장 상태 판정 자체에 실패하면 RuntimeError를 내며 즉시 Fail-Stop하는지 검증"""
    mock_check_open_day.side_effect = ValueError("Network Timeout") # API 에러 모킹
    
    config = FakeConfigManager({
        "system": {
            "price_hydrate_stale_threshold_seconds": 3600,
            "db_path": ":memory:",
            "strategies_dir": "src/engine/strategies"
        }
    })
    
    current_time = time.time()
    ts = int(current_time) - 1000
    
    candles = {
        ("kis", "009150"): {
            "close": 2000000.0,
            "timestamp": ts
        }
    }
    
    repo = MockMarketDataRepository(candles)
    event_bus = AsyncMock()
    service = StrategyService(config_manager=config, event_bus=event_bus, market_data_repository=repo)
    
    mock_pm = AsyncMock()
    mock_pm.load_from_db = AsyncMock()
    
    portfolio = Portfolio("port_test", "Test Portfolio", "simulation")
    portfolio.positions[("kis", "009150")] = Position("kis", "009150", quantity=1.0, avg_price=2000000.0)
    mock_pm.get_active_simulation_portfolio = Mock(return_value=portfolio)
    mock_pm_class.return_value = mock_pm
    
    service.reload_trade_engines = AsyncMock(return_value={})
    
    # 예외가 RuntimeError로 변환되어 던져져야 함 (Fail-Fast 원칙)
    with pytest.raises(RuntimeError) as exc_info:
        await service.start()
    assert "Failed to resolve KIS market open/closed state" in str(exc_info.value)


@pytest.mark.asyncio
@patch("src.services.strategy_service.PortfolioManager")
async def test_bithumb_low_volume_stale_bypass(mock_pm_class):
    """빗썸 저유동성 종목 가격이 stale(10300초 전)하지만 타 종목 최신 캔들(10초 전)이 있어 수집기 작동 중으로 판단하여 기동 통과"""
    config = FakeConfigManager({
        "system": {
            "price_hydrate_stale_threshold_seconds": 3600,
            "price_hydrate_stale_threshold_seconds_bithumb": 7200,
            "db_path": ":memory:",
            "strategies_dir": "src/engine/strategies"
        }
    })
    
    current_time = time.time()
    # AMO는 10300초 전으로 stale 상태
    ts_amo = int(current_time) - 10300
    
    candles = {
        ("bithumb", "AMO"): {
            "close": 0.5,
            "timestamp": ts_amo
        }
    }
    
    # 빗썸 전체 최신 캔들 타임스탬프는 10초 전(fresh)으로 수집기 생존 모킹
    repo = MockMarketDataRepository(candles, latest_candle_ts_val=int(current_time) - 10)
    event_bus = AsyncMock()
    service = StrategyService(config_manager=config, event_bus=event_bus, market_data_repository=repo)
    
    mock_pm = AsyncMock()
    mock_pm.load_from_db = AsyncMock()
    
    portfolio = Portfolio("port_test", "Test Portfolio", "simulation")
    portfolio.positions[("bithumb", "AMO")] = Position("bithumb", "AMO", quantity=100.0, avg_price=0.5)
    mock_pm.get_active_simulation_portfolio = Mock(return_value=portfolio)
    mock_pm_class.return_value = mock_pm
    
    service.reload_trade_engines = AsyncMock(return_value={})
    
    # 바이패스하여 기동 완료
    await service.start()
    assert service.latest_prices[("bithumb", "AMO")] == 0.5


@pytest.mark.asyncio
@patch("src.services.strategy_service.PortfolioManager")
async def test_bithumb_collector_dead_fail_stop(mock_pm_class):
    """빗썸 저유동성 종목 가격도 stale하고 빗썸 전체 최신 캔들도 10300초 전(stale)이라 수집기 장애로 판정하여 KeyError 발생"""
    config = FakeConfigManager({
        "system": {
            "price_hydrate_stale_threshold_seconds": 3600,
            "price_hydrate_stale_threshold_seconds_bithumb": 7200,
            "db_path": ":memory:",
            "strategies_dir": "src/engine/strategies"
        }
    })
    
    current_time = time.time()
    ts_amo = int(current_time) - 10300
    
    candles = {
        ("bithumb", "AMO"): {
            "close": 0.5,
            "timestamp": ts_amo
        }
    }
    
    # 전체 최신 캔들도 10300초 전으로 stale 모킹
    repo = MockMarketDataRepository(candles, latest_candle_ts_val=ts_amo)
    event_bus = AsyncMock()
    service = StrategyService(config_manager=config, event_bus=event_bus, market_data_repository=repo)
    
    mock_pm = AsyncMock()
    mock_pm.load_from_db = AsyncMock()
    
    portfolio = Portfolio("port_test", "Test Portfolio", "simulation")
    portfolio.positions[("bithumb", "AMO")] = Position("bithumb", "AMO", quantity=100.0, avg_price=0.5)
    mock_pm.get_active_simulation_portfolio = Mock(return_value=portfolio)
    mock_pm_class.return_value = mock_pm
    
    service.reload_trade_engines = AsyncMock(return_value={})
    
    with pytest.raises(KeyError) as exc_info:
        await service.start()
    assert "appears to be dead" in str(exc_info.value)


@pytest.mark.asyncio
@patch("src.services.strategy_service.PortfolioManager")
async def test_exchange_latest_timestamp_missing_fail_stop(mock_pm_class):
    """최신 캔들 타임스탬프 조회 결과가 None(데이터 없음)인 경우 RuntimeError 발생"""
    config = FakeConfigManager({
        "system": {
            "price_hydrate_stale_threshold_seconds": 3600,
            "price_hydrate_stale_threshold_seconds_bithumb": 7200,
            "db_path": ":memory:",
            "strategies_dir": "src/engine/strategies"
        }
    })
    
    current_time = time.time()
    ts_amo = int(current_time) - 10300
    candles = {
        ("bithumb", "AMO"): {
            "close": 0.5,
            "timestamp": ts_amo
        }
    }
    
    # None 반환 모킹
    repo = MockMarketDataRepository(candles, latest_candle_ts_val=None)
    event_bus = AsyncMock()
    service = StrategyService(config_manager=config, event_bus=event_bus, market_data_repository=repo)
    
    mock_pm = AsyncMock()
    mock_pm.load_from_db = AsyncMock()
    
    portfolio = Portfolio("port_test", "Test Portfolio", "simulation")
    portfolio.positions[("bithumb", "AMO")] = Position("bithumb", "AMO", quantity=100.0, avg_price=0.5)
    mock_pm.get_active_simulation_portfolio = Mock(return_value=portfolio)
    mock_pm_class.return_value = mock_pm
    
    service.reload_trade_engines = AsyncMock(return_value={})
    
    with pytest.raises(RuntimeError) as exc_info:
        await service.start()
    assert "No latest candle timestamp found" in str(exc_info.value)


@pytest.mark.asyncio
@patch("src.services.strategy_service.PortfolioManager")
async def test_exchange_latest_timestamp_query_exception_fail_stop(mock_pm_class):
    """최신 캔들 타임스탬프 조회 중 DB 예외 발생 시 RuntimeError로 래핑하고 원본 예외 정보 노출"""
    config = FakeConfigManager({
        "system": {
            "price_hydrate_stale_threshold_seconds": 3600,
            "price_hydrate_stale_threshold_seconds_bithumb": 7200,
            "db_path": ":memory:",
            "strategies_dir": "src/engine/strategies"
        }
    })
    
    current_time = time.time()
    ts_amo = int(current_time) - 10300
    candles = {
        ("bithumb", "AMO"): {
            "close": 0.5,
            "timestamp": ts_amo
        }
    }
    
    # DB 조회 예외 시뮬레이션
    db_error = ConnectionError("DB connection lost")
    repo = MockMarketDataRepository(candles, query_exception=db_error)
    event_bus = AsyncMock()
    service = StrategyService(config_manager=config, event_bus=event_bus, market_data_repository=repo)
    
    mock_pm = AsyncMock()
    mock_pm.load_from_db = AsyncMock()
    
    portfolio = Portfolio("port_test", "Test Portfolio", "simulation")
    portfolio.positions[("bithumb", "AMO")] = Position("bithumb", "AMO", quantity=100.0, avg_price=0.5)
    mock_pm.get_active_simulation_portfolio = Mock(return_value=portfolio)
    mock_pm_class.return_value = mock_pm
    
    service.reload_trade_engines = AsyncMock(return_value={})
    
    with pytest.raises(RuntimeError) as exc_info:
        await service.start()
    assert "Failed to query latest candle timestamp" in str(exc_info.value)
    assert isinstance(exc_info.value.__cause__, ConnectionError)


@pytest.mark.asyncio
@patch("src.services.strategy_service.PortfolioManager")
@patch("src.engine.credentials.CredentialProvider.check_kis_open_day", new_callable=AsyncMock)
async def test_kis_open_market_never_bypass_stale(mock_check_open_day, mock_pm_class):
    """KIS 개장 중에는 다른 주식이 fresh(10초 전)하더라도 절대 바이패스하지 않고 KeyError 발생"""
    mock_check_open_day.return_value = True
    
    config = FakeConfigManager({
        "system": {
            "price_hydrate_stale_threshold_seconds": 3600,
            "price_hydrate_stale_threshold_seconds_kis_open": 3600,
            "db_path": ":memory:",
            "strategies_dir": "src/engine/strategies"
        }
    })
    
    current_time = time.time()
    # KIS 009150 종목은 5000초 전으로 stale 상태
    ts_kis = int(current_time) - 5000
    
    candles = {
        ("kis", "009150"): {
            "close": 2000000.0,
            "timestamp": ts_kis
        }
    }
    
    # KIS 전체 최신 캔들은 10초 전(fresh)으로 수집기 생존 상태 모킹
    repo = MockMarketDataRepository(candles, latest_candle_ts_val=int(current_time) - 10)
    event_bus = AsyncMock()
    service = StrategyService(config_manager=config, event_bus=event_bus, market_data_repository=repo)
    
    # 개장 상태 강제 분기
    async def mock_is_open_now():
        return True
    service._is_kis_market_open_now = mock_is_open_now
    
    mock_pm = AsyncMock()
    mock_pm.load_from_db = AsyncMock()
    
    portfolio = Portfolio("port_test", "Test Portfolio", "simulation")
    portfolio.positions[("kis", "009150")] = Position("kis", "009150", quantity=1.0, avg_price=2000000.0)
    mock_pm.get_active_simulation_portfolio = Mock(return_value=portfolio)
    mock_pm_class.return_value = mock_pm
    
    service.reload_trade_engines = AsyncMock(return_value={})
    
    # KIS는 바이패스가 작동하지 않아야 하므로 KeyError 발생해야 함
    with pytest.raises(KeyError) as exc_info:
        await service.start()
    assert "is stale in DB candles" in str(exc_info.value)
    assert "appears to be dead" not in str(exc_info.value)
