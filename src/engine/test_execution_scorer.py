import pytest
from typing import Dict, Any, Optional
from src.engine.execution_scorer import ExecutionScorer
from src.engine.portfolio import Portfolio, Position

class MockSignal:
    def __init__(self, symbol: str, action: str, exchange: str = "upbit", strategy_id: str = "test", reason: str = "test_reason"):
        self.symbol = symbol
        self.action = action
        self.exchange = exchange
        self.strategy_id = strategy_id
        self.reason = reason
        self.context = {}

@pytest.fixture
def scorer():
    return ExecutionScorer()

def test_calculate_position_size_buy_default_ratio(scorer):
    # 포트폴리오 준비 (현금 100만 원, exchange_id="upbit" 명시)
    portfolio = Portfolio(portfolio_id="test_port", name="Test Portfolio", initial_cash=1000000, exchange_id="upbit")
    signal = MockSignal("KRW-BTC", "BUY")
    
    # 1. context에 비율이 없는 경우 (디폴트 10% = 10만 원)
    qty, val = scorer.calculate_position_size(portfolio, signal, 50000000)
    assert val == 100000.0
    assert qty == 100000.0 / 50000000

def test_calculate_position_size_buy_explicit_weight(scorer):
    portfolio = Portfolio(portfolio_id="test_port", name="Test Portfolio", initial_cash=1000000, exchange_id="upbit")
    signal = MockSignal("KRW-BTC", "BUY")
    
    # 2. context에 명시적 weight가 지정된 경우 (20% = 20만 원)
    signal.context = {"weight": 0.2}
    qty, val = scorer.calculate_position_size(portfolio, signal, 50000000)
    assert val == 200000.0
    assert qty == 200000.0 / 50000000

def test_calculate_position_size_sell(scorer):
    portfolio = Portfolio(portfolio_id="test_port", name="Test Portfolio", initial_cash=1000000, exchange_id="upbit")
    # 3. SELL 신호 시 보유 수량 전량 반환
    portfolio.update_position("upbit", "KRW-BTC", "BUY", 50000000, 0.005, 125)
    signal_sell = MockSignal("KRW-BTC", "SELL")
    qty, val = scorer.calculate_position_size(portfolio, signal_sell, 50000000)
    assert qty == 0.005
    assert val == 0.005 * 50000000

def test_calculate_position_size_sell_no_position(scorer):
    portfolio = Portfolio(portfolio_id="test_port", name="Test Portfolio", initial_cash=1000000, exchange_id="upbit")
    signal_sell = MockSignal("KRW-BTC", "SELL")
    qty, val = scorer.calculate_position_size(portfolio, signal_sell, 50000000)
    assert qty == 0.0
    assert val == 0.0

def test_check_risk_limits_insufficient_cash(scorer):
    portfolio = Portfolio(portfolio_id="test_port", name="Test Portfolio", initial_cash=1000000, exchange_id="upbit")
    signal = MockSignal("KRW-BTC", "BUY")
    
    # 1. 잔고 부족 시나리오 (수수료율 0.05% 적용)
    # 소요현금: 1,000,000 * 1.0005 = 1,000,500원 > 보유현금: 1,000,000원
    passed, reason = scorer.check_risk_limits(
        portfolio=portfolio,
        signal=signal,
        price=50000000,
        qty=0.02,
        target_value=1000000,
        fee_rate=0.0005
    )
    assert not passed
    assert "잔고 부족" in reason

def test_check_risk_limits_max_weight_exceeded(scorer):
    portfolio = Portfolio(portfolio_id="test_port", name="Test Portfolio", initial_cash=1000000, exchange_id="upbit")
    signal = MockSignal("KRW-BTC", "BUY")
    
    # 2. 단일 종목 투자 한도(30%) 초과 시나리오 (수수료 여유있으나 한도 초과)
    # 100만 원 자산인데 한 종목에 40만 원 매수 요청
    passed, reason = scorer.check_risk_limits(
        portfolio=portfolio,
        signal=signal,
        price=50000000,
        qty=0.008,
        target_value=400000,
        fee_rate=0.0005
    )
    assert not passed
    assert "단일 종목 투자 한도" in reason

def test_check_risk_limits_passed(scorer):
    portfolio = Portfolio(portfolio_id="test_port", name="Test Portfolio", initial_cash=1000000, exchange_id="upbit")
    signal = MockSignal("KRW-BTC", "BUY")
    
    # 3. 리스크 필터 통과 시나리오 (20만 원 매수 요청, 20% 비중)
    passed, reason = scorer.check_risk_limits(
        portfolio=portfolio,
        signal=signal,
        price=50000000,
        qty=0.004,
        target_value=200000,
        fee_rate=0.0005
    )
    assert passed
    assert reason == ""

def test_check_risk_limits_disabled(scorer):
    portfolio = Portfolio(portfolio_id="test_port", name="Test Portfolio", initial_cash=1000000, exchange_id="upbit")
    signal = MockSignal("KRW-BTC", "BUY")
    
    # 4. 리스크 한도 비활성화 시 통과 보장
    passed, reason = scorer.check_risk_limits(
        portfolio=portfolio,
        signal=signal,
        price=50000000,
        qty=0.008,
        target_value=400000,
        fee_rate=0.0005,
        risk_limits_enabled=False
    )
    assert passed
    assert reason == ""

def test_apply_slippage_buy(scorer):
    # BUY: 0.1% 불리하게 상승
    signal_buy = MockSignal("KRW-BTC", "BUY")
    price_buy = scorer.apply_slippage(signal_buy, 1000.0, 0.001)
    assert price_buy == pytest.approx(1001.0)

def test_apply_slippage_sell(scorer):
    # SELL: 0.1% 불리하게 하락
    signal_sell = MockSignal("KRW-BTC", "SELL")
    price_sell = scorer.apply_slippage(signal_sell, 1000.0, 0.001)
    assert price_sell == pytest.approx(999.0)

def test_apply_slippage_zero_rate(scorer):
    signal_buy = MockSignal("KRW-BTC", "BUY")
    price_buy = scorer.apply_slippage(signal_buy, 1000.0, 0.0)
    assert price_buy == 1000.0
