import pytest
import asyncio
import os
from typing import Dict, Any
from src.engine.pipeline import ExecutionPipeline
from src.engine.portfolio import PortfolioManager, Portfolio
from src.engine.strategy import TradeSignal
from src.database.schema import init_db

TEST_DB_PATH = "test_pipeline.db"

@pytest.fixture(autouse=True)
def setup_test_db():
    """테스트 구동 전 임시 DB를 셋업하고, 테스트 완료 후 디스크에서 완전히 삭제합니다."""
    import asyncio
    asyncio.run(init_db(TEST_DB_PATH))
    yield
    # 테스트 완료 후 DB 잔여 임시 파일 청소 (TearDown)
    for ext in ["", "-wal", "-shm"]:
        path = TEST_DB_PATH + ext
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass

class MockSignal:
    def __init__(self, symbol: str, action: str, exchange: str = "upbit", strategy_id: str = "test", reason: str = "test_reason"):
        self.symbol = symbol
        self.action = action
        self.exchange = exchange
        self.strategy_id = strategy_id
        self.reason = reason
        self.context = {}

@pytest.mark.asyncio
async def test_calculate_position_size():
    # 포트폴리오 준비 (현금 100만 원, exchange_id="upbit" 명시)
    portfolio = Portfolio(portfolio_id="default", name="Default Portfolio", initial_cash=1000000, exchange_id="upbit")
    # 🌟 DI 적용: PortfolioManager에 격리된 테스트용 DB 주입
    pm = PortfolioManager(db_path=TEST_DB_PATH)
    pipeline = ExecutionPipeline(pm)

    # 1. context에 비율이 없는 경우 (디폴트 10% = 10만 원)
    signal = MockSignal("KRW-BTC", "BUY")
    qty, val = pipeline.calculate_position_size(portfolio, signal, 50000000)
    assert val == 100000.0
    assert qty == 100000.0 / 50000000

    # 2. context에 명시적 weight가 지정된 경우 (20% = 20만 원)
    signal.context = {"weight": 0.2}
    qty, val = pipeline.calculate_position_size(portfolio, signal, 50000000)
    assert val == 200000.0
    assert qty == 200000.0 / 50000000

    # 3. SELL 신호 시 보유 수량 전량 반환 (exchange="upbit" 인자 주입)
    portfolio.update_position("upbit", "KRW-BTC", "BUY", 50000000, 0.005, 125)
    signal_sell = MockSignal("KRW-BTC", "SELL")
    qty, val = pipeline.calculate_position_size(portfolio, signal_sell, 50000000)
    assert qty == 0.005
    assert val == 0.005 * 50000000

@pytest.mark.asyncio
async def test_apply_slippage():
    # 🌟 DI 적용: PortfolioManager에 격리된 테스트용 DB 주입
    pm = PortfolioManager(db_path=TEST_DB_PATH)
    pipeline = ExecutionPipeline(pm)

    # BUY: 0.1% 불리하게 상승
    signal_buy = MockSignal("KRW-BTC", "BUY")
    price_buy = pipeline.apply_slippage(signal_buy, 1000.0)
    assert price_buy == pytest.approx(1001.0)

    # SELL: 0.1% 불리하게 하락
    signal_sell = MockSignal("KRW-BTC", "SELL")
    price_sell = pipeline.apply_slippage(signal_sell, 1000.0)
    assert price_sell == pytest.approx(999.0)

@pytest.mark.asyncio
async def test_check_risk_limits():
    # 🌟 DI 적용: PortfolioManager에 격리된 테스트용 DB 주입
    pm = PortfolioManager(db_path=TEST_DB_PATH)
    portfolio = Portfolio(portfolio_id="default", name="Default Portfolio", initial_cash=1000000, exchange_id="upbit")
    pipeline = ExecutionPipeline(pm)

    # 1. 잔고 부족 시나리오
    # 100만 원 자산인데 110만 원 매수 요청
    signal = MockSignal("KRW-BTC", "BUY")
    passed, reason = pipeline.check_risk_limits(portfolio, signal, 50000000, 0.022, 1100000)
    assert not passed
    assert "잔고 부족" in reason

    # 2. 단일 종목 투자 한도(30%) 초과 시나리오
    # 100만 원 자산인데 한 종목에 40만 원 매수 요청
    passed, reason = pipeline.check_risk_limits(portfolio, signal, 50000000, 0.008, 400000)
    assert not passed
    assert "단일 종목 투자 한도" in reason

    # 3. 리스크 필터 통과 시나리오 (20만 원 매수 요청, 20% 비중)
    passed, reason = pipeline.check_risk_limits(portfolio, signal, 50000000, 0.004, 200000)
    assert passed
    assert reason == ""
