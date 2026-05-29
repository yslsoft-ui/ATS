import pytest
import asyncio
import os
from src.engine.portfolio import Portfolio, PortfolioManager, VirtualOrderExecutorAdapter
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
    portfolio = Portfolio(portfolio_id="test_id", name="Test Portfolio", initial_cash=10000000, exchange_id="upbit")
    
    # Buy operation (exchange 인자 주입)
    portfolio.update_position(exchange="upbit", symbol="KRW-BTC", side="BUY", price=50000000, quantity=0.01, fee=250)
    
    assert portfolio.positions[("upbit", "KRW-BTC")].quantity == 0.01
    assert portfolio.positions[("upbit", "KRW-BTC")].avg_price == 50000000
    assert portfolio.cash == 10000000 - (500000 + 250)
    
    # Sell operation (exchange 인자 주입)
    portfolio.update_position(exchange="upbit", symbol="KRW-BTC", side="SELL", price=60000000, quantity=0.01, fee=300)
    
    assert portfolio.positions[("upbit", "KRW-BTC")].quantity == 0.00
    assert portfolio.cash == (10000000 - 500250) + (600000 - 300)

@pytest.mark.asyncio
async def test_virtual_executor():
    portfolio = Portfolio(portfolio_id="sim_id", name="Simulation", initial_cash=1000000, exchange_id="upbit")
    # VirtualOrderExecutorAdapter 사용 및 fee_rate 주입 구조로 변경
    executor = VirtualOrderExecutorAdapter(fee_rate=0.0005)
    
    orderbook_data = {
        'asks': [[50000000, 1.0], [50100000, 1.0]],
        'bids': [[49900000, 1.0], [49800000, 1.0]]
    }
    
    # Market BUY (exchange="upbit" 누락 매개변수 주입, portfolio 인자 제거)
    result = await executor.execute_order(
        exchange="upbit",
        symbol="KRW-BTC",
        side="BUY",
        quantity=0.1,
        orderbook=orderbook_data
    )
    
    assert result is not None
    assert result['price'] == 50000000
    assert result['quantity'] == 0.1
    
    # 분리된 설계에 따라 포트폴리오가 반환받은 체결 정보로 자산을 업데이트하는지 수동 검증
    portfolio.update_position(
        exchange=result['exchange'],
        symbol=result['symbol'],
        side=result['side'],
        price=result['price'],
        quantity=result['quantity'],
        fee=result['fee']
    )
    assert portfolio.positions[("upbit", "KRW-BTC")].quantity == 0.1
    
    # Market SELL (portfolio 인자 제거)
    result = await executor.execute_order(
        exchange="upbit",
        symbol="KRW-BTC",
        side="SELL",
        quantity=0.05,
        orderbook=orderbook_data
    )
    
    assert result is not None
    assert result['price'] == 49900000
    
    # 분리된 설계에 따라 포트폴리오가 반환받은 체결 정보로 자산을 업데이트하는지 수동 검증
    portfolio.update_position(
        exchange=result['exchange'],
        symbol=result['symbol'],
        side=result['side'],
        price=result['price'],
        quantity=result['quantity'],
        fee=result['fee']
    )
    assert portfolio.positions[("upbit", "KRW-BTC")].quantity == 0.05

@pytest.mark.asyncio
async def test_portfolio_manager_handle_signal():
    # 🌟 DI 철학 적용: PortfolioManager에 격리된 테스트용 DB 주입!
    pm = PortfolioManager(db_path=TEST_DB_PATH)
    # upbit 신호는 내부적으로 'default' ID 포트폴리오로 우회되므로 'default'로 개설!
    portfolio = Portfolio(portfolio_id="default", name="Main Portfolio", initial_cash=1000000, exchange_id="upbit")
    pm.add_portfolio(portfolio)
    await pm.save_to_db("default")
    
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
    assert portfolio.positions[("upbit", "KRW-BTC")].quantity > 0
    
    # SELL signal handles all holdings (exchange="upbit" 누락 인자 주입)
    sell_signal = TradeSignal(exchange="upbit", symbol="KRW-BTC", action="SELL", price=50000000, reason="Test", interval=60)
    result = await pm.handle_signal("default", sell_signal, 50000000, orderbook_data)
    
    assert result is not None
    assert result['side'] == 'SELL'
    assert portfolio.positions[("upbit", "KRW-BTC")].quantity == 0

@pytest.mark.asyncio
async def test_portfolio_report_data_generation():
    """get_portfolio_current_prices 및 get_portfolio_report_data 메서드가 정상적으로 데이터를 빌드하는지 검증합니다."""
    pm = PortfolioManager(db_path=TEST_DB_PATH)
    portfolio = Portfolio(portfolio_id="test_report_id", name="Report Portfolio", initial_cash=1000000, exchange_id="upbit")
    pm.add_portfolio(portfolio)
    await pm.save_to_db("test_report_id")
    
    class DummySystem:
        def __init__(self):
            self.latest_prices = {}
            
    system = DummySystem()
    
    # 1. 가격 헬퍼 테스트
    prices = await pm.get_portfolio_current_prices("test_report_id", system)
    assert isinstance(prices, dict)
    
    # 2. 리포트 데이터 테스트
    report = await pm.get_portfolio_report_data("test_report_id", system)
    assert report["status"] == "success"
    assert report["id"] == "test_report_id"
    assert report["initial_cash"] == 1000000
    assert report["cash"] == 1000000
    assert report["summary"]["initial_cash"] == 1000000
    assert isinstance(report["results"], list)
