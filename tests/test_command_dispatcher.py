import os
import pytest
import json
import time
import asyncio
from typing import Dict, Any, Optional
from src.engine.command import UserCommand, UserCommandDispatcher
from src.database.repository import InMemoryTradingRepository
from src.database.schema import init_db

# --- Fake (Mock) 클래스 구현 ---

class FakePublisher:
    """ZeroMQ EventBusPublisher 역할을 하는 Fake 객체"""
    def __init__(self):
        self.published = []

    async def publish(self, topic: str, message: dict):
        self.published.append((topic, message))

class FakeConfigManager:
    """ConfigManager 역할을 하는 Fake 객체"""
    def __init__(self, initial_config=None):
        self.config = initial_config or {
            "exchanges": {
                "upbit": {"enabled": False, "fee_rate": 0.0005},
                "kis": {"enabled": False, "fee_rate": 0.00015}
            },
            "strategies": {
                "momentum": {"enabled": False, "params": {"period": 14}}
            }
        }

    def get(self, key: str, default=None):
        keys = key.split('.')
        val = self.config
        for k in keys:
            if isinstance(val, dict) and k in val:
                val = val[k]
            else:
                return default
        return val

    def update(self, key: str, value: Any):
        keys = key.split('.')
        val = self.config
        for k in keys[:-1]:
            if k not in val:
                val[k] = {}
            val = val[k]
        val[keys[-1]] = value

from src.engine.portfolio import PortfolioDict

class FakePortfolioManager:
    """PortfolioManager 역할을 하는 Fake 객체"""
    def __init__(self, db_path):
        self.db_path = db_path
        self.portfolios = PortfolioDict()
        self.executors = {
            "simulation": FakeOrderExecutor()
        }

    def add_portfolio(self, portfolio):
        self.portfolios[portfolio.id] = portfolio

    def get_active_simulation_portfolio(self):
        sim_ports = [p for p in self.portfolios.values() if p.portfolio_type == 'simulation']
        if not sim_ports:
            return None
        sim_ports.sort(key=lambda x: x.id, reverse=True)
        return sim_ports[0]

    async def db_save_portfolio(self, db, portfolio):
        ended_at = getattr(portfolio, 'ended_at', None)
        await db.execute('''
            INSERT INTO portfolios (id, name, type, ended_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET ended_at = excluded.ended_at
        ''', (portfolio.id, portfolio.name, portfolio.portfolio_type, ended_at))
        
        if hasattr(portfolio, 'exchange_cash') and portfolio.exchange_cash:
            for ex_id, ex_cash in portfolio.exchange_cash.items():
                init_cash = portfolio.exchange_initial_cash.get(ex_id, ex_cash)
                await db.execute('''
                    INSERT INTO portfolio_exchanges (portfolio_id, exchange_id, initial_cash, cash)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(portfolio_id, exchange_id) DO UPDATE SET cash = excluded.cash
                ''', (portfolio.id, ex_id, init_cash, ex_cash))
        else:
            await db.execute('''
                INSERT INTO portfolio_exchanges (portfolio_id, exchange_id, initial_cash, cash)
                VALUES (?, 'upbit', 10000000.0, ?)
                ON CONFLICT(portfolio_id, exchange_id) DO UPDATE SET cash = excluded.cash
            ''', (portfolio.id, portfolio.cash))

    async def save_to_db(self, portfolio_id: str):
        portfolio = self.portfolios.get(portfolio_id)
        if not portfolio:
            return
        from src.database.connection import get_db_conn
        async with get_db_conn(self.db_path) as db:
            await self.db_save_portfolio(db, portfolio)
            await db.commit()

    async def get_portfolio_current_prices(self, portfolio_id: str, system):
        # 테스트 시 모든 종목의 종가를 10000원으로 고정
        portfolio = self.portfolios.get(portfolio_id)
        if not portfolio:
            return {}
        return {pos.symbol: 10000.0 for pos in portfolio.positions.values()}

class FakeOrderExecutor:
    """주문 집행을 모방하는 Fake 객체"""
    async def execute_order(self, exchange: str, symbol: str, side: str, quantity: float, **kwargs) -> Optional[Dict]:
        return {
            'exchange': exchange,
            'market': 'KRW',
            'symbol': symbol,
            'side': side,
            'price': 10000.0,
            'quantity': quantity,
            'fee': 5.0,
            'executed_value': 10000.0 * quantity,
            'timestamp': int(time.time() * 1000)
        }

# --- Pytest 단위 테스트 케이스 ---

@pytest.fixture
def setup_dispatcher():
    # 임시 DB 파일 경로 설정
    test_db_path = "data/test_dispatcher.db"
    
    # 1. DB 스키마 초기화 (동기 컨텍스트에서 비동기 실행)
    loop = asyncio.new_event_loop()
    loop.run_until_complete(init_db(test_db_path))
    loop.close()
    
    repo = InMemoryTradingRepository()
    config = FakeConfigManager()
    pm = FakePortfolioManager(db_path=test_db_path)
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
    if os.path.exists(test_db_path):
        try:
            os.remove(test_db_path)
        except Exception:
            pass
    # sqlite-journal 파일들도 있으면 청소
    for suffix in ["-wal", "-shm"]:
        p = f"{test_db_path}{suffix}"
        if os.path.exists(p):
            try:
                os.remove(p)
            except Exception:
                pass

@pytest.mark.asyncio
async def test_collector_start_success(setup_dispatcher):
    dispatcher, repo, config, pm, control_pub, strategy_pub = setup_dispatcher

    # 1. 수집기 시작 명령 dispatch
    await dispatcher.dispatch(UserCommand.COLLECTOR_START, {"exchange": "upbit"})

    # 설정 반영 상태 검증
    assert config.get("exchanges.upbit.enabled") is True

    # 감사 로그(system_events) 검증
    events = repo.system_events
    assert len(events) == 2
    
    req_event = events[0]
    succ_event = events[1]
    
    assert req_event["event_type"] == "COLLECTOR_START_REQUEST"
    assert succ_event["event_type"] == "COLLECTOR_START_SUCCESS"
    assert req_event["target"] == "upbit"
    assert succ_event["target"] == "upbit"
    
    # command_id가 서로 동일한지 검증
    req_context = json.loads(req_event["context"])
    succ_context = json.loads(succ_event["context"])
    assert req_context["command_id"] == succ_context["command_id"]
    assert req_context["payload"]["exchange"] == "upbit"

@pytest.mark.asyncio
async def test_collector_start_failed_invalid_exchange(setup_dispatcher):
    dispatcher, repo, config, pm, control_pub, strategy_pub = setup_dispatcher

    # 존재하지 않는 거래소 시작 -> ValueError 유발 예상
    with pytest.raises(ValueError) as excinfo:
        await dispatcher.dispatch(UserCommand.COLLECTOR_START, {"exchange": "binance"})
    
    assert "Configuration for exchange 'binance' not found" in str(excinfo.value)

    # 감사 로그 검증 (REQUEST -> FAILED)
    events = repo.system_events
    assert len(events) == 2
    
    req_event = events[0]
    fail_event = events[1]
    
    assert req_event["event_type"] == "COLLECTOR_START_REQUEST"
    assert fail_event["event_type"] == "COLLECTOR_START_FAILED"
    
    req_context = json.loads(req_event["context"])
    fail_context = json.loads(fail_event["context"])
    assert req_context["command_id"] == fail_context["command_id"]
    assert "Configuration for exchange" in fail_context["error"]

@pytest.mark.asyncio
async def test_collector_restart_daemon_publishes_to_zmq(setup_dispatcher):
    dispatcher, repo, config, pm, control_pub, strategy_pub = setup_dispatcher

    await dispatcher.dispatch(UserCommand.COLLECTOR_RESTART_DAEMON, {"target": "collector_daemon"})

    # ZMQ 퍼블리시 발행 결과 검증
    assert len(control_pub.published) == 1
    topic, msg = control_pub.published[0]
    assert topic == "collector_control"
    assert msg["type"] == "restart_daemon"

    events = repo.system_events
    assert events[0]["event_type"] == "DAEMON_RESTART_SIGNAL_REQUEST"
    assert events[1]["event_type"] == "DAEMON_RESTART_SIGNAL_SUCCESS"

@pytest.mark.asyncio
async def test_strategy_parameter_update(setup_dispatcher):
    dispatcher, repo, config, pm, control_pub, strategy_pub = setup_dispatcher

    payload = {
        "strategy_id": "momentum",
        "params": {"period": 20, "multiplier": 2.0}
    }
    await dispatcher.dispatch(UserCommand.STRATEGY_UPDATE_PARAMS, payload)

    # 설정 저장 및 보존 검증
    assert config.get("strategies.momentum.params.period") == 20
    assert config.get("strategies.momentum.params.multiplier") == 2.0

    events = repo.system_events
    assert events[0]["event_type"] == "STRATEGY_UPDATE_PARAMS_REQUEST"
    assert events[1]["event_type"] == "STRATEGY_UPDATE_PARAMS_SUCCESS"
    assert events[0]["target"] == "momentum"

@pytest.mark.asyncio
async def test_portfolio_lifecycle(setup_dispatcher):
    dispatcher, repo, config, pm, control_pub, strategy_pub = setup_dispatcher

    # 1. 모의투자 시작
    start_payload = {
        "initial_cash": 10000000.0,
        "strategies": {"momentum": {"weight": 1.0}},
        "portfolio_id": "sim_test_123"
    }
    start_res = await dispatcher.dispatch(UserCommand.PORTFOLIO_START, start_payload)
    
    assert start_res["portfolio_id"] == "sim_test_123"
    assert "sim_test_123" in pm.portfolios
    
    portfolio = pm.portfolios["sim_test_123"]
    assert portfolio.portfolio_type == "simulation"
    assert portfolio.cash == 10000000.0

    # 2. ZMQ 업데이트 발행 검증
    assert len(strategy_pub.published) == 1
    assert strategy_pub.published[0] == ("strategy_control", {"type": "update_portfolio", "portfolio_id": "sim_test_123"})

    # 3. 세션 마감(PORTFOLIO_END)
    end_payload = {"portfolio_id": "sim_test_123"}
    await dispatcher.dispatch(UserCommand.PORTFOLIO_END, end_payload)

    assert portfolio.portfolio_type == "simulation"
    assert portfolio.ended_at is not None
