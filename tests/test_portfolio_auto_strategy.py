import pytest
import json
import time
import asyncio
from src.engine.command import UserCommand, UserCommandDispatcher
from src.database.repository import InMemoryTradingRepository
from src.engine.portfolio import Portfolio, PortfolioManager, Position
from src.services.strategy_service import StrategyService
from src.engine.strategy import BaseStrategy, StrategyRegistry
from tests.test_command_dispatcher import FakePublisher, FakeConfigManager, FakePortfolioManager

# 테스트용 모의 전략 등록
@StrategyRegistry.register
class MockTestStrategy(BaseStrategy):
    default_params = {
        "param1": {"type": "int", "default": 10},
        "param2": {"type": "float", "default": 2.5}
    }
    def __init__(self, strategy_id: str, params: dict = None):
        super().__init__(strategy_id, params)
    def on_update(self, context):
        return None

@pytest.fixture
def setup_auto_strategy_env():
    # 임시 DB 파일 경로 설정
    test_db_path = "data/test_auto_strategy.db"
    
    # 1. DB 스키마 초기화
    from src.database.schema import init_db
    loop = asyncio.new_event_loop()
    loop.run_until_complete(init_db(test_db_path))
    loop.close()

    repo = InMemoryTradingRepository()
    
    # 설정 모의 (MockTestStrategy 전역 활성화 및 거래소 오버라이드 정의)
    initial_config = {
        "exchanges": {
            "upbit": {"enabled": True, "fee_rate": 0.0005},
            "kis": {"enabled": True, "fee_rate": 0.00015}
        },
        "strategies": {
            "mockteststrategy": {
                "enabled": True,
                "params": {"param1": 10, "param2": 2.5},
                "overrides": {
                    "kis": {
                        "enabled": True,
                        "params": {"param1": 99}  # KIS 거래소는 param1 오버라이드
                    },
                    "upbit": {
                        "enabled": False  # upbit 거래소에서는 mockteststrategy 가동 비활성화
                    }
                }
            }
        },
        "system": {
            "strategies_dir": "src/engine/strategies"
        }
    }
    config = FakeConfigManager(initial_config)
    pm = FakePortfolioManager(db_path=test_db_path)
    pm.repository = repo
    control_pub = FakePublisher()
    strategy_pub = FakePublisher()
    
    dispatcher = UserCommandDispatcher(
        repository=repo,
        config_manager=config,
        portfolio_manager=pm,
        control_publisher=control_pub,
        strategy_control_publisher=strategy_pub
    )
    
    yield dispatcher, repo, config, pm, control_pub, strategy_pub

    # 2. Cleanup: 테스트 DB 파일 삭제
    import os
    if os.path.exists(test_db_path):
        try:
            os.remove(test_db_path)
        except Exception:
            pass
    for suffix in ["-wal", "-shm"]:
        p = f"{test_db_path}{suffix}"
        if os.path.exists(p):
            try:
                os.remove(p)
            except Exception:
                pass

@pytest.mark.asyncio
async def test_portfolio_start_without_strategies_fallback_to_config(setup_auto_strategy_env):
    dispatcher, repo, config, pm, control_pub, strategy_pub = setup_auto_strategy_env
    
    # 1. DB에 챔피언 버전 정보가 아예 없는 상황에서 strategies 생략 기동
    payload = {
        "initial_cash": 20000000.0,
        "portfolio_id": "test_auto_sim_1",
        "strategies": None  # strategies 생략
    }
    
    res = await dispatcher.dispatch(UserCommand.PORTFOLIO_START, payload)
    assert res["portfolio_id"] == "test_auto_sim_1"
    
    portfolio = pm.portfolios["test_auto_sim_1"]
    assert portfolio.strategy_info is not None
    
    meta = json.loads(portfolio.strategy_info)
    applied_strategies = meta.get("applied_strategies", {})
    
    # 2. DB에 챔피언이 없어 config(settings.yaml)의 기본 활성 전략인 mockteststrategy가 복원되었는지 검증
    assert "MockTestStrategy" in applied_strategies
    assert applied_strategies["MockTestStrategy"]["enabled"] is True
    assert applied_strategies["MockTestStrategy"]["params"]["param1"] == 10

@pytest.mark.asyncio
async def test_portfolio_start_without_strategies_loads_db_champion(setup_auto_strategy_env):
    dispatcher, repo, config, pm, control_pub, strategy_pub = setup_auto_strategy_env
    
    # 1. DB(Repository)에 mockteststrategy 챔피언 버전(V2) 및 최적 파라미터 등록
    champion_params = {"param1": 42, "param2": 9.9}
    await repo.save_strategy_version("mockteststrategy", 2, champion_params, int(time.time() * 1000))
    
    # 2. strategies 파라미터를 빈 값({})으로 전달하여 시작
    payload = {
        "initial_cash": 30000000.0,
        "portfolio_id": "test_auto_sim_2",
        "strategies": {}  # 빈 딕셔너리
    }
    
    res = await dispatcher.dispatch(UserCommand.PORTFOLIO_START, payload)
    portfolio = pm.portfolios["test_auto_sim_2"]
    meta = json.loads(portfolio.strategy_info)
    applied_strategies = meta.get("applied_strategies", {})
    
    # 3. DB의 챔피언 파라미터(V2)가 자동 기용되었는지 검증
    assert "MockTestStrategy" in applied_strategies
    assert applied_strategies["MockTestStrategy"]["params"]["param1"] == 42
    assert applied_strategies["MockTestStrategy"]["params"]["param2"] == 9.9

@pytest.mark.asyncio
async def test_strategy_service_reload_engines_applies_overrides(setup_auto_strategy_env):
    dispatcher, repo, config, pm, control_pub, strategy_pub = setup_auto_strategy_env
    
    # 1. DB에 챔피언 버전 등록 (mockteststrategy, V3, 기본 params)
    champion_params = {"param1": 5, "param2": 1.2}
    await repo.save_strategy_version("mockteststrategy", 3, champion_params, int(time.time() * 1000))
    
    # 2. 모의투자 시작 및 포트폴리오 적재
    payload = {
        "initial_cash": 10000000.0,
        "portfolio_id": "test_override_sim",
        "strategies": {}
    }
    await dispatcher.dispatch(UserCommand.PORTFOLIO_START, payload)
    portfolio = pm.portfolios["test_override_sim"]
    
    # 3. StrategyService를 생성하여 reload_trade_engines 오버라이드 병합 검증
    from src.database.repository import SqliteMarketDataRepository
    md_repo = SqliteMarketDataRepository(db_path="data/test_auto_strategy.db")
    service = StrategyService(config_manager=config, event_bus=None, market_data_repository=md_repo)
    service.portfolio_manager = pm
    
    from unittest.mock import AsyncMock, Mock
    mock_ns = Mock()
    mock_ns.publish = AsyncMock(return_value=True)
    service.notification_service = mock_ns
    
    # exchange_symbols 동적 페치 우회
    async def mock_fetch_symbols(ex_id, conf):
        return ["BTC"] if ex_id == "upbit" else ["005930"]
    service.fetch_exchange_symbols = mock_fetch_symbols
    
    new_engines = await service.reload_trade_engines(portfolio)
    
    # 4. 거래소 오버라이드 반영 여부 교차 검증
    # 4.1. upbit 거래소의 경우 overrides.upbit.enabled = False 이므로 mockteststrategy 엔진이 기용되지 않아야 함 (skip)
    assert "upbit:BTC" not in new_engines
    
    # 4.2. kis 거래소의 경우 overrides.kis.enabled = True 이므로 mockteststrategy 엔진이 기용되어야 함
    assert "kis:005930" in new_engines
    
    engine = new_engines["kis:005930"]
    assert len(engine.strategies) == 1
    strat = engine.strategies[0]
    
    # 4.3. kis 거래소 엔진의 전략 파라미터는 overrides.kis.params.param1 = 99 로 덮어씌워져야(Merge) 함
    assert strat.params["param1"] == 99
    assert strat.params["param2"] == 1.2  # overrides에 없는 param2는 챔피언 파라미터(1.2) 유지
