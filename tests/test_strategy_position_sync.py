import pytest
import time
from typing import Dict, List, Any, Optional
from src.engine.portfolio import Position, Portfolio
from src.engine.trade_engine import TradeEngine
from src.engine.strategy import BaseStrategy, StrategyResult, StrategyType
from src.engine.candles import Candle

class MockStrategy(BaseStrategy):
    def __init__(self, strategy_id="MockStrategy"):
        super().__init__(strategy_id, {"interval": 60})
        self.type = StrategyType.BOTH
        self.in_position = False
        self.buy_price = None
        self.peak_price = None
        self.entry_time = None
        self.enabled = True
        # 테스트를 위해 강제로 지정할 액션
        self.force_action = "HOLD"
        
    def on_update(self, context) -> StrategyResult:
        if self.force_action == "BUY":
            # 실제 전략처럼 포지션 진입 처리를 모사
            self.in_position = True
            self.buy_price = context.last_candle.close if context.last_candle else 0.0
            self.peak_price = self.buy_price
            return StrategyResult(action="BUY", price=self.buy_price)
        elif self.force_action == "SELL":
            self.in_position = False
            return StrategyResult(action="SELL", price=context.last_candle.close if context.last_candle else 0.0)
        return StrategyResult(action="HOLD")

    def _reset_position_state(self):
        self.in_position = False
        self.buy_price = None
        self.peak_price = None
        self.entry_time = None

class MockPortfolioManager:
    def __init__(self):
        self.portfolios = {}
        self.repository = MockRepository()

    def add_portfolio(self, portfolio):
        self.portfolios[portfolio.id] = portfolio

    def get_portfolio_summary(self, symbol: str, portfolio_id: str = "default", exchange_id: Optional[str] = None) -> Dict[str, Any]:
        portfolio = self.portfolios.get(portfolio_id)
        if not portfolio:
            return {"cash": 0.0, "quantity": 0.0, "avg_price": 0.0}
        ex_key = exchange_id.lower() if exchange_id else ""
        pos = portfolio.positions.get((ex_key, symbol))
        cash_val = portfolio.exchange_cash.get(ex_key, 0.0)
        return {
            "cash": cash_val,
            "quantity": pos.quantity if pos else 0.0,
            "avg_price": pos.avg_price if pos else 0.0
        }

class MockRepository:
    def __init__(self):
        self.saved_portfolios = []

    async def save_portfolio(self, portfolio):
        self.saved_portfolios.append(portfolio)

def make_candle(close: float, timestamp: int) -> Candle:
    return Candle(
        exchange_id="bithumb",
        symbol="D",
        interval=60,
        timestamp=timestamp,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=100.0,
        is_closed=True
    )

@pytest.mark.asyncio
async def test_strategy_position_sync_after_warmup():
    """TradeEngine 워밍업 혹은 동기화 호출 시 포지션 상태가 전략 인메모리에 제대로 반영되는지 테스트"""
    strategy = MockStrategy()
    engine = TradeEngine(exchange_id="bithumb", symbol="D", strategies=[strategy])
    
    pm = MockPortfolioManager()
    portfolio = Portfolio("2012", "Bithumb Simulation")
    portfolio.exchange_cash = {"bithumb": 1000000.0}
    portfolio.exchange_initial_cash = {"bithumb": 1000000.0}
    
    # 1. 포지션이 없는 상태에서 동기화 -> 전략의 in_position은 False 유지
    engine.sync_position_state(pm)
    assert strategy.in_position is False

    # 2. 포트폴리오에 D 종목의 포지션 잔고 추가 (평단가 6.60)
    portfolio.update_position("bithumb", "D", "BUY", 6.60, 100.0, fee=0.0)
    pm.add_portfolio(portfolio)
    
    # 3. 동기화 재수행 -> 전략의 in_position이 True로 갱신되고 buy_price가 6.60으로 동기화되어야 함
    engine.sync_position_state(pm)
    assert strategy.in_position is True
    assert strategy.buy_price == 6.60
    assert strategy.peak_price == 6.60

    # 4. 포지션을 청산한 상태 시뮬레이션
    portfolio.positions.clear()
    engine.sync_position_state(pm)
    assert strategy.in_position is False
    assert strategy.buy_price is None

@pytest.mark.asyncio
async def test_duplicate_buy_signal_blocked_when_position_exists():
    """이미 포지션을 보유하고 있을 때 전략이 오작동하여 BUY 신호를 보내도 TradeEngine에서 차단하는지 테스트"""
    strategy = MockStrategy()
    strategy.force_action = "BUY"
    engine = TradeEngine(exchange_id="bithumb", symbol="D", strategies=[strategy])
    
    pm = MockPortfolioManager()
    portfolio = Portfolio("2012", "Bithumb Simulation")
    portfolio.exchange_cash = {"bithumb": 1000000.0}
    portfolio.exchange_initial_cash = {"bithumb": 1000000.0}
    pm.add_portfolio(portfolio)
    
    # Context에 기초 캔들 채우기
    now_ts = int(time.time() * 1000)
    # CandleGenerator의 인터벌 계산(timestamp // interval)에 영향을 주지 않도록 정렬된 타임스탬프 사용
    # 초 단위로 60초 간격인 시작 시간들 설정
    base_s = (now_ts // 1000 // 60) * 60
    
    for i in range(5):
        candle = make_candle(6.5 + i * 0.05, (base_s - (5 - i) * 60))
        engine.contexts[60].add_candle(candle)

    # 1. 캔들 생성을 위해 첫 번째 틱 주입 (인터벌 60초 전)
    tick_init = {
        "trade_price": 6.55,
        "trade_volume": 10.0,
        "ask_bid": "BID",
        "trade_timestamp": (base_s - 60) * 1000
    }
    await engine.process_tick(tick_init, pm)

    # 2. 포지션이 없는 상황에서 새로운 분(minute)의 틱 주입 -> 캔들이 닫히며 BUY 신호가 발생해야 함
    tick1 = {
        "trade_price": 6.60,
        "trade_volume": 10.0,
        "ask_bid": "BID",
        "trade_timestamp": base_s * 1000
    }
    signals, _ = await engine.process_tick(tick1, pm)
    assert len(signals) == 1
    assert signals[0].action == "BUY"
    assert signals[0].symbol == "D"
    
    # 3. 포트폴리오에 포지션 잔고 추가 (진입 완료 상황 모사)
    portfolio.update_position("bithumb", "D", "BUY", 6.60, 100.0, fee=0.0)
    engine.sync_position_state(pm)
    assert strategy.in_position is True

    # 4. 포지션이 있는 상황에서 다시 틱이 유입되고 전략이 BUY 신호를 보낼 때 -> BUY 신호가 강제 차단(Skip)되어야 함
    # 새로운 캔들을 완성시키기 위해 다시 60초 경과한 틱 주입
    tick2 = {
        "trade_price": 6.70,
        "trade_volume": 10.0,
        "ask_bid": "BID",
        "trade_timestamp": (base_s + 60) * 1000
    }
    signals, _ = await engine.process_tick(tick2, pm)
    # signals는 비어 있어야 함 (BUY가 차단되었으므로)
    assert len(signals) == 0
