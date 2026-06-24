import pytest
import asyncio
import os
from typing import Dict, List, Any, Optional

from src.engine.replay_runner import TickReplayRunner, BacktestPortfolioManagerProxy
from src.engine.portfolio import PortfolioManager, Portfolio, VirtualOrderExecutorAdapter
from src.engine.pipeline import ExecutionPipeline
from src.engine.trade_engine import TradeEngine
from src.engine.strategy import BaseStrategy, TradeSignal, StrategyResult
from src.database.schema import init_db

TEST_DB_PATH = "test_replay_runner.db"

@pytest.fixture(autouse=True)
def setup_test_db():
    """테스트용 임시 DB를 셋업하고 종료 시 청소합니다."""
    import asyncio
    asyncio.run(init_db(TEST_DB_PATH))
    yield
    for ext in ["", "-wal", "-shm"]:
        path = TEST_DB_PATH + ext
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass

# 테스트를 위한 더미 전략 정의
class DummyStrategy(BaseStrategy):
    def __init__(self, strategy_id: str = "DummyStrategy", params: Dict[str, Any] = None):
        # interval 기본값을 1로 주어 틱마다 캔들이 닫히기 쉽게 구성
        actual_params = {"interval": 1}
        if params:
            actual_params.update(params)
        super().__init__(strategy_id, actual_params)
        self.tick_count = 0

    def on_update(self, context: Any) -> Optional[StrategyResult]:
        self.tick_count += 1
        # 특정 캔들 업데이트 횟수 때 매수/매도 신호 생성
        if self.tick_count == 2:
            return StrategyResult(
                action="BUY",
                price=50100000.0,
                reason="buy_signal_tick_2",
                context={"weight": 0.2}
            )
        elif self.tick_count == 4:
            return StrategyResult(
                action="SELL",
                price=49900000.0,
                reason="sell_signal_tick_4"
            )
        return StrategyResult(action="HOLD")

# 무상태성 검증을 위한 커스텀 더미 전략
class DummyStatelessStrategy(BaseStrategy):
    def __init__(self, strategy_id: str = "DummyStatelessStrategy", params: Dict[str, Any] = None):
        super().__init__(strategy_id, {"interval": 1})
        self.tick_count = 0

    def on_update(self, context: Any) -> Optional[StrategyResult]:
        self.tick_count += 1
        if self.tick_count == 1:
            return StrategyResult(
                action="BUY",
                price=3050000.0,
                reason="buy_immediate"
            )
        return StrategyResult(action="HOLD")

@pytest.mark.asyncio
async def test_replay_runner_equivalence():
    """
    기존에 backtest.py 내에 구현되어 있던 리플레이 루프 동작과
    분리 신설된 TickReplayRunner를 통한 리플레이 동작이
    동일한 틱 입력 및 환경에서 100% 동일한 결과를 산출하는지 교차 검증합니다.
    """
    pm = PortfolioManager(db_path=TEST_DB_PATH)
    virtual_executor = VirtualOrderExecutorAdapter(fee_rate=0.0005, sell_tax_pct=0.0)
    pm.executors['simulation'] = virtual_executor
    pm.executors['simulation_upbit'] = virtual_executor
    
    from unittest.mock import AsyncMock, Mock
    mock_ns = Mock()
    mock_ns.publish = AsyncMock(return_value=True)
    execution_pipeline = ExecutionPipeline(pm, notification_service=mock_ns)

    # 1초(1000ms) 간격의 모의 틱 데이터 (rows)
    # interval=1 이므로 1초 이상 차이가 나면 캔들이 닫힙니다.
    raw_rows = [
        {"trade_price": 50000000.0, "trade_volume": 0.1, "ask_bid": "BID", "trade_timestamp": 1600000000000},
        {"trade_price": 50100000.0, "trade_volume": 0.2, "ask_bid": "ASK", "trade_timestamp": 1600000002000},
        {"trade_price": 50200000.0, "trade_volume": 0.15, "ask_bid": "BID", "trade_timestamp": 1600000004000},
        {"trade_price": 49900000.0, "trade_volume": 0.3, "ask_bid": "ASK", "trade_timestamp": 1600000006000},
        {"trade_price": 50000000.0, "trade_volume": 0.1, "ask_bid": "BID", "trade_timestamp": 1600000008000},
    ]

    # --- 1. 리팩토링 이전 방식의 루프 수행 ---
    port_before = Portfolio(portfolio_id="port_before", name="Before Refactoring", portfolio_type="backtest")
    port_before.exchange_cash["upbit"] = 10000000.0
    port_before.exchange_initial_cash["upbit"] = 10000000.0
    pm.add_portfolio(port_before)
    await pm.save_to_db("port_before")

    strategy_before = DummyStrategy(strategy_id="DummyBefore")
    engine_before = TradeEngine("upbit", "KRW-BTC", [strategy_before])
    proxy_before = BacktestPortfolioManagerProxy(pm, "port_before")

    candle_history_before = []
    # 기존 backtest.py run 메서드의 리플레이 루프 복제
    for row in raw_rows:
        tick = {
            "trade_price": row["trade_price"],
            "trade_volume": row["trade_volume"],
            "ask_bid": row["ask_bid"],
            "trade_timestamp": row["trade_timestamp"]
        }
        
        signals, closed_candles = await engine_before.process_tick(tick, proxy_before)
        for c in closed_candles:
            candle_history_before.append({
                "time": c.timestamp,
                "open": c.open,
                "high": c.high,
                "low": c.low,
                "close": c.close,
                "volume": c.volume
            })
        
        for sig in signals:
            await execution_pipeline.process_signal(
                signal=sig,
                price=tick["trade_price"],
                portfolio_id="port_before",
                risk_limits_enabled=True,
                slippage_rate=0.001,
                size_ratio=0.95
            )

    # --- 2. 신규 TickReplayRunner를 통한 루프 수행 ---
    port_after = Portfolio(portfolio_id="port_after", name="After Refactoring", portfolio_type="backtest")
    port_after.exchange_cash["upbit"] = 10000000.0
    port_after.exchange_initial_cash["upbit"] = 10000000.0
    pm.add_portfolio(port_after)
    await pm.save_to_db("port_after")

    strategy_after = DummyStrategy(strategy_id="DummyAfter")
    engine_after = TradeEngine("upbit", "KRW-BTC", [strategy_after])
    proxy_after = BacktestPortfolioManagerProxy(pm, "port_after")

    ticks_normalized = [
        {
            "exchange_id": "upbit",
            "symbol": "KRW-BTC",
            "trade_price": r["trade_price"],
            "trade_volume": r["trade_volume"],
            "ask_bid": r["ask_bid"],
            "trade_timestamp": r["trade_timestamp"]
        }
        for r in raw_rows
    ]
    engines_map = {"upbit_KRW-BTC": engine_after}

    runner = TickReplayRunner(
        portfolio_id="port_after",
        execution_pipeline=execution_pipeline,
        size_ratio=0.95,
        risk_limits_enabled=True,
        slippage_rate=0.001
    )

    replay_result = await runner.run(ticks_normalized, engines_map, proxy_after)
    candle_history_after = replay_result["candle_histories"].get("upbit_KRW-BTC", [])

    # --- 3. 결과 100% 동일성 검증 ---
    # 캔들 히스토리 비교
    assert candle_history_before == candle_history_after

    # 포트폴리오 평가액 비교
    current_prices = {("upbit", "KRW-BTC"): raw_rows[-1]["trade_price"]}
    val_before = port_before.get_total_value(current_prices)
    val_after = port_after.get_total_value(current_prices)
    assert val_before == val_after

    # 히스토리(체결 기록) 비교
    assert len(port_before.history) == len(port_after.history)
    for h_bef, h_aft in zip(port_before.history, port_after.history):
        assert h_bef["side"] == h_aft["side"]
        assert h_bef["price"] == h_aft["price"]
        assert h_bef["quantity"] == h_aft["quantity"]
        assert h_bef["fee"] == h_aft["fee"]
        assert h_bef["timestamp"] == h_aft["timestamp"]
        assert h_bef["reason"] == h_aft["reason"]


@pytest.mark.asyncio
async def test_replay_runner_statelessness():
    """
    TickReplayRunner가 DB나 파일 등 외부 전역 상태에 직접 접근하지 않고,
    주입받은 pipeline, portfolio, engines, ticks 데이터를 통해서만
    정확히 독립적으로 동작함을 검증합니다.
    """
    # 임시 목업 execution pipeline
    class MockPipeline:
        def __init__(self):
            self.processed_signals = []

        async def process_signal(self, signal, price, portfolio_id, risk_limits_enabled, slippage_rate, size_ratio):
            self.processed_signals.append({
                "signal": signal,
                "price": price,
                "portfolio_id": portfolio_id,
                "risk_limits_enabled": risk_limits_enabled,
                "slippage_rate": slippage_rate,
                "size_ratio": size_ratio
            })

    mock_pipeline = MockPipeline()
    
    # 틱 데이터
    ticks = [
        {"exchange_id": "bithumb", "symbol": "ETH", "trade_price": 3000000.0, "trade_volume": 1.0, "ask_bid": "BID", "trade_timestamp": 1000},
        {"exchange_id": "bithumb", "symbol": "ETH", "trade_price": 3050000.0, "trade_volume": 1.5, "ask_bid": "ASK", "trade_timestamp": 3000},
    ]

    strategy = DummyStatelessStrategy()
    engine = TradeEngine("bithumb", "ETH", [strategy])
    engines = {"bithumb_ETH": engine}
    
    # proxy manager 목업
    class MockProxyManager:
        def get_portfolio_summary(self, symbol, portfolio_id=None, exchange_id=None):
            return {"cash": 5000000.0, "positions": {}}

    proxy = MockProxyManager()

    runner = TickReplayRunner(
        portfolio_id="stateless_test_portfolio",
        execution_pipeline=mock_pipeline,
        size_ratio=0.5,
        risk_limits_enabled=False,
        slippage_rate=0.0
    )

    result = await runner.run(ticks, engines, proxy)
    
    # 검증: 첫 번째 캔들이 닫히는 두 번째 틱에서 DummyStatelessStrategy(tick_count=1)에 의해 BUY 신호 발생
    assert len(mock_pipeline.processed_signals) == 1
    processed = mock_pipeline.processed_signals[0]
    assert processed["portfolio_id"] == "stateless_test_portfolio"
    assert processed["price"] == 3050000.0
    assert processed["size_ratio"] == 0.5
    assert processed["risk_limits_enabled"] is False
    assert processed["signal"].symbol == "ETH"
    assert processed["signal"].action == "BUY"
    
    # last_prices 검증
    assert result["last_prices"] == {("bithumb", "ETH"): 3050000.0}
