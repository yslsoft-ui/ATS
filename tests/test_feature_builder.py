# -*- coding: utf-8 -*-

import pytest
import time
import math
from typing import Dict, List, Optional, Any
from src.engine.feature_builder import FeatureBuilder, FeatureBuildRequest, Clock
from src.database.repository import InMemoryMarketDataRepository
from src.engine.market_data_context import MarketDataContext
from src.engine.candles import Candle
from src.engine.trade_engine import TradeEngine
from src.engine.strategy import BaseStrategy, StrategyResult, StrategyType, StrategyRegistry


class MockConfigManager:
    def __init__(self, config_dict: Dict[str, Any]):
        self.config_dict = config_dict

    def get(self, key: str, default: Any = None) -> Any:
        parts = key.split('.')
        val = self.config_dict
        for part in parts:
            if isinstance(val, dict):
                val = val.get(part)
            else:
                return default
        return val if val is not None else default


class DummyStrategy(BaseStrategy):
    type = StrategyType.ENTRY
    default_params = {"interval": 60}

    def __init__(self, strategy_id: str = "dummy_strat", params: Dict[str, Any] = None):
        super().__init__(strategy_id, params)

    def on_update(self, context: Any) -> Optional[StrategyResult]:
        return StrategyResult(action="HOLD")


class DummyHost:
    def __init__(self, strategy_id: str, interval: int = 60):
        self.strategy = DummyStrategy(strategy_id=strategy_id)
        self.interval = interval


@pytest.mark.asyncio
async def test_clock_and_freshness_ttl():
    # 1. 기획 및 설정 준비
    # 시스템 clock을 2026-06-10 19:00:00 KST (1781114400초)로 설정
    base_time = 1781114400.0
    clock = Clock(start_time=base_time)
    
    repo = InMemoryMarketDataRepository()
    config_dict = {
        "system": {
            "freshness_ttl": {
                "crypto": {"trade": 10, "indicator": 60},
                "stock": {"trade": 5, "indicator": 30}
            }
        }
    }
    config_manager = MockConfigManager(config_dict)
    builder = FeatureBuilder(market_data_repo=repo, config_manager=config_manager, clock=clock)

    # Mock context와 host 준비
    host = DummyHost("dummy_strat", interval=60)
    context = MarketDataContext("mock_exchange", "BTC", 60)
    
    # 2. Case A: 틱이 없음, 캔들이 없음 -> Fresh 아님
    req_empty = FeatureBuildRequest(hosts=[host], contexts={60: context})
    snapshot_empty = await builder.capture_feature_snapshot(
        proposal_id="p-1",
        strategy_id="dummy_strat",
        exchange_id="mock_exchange",
        symbol="BTC",
        proposal_type="CRYPTO",
        request=req_empty
    )
    assert snapshot_empty.is_fresh is False
    assert "NO_TICK_RECEIVED" in snapshot_empty.stale_reason
    assert "NO_CANDLES" in snapshot_empty.stale_reason

    # 3. Case B: 틱과 캔들이 존재하며, 아주 신선함 (1초 전)
    tick_time_ms = int((base_time - 1) * 1000)
    repo.add_trade("mock_exchange", "BTC", {
        "trade_price": 50000.0,
        "trade_volume": 0.5,
        "ask_bid": "BID",
        "trade_timestamp": tick_time_ms
    })
    
    # 캔들 추가
    candle_time_s = int(base_time - 5)
    candle = Candle(
        exchange_id="mock_exchange",
        symbol="BTC",
        interval=60,
        timestamp=candle_time_s,
        open=49900.0,
        high=50100.0,
        low=49800.0,
        close=50000.0,
        volume=10.0,
        is_closed=True
    )
    context.add_candle(candle)

    snapshot_fresh = await builder.capture_feature_snapshot(
        proposal_id="p-2",
        strategy_id="dummy_strat",
        exchange_id="mock_exchange",
        symbol="BTC",
        proposal_type="CRYPTO",
        request=req_empty
    )
    assert snapshot_fresh.is_fresh is True
    assert snapshot_fresh.stale_reason == ""

    # 4. Case C: 틱 TTL 초과 (12초 전 체결, TTL은 10초)
    clock.set_time(base_time + 12)
    snapshot_stale_tick = await builder.capture_feature_snapshot(
        proposal_id="p-3",
        strategy_id="dummy_strat",
        exchange_id="mock_exchange",
        symbol="BTC",
        proposal_type="CRYPTO",
        request=req_empty
    )
    assert snapshot_stale_tick.is_fresh is False
    assert "TICK_TTL_EXCEEDED" in snapshot_stale_tick.stale_reason

    # 5. Case D: 캔들 지표 TTL 초과 (80초 전 캔들, TTL은 60초)
    # clock을 75초 뒤로 밀어 캔들(base_time - 5s)이 80초 전 캔들이 되도록 함
    clock.set_time(base_time + 75)
    # 틱은 1초 전으로 다시 추가
    tick_time_ms_new = int((clock.now() - 1) * 1000)
    repo.add_trade("mock_exchange", "BTC", {
        "trade_price": 50100.0,
        "trade_volume": 0.5,
        "ask_bid": "BID",
        "trade_timestamp": tick_time_ms_new
    })
    
    snapshot_stale_indicator = await builder.capture_feature_snapshot(
        proposal_id="p-4",
        strategy_id="dummy_strat",
        exchange_id="mock_exchange",
        symbol="BTC",
        proposal_type="CRYPTO",
        request=req_empty
    )
    assert snapshot_stale_indicator.is_fresh is False
    assert "INDICATOR_STALE" in snapshot_stale_indicator.stale_reason


@pytest.mark.asyncio
async def test_liquidity_proxy_calculation():
    # 20분 이내 틱들의 유동성 프록시 요약이 올바르게 수치 계산되는지 검증
    base_time = 1781114400.0
    clock = Clock(start_time=base_time)
    repo = InMemoryMarketDataRepository()
    
    # 20분 = 1200초 이내에 틱 3개 추가 (10초 전, 30초 전, 50초 전)
    # 가격: 100.0, 볼륨: 2.0 -> value: 200.0
    # 가격: 101.0, 볼륨: 3.0 -> value: 303.0
    # 가격: 99.0, 볼륨: 5.0 -> value: 495.0
    # 전체 볼륨: 10.0, 전체 가치: 998.0
    repo.add_trade("mock_exchange", "BTC", {"trade_price": 100.0, "trade_volume": 2.0, "ask_bid": "ASK", "trade_timestamp": int((base_time - 10) * 1000)})
    repo.add_trade("mock_exchange", "BTC", {"trade_price": 101.0, "trade_volume": 3.0, "ask_bid": "BID", "trade_timestamp": int((base_time - 30) * 1000)})
    repo.add_trade("mock_exchange", "BTC", {"trade_price": 99.0, "trade_volume": 5.0, "ask_bid": "ASK", "trade_timestamp": int((base_time - 50) * 1000)})

    # 20분 밖의 틱 추가 (1300초 전) -> 유동성 프록시 계산에서 제외되어야 함
    repo.add_trade("mock_exchange", "BTC", {"trade_price": 200.0, "trade_volume": 100.0, "ask_bid": "ASK", "trade_timestamp": int((base_time - 1300) * 1000)})

    host = DummyHost("dummy_strat", interval=60)
    context = MarketDataContext("mock_exchange", "BTC", 60)
    # 캔들도 추가하여 NO_CANDLES 방지
    context.add_candle(Candle(exchange_id="mock_exchange", symbol="BTC", interval=60, timestamp=int(base_time - 10), open=100.0, high=101.0, low=99.0, close=100.0, volume=10.0, is_closed=True))

    builder = FeatureBuilder(market_data_repo=repo, clock=clock)
    req = FeatureBuildRequest(hosts=[host], contexts={60: context})
    
    snapshot = await builder.capture_feature_snapshot(
        proposal_id="p-5",
        strategy_id="dummy_strat",
        exchange_id="mock_exchange",
        symbol="BTC",
        proposal_type="CRYPTO",
        request=req
    )

    # 볼륨 검증 (2.0 + 3.0 + 5.0 = 10.0)
    assert snapshot.liquidity_features["volume"] == pytest.approx(10.0)
    # 가치 검증 (200.0 + 303.0 + 495.0 = 998.0)
    assert snapshot.liquidity_features["value"] == pytest.approx(998.0)
    # TPS 검증 (3 ticks / 1200 seconds = 0.0025)
    assert snapshot.liquidity_features["tps"] == pytest.approx(0.0025)
    
    # 평균 인터벌 (10초-30초 차이인 20초, 30초-50초 차이인 20초의 평균 -> 20.0초)
    assert snapshot.liquidity_features["idle_time"] == pytest.approx(20.0)


@pytest.mark.asyncio
async def test_hash_determinism():
    # 동일한 입력 조건 하에 feature_hash와 feature_vector_hash가 항상 결정론적으로 100% 일치함을 증증
    base_time = 1781114400.0
    clock = Clock(start_time=base_time)
    
    # Repo 1
    repo1 = InMemoryMarketDataRepository()
    repo1.add_trade("mock_exchange", "BTC", {"trade_price": 500.0, "trade_volume": 1.5, "ask_bid": "ASK", "trade_timestamp": int((base_time - 10) * 1000)})
    context1 = MarketDataContext("mock_exchange", "BTC", 60)
    context1.add_candle(Candle(exchange_id="mock_exchange", symbol="BTC", interval=60, timestamp=int(base_time - 10), open=490.0, high=510.0, low=485.0, close=500.0, volume=15.0, is_closed=True))
    
    builder1 = FeatureBuilder(market_data_repo=repo1, clock=clock)
    req1 = FeatureBuildRequest(hosts=[DummyHost("dummy_strat")], contexts={60: context1})

    # Repo 2 (완전히 동일한 상태의 별개 데이터 셋)
    repo2 = InMemoryMarketDataRepository()
    repo2.add_trade("mock_exchange", "BTC", {"trade_price": 500.0, "trade_volume": 1.5, "ask_bid": "ASK", "trade_timestamp": int((base_time - 10) * 1000)})
    context2 = MarketDataContext("mock_exchange", "BTC", 60)
    context2.add_candle(Candle(exchange_id="mock_exchange", symbol="BTC", interval=60, timestamp=int(base_time - 10), open=490.0, high=510.0, low=485.0, close=500.0, volume=15.0, is_closed=True))
    
    builder2 = FeatureBuilder(market_data_repo=repo2, clock=clock)
    req2 = FeatureBuildRequest(hosts=[DummyHost("dummy_strat")], contexts={60: context2})

    snapshot1 = await builder1.capture_feature_snapshot("prop-1", "dummy_strat", "mock_exchange", "BTC", "CRYPTO", req1)
    snapshot2 = await builder2.capture_feature_snapshot("prop-1", "dummy_strat", "mock_exchange", "BTC", "CRYPTO", req2)

    # 해시 결정성 확인
    assert snapshot1.feature_hash == snapshot2.feature_hash
    assert snapshot1.feature_vector_hash == snapshot2.feature_vector_hash
    assert snapshot1.snapshot_hash == snapshot2.snapshot_hash


@pytest.mark.asyncio
async def test_trade_engine_delegation_integration():
    # TradeEngine.capture_feature_snapshot이 FeatureBuilder에 올바르게 연동 위임되는지 검증
    # 1. Mock 전략 정의
    @StrategyRegistry.register
    class MockEngineTestStrategy(BaseStrategy):
        type = StrategyType.ENTRY
        default_params = {"interval": 60}
        def __init__(self, strategy_id: str = "mockengineteststrategy", params: Dict[str, Any] = None):
            super().__init__(strategy_id, params)
        def on_update(self, context: Any) -> Optional[StrategyResult]:
            return None

    strategy = MockEngineTestStrategy(strategy_id="mockengineteststrategy")

    # 2. InMemory 레포 생성 및 틱/캔들 준비
    repo = InMemoryMarketDataRepository()
    now_ms = int(time.time() * 1000)
    repo.add_trade("mock_exchange", "BTC", {
        "trade_price": 1000.0,
        "trade_volume": 1.0,
        "ask_bid": "BID",
        "trade_timestamp": now_ms
    })
    
    # 3. TradeEngine 초기화
    engine = TradeEngine(
        exchange_id="mock_exchange",
        symbol="BTC",
        strategies=[strategy],
        market_data_repo=repo
    )
    
    # 캔들 1개 주입 (TradeEngine의 context에 캔들 존재해야 Fresh로 판정됨)
    candle = Candle(
        exchange_id="mock_exchange",
        symbol="BTC",
        interval=60,
        timestamp=int(now_ms // 1000 - 5),
        open=1000.0,
        high=1005.0,
        low=995.0,
        close=1000.0,
        volume=10.0,
        is_closed=True
    )
    engine.contexts[60].add_candle(candle)
    # TradeEngine.last_tick 갱신
    engine.last_tick = {
        "trade_price": 1000.0,
        "trade_volume": 1.0,
        "ask_bid": "BID",
        "trade_timestamp": now_ms
    }

    # 4. capture_feature_snapshot 시그니처 호출 검증
    snapshot = await engine.capture_feature_snapshot(
        proposal_id="proposal-xyz",
        strategy_id="mockengineteststrategy",
        exchange_id="mock_exchange",
        symbol="BTC",
        proposal_type="CRYPTO"
    )

    # 5. 위임된 결과 검증
    assert snapshot.exchange_id == "mock_exchange"
    assert snapshot.price_features["close"] == 1000.0
    assert snapshot.is_fresh is True
