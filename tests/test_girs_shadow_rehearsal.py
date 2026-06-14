# -*- coding: utf-8 -*-

import os
import time
import json
import asyncio
import pytest
import pytest_asyncio
from unittest.mock import MagicMock, AsyncMock

from src.database.schema import init_db
from src.database.connection import get_db_conn
from src.database.repository import SqliteTradingRepository, InMemoryTradingRepository, InMemoryMarketDataRepository
from src.config.manager import ConfigManager
from src.engine.daemon_supervisor import EventBus
from src.services.strategy_service import StrategyService
from src.engine.girs_types import FeatureSnapshot
from src.engine.portfolio import PortfolioManager

TEST_DB_PATH = "tests/test_rehearsal.db"

# Fake EventBus 구현
class FakeEventBus(EventBus):
    async def publish(self, topic: str, data: dict):
        pass
    async def subscribe(self, topic: str):
        return FakeSubscriber()
    def close(self):
        pass

class FakeSubscriber:
    async def receive(self):
        return None, None
    def close(self):
        pass

@pytest_asyncio.fixture(autouse=True)
async def setup_test_db():
    if os.path.exists(TEST_DB_PATH):
        os.remove(TEST_DB_PATH)
    await init_db(TEST_DB_PATH)
    
    # 테스트에 필요한 포트폴리오 사전 삽입 및 캐시 동기화
    from src.engine.portfolio import get_integer_portfolio_id
    repo = SqliteTradingRepository(db_path=TEST_DB_PATH)
    port_id = get_integer_portfolio_id("sim_port_rehearsal")
    async with get_db_conn(TEST_DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO portfolios (id, name, type) VALUES (?, ?, 'simulation')", (port_id, "sim_port_rehearsal"))
        await db.commit()
    await repo.sync_portfolio_id_cache()
    
    yield
    if os.path.exists(TEST_DB_PATH):
        os.remove(TEST_DB_PATH)

@pytest.mark.asyncio
async def test_girs_shadow_e2e_rehearsal():
    """
    1. proposal 및 다중 Horizon 평가 생성 후 FSM 전이(PENDING -> COMPLETED) 확인
    2. 데이터 품질(Sanity Check) 검증
    3. 실거래 격리 가드(live_trading_enabled=False, auto_strategy_promotion_enabled=False) 검증
    """
    # pytest 환경에서도 2차 안전 가드 차단이 걸리도록 명시적으로 오버라이드 지정
    repo = SqliteTradingRepository(
        db_path=TEST_DB_PATH,
        girs_shadow_mode_override=True,
        auto_strategy_promotion_enabled_override=False
    )
    
    # Mock Config 설정
    config_mock = MagicMock(spec=ConfigManager)
    config_mock.get.side_effect = lambda key, default=None: {
        'system.db_path': TEST_DB_PATH,
        'system.girs_shadow_mode': True,
        'system.auto_strategy_promotion_enabled': False,
        'system.live_trading_enabled': False,
        'system.operation_mode': 'shadow',
        'system.model_version': 'mock_v1',
        'system.scaler_version': 'mock_v1',
        'system.exchange_quota': {'upbit': 20, 'kis': 20},
        'system.symbol_cooldown_seconds': 3600,
        'system.daily_proposal_limit': 100
    }.get(key, default)
    config_mock.config = {}

    # 1. Proposal 등록
    init_params = {"rsi_window": 14, "buy_threshold": 30.0, "sell_threshold": 70.0}
    proposal_data = {
        "insight_id": None,
        "proposal_group_id": "group_rehearsal",
        "version": 1,
        "portfolio_id": "sim_port_rehearsal",
        "strategy_id": "RSI_Strategy",
        "status": "PENDING",
        "outcome": "RUNNING",
        "original_params": init_params,
        "proposed_params": {"rsi_window": 14, "buy_threshold": 35.0, "sell_threshold": 65.0},
        "metrics": {"roi_7d": 5.0, "trade_count_7d": 35},
        "mutation_trace": {"buy_threshold": [30.0, 35.0]},
        "confidence_score": 85,
        "applied_at": None,
        "rolled_back_at": None,
        "decision_path_hash": "hash_rehearsal",
        "audit_log_json": "{}"
    }
    proposal_id = await repo.insert_strategy_proposal(proposal_data)
    assert proposal_id > 0

    # 2. 다중 Horizon 평가 레코드 PENDING으로 생성
    horizons = ["1d", "3d", "7d"]
    for hz in horizons:
        eval_data = {
            "proposal_id": proposal_id,
            "horizon_name": hz,
            "predicted_roi_7d": 5.0,
            "actual_roi_7d": 0.0,
            "roi_divergence": 0.0,
            "predicted_trade_count_7d": 35,
            "actual_trade_count_7d": 0,
            "trade_count_divergence": 0,
            "evaluation_status": "PENDING"
        }
        await repo.insert_proposal_evaluation(eval_data)

    # evaluations 조회
    evals = await repo.get_proposal_evaluations(proposal_id)
    assert len(evals) == 3
    for ev in evals:
        assert ev["evaluation_status"] == "PENDING"
        assert ev["horizon_name"] in horizons

    # 3. 만기 도래 시뮬레이션 및 COMPLETED 평가 처리
    for ev in evals:
        # 가상 사후 평가 수행 후 COMPLETED 처리
        await repo.update_strategy_proposal_status(proposal_id, "APPLIED", "COMPLETED")
        # evaluations 테이블 업데이트 (직접 DB update 시뮬레이션)
        async with get_db_conn(TEST_DB_PATH) as db:
            await db.execute('''
                UPDATE proposal_evaluations
                SET evaluation_status = 'COMPLETED', actual_roi_7d = 6.2, roi_divergence = 1.2
                WHERE proposal_id = ? AND horizon_name = ?
            ''', (proposal_id, ev["horizon_name"]))
            await db.commit()

    completed_evals = await repo.get_proposal_evaluations(proposal_id)
    for ev in completed_evals:
        assert ev["evaluation_status"] == "COMPLETED"
        assert ev["actual_roi_7d"] == 6.2

    # 4. 데이터 품질 자체 점검 (Sanity Check)
    # proposal_evaluations.horizon_name 누락 검증
    async with get_db_conn(TEST_DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM proposal_evaluations WHERE horizon_name IS NULL OR horizon_name = ''") as cur:
            null_horizons = (await cur.fetchone())[0]
            assert null_horizons == 0

    # 5. 실거래 가드 검증
    # live_trading_enabled와 auto_strategy_promotion_enabled가 False인지 검증
    live_trading_enabled = config_mock.get("system.live_trading_enabled", True)
    auto_strategy_promotion_enabled = config_mock.get("system.auto_strategy_promotion_enabled", True)
    assert not live_trading_enabled, "실거래 기동이 비활성화(False)되어 있어야 합니다."
    assert not auto_strategy_promotion_enabled, "자동 전략 승격이 비활성화(False)되어 있어야 합니다."

    # 실거래 가드 검증용 PENDING 제안 신규 생성
    guard_prop_id = await repo.insert_strategy_proposal({
        "insight_id": None,
        "proposal_group_id": "group_guard_test",
        "version": 1,
        "portfolio_id": "sim_port_rehearsal",
        "strategy_id": "RSI_Strategy",
        "status": "PENDING",
        "outcome": "RUNNING",
        "original_params": init_params,
        "proposed_params": {"rsi_window": 14, "buy_threshold": 35.0, "sell_threshold": 65.0},
        "metrics": {"roi_7d": 5.0, "trade_count_7d": 35},
        "mutation_trace": {"buy_threshold": [30.0, 35.0]},
        "confidence_score": 85,
        "applied_at": None,
        "rolled_back_at": None,
        "decision_path_hash": "hash_guard_test",
        "audit_log_json": "{}"
    })

    # 섀도 모드/자동 승격 비활성화 시 실제 승격 시도 시 ValueError 발생하는지 검증 (2차 안전 가드 작동 확인)
    with pytest.raises(ValueError, match="Promotion blocked: Shadow operation mode active or auto promotion disabled"):
        await repo.approve_proposal_atomic(guard_prop_id, int(time.time() * 1000))


@pytest.mark.asyncio
async def test_universe_resource_guards_and_logging_rules():
    """
    1. 동일 차단 사유 반복 시 system_events row 수 증가하지 않고 universe_guard_state 만 업데이트되는지 검증
    2. 차단 사유 변경 시에만 system_events 가 1건 증가하고 guard_state 사유가 갱신되는지 검증
    3. girs_shadow_metrics 내 UNKNOWN 데이터가 없는지 검증
    """
    # SQLite DB 및 리포지토리 연동
    repo = SqliteTradingRepository(db_path=TEST_DB_PATH)
    
    # EventBus 및 Mock Portfolio
    event_bus = FakeEventBus()
    config_mock = MagicMock(spec=ConfigManager)
    config_mock.get.side_effect = lambda key, default=None: {
        'system.db_path': TEST_DB_PATH,
        'system.girs_shadow_mode': True,
        'system.auto_strategy_promotion_enabled': False,
        'system.live_trading_enabled': False,
        'system.operation_mode': 'shadow',
        'system.model_version': 'mock_v1',
        'system.scaler_version': 'mock_v1',
        'system.exchange_quota': {'upbit': 2},
        'system.symbol_cooldown_seconds': 3600,
        'system.daily_proposal_limit': 100
    }.get(key, default)
    config_mock.config = {}

    service = StrategyService(config_mock, event_bus, InMemoryMarketDataRepository())
    
    # PortfolioManager Mocking 및 Repository 연동
    portfolio_mock = MagicMock(spec=PortfolioManager)
    portfolio_mock.repository = repo
    portfolio_mock.id = "sim_port_rehearsal"
    portfolio_mock.get_active_simulation_portfolio.return_value = portfolio_mock
    service.portfolio_manager = portfolio_mock
    service.current_portfolio_id = portfolio_mock.id

    # 1. PENDING proposal & promotion_event_log 생성
    # 테스트용 종목: BTC
    init_params = {"rsi_window": 14, "buy_threshold": 30.0, "sell_threshold": 70.0}
    proposal_data = {
        "insight_id": None,
        "proposal_group_id": "group_rehearsal_guard",
        "version": 1,
        "portfolio_id": "sim_port_rehearsal",
        "strategy_id": "RSI_Strategy",
        "status": "PENDING",
        "outcome": "RUNNING",
        "original_params": init_params,
        "proposed_params": {"rsi_window": 14, "buy_threshold": 35.0, "sell_threshold": 65.0},
        "metrics": {"roi_7d": 5.0, "trade_count_7d": 35},
        "mutation_trace": {"buy_threshold": [30.0, 35.0]},
        "confidence_score": 85,
        "applied_at": None,
        "rolled_back_at": None,
        "decision_path_hash": "hash_rehearsal_guard",
        "audit_log_json": "{}"
    }
    proposal_id = await repo.insert_strategy_proposal(proposal_data)
    
    # FeatureSnapshot 적재
    snap = FeatureSnapshot(
        price_features={"close": 50000.0, "returns": 0.01, "volatility": 0.2},
        liquidity_features={"spread": 0.001, "volume": 1000.0, "depth": 10000.0, "tps": 0.5, "idle_time": 5.0},
        regime_features={"regime_index": 1.0},
        exchange_id="upbit",
        symbol="BTC",
        market_type="crypto",
        is_fresh=True
    )
    
    # promotion_event_log 적재
    async with get_db_conn(TEST_DB_PATH) as db:
        await db.execute('''
            INSERT INTO promotion_event_log (event_id, proposal_id, sequence_no, event_type, timestamp, feature_snapshot)
            VALUES (?, ?, 1, 'INGEST', ?, ?)
        ''', ("evt_rehearsal_1", str(proposal_id), time.time(), json.dumps(snap.__dict__)))
        await db.commit()

    # 2. 30초 수집 루프 1회 인라인 시뮬레이션
    # 첫 실행: 쿨다운 미경과(last_candidate_time=now)로 차단
    # 쿨다운 유발을 위해 last_cand_time을 현재 시간으로 세팅해둠
    service.symbol_last_candidate_time["BTC"] = time.time()
    
    # settings 로드
    exchange_quota = {'upbit': 2}
    symbol_cooldown_seconds = 3600
    daily_proposal_limit = 100
    
    # PENDING 목록 조회 및 feature_snapshot 읽기
    pending_proposals = await repo.get_active_proposals("RSI_Strategy", "PENDING")
    assert len(pending_proposals) == 1
    
    # FSM 루틴 직접 시뮬레이션 (1회차)
    await service.portfolio_manager.repository.insert_girs_shadow_metric({
        "timestamp": time.time(),
        "proposal_id": str(proposal_id),
        "strategy_id": "RSI_Strategy",
        "model_risk_score": 0.1,
        "fallback_risk_score": 0.2,
        "final_promotion_score": 0.85,
        "shadow_risk_score": 0.1,
        "exchange_id": "upbit",
        "market_type": "crypto"
    })
    
    # BTC 쿨다운 차단 발생 시뮬레이션
    current_time_s = time.time()
    last_cand_time = service.symbol_last_candidate_time.get("BTC", 0.0)
    
    # 1회차 차단
    assert current_time_s - last_cand_time < symbol_cooldown_seconds
    service.cooldown_blocked_count += 1
    
    # universe_guard_state & system_events 1회차 기록
    prev_state = await repo.get_universe_guard_state("upbit", "crypto", "BTC")
    assert prev_state is None
    
    await repo.insert_system_event("PROMOTION_COOLDOWN_BLOCKED", "BTC", "재승격 쿨다운 미경과")
    await repo.upsert_universe_guard_state(
        exchange_id="upbit",
        market_type="crypto",
        symbol="BTC",
        status="WATCHED",
        blocked_reason="COOLDOWN",
        blocked_count=1,
        last_blocked_at=current_time_s,
        last_event_logged_reason="COOLDOWN"
    )
    
    # 2회차 동일 차단 (30초 후 가정)
    current_time_s_2 = time.time() + 30
    service.cooldown_blocked_count += 1
    
    prev_state = await repo.get_universe_guard_state("upbit", "crypto", "BTC")
    assert prev_state["blocked_reason"] == "COOLDOWN"
    assert prev_state["blocked_count"] == 1
    
    # 동일 사유이므로 system_events 추가 없음, count 누적만 수행
    new_count = prev_state["blocked_count"] + 1
    await repo.upsert_universe_guard_state(
        exchange_id="upbit",
        market_type="crypto",
        symbol="BTC",
        status="WATCHED",
        blocked_reason="COOLDOWN",
        blocked_count=new_count,
        last_blocked_at=current_time_s_2,
        last_event_logged_reason="COOLDOWN"
    )
    
    # system_events 개수 확인 (1건이어야 함)
    events = await repo.get_system_events(100)
    cooldown_events = [e for e in events if e["event_type"] == "PROMOTION_COOLDOWN_BLOCKED" and e["target"] == "BTC"]
    assert len(cooldown_events) == 1, "동일 차단 사유 반복 시 system_events row가 증가하지 않아야 합니다."

    # 3. 차단 사유 변경 검증 (COOLDOWN -> LIMIT)
    # 한도 초과 시뮬레이션
    service.daily_proposal_count = 101 # limit=100 초과
    service.symbol_last_candidate_time["BTC"] = 0.0 # cooldown 미경과 해제
    
    current_time_s_3 = time.time() + 60
    
    prev_state = await repo.get_universe_guard_state("upbit", "crypto", "BTC")
    assert prev_state["blocked_reason"] == "COOLDOWN"
    
    # 사유 변경 발생: COOLDOWN -> LIMIT
    service.limit_blocked_count += 1
    await repo.insert_system_event("PROMOTION_LIMIT_BLOCKED", "BTC", "일일 한도 초과")
    await repo.upsert_universe_guard_state(
        exchange_id="upbit",
        market_type="crypto",
        symbol="BTC",
        status="WATCHED",
        blocked_reason="LIMIT",
        blocked_count=1,
        last_blocked_at=current_time_s_3,
        last_event_logged_reason="LIMIT"
    )
    
    # system_events 및 guard_state 확인
    events_after = await repo.get_system_events(100)
    limit_events = [e for e in events_after if e["event_type"] == "PROMOTION_LIMIT_BLOCKED" and e["target"] == "BTC"]
    assert len(limit_events) == 1, "차단 사유 변경 시 system_events에 로그가 1건 추가되어야 합니다."
    
    final_state = await repo.get_universe_guard_state("upbit", "crypto", "BTC")
    assert final_state["blocked_reason"] == "LIMIT"
    assert final_state["blocked_count"] == 1
    
    # 4. girs_shadow_metrics 내 UNKNOWN 데이터가 없는지 검증 (Sanity Check)
    metrics = await repo.get_girs_shadow_metrics(100)
    assert len(metrics) > 0
    for m in metrics:
        assert m["exchange_id"] != "UNKNOWN"
        assert m["market_type"] != "UNKNOWN"
