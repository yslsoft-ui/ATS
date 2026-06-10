# -*- coding: utf-8 -*-

import os
import uuid
import json
import pytest
import aiosqlite
from src.engine.girs_types import FeatureSnapshot, CandidateProposal
from src.engine.promotion_queue import Clock, PromotionQueue, ProposalStateView

DB_FILE = "data/test_promotion_queue.db"

@pytest.fixture(autouse=True)
def setup_teardown_db():
    if os.path.exists(DB_FILE):
        try:
            os.remove(DB_FILE)
        except OSError:
            pass
    os.makedirs(os.path.dirname(DB_FILE), exist_ok=True)
    yield
    if os.path.exists(DB_FILE):
        try:
            os.remove(DB_FILE)
        except OSError:
            pass

@pytest.mark.asyncio
async def test_fsm_normal_transitions():
    clock = Clock(start_time=1000.0)
    queue = PromotionQueue(
        db_path=DB_FILE,
        clock=clock,
        proposal_ttl=100.0,
        lock_timeout=10.0,
        rejected_max_age=50.0,
        cooldown_period=20.0
    )
    await queue.init_table()
    
    snap = FeatureSnapshot(
        price_features={"close": 100.0, "returns": 0.0, "volatility": 0.1},
        liquidity_features={"spread": 0.002, "volume": 1000.0, "depth": 2000.0},
        regime_features={"regime_index": 1.0}
    )
    proposal = CandidateProposal(
        proposal_id="prop_1",
        source_strategy_id="strat_1",
        features=snap,
        backtest_result={"roi": 15.5},
        model_version="v1",
        scaler_version="s1"
    )
    
    evt_id = str(uuid.uuid4())
    success = await queue.ingest_proposal(proposal, evt_id)
    assert success
    assert queue.materialized_views["prop_1"].status == "CANDIDATE"

    evt_id_2 = str(uuid.uuid4())
    success = await queue.transition_state("prop_1", "SCORED", evt_id_2, {"final_promotion_score": 0.85})
    assert success
    assert queue.materialized_views["prop_1"].status == "SCORED"
    assert queue.materialized_views["prop_1"].final_promotion_score == 0.85

    evt_id_3 = str(uuid.uuid4())
    success = await queue.transition_state("prop_1", "RANKED", evt_id_3)
    assert success
    assert queue.materialized_views["prop_1"].status == "RANKED"

    evt_id_4 = str(uuid.uuid4())
    success = await queue.transition_state("prop_1", "PROMOTION_PENDING", evt_id_4)
    assert success
    assert queue.materialized_views["prop_1"].status == "PROMOTION_PENDING"

    evt_id_5 = str(uuid.uuid4())
    success = await queue.transition_state("prop_1", "PROMOTION_LOCKED", evt_id_5)
    assert success
    assert queue.materialized_views["prop_1"].status == "PROMOTION_LOCKED"

    evt_id_6 = str(uuid.uuid4())
    success = await queue.transition_state("prop_1", "PROMOTION_EXECUTED", evt_id_6)
    assert success
    assert queue.materialized_views["prop_1"].status == "PROMOTION_EXECUTED"

    evt_id_7 = str(uuid.uuid4())
    success = await queue.transition_state("prop_1", "SCORED", evt_id_7)
    assert not success


@pytest.mark.asyncio
async def test_fsm_timeouts_and_ttl():
    clock = Clock(start_time=1000.0)
    queue = PromotionQueue(
        db_path=DB_FILE,
        clock=clock,
        proposal_ttl=100.0,
        lock_timeout=10.0,
        rejected_max_age=50.0,
        cooldown_period=20.0
    )
    await queue.init_table()
    
    snap = FeatureSnapshot(price_features={}, liquidity_features={}, regime_features={})
    proposal_1 = CandidateProposal(proposal_id="prop_ttl", source_strategy_id="s1", features=snap, backtest_result={})
    proposal_2 = CandidateProposal(proposal_id="prop_lock", source_strategy_id="s1", features=snap, backtest_result={})
    proposal_3 = CandidateProposal(proposal_id="prop_rej", source_strategy_id="s1", features=snap, backtest_result={})

    await queue.ingest_proposal(proposal_1, str(uuid.uuid4()))
    await queue.ingest_proposal(proposal_2, str(uuid.uuid4()))
    await queue.ingest_proposal(proposal_3, str(uuid.uuid4()))

    await queue.transition_state("prop_lock", "SCORED", str(uuid.uuid4()))
    await queue.transition_state("prop_lock", "PROMOTION_PENDING", str(uuid.uuid4()))
    await queue.transition_state("prop_lock", "PROMOTION_LOCKED", str(uuid.uuid4()))

    await queue.transition_state("prop_rej", "SCORED", str(uuid.uuid4()))
    await queue.transition_state("prop_rej", "PROMOTION_PENDING", str(uuid.uuid4()))
    await queue.transition_state("prop_rej", "PROMOTION_REJECTED", str(uuid.uuid4()))

    # 1. 시간 60초 경과 -> lock_timeout(10초) 경과, rejected_max_age(50초) 경과
    clock.sleep(60.0)
    triggered = await queue.check_lifecycle_and_timeouts()
    
    assert "prop_lock" in triggered
    assert "prop_rej" in triggered
    assert "prop_ttl" not in triggered

    assert queue.materialized_views["prop_lock"].status == "PROMOTION_REJECTED"
    assert queue.materialized_views["prop_rej"].status == "EXPIRED"

    # 2. 추가로 50초 경과 (총 110초 경과) -> proposal_ttl(100초) 경과
    clock.sleep(50.0)
    triggered_2 = await queue.check_lifecycle_and_timeouts()
    
    assert "prop_ttl" in triggered_2
    assert queue.materialized_views["prop_ttl"].status == "EXPIRED"


@pytest.mark.asyncio
async def test_rejected_cooldown_and_max_age():
    clock = Clock(start_time=1000.0)
    queue = PromotionQueue(
        db_path=DB_FILE,
        clock=clock,
        proposal_ttl=1000.0,
        lock_timeout=60.0,
        rejected_max_age=100.0,
        cooldown_period=30.0
    )
    await queue.init_table()

    snap = FeatureSnapshot(price_features={}, liquidity_features={}, regime_features={})
    proposal = CandidateProposal(proposal_id="prop_c", source_strategy_id="s1", features=snap, backtest_result={})
    await queue.ingest_proposal(proposal, str(uuid.uuid4()))
    
    await queue.transition_state("prop_c", "SCORED", str(uuid.uuid4()))
    await queue.transition_state("prop_c", "PROMOTION_PENDING", str(uuid.uuid4()))
    await queue.transition_state("prop_c", "PROMOTION_REJECTED", str(uuid.uuid4()))

    # 1. 10초 경과 후 SCORED 복귀 시도 -> cooldown(30초) 미달로 거절되어야 함
    clock.sleep(10.0)
    success = await queue.transition_state("prop_c", "SCORED", str(uuid.uuid4()))
    assert not success
    assert queue.materialized_views["prop_c"].status == "PROMOTION_REJECTED"

    # 2. 추가로 25초 경과 (총 35초 경과) -> SCORED 복귀 가능해야 함
    clock.sleep(25.0)
    success_2 = await queue.transition_state("prop_c", "SCORED", str(uuid.uuid4()))
    assert success_2
    assert queue.materialized_views["prop_c"].status == "SCORED"


@pytest.mark.asyncio
async def test_expired_terminal():
    clock = Clock(start_time=1000.0)
    queue = PromotionQueue(db_path=DB_FILE, clock=clock)
    await queue.init_table()

    snap = FeatureSnapshot(price_features={}, liquidity_features={}, regime_features={})
    proposal = CandidateProposal(proposal_id="prop_t", source_strategy_id="s1", features=snap, backtest_result={})
    await queue.ingest_proposal(proposal, str(uuid.uuid4()))
    
    await queue.transition_state("prop_t", "EXPIRED", str(uuid.uuid4()))
    assert queue.materialized_views["prop_t"].status == "EXPIRED"

    # EXPIRED에서 다른 상태로의 전이는 모두 거부되어야 함
    success = await queue.transition_state("prop_t", "SCORED", str(uuid.uuid4()))
    assert not success


@pytest.mark.asyncio
async def test_event_log_idempotency():
    clock = Clock(start_time=1000.0)
    queue = PromotionQueue(db_path=DB_FILE, clock=clock)
    await queue.init_table()

    snap = FeatureSnapshot(price_features={}, liquidity_features={}, regime_features={})
    proposal = CandidateProposal(proposal_id="prop_i", source_strategy_id="s1", features=snap, backtest_result={})
    
    evt_id = "uniq_event_123"
    success_1 = await queue.ingest_proposal(proposal, evt_id)
    assert success_1

    # 1. 동일 event_id로 중복 인입 시도 -> IntegrityError 예외 캐치 후 False 반환 검증
    success_2 = await queue.ingest_proposal(proposal, evt_id)
    assert not success_2

    # 2. 동일 proposal_id에 대해 sequence_no = 1을 강제로 중복 삽입 시도 (sequence_no UNIQUE 제약 위반)
    success_3 = await queue.ingest_proposal(proposal, "another_evt_id")
    assert not success_3


@pytest.mark.asyncio
async def test_replay_rebuild_determinism_and_view_integrity():
    clock = Clock(start_time=1000.0)
    queue = PromotionQueue(db_path=DB_FILE, clock=clock)
    await queue.init_table()

    snap = FeatureSnapshot(
        price_features={"close": 1.23},
        liquidity_features={"spread": 0.05},
        regime_features={"regime_index": 3.0}
    )
    proposal = CandidateProposal(
        proposal_id="prop_r",
        source_strategy_id="strat_1",
        features=snap,
        backtest_result={"roi": 22.0},
        model_version="mv_1",
        scaler_version="sv_1"
    )

    await queue.ingest_proposal(proposal, str(uuid.uuid4()))
    await queue.transition_state("prop_r", "SCORED", str(uuid.uuid4()), {"final_promotion_score": 0.99})
    await queue.transition_state("prop_r", "RANKED", str(uuid.uuid4()))
    
    # 캐시 완전 클리어 후 DB 로그로부터 리플레이 리빌드 수행
    await queue.rebuild_materialized_view()
    
    assert "prop_r" in queue.materialized_views
    rebuilt_view = queue.materialized_views["prop_r"]
    
    assert rebuilt_view.status == "RANKED"
    assert rebuilt_view.sequence_no == 3
    assert rebuilt_view.final_promotion_score == 0.99
    assert rebuilt_view.features.price_features["close"] == 1.23
    assert rebuilt_view.features.liquidity_features["spread"] == 0.05
    assert rebuilt_view.features.regime_features["regime_index"] == 3.0
    assert rebuilt_view.model_version == "mv_1"
    assert rebuilt_view.scaler_version == "sv_1"


@pytest.mark.asyncio
async def test_replay_drift_calculation():
    clock = Clock(start_time=1000.0)
    queue = PromotionQueue(db_path=DB_FILE, clock=clock)
    await queue.init_table()

    # 1. Empty guard 테스트
    drift, action = await queue.run_replay_correction({}, {})
    assert drift == 0.0
    assert action == "NOOP"

    # 2. normal drift 계산 테스트
    import math
    fast_ranks = {"p1": 1, "p2": 2}
    replay_ranks = {"p1": 2, "p2": 1}
    drift, action = await queue.run_replay_correction(fast_ranks, replay_ranks)
    assert math.isclose(drift, 0.5, rel_tol=1e-5)
    assert action == "CORRECTION_ACTIVE"

    # 3. missing candidate 및 가중치 min_r 테스트
    fast_ranks_2 = {"p1": 1}
    replay_ranks_2 = {"p2": 1}
    drift_2, action_2 = await queue.run_replay_correction(fast_ranks_2, replay_ranks_2)
    assert math.isclose(drift_2, 1.0, rel_tol=1e-5)


@pytest.mark.asyncio
async def test_hysteresis_rule_and_event_logging():
    clock = Clock(start_time=1000.0)
    queue = PromotionQueue(db_path=DB_FILE, clock=clock)
    await queue.init_table()

    # 1. 초기 상태: correction_active = False
    assert not queue.correction_active

    # 2. drift = 0.5 유도 -> correction_active = True & 이벤트 로깅
    drift, action = await queue.run_replay_correction({"p1": 1, "p2": 2}, {"p1": 2, "p2": 1})
    assert queue.correction_active
    assert action == "CORRECTION_ACTIVE"

    # DB에 이벤트가 기록되었는지 검증
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT event_type FROM promotion_event_log WHERE proposal_id = 'SYSTEM'") as cursor:
            rows = await cursor.fetchall()
            events = [r[0] for r in rows]
            assert "REPLAY_DRIFT_HIGH" in events
            assert "REPLAY_CORRECTION_ENABLED" in events

    # 3. drift = 0.2 유도 (중간 구간) -> 상태 유지 (True)
    import math
    drift_mid, action_mid = await queue.run_replay_correction(
        {"p1": 1, "p2": 2, "p3": 3, "p4": 4, "p5": 5},
        {"p1": 2, "p2": 1, "p3": 3, "p4": 4, "p5": 5}
    )
    assert 0.1 < drift_mid < 0.3
    assert queue.correction_active
    assert action_mid == "KEEP_STATE"

    # 4. drift = 0.0 유도 -> correction_active = False & 이벤트 로깅
    drift_low, action_low = await queue.run_replay_correction({"p1": 1}, {"p1": 1})
    assert not queue.correction_active
    assert action_low == "CORRECTION_INACTIVE"

    # DB에 LOW/DISABLED 이벤트 기록 검증
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT event_type FROM promotion_event_log WHERE proposal_id = 'SYSTEM' ORDER BY global_sequence_no DESC LIMIT 2") as cursor:
            rows = await cursor.fetchall()
            events = [r[0] for r in rows]
            assert "REPLAY_CORRECTION_DISABLED" in events
            assert "REPLAY_DRIFT_LOW" in events

    # 5. Replay Rebuild 시에 Hysteresis State 가 복원되는지 확인
    await queue.run_replay_correction({"p1": 1, "p2": 2}, {"p1": 2, "p2": 1})
    assert queue.correction_active

    new_queue = PromotionQueue(db_path=DB_FILE, clock=clock)
    await new_queue.rebuild_materialized_view()
    assert new_queue.correction_active
    assert new_queue.system_event_seq == queue.system_event_seq

@pytest.mark.asyncio
async def test_promotion_blocked_by_replay_correction():
    """active replay correction 상황에서 승격 관련 전이가 차단되는지 검증합니다."""
    clock = Clock(start_time=1000.0)
    queue = PromotionQueue(db_path=DB_FILE, clock=clock)
    await queue.init_table()

    snap = FeatureSnapshot(price_features={}, liquidity_features={}, regime_features={})
    proposal = CandidateProposal(
        proposal_id="prop_block",
        source_strategy_id="strat_1",
        features=snap,
        backtest_result={"roi": 12.0},
        model_version="v1",
        scaler_version="s1"
    )

    # 제안 인입 및 기본 전이
    await queue.ingest_proposal(proposal, str(uuid.uuid4()))
    
    # 1. correction_active=True 설정
    queue.correction_active = True
    queue.rank_drift = 0.4
    
    # 2. CANDIDATE -> SCORED 전이 시도: 랭킹 및 채점은 승격 전이가 아니므로 허용되어야 함
    success = await queue.transition_state("prop_block", "SCORED", str(uuid.uuid4()), {"final_promotion_score": 0.8})
    assert success
    assert queue.materialized_views["prop_block"].status == "SCORED"

    # 3. SCORED -> PROMOTION_PENDING 전이 시도 (승격 전이): 차단되어야 함
    success_promo = await queue.transition_state("prop_block", "PROMOTION_PENDING", str(uuid.uuid4()))
    assert not success_promo
    # 상태는 SCORED로 유지되어야 함
    assert queue.materialized_views["prop_block"].status == "SCORED"
    assert queue.promotion_block_reason == "REPLAY_CORRECTION_ACTIVE"

    # DB에 PROMOTION_BLOCKED_BY_REPLAY_CORRECTION 이벤트가 쌓였는지 확인
    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM promotion_event_log WHERE event_type = 'PROMOTION_BLOCKED_BY_REPLAY_CORRECTION'") as cursor:
            row = await cursor.fetchone()
            assert row is not None
            assert row["proposal_id"] == "prop_block"
            payload = json.loads(row["payload"])
            assert payload["drift"] == 0.4
            assert payload["target_state"] == "PROMOTION_PENDING"

    # 4. correction_active=False 설정 (안정화)
    queue.correction_active = False
    queue.promotion_block_reason = None
    
    # 5. SCORED -> PROMOTION_PENDING 전이 시도: 이제 통과되어야 함
    success_promo_2 = await queue.transition_state("prop_block", "PROMOTION_PENDING", str(uuid.uuid4()))
    assert success_promo_2
    assert queue.materialized_views["prop_block"].status == "PROMOTION_PENDING"


@pytest.mark.asyncio
async def test_replay_correction_safety_guard_details():
    clock = Clock(start_time=1000.0)
    queue = PromotionQueue(db_path=DB_FILE, clock=clock)
    await queue.init_table()

    snap = FeatureSnapshot(price_features={}, liquidity_features={}, regime_features={})
    proposal = CandidateProposal(
        proposal_id="prop_guard_test",
        source_strategy_id="strat_1",
        features=snap,
        backtest_result={"roi": 10.0},
        model_version="v1",
        scaler_version="s1"
    )

    # 제안 인입
    await queue.ingest_proposal(proposal, str(uuid.uuid4()))
    # SCORED 상태로 전이
    await queue.transition_state("prop_guard_test", "SCORED", str(uuid.uuid4()))
    
    assert queue.materialized_views["prop_guard_test"].status == "SCORED"
    initial_seq = queue.materialized_views["prop_guard_test"].sequence_no

    # 1. correction_active=True 일 때 승격 전이(SCORED -> PROMOTION_PENDING) 차단 검증
    # drift = 0.5 유도 -> correction_active=True
    await queue.run_replay_correction({"p1": 1, "p2": 2}, {"p1": 2, "p2": 1})
    assert queue.correction_active
    assert queue.promotion_block_reason == "REPLAY_CORRECTION_ACTIVE"

    clock.sleep(5.0)  # 시간 경과
    blocked_time = clock.now()
    
    # 승격 시도 -> 차단되어야 함
    success = await queue.transition_state("prop_guard_test", "PROMOTION_PENDING", str(uuid.uuid4()))
    assert not success
    
    # status는 SCORED 그대로 유지되는지 검증
    assert queue.materialized_views["prop_guard_test"].status == "SCORED"
    
    # sequence_no와 last_updated_at은 갱신되었는지 검증
    blocked_seq = queue.materialized_views["prop_guard_test"].sequence_no
    assert blocked_seq == initial_seq + 1
    assert queue.materialized_views["prop_guard_test"].last_updated_at == blocked_time

    # 2. rebuild_materialized_view() 수행 후 status 유지, sequence_no/last_updated_at 복원 확인
    new_queue = PromotionQueue(db_path=DB_FILE, clock=clock)
    await new_queue.rebuild_materialized_view()
    
    assert new_queue.correction_active
    assert new_queue.promotion_block_reason == "REPLAY_CORRECTION_ACTIVE"
    assert new_queue.materialized_views["prop_guard_test"].status == "SCORED"
    assert new_queue.materialized_views["prop_guard_test"].sequence_no == blocked_seq
    assert new_queue.materialized_views["prop_guard_test"].last_updated_at == blocked_time

    # 3. correction_active=false로 해제되면 promotion_block_reason이 stale 상태로 남지 않고 해제되는지 검증
    # drift = 0.0 유도
    await queue.run_replay_correction({"p1": 1}, {"p1": 1})
    assert not queue.correction_active
    assert queue.promotion_block_reason is None

    # rebuild 후에도 stale 상태 없이 복원되는지 검증
    rebuild_queue_2 = PromotionQueue(db_path=DB_FILE, clock=clock)
    await rebuild_queue_2.rebuild_materialized_view()
    assert not rebuild_queue_2.correction_active
    assert rebuild_queue_2.promotion_block_reason is None

    # 4. 존재하지 않는 proposal_id에 대한 차단 이벤트는 상태뷰를 새로 만들지 않고 안전하게 무시되는지 검증
    # 수동으로 DB에 존재하지 않는 proposal_id ("non_existent_prop")에 대한 차단 이벤트를 삽입
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("""
            INSERT INTO promotion_event_log (
                event_id, proposal_id, sequence_no, event_type, payload, timestamp
            ) VALUES (?, ?, ?, ?, ?, ?)
        """, (
            str(uuid.uuid4()), "non_existent_prop", 1, "PROMOTION_BLOCKED_BY_REPLAY_CORRECTION",
            json.dumps({"drift": 0.5, "target_state": "PROMOTION_PENDING"}), clock.now()
        ))
        await db.commit()

    # rebuild 실행
    rebuild_queue_3 = PromotionQueue(db_path=DB_FILE, clock=clock)
    await rebuild_queue_3.rebuild_materialized_view()
    
    # 존재하지 않는 proposal_id인 "non_existent_prop"이 뷰에 새로 생성되지 않았음을 확인
    assert "non_existent_prop" not in rebuild_queue_3.materialized_views


