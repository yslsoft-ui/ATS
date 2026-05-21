import pytest
import asyncio
import os
from src.engine.portfolio import Portfolio, PortfolioManager, VirtualExecutor
from src.engine.trade_engine import TradeSignal
from src.database.schema import init_db

TEST_DB_PATH = "test_portfolio.db"

@pytest.fixture(autouse=True)
def setup_test_db():
    """테스트 세션 시작 전 격리된 테스트용 DB를 초기화하고, 완료 후 말끔히 삭제합니다."""
    import asyncio
    asyncio.run(init_db(TEST_DB_PATH))
    yield
    # 테스트 종료 후 임시 데이터베이스 잔여 파일 청소 (TearDown)
    for ext in ["", "-wal", "-shm"]:
        path = TEST_DB_PATH + ext
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass

@pytest.mark.asyncio
async def test_portfolio_basic_operations():
    portfolio = Portfolio(portfolio_id="test_id", name="Test Portfolio", initial_cash=1000000, exchange_id="upbit")
    
    # Buy operation (exchange 인자 주입)
    portfolio.update_position(exchange="upbit", symbol="KRW-BTC", side="BUY", price=50000000, quantity=0.01, fee=250)
    
    assert portfolio.positions["KRW-BTC"].quantity == 0.01
    assert portfolio.positions["KRW-BTC"].avg_price == 50000000
    assert portfolio.cash == 1000000 - (500000 + 250)
    
    # Sell operation (exchange 인자 주입)
    portfolio.update_position(exchange="upbit", symbol="KRW-BTC", side="SELL", price=60000000, quantity=0.01, fee=300)
    
    assert portfolio.positions["KRW-BTC"].quantity == 0.00
    assert portfolio.cash == 499750 + (600000 - 300)

@pytest.mark.asyncio
async def test_virtual_executor():
    portfolio = Portfolio(portfolio_id="sim_id", name="Simulation", initial_cash=1000000, exchange_id="upbit")
    # default_fee_rate 키워드 인자 매핑 버그 수정
    executor = VirtualExecutor(default_fee_rate=0.0005)
    
    orderbook_data = {
        'asks': [[50000000, 1.0], [50100000, 1.0]],
        'bids': [[49900000, 1.0], [49800000, 1.0]]
    }
    
    # Market BUY (exchange="upbit" 누락 매개변수 주입)
    result = await executor.execute_order(
        portfolio=portfolio,
        exchange="upbit",
        symbol="KRW-BTC",
        side="BUY",
        quantity=0.1,
        orderbook=orderbook_data
    )
    
    assert result is not None
    assert result['price'] == 50000000
    assert result['quantity'] == 0.1
    assert portfolio.positions["KRW-BTC"].quantity == 0.1
    
    # Market SELL (exchange="upbit" 누락 매개변수 주입)
    result = await executor.execute_order(
        portfolio=portfolio,
        exchange="upbit",
        symbol="KRW-BTC",
        side="SELL",
        quantity=0.05,
        orderbook=orderbook_data
    )
    
    assert result is not None
    assert result['price'] == 49900000
    assert portfolio.positions["KRW-BTC"].quantity == 0.05

@pytest.mark.asyncio
async def test_portfolio_manager_handle_signal():
    # 🌟 DI 철학 적용: PortfolioManager에 격리된 테스트용 DB 주입!
    pm = PortfolioManager(db_path=TEST_DB_PATH)
    # upbit 신호는 내부적으로 'default' ID 포트폴리오로 우회되므로 'default'로 개설!
    portfolio = Portfolio(portfolio_id="default", name="Main Portfolio", initial_cash=1000000, exchange_id="upbit")
    pm.add_portfolio(portfolio)
    
    # TradeSignal 생성자에 exchange="upbit" 누락 인자 주입
    signal = TradeSignal(exchange="upbit", symbol="KRW-BTC", action="BUY", price=50000000, reason="Test", interval=60)
    orderbook_data = {
        'asks': [[50000000, 10.0]],
        'bids': [[49000000, 10.0]]
    }
    
    # BUY signal handles 10% of cash
    result = await pm.handle_signal("default", signal, 50000000, orderbook_data)
    
    assert result is not None
    assert result['side'] == 'BUY'
    assert portfolio.positions["KRW-BTC"].quantity > 0
    
    # SELL signal handles all holdings (exchange="upbit" 누락 인자 주입)
    sell_signal = TradeSignal(exchange="upbit", symbol="KRW-BTC", action="SELL", price=50000000, reason="Test", interval=60)
    result = await pm.handle_signal("default", sell_signal, 50000000, orderbook_data)
    
    assert result is not None
    assert result['side'] == 'SELL'
    assert portfolio.positions["KRW-BTC"].quantity == 0
