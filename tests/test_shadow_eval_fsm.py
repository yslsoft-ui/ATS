# -*- coding: utf-8 -*-

import asyncio
import time
import pytest
from src.database.repository import InMemoryTradingRepository

@pytest.mark.asyncio
async def test_eval_claim_and_double_claim():
    """
    evaluation claim이 원자적으로 작동하여 중복 선점이 차단되는지 검증합니다.
    """
    repo = InMemoryTradingRepository()
    
    # 1. PENDING evaluation 인메모리 생성
    pe_id = await repo.insert_proposal_evaluation({
        "proposal_id": 1001,
        "horizon_name": "10m",
        "due_at": int(time.time()) - 100,
        "predicted_roi_7d": 5.0,
        "actual_roi_7d": 0.0,
        "roi_divergence": 0.0,
        "predicted_trade_count_7d": 10,
        "actual_trade_count_7d": 0,
        "trade_count_divergence": 0,
        "evaluation_status": "PENDING"
    })
    
    # 2. 첫 번째 워커가 선점 시도 -> 성공 기대
    now_ts = int(time.time())
    success1 = await repo.claim_evaluation(pe_id, now_ts)
    assert success1 is True, "첫 번째 claim은 반드시 성공해야 합니다."
    
    # 3. 두 번째 워커가 동시에 선점 시도 -> 실패 기대 (PENDING이 아니므로)
    success2 = await repo.claim_evaluation(pe_id, now_ts)
    assert success2 is False, "이미 EVALUATING 상태인 레코드는 claim에 실패해야 합니다."

@pytest.mark.asyncio
async def test_eval_complete():
    """
    평가 완료(complete_evaluation) 후 결과 적재 및 락 해제를 검증합니다.
    """
    repo = InMemoryTradingRepository()
    pe_id = await repo.insert_proposal_evaluation({
        "proposal_id": 1002,
        "horizon_name": "30m",
        "due_at": int(time.time()) - 10,
        "predicted_roi_7d": 5.0,
        "actual_roi_7d": 0.0,
        "roi_divergence": 0.0,
        "predicted_trade_count_7d": 20,
        "actual_trade_count_7d": 0,
        "trade_count_divergence": 0,
        "evaluation_status": "PENDING"
    })
    
    # claim
    await repo.claim_evaluation(pe_id, int(time.time()))
    
    # complete
    evaluated_at = int(time.time())
    await repo.complete_evaluation(
        pe_id=pe_id,
        actual_roi=4.5,
        roi_div=-0.5,
        actual_trades=18,
        trade_div=-2,
        evaluated_at=evaluated_at
    )
    
    # 상태 대조
    evals = await repo.get_proposal_evaluations(1002)
    assert len(evals) == 1
    ev = evals[0]
    assert ev["evaluation_status"] == "COMPLETED"
    assert ev["actual_roi_7d"] == 4.5
    assert ev["roi_divergence"] == -0.5
    assert ev["actual_trade_count_7d"] == 18
    assert ev["trade_count_divergence"] == -2
    assert ev["locked_at"] is None

@pytest.mark.asyncio
async def test_eval_fail_retry_under_limit():
    """
    실패(fail_evaluation) 시 최대 재시도 횟수 미만이면 PENDING 원복 및 retry_count 가산을 검증합니다.
    """
    repo = InMemoryTradingRepository()
    pe_id = await repo.insert_proposal_evaluation({
        "proposal_id": 1003,
        "horizon_name": "2h",
        "due_at": int(time.time()) - 50,
        "predicted_roi_7d": 5.0,
        "actual_roi_7d": 0.0,
        "roi_divergence": 0.0,
        "predicted_trade_count_7d": 30,
        "actual_trade_count_7d": 0,
        "trade_count_divergence": 0,
        "evaluation_status": "PENDING"
    })
    
    # 1. 1회차 실패 처리
    await repo.claim_evaluation(pe_id, int(time.time()))
    await repo.fail_evaluation(pe_id, "network error", retry_count=0, max_retries=3)
    
    evals = await repo.get_proposal_evaluations(1003)
    ev = evals[0]
    assert ev["evaluation_status"] == "PENDING"
    assert ev["retry_count"] == 1
    assert ev["last_error"] == "network error"
    assert ev["locked_at"] is None
    
    # 2. 2회차 실패 처리
    await repo.claim_evaluation(pe_id, int(time.time()))
    await repo.fail_evaluation(pe_id, "timeout", retry_count=1, max_retries=3)
    
    evals = await repo.get_proposal_evaluations(1003)
    ev = evals[0]
    assert ev["evaluation_status"] == "PENDING"
    assert ev["retry_count"] == 2
    assert ev["last_error"] == "timeout"

@pytest.mark.asyncio
async def test_eval_fail_retry_over_limit():
    """
    재시도 횟수가 한도(max_retries)에 도달하면 ERROR로 고정 종결되는지 검증합니다.
    """
    repo = InMemoryTradingRepository()
    pe_id = await repo.insert_proposal_evaluation({
        "proposal_id": 1004,
        "horizon_name": "7d",
        "due_at": int(time.time()) - 100,
        "predicted_roi_7d": 3.0,
        "actual_roi_7d": 0.0,
        "roi_divergence": 0.0,
        "predicted_trade_count_7d": 15,
        "actual_trade_count_7d": 0,
        "trade_count_divergence": 0,
        "evaluation_status": "PENDING"
    })
    
    # 3회 실패하고 4회째 한도 초과 시도 가정
    await repo.claim_evaluation(pe_id, int(time.time()))
    await repo.fail_evaluation(pe_id, "fatal lock exceeded", retry_count=3, max_retries=3)
    
    evals = await repo.get_proposal_evaluations(1004)
    ev = evals[0]
    assert ev["evaluation_status"] == "ERROR", "재시도 한도를 넘어가면 ERROR 상태여야 합니다."
    assert ev["last_error"] == "fatal lock exceeded"
    assert ev["locked_at"] is None

@pytest.mark.asyncio
async def test_stale_lock_recovery():
    """
    락 타임아웃 경과(stale lock)된 레코드를 정상 감지하고 복구 루틴으로 복구하는지 검증합니다.
    """
    repo = InMemoryTradingRepository()
    pe_id = await repo.insert_proposal_evaluation({
        "proposal_id": 1005,
        "horizon_name": "30m",
        "due_at": int(time.time()) - 500,
        "predicted_roi_7d": 5.0,
        "actual_roi_7d": 0.0,
        "roi_divergence": 0.0,
        "predicted_trade_count_7d": 20,
        "actual_trade_count_7d": 0,
        "trade_count_divergence": 0,
        "evaluation_status": "PENDING"
    })
    
    # 350초 전에 락이 걸린 상태로 획득 시뮬레이션
    stale_time_s = int(time.time()) - 350
    await repo.claim_evaluation(pe_id, stale_time_s)
    
    # cutoff 설정 (300초 전)
    cutoff = int(time.time()) - 300
    
    # stale 락 감지 조회
    stale_evals = await repo.get_stale_evaluating_evaluations(cutoff)
    assert len(stale_evals) == 1, "300초 이전에 락이 걸린 EVALUATING 상태는 스캔되어야 합니다."
    
    # 복구 실행
    target = stale_evals[0]
    await repo.recover_stale_evaluation(
        pe_id=target["id"],
        retry_count=target.get("retry_count", 0),
        max_retries=3,
        error_msg="LOCK_TIMEOUT"
    )
    
    # 복구 상태 대조
    evals = await repo.get_proposal_evaluations(1005)
    ev = evals[0]
    assert ev["evaluation_status"] == "PENDING", "stale lock 복구 후 PENDING 상태로 롤백되어야 합니다."
    assert ev["retry_count"] == 1
    assert ev["locked_at"] is None

@pytest.mark.asyncio
async def test_baseline_capture_and_usage():
    """
    baseline_value가 NULL인 pending 평가 목록 조회 및 baseline 업데이트가 정상 수행되는지 검증합니다.
    """
    repo = InMemoryTradingRepository()
    now_ts = int(time.time())
    
    # 1. baseline이 없는 PENDING 추가 (due_at - horizon_value <= now_ts 도달한 상황 가정)
    pe_id = await repo.insert_proposal_evaluation({
        "proposal_id": 1006,
        "horizon_name": "10m",
        "horizon_value": 600,
        "due_at": now_ts + 300, # start_ts = now_ts - 300, 시작 시점 지났음
        "predicted_roi_7d": 1.0,
        "actual_roi_7d": 0.0,
        "evaluation_status": "PENDING"
    })
    
    # 2. baseline이 아직 없는 대상 조회
    pending_list = await repo.get_pending_evaluations_without_baseline(now_ts)
    assert len(pending_list) == 1
    assert pending_list[0]["id"] == pe_id
    
    # 3. baseline 업데이트
    await repo.update_baseline_snapshot(pe_id, 50000.0, now_ts - 300, 0)
    
    # 4. 업데이트 후 더 이상 baseline 미적용 대상으로 조회되지 않는지 검증
    pending_list_after = await repo.get_pending_evaluations_without_baseline(now_ts)
    assert len(pending_list_after) == 0
    
    # 5. DB에 정상 반영되었는지 값 검증
    evals = await repo.get_proposal_evaluations(1006)
    assert evals[0]["baseline_value"] == 50000.0
    assert evals[0]["baseline_timestamp"] == now_ts - 300

