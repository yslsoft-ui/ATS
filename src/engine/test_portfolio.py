import pytest
import asyncio
from src.engine.portfolio import Portfolio, PortfolioManager, VirtualExecutor
from src.engine.trade_engine import TradeSignal

@pytest.mark.asyncio
async def test_portfolio_basic_operations():
    portfolio = Portfolio(portfolio_id="test_id", name="Test Portfolio", initial_cash=1000000)
    
    # Buy operation
    portfolio.update_position(symbol="KRW-BTC", side="BUY", price=50000000, quantity=0.01, fee=250)
    
    assert portfolio.positions["KRW-BTC"].quantity == 0.01
    assert portfolio.positions["KRW-BTC"].avg_price == 50000000
    assert portfolio.cash == 1000000 - (500000 + 250)
    
    # Sell operation
    portfolio.update_position(symbol="KRW-BTC", side="SELL", price=60000000, quantity=0.01, fee=300)
    
    assert portfolio.positions["KRW-BTC"].quantity == 0.00
    assert portfolio.cash == 499750 + (600000 - 300)

@pytest.mark.asyncio
async def test_virtual_executor():
    portfolio = Portfolio(portfolio_id="sim_id", name="Simulation", initial_cash=1000000)
    executor = VirtualExecutor(fee_rate=0.0005)
    
    orderbook_data = {
        'asks': [[50000000, 1.0], [50100000, 1.0]],
        'bids': [[49900000, 1.0], [49800000, 1.0]]
    }
    
    # Market BUY
    result = await executor.execute_order(
        portfolio=portfolio,
        symbol="KRW-BTC",
        side="BUY",
        quantity=0.1,
        orderbook=orderbook_data
    )
    
    assert result is not None
    assert result['price'] == 50000000
    assert result['quantity'] == 0.1
    assert portfolio.positions["KRW-BTC"].quantity == 0.1
    
    # Market SELL
    result = await executor.execute_order(
        portfolio=portfolio,
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
    pm = PortfolioManager()
    portfolio = Portfolio(portfolio_id="p1", name="Main Portfolio", initial_cash=1000000)
    pm.add_portfolio(portfolio)
    
    signal = TradeSignal(symbol="KRW-BTC", action="BUY", price=50000000, reason="Test", interval=60)
    orderbook_data = {
        'asks': [[50000000, 10.0]],
        'bids': [[49000000, 10.0]]
    }
    
    # BUY signal handles 10% of cash
    result = await pm.handle_signal("p1", signal, orderbook_data)
    
    assert result is not None
    assert result['side'] == 'BUY'
    assert portfolio.positions["KRW-BTC"].quantity > 0
    
    # SELL signal handles all holdings
    sell_signal = TradeSignal(symbol="KRW-BTC", action="SELL", price=50000000, reason="Test", interval=60)
    result = await pm.handle_signal("p1", sell_signal, orderbook_data)
    
    assert result is not None
    assert result['side'] == 'SELL'
    assert portfolio.positions["KRW-BTC"].quantity == 0
