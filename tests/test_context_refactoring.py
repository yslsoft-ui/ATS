import pytest
import asyncio
from typing import Dict, Any, List, Optional
from src.engine.candles import Candle
from src.engine.market_data_context import MarketDataContext
from src.engine.trade_engine import TradeEngine
from src.engine.strategy import BaseStrategy, TradeSignal, StrategyResult
from src.engine.portfolio import Portfolio, Position

# 더미 전략 생성
class DummyStrategy(BaseStrategy):
    def __init__(self, params: Dict[str, Any]):
        super().__init__(strategy_id="DummyStrategy", params=params)
        self.call_count = 0

    @property
    def required_indicators(self) -> List[str]:
        return []

    def on_update(self, context: Any) -> Optional[StrategyResult]:
        self.call_count += 1
        return StrategyResult(action="BUY", price=context.candles[-1].close, reason="Dummy trigger")

@pytest.mark.asyncio
async def test_market_data_context_candle_generation():
    # 1. 10초 봉을 생성하는 MarketDataContext 인스턴스화
    context = MarketDataContext(exchange_id="upbit", symbol="BTC", interval=10)
    
    # 2. 틱 주입 (아직 10초 경계가 안 지나서 마감 캔들 없음)
    closed = context.add_tick({
        "trade_price": 50000.0,
        "trade_volume": 1.0,
        "ask_bid": "BID",
        "trade_timestamp": 1600000000000  # 1600000000 초
    })
    assert len(closed) == 0
    assert len(context.candles) == 0

    # 3. 10초 경계를 넘어서는 틱 주입 (마감 발생)
    closed2 = context.add_tick({
        "trade_price": 50100.0,
        "trade_volume": 2.0,
        "ask_bid": "ASK",
        "trade_timestamp": 1600000011000  # 1600000011 초 (11초 경과)
    })
    assert len(closed2) == 1
    assert len(context.candles) == 1
    assert context.candles[0].close == 50000.0  # 이전 틱의 종가
    assert context.candles[0].volume == 1.0

@pytest.mark.asyncio
async def test_trade_engine_evaluation_only_on_snapshot():
    # 1. 60초 봉 전략으로 TradeEngine 구성
    dummy_strategy = DummyStrategy({"interval": 60})
    engine = TradeEngine(
        exchange_id="upbit",
        symbol="BTC",
        strategies=[dummy_strategy]
    )

    # 2. 첫 번째 틱 주입 (전략 실행되지 않아야 함)
    signals, closed = await engine.process_tick({
        "trade_price": 60000.0,
        "trade_volume": 1.5,
        "ask_bid": "BID",
        "trade_timestamp": 1700000000000
    }, None)
    assert len(closed) == 0
    assert len(signals) == 0
    assert dummy_strategy.call_count == 0

    # 3. 60초 이후의 틱 주입 (첫 캔들 마감 및 전략 1회 호출 유도)
    signals2, closed2 = await engine.process_tick({
        "trade_price": 60500.0,
        "trade_volume": 0.5,
        "ask_bid": "ASK",
        "trade_timestamp": 1700000061000
    }, None)
    assert len(closed2) == 1
    assert dummy_strategy.call_count == 1
    assert len(signals2) == 1
    assert signals2[0].action == "BUY"

@pytest.mark.asyncio
async def test_trade_engine_instant_common_exit_evaluation():
    # 1. 공통 청산 설정을 지닌 TradeEngine 구성
    # trailing_stop 등의 청산 규칙 활성화
    from src.config.manager import ConfigManager
    config_manager = ConfigManager()
    config_manager.config["system"] = {
        "exit_rules": {
            "stop_loss_pct": 2.0,  # 2% 손절 한도
            "trailing_stop_pct": 5.0
        }
    }
    
    dummy_strategy = DummyStrategy({"interval": 60})
    engine = TradeEngine(
        exchange_id="upbit",
        symbol="BTC",
        strategies=[dummy_strategy]
    )
    engine.exit_evaluator.config = config_manager.config

    # 2. 포지션 구축된 가상 포트폴리오 모킹
    portfolio = Portfolio(portfolio_id="test_port", name="test_port", portfolio_type="simulated")
    portfolio.exchange_cash = {"upbit": 100000000.0}
    portfolio.exchange_initial_cash = {"upbit": 100000000.0}
    # 60,000원에 1개 매수한 포지션
    portfolio.update_position("upbit", "BTC", "BUY", 60000.0, 1.0, 0.0)
    
    # 3. 틱 주입: 가격 폭락 (2% 손절선 58,800원 미만인 57,000원 주입)
    # 캔들 마감 시간 전이지만 (10초 경과), 틱 즉시 청산 평가가 일어나야 함
    from unittest import mock
    class SimplePortfolioManager:
        def __init__(self, portfolios):
            self.portfolios = portfolios
            self.repository = mock.AsyncMock()

    portfolio_manager_mock = SimplePortfolioManager({portfolio.id: portfolio})

    signals, closed = await engine.process_tick({
        "trade_price": 57000.0,
        "trade_volume": 0.1,
        "ask_bid": "ASK",
        "trade_timestamp": 1700000010000
    }, portfolio_manager_mock)

    # 캔들은 마감되지 않았어야 함
    assert len(closed) == 0
    # 손절 신호(SELL)가 즉시 검출되어야 함
    assert len(signals) == 1
    assert signals[0].action == "SELL"
    assert "Common Exit: STOP_LOSS" in signals[0].reason
