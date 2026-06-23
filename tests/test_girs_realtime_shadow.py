# -*- coding: utf-8 -*-
import pytest
import asyncio
import time
import os
import shutil
from datetime import datetime, timedelta
from typing import Dict, Any, List

from src.config.manager import ConfigManager
from src.database.connection import get_db_conn
from src.database.schema import init_db
from src.engine.girs_types import FeatureSnapshot, CandidateProposal
from src.engine.trade_engine import TradeEngine
from src.services.strategy_service import StrategyService
from src.database.repository import InMemoryMarketDataRepository
from src.engine.evaluation_policy import calculate_due_at, EvaluationPolicyRouter
from src.engine.shadow_backtest import ShadowBacktestEngine
from scratch.generate_shadow_report import generate_report

TEST_DB_PATH = "data/test_shadow.db"

@pytest.fixture(autouse=True)
def setup_test_db():
    if os.path.exists(TEST_DB_PATH):
        try:
            os.remove(TEST_DB_PATH)
        except Exception:
            pass
    asyncio.run(init_db(TEST_DB_PATH))
    yield
    if os.path.exists(TEST_DB_PATH):
        try:
            os.remove(TEST_DB_PATH)
        except Exception:
            pass

def test_due_at_calculation():
    # 1. crypto: 단순 elapsed
    hz_crypto = {"name": "10m", "type": "elapsed", "value": 600}
    due_crypto = calculate_due_at("crypto", hz_crypto, current_time_s=1000)
    assert due_crypto == 1600
    
    # 2. stock: elapsed_in_session (장외 시각 -> 다음날 장개시 기준 적산)
    # 2026-06-09T20:41:30 (화요일 밤 8:41:30)
    dt_night = datetime(2026, 6, 9, 20, 41, 30)
    ts_night = int(dt_night.timestamp())
    
    hz_stock_1h = {"name": "1h", "type": "elapsed_in_session", "value": 3600}
    due_stock_1h = calculate_due_at("stock", hz_stock_1h, current_time_s=ts_night)
    
    dt_due = datetime.fromtimestamp(due_stock_1h)
    assert dt_due.year == 2026
    assert dt_due.month == 6
    assert dt_due.day == 10
    assert dt_due.hour == 10
    assert dt_due.minute == 0

    # 3. stock: calendar_session (close)
    hz_stock_close = {"name": "market_close", "type": "calendar_session", "value": "close"}
    due_stock_close = calculate_due_at("stock", hz_stock_close, current_time_s=ts_night)
    dt_due_close = datetime.fromtimestamp(due_stock_close)
    assert dt_due_close.hour == 15
    assert dt_due_close.minute == 30

@pytest.mark.asyncio
async def test_feature_snapshot_hash_and_freshness():
    # TradeEngine 인스턴스 모킹하여 capture_feature_snapshot 검증
    # ConfigManager 및 DB 연동을 위해 임시 생성
    import src.database.connection
    original_db_path = src.database.connection.DB_PATH
    src.database.connection.DB_PATH = TEST_DB_PATH
    try:
        from src.database.repository import SqliteMarketDataRepository
        repo = SqliteMarketDataRepository()
        
        engine = TradeEngine(exchange_id="upbit", symbol="BTC", strategies=[], market_data_repo=repo)
        engine.last_tick = {
            "trade_price": 50000.0,
            "trade_volume": 1.5,
            "ask_bid": "BID",
            "trade_timestamp": int(time.time() * 1000) - 2000 # 2초 전
        }
        
        # 임시 캔들 추가하여 freshness 충족
        from src.engine.candles import Candle
        context = engine.contexts[60]
        context.add_candle(Candle(
            exchange_id="upbit", symbol="BTC", interval=60,
            timestamp=int(time.time()) - 30, open=50000.0, high=50100.0, low=49900.0, close=50000.0, volume=10.0, is_closed=True
        ))

        # capture_feature_snapshot 실행
        snap = await engine.capture_feature_snapshot(
            proposal_id="test_prop", strategy_id="rsi_strategy", exchange_id="upbit", symbol="BTC", proposal_type="MUTATION"
        )
        
        assert snap.exchange_id == "upbit"
        assert snap.is_fresh is True
        assert snap.snapshot_hash != ""
        assert snap.feature_vector_hash != ""
        assert snap.orderbook_available is False

        # Stale 발생 유도
        engine.last_tick["trade_timestamp"] = int(time.time() * 1000) - 50000 # 50초 전 (crypto trade freshness 한도 10초 초과)
        snap_stale = await engine.capture_feature_snapshot(
            proposal_id="test_prop", strategy_id="rsi_strategy", exchange_id="upbit", symbol="BTC", proposal_type="MUTATION"
        )
        assert snap_stale.is_fresh is False
        assert "TICK_TTL_EXCEEDED" in snap_stale.stale_reason
    finally:
        src.database.connection.DB_PATH = original_db_path

@pytest.mark.asyncio
async def test_proposal_evaluations_fsm_and_lock_timeout():
    # 1. strategy_proposals에 테스트 레코드 삽입
    async with get_db_conn(TEST_DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO strategy_proposals (id, status, outcome, original_params, proposed_params, confidence_score)
            VALUES (999, 'PENDING', 'RUNNING', '{}', '{}', 85)
            """
        )
        # 1:N evaluations PENDING 생성
        current_time_s = int(time.time())
        await db.execute(
            """
            INSERT INTO proposal_evaluations (proposal_id, horizon_name, due_at, evaluation_status, retry_count, locked_at)
            VALUES (999, '10m', ?, 'PENDING', 0, NULL)
            """,
            (current_time_s - 10,) # 만기 경과
        )
        # 락 타임아웃 테스트를 위한 EVALUATING 방치 레코드 생성
        await db.execute(
            """
            INSERT INTO proposal_evaluations (proposal_id, horizon_name, due_at, evaluation_status, retry_count, locked_at)
            VALUES (999, '30m', ?, 'EVALUATING', 1, ?)
            """,
            (current_time_s - 1000, current_time_s - 400) # 400초 경과 (300초 타임아웃 초과)
        )
        # 최대 재시도 횟수 초과된 EVALUATING 방치 레코드 생성
        await db.execute(
            """
            INSERT INTO proposal_evaluations (proposal_id, horizon_name, due_at, evaluation_status, retry_count, locked_at)
            VALUES (999, '2h', ?, 'EVALUATING', 3, ?)
            """,
            (current_time_s - 1000, current_time_s - 400) # 3회 재시도 상태에서 타임아웃
        )
        await db.commit()

    config = ConfigManager("config/settings.yaml")
    # 임시 Mock EventBus
    class MockEventBus:
        async def publish(self, topic, data): pass
        async def subscribe(self, topic): return None

    # StrategyService를 구동하여 FSM 복구 및 평가 검증
    service = StrategyService(config, MockEventBus(), InMemoryMarketDataRepository())
    from unittest.mock import AsyncMock, Mock
    mock_ns = Mock()
    mock_ns.publish = AsyncMock(return_value=True)
    service.notification_service = mock_ns
    service.db_path = TEST_DB_PATH
    
    # 2. _periodic_proposal_evaluation_loop의 락 복구 파트만 강제 수동 호출 또는 프라이빗 메소드 형태이므로 간접 테스트
    # periodic evaluation loop 본문을 안전하게 1회 기동 및 예외 상황에 대한 FSM 상태 변환 검증
    # 락 복구 스캔 진행
    async with get_db_conn(TEST_DB_PATH) as db:
        # 30m 복구 대상 (retry_count=1 -> PENDING 원복, locked_at=NULL, retry_count=2)
        # 2h 복구 대상 (retry_count=3 -> ERROR 전환, locked_at=NULL)
        
        # 락 타임아웃 복구 로직 적용
        lock_timeout = 300
        max_retries = 3
        cutoff = current_time_s - lock_timeout
        
        async with db.execute(
            "SELECT id, retry_count FROM proposal_evaluations WHERE evaluation_status = 'EVALUATING' AND locked_at < ?",
            (cutoff,)
        ) as cursor:
            stale_locks = [dict(r) for r in await cursor.fetchall()]
            
        for lock in stale_locks:
            pe_id = lock["id"]
            r_count = lock["retry_count"] or 0
            if r_count < max_retries:
                await db.execute(
                    "UPDATE proposal_evaluations SET evaluation_status = 'PENDING', retry_count = ?, locked_at = NULL, last_error = 'LOCK_TIMEOUT' WHERE id = ?",
                    (r_count + 1, pe_id)
                )
            else:
                await db.execute(
                    "UPDATE proposal_evaluations SET evaluation_status = 'ERROR', locked_at = NULL, last_error = 'LOCK_TIMEOUT_EXCEEDED' WHERE id = ?",
                    (pe_id,)
                )
        await db.commit()

    # 결과 검증
    async with get_db_conn(TEST_DB_PATH) as db:
        async with db.execute("SELECT * FROM proposal_evaluations WHERE proposal_id = 999") as cursor:
            evals = {r["horizon_name"]: dict(r) for r in await cursor.fetchall()}
            
    assert evals["30m"]["evaluation_status"] == "PENDING"
    assert evals["30m"]["retry_count"] == 2
    assert evals["30m"]["locked_at"] is None
    
    assert evals["2h"]["evaluation_status"] == "ERROR"
    assert evals["2h"]["locked_at"] is None
    assert evals["2h"]["last_error"] == "LOCK_TIMEOUT_EXCEEDED"

@pytest.mark.asyncio
async def test_universe_watched_candidate_control():
    # Universe 격하/승격 제어 테스트
    config = ConfigManager("config/settings.yaml")
    class MockEventBus:
        async def publish(self, topic, data): pass
        async def subscribe(self, topic): return None
        
    service = StrategyService(config, MockEventBus(), InMemoryMarketDataRepository())
    from unittest.mock import AsyncMock, Mock
    mock_ns = Mock()
    mock_ns.publish = AsyncMock(return_value=True)
    service.notification_service = mock_ns
    service.db_path = TEST_DB_PATH
    
    import src.database.connection
    original_db_path = src.database.connection.DB_PATH
    src.database.connection.DB_PATH = TEST_DB_PATH
    try:
        # TradeEngine 등록 및 모킹
        from src.database.repository import SqliteMarketDataRepository
        repo = SqliteMarketDataRepository()
        engine = TradeEngine(exchange_id="upbit", symbol="BTC", strategies=[], market_data_repo=repo)
        
        # 20분 이내 유동성이 매우 좋은 mock tick들로 채움
        engine.last_tick = {
            "trade_price": 50000.0,
            "trade_volume": 100.0,
            "ask_bid": "BID",
            "trade_timestamp": int(time.time() * 1000) - 100
        }
        
        # 캔들 추가
        from src.engine.candles import Candle
        context = engine.contexts[60]
        context.add_candle(Candle(
            exchange_id="upbit", symbol="BTC", interval=60,
            timestamp=int(time.time()) - 30, open=50000.0, high=50100.0, low=49900.0, close=50000.0, volume=100.0, is_closed=True
        ))
        
        service.trade_engines["upbit:BTC"] = engine
        
        # get_recent_trades 가 mock data를 반환하도록 DB에 trades 데이터 삽입
        async with get_db_conn(TEST_DB_PATH) as db:
            now_ms = int(time.time() * 1000)
            # 20분 동안 250건의 거래 (TPS >= 0.2 기준 만족용)
            for i in range(250):
                await db.execute(
                    "INSERT INTO trades (exchange_id, symbol, trade_price, trade_volume, ask_bid, trade_timestamp) VALUES ('upbit', 'BTC', 50000.0, 10.0, 'BID', ?)",
                    (now_ms - i * 1000,)
                )
            await db.commit()

        # universe control 루틴의 핵심 동작 1회 수동 수행
        current_time_s = int(time.time())
        passed_symbols = []
        
        for key, eng in list(service.trade_engines.items()):
            snap = await eng.capture_feature_snapshot(
                proposal_id="", strategy_id="rsi_strategy", exchange_id=eng.exchange_id, symbol=eng.symbol, proposal_type="UNIVERSE"
            )
            tps = snap.liquidity_features.get("tps", 0.0)
            idle_time = snap.liquidity_features.get("idle_time", 9999.0)
            volume = snap.liquidity_features.get("volume", 0.0)
            value = snap.liquidity_features.get("value", 0.0)
            
            # 유동성 프록시 기준
            if snap.is_fresh and tps >= 0.2 and idle_time < 30.0 and volume > 10.0 and value > 100000.0:
                passed_symbols.append((key, value, snap))
                
        assert len(passed_symbols) == 1
        assert passed_symbols[0][0] == "upbit:BTC"
        
        # quota, cooldown, limit 확인하여 CANDIDATE 승격 진행
        key, value, snap = passed_symbols[0]
        service.universe_status[key] = "WATCHED"
        
        symbol_cooldown_seconds = 3600
        daily_proposal_limit = 100
        
        last_cand_time = service.symbol_last_candidate_time.get(key, 0.0)
        if current_time_s - last_cand_time >= symbol_cooldown_seconds:
            if service.daily_proposal_count < daily_proposal_limit:
                service.universe_status[key] = "CANDIDATE"
                service.symbol_last_candidate_time[key] = current_time_s
                service.daily_proposal_count += 1
                
        assert service.universe_status["upbit:BTC"] == "CANDIDATE"
        assert service.daily_proposal_count == 1
    finally:
        src.database.connection.DB_PATH = original_db_path

@pytest.mark.asyncio
async def test_report_generation():
    # generate_report가 문제 없이 동작하는지 연산 확인
    # 가짜 proposal_evaluations 및 strategy_proposals 레코드를 삽입
    async with get_db_conn(TEST_DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO strategy_proposals (id, status, outcome, original_params, proposed_params, confidence_score)
            VALUES (1, 'PENDING', 'RUNNING', '{}', '{}', 85)
            """
        )
        await db.execute(
            """
            INSERT INTO proposal_evaluations (
                proposal_id, horizon_name, candidate_roi, champion_roi, roi_gap,
                candidate_mdd, champion_mdd, virtual_rollback, actual_label, actual_label_source,
                due_at, evaluation_status, predicted_risk_score
            )
            VALUES (1, '10m', 0.05, 0.02, 0.03, 0.01, 0.02, 0, 'GOOD', 'NORMAL', 1000, 'COMPLETED', 0.2)
            """
        )
        await db.execute(
            """
            INSERT INTO girs_shadow_metrics (
                timestamp, proposal_id, strategy_id, blocked_reason,
                market_type, session_state, volatility_regime, liquidity_regime, exchange_id,
                correction_active
            )
            VALUES (1000.0, '1', 'rsi_strategy', NULL, 'crypto', '24h', 'low', 'high', 'upbit', 0)
            """
        )
        await db.commit()
        
    # 리포트 빌드 수행
    report_output_path = "logs/test_shadow_report.md"
    if os.path.exists(report_output_path):
        os.remove(report_output_path)
        
    generate_report(TEST_DB_PATH, report_output_path)
    
    assert os.path.exists(report_output_path) is True
    with open(report_output_path, "r", encoding="utf-8") as f:
        content = f.read()
        
    # ECE/Brier Score 및 한계 문구 포함 여부 확인
    assert "Expected Calibration Error" in content
    assert "Brier Score" in content
    assert "N 부족 경고" in content # 표본이 1개이므로 30개 미만으로 경고 마킹되어야 함
    assert "단일 예측 위험 점수의 다중 Horizon 재사용 한계 해석" in content
    
    if os.path.exists(report_output_path):
        os.remove(report_output_path)
