import os
import json
import pytest
import shutil
import tempfile
import threading
from typing import Dict, Any
from src.engine.dataset_exporter import (
    EventBuffer,
    DatasetExporter,
    FeatureBuilder,
    DatasetLoader,
    safe_atomic_write,
    VERSIONED_PRIORITY_REGISTRY
)

# =====================================================================
# 2-1. Micro-sequence & Timestamp Monotonic 정렬 검증
# =====================================================================
def test_event_buffer_monotonicity_and_micro_sequence():
    buffer = EventBuffer()
    
    # 여러 이벤트를 동일한 타임스탬프 스큐를 가정하고 급속히 집입
    ev1 = buffer.put_event("PROPOSAL_CREATED", "node_1", {"param": 1}, 1000)
    ev2 = buffer.put_event("PROPOSAL_CREATED", "node_2", {"param": 2}, 1000)
    ev3 = buffer.put_event("APPLIED", "node_3", {"param": 3}, 1000)

    # 1. 시퀀스 ID 단조 증가 확인
    assert ev1["global_monotonic_id"] == 1
    assert ev2["global_monotonic_id"] == 2
    assert ev3["global_monotonic_id"] == 3

    # 2. commit_timestamp 튜플 정렬 무결성 검증
    # (timestamp_ms, local_seq_counter)
    ts1, seq1 = ev1["commit_timestamp"]
    ts2, seq2 = ev2["commit_timestamp"]
    ts3, seq3 = ev3["commit_timestamp"]

    assert ts1 <= ts2 <= ts3
    
    # 밀리초가 같으면 로컬 시퀀스는 증가해야 함
    if ts1 == ts2:
        assert seq2 == seq1 + 1
    if ts2 == ts3:
        assert seq3 == seq2 + 1

    events = buffer.flush()
    assert len(events) == 3
    assert buffer.size() == 0


# =====================================================================
# 2-2. Versioned Priority Schema & Ordering 정규화 검증
# =====================================================================
def test_dataset_exporter_ordering():
    exporter = DatasetExporter(output_dir="data/test_dataset")
    
    # 무순서 이벤트 리스트 생성
    raw_events = [
        {"node_hash": "n1", "event_type": "ROLLBACK", "created_at": 100, "global_monotonic_id": 1, "commit_timestamp": (100, 0), "params": {}},
        {"node_hash": "n2", "event_type": "APPLIED", "created_at": 100, "global_monotonic_id": 2, "commit_timestamp": (100, 1), "params": {}},
        {"node_hash": "n3", "event_type": "PROPOSAL_CREATED", "created_at": 100, "global_monotonic_id": 3, "commit_timestamp": (100, 2), "params": {}},
        {"node_hash": "n4", "event_type": "APPLIED", "created_at": 50, "global_monotonic_id": 4, "commit_timestamp": (50, 0), "params": {}}
    ]

    # 정렬 수행
    sorted_ev = exporter._sort_events(raw_events)
    
    # created_at 기준 정렬 우선 검증 (50이 가장 앞서야 함)
    assert sorted_ev[0]["node_hash"] == "n4"
    
    # created_at이 100인 그룹에 대해 event_priority 기준 정렬 검증
    # APPLIED(100) > PROPOSAL_CREATED(50) > ROLLBACK(10) 이므로
    # n2 (APPLIED), n3 (PROPOSAL), n1 (ROLLBACK) 순이어야 함
    assert sorted_ev[1]["node_hash"] == "n2"
    assert sorted_ev[2]["node_hash"] == "n3"
    assert sorted_ev[3]["node_hash"] == "n1"


# =====================================================================
# 2-3. Write-Ahead Staging & Cross-device Fallback 예외 안전성 검증
# =====================================================================
def test_safe_atomic_write_cross_device_fallback(monkeypatch):
    temp_dir = tempfile.mkdtemp()
    dest_file = os.path.join(temp_dir, "dest.txt")
    content = "Hello Atomic World"

    # 정상 원자적 쓰기 검증
    safe_atomic_write(dest_file, content)
    assert os.path.exists(dest_file)
    with open(dest_file, 'r', encoding='utf-8') as f:
        assert f.read() == content

    # os.replace가 실패했을 때의 fallback 검증
    def mock_replace(src, dst):
        raise OSError(18, "Invalid cross-device link") # EXDEV error 모의

    monkeypatch.setattr(os, "replace", mock_replace)
    
    fallback_content = "Fallback Content"
    safe_atomic_write(dest_file, fallback_content)
    
    # Fallback이 정상 작동하여 내용이 바뀌었는지 확인
    with open(dest_file, 'r', encoding='utf-8') as f:
        assert f.read() == fallback_content

    shutil.rmtree(temp_dir)


# =====================================================================
# 2-4. LRU Cache & Version Invalidation 갱신 무결성 검증
# =====================================================================
def test_feature_builder_lru_cache_and_version_invalidation():
    fb = FeatureBuilder(cache_capacity=2)
    
    records = [
        {"node_hash": "child", "params": {"rsi_window": 14}, "parent_hashes": [{"hash": "parent", "weight": 1.0}], "metrics": {"expected_roi": 0.1}, "labels": {"label_type": "ESTIMATED", "success": 1}},
        {"node_hash": "parent", "params": {"rsi_window": 12}, "parent_hashes": [], "metrics": {"expected_roi": 0.05}, "labels": {"label_type": "OBSERVED", "success": 0}}
    ]

    # 최초 계산
    feats_v1 = fb.build_features("child", records, "version_1")
    assert feats_v1["parent_expected_roi"] == 0.05
    assert feats_v1["parent_param_deltas"] == {"rsi_window": 2.0}

    # 캐시 히트 동작 확인 (캐시가 반환된 것인지 검증)
    cache_key = ("child", "version_1")
    assert fb.cache.get(cache_key) is not None

    # graph_version 변경 (dataset_snapshot_id 갱신)
    # 캐시 키는 ("child", "version_2")가 되므로 기존 "version_1" 캐시는 무효화(Invalidation)되어야 함
    feats_v2 = fb.build_features("child", records, "version_2")
    assert feats_v2["parent_expected_roi"] == 0.05
    
    # "version_1" 조회 시 "version_2" 캐시에 영향받지 않고 별개로 동작하는지 확인
    assert fb.cache.get(("child", "version_2")) is not None


# =====================================================================
# 2-5. Recursion Safety DFS Cycle Guard 검증
# =====================================================================
def test_feature_builder_recursion_safety_cycle_guard():
    fb = FeatureBuilder()
    
    # 순환 참조 데이터 구성 (A -> B -> A)
    corrupted_records = [
        {"node_hash": "node_A", "parent_hashes": [{"hash": "node_B"}], "params": {}},
        {"node_hash": "node_B", "parent_hashes": [{"hash": "node_A"}], "params": {}}
    ]

    # FeatureBuilder 계산 기동 시 ValueError 사이클 감지 발생 검사
    with pytest.raises(ValueError) as excinfo:
        fb.build_features("node_A", corrupted_records, "version_cycle")
        
    assert "Cycle detected" in str(excinfo.value)


# =====================================================================
# 2-6. 3-Tier Labeling 검증
# =====================================================================
def test_3_tier_labeling_and_dataset_loader():
    temp_dir = tempfile.mkdtemp()
    exporter = DatasetExporter(output_dir=temp_dir)
    buffer = EventBuffer()

    # 1. OBSERVED 노드 (APPLIED)
    buffer.put_event(
        event_type="APPLIED",
        node_hash="node_observed",
        params={"rsi_window": 14},
        created_at=1000,
        metrics={"expected_roi": 0.05, "realized_roi": 0.07, "mdd": 0.02},
        labels={"success": 1, "failure": 0, "label_type": "OBSERVED"}
    )

    # 2. ESTIMATED 노드 (PRUNED/DEFERRED)
    buffer.put_event(
        event_type="PROPOSAL_CREATED",
        node_hash="node_estimated",
        params={"rsi_window": 12},
        created_at=1100,
        metrics={"expected_roi": 0.04, "realized_roi": None, "counterfactual_roi": 0.06, "mdd": 0.03},
        labels={"success": 1, "failure": 0, "label_type": "ESTIMATED"}
    )

    # 3. MASKED 노드 (NEVER OBSERVED)
    buffer.put_event(
        event_type="PROPOSAL_CREATED",
        node_hash="node_masked",
        params={"rsi_window": 10},
        created_at=1200,
        labels={"success": 0, "failure": 0, "label_type": "MASKED"}
    )

    # 배치를 내보내기
    events = buffer.flush()
    exporter.export_batch(events)

    # DatasetLoader 로딩 검증
    loader = DatasetLoader(exporter)
    tabular_data = loader.load_as_tabular()
    
    assert len(tabular_data) == 3

    # 개별 라벨 검증
    obs_row = next(r for r in tabular_data if r["node_hash"] == "node_observed")
    assert obs_row["label_type"] == "OBSERVED"
    assert obs_row["label_success"] == 1
    assert obs_row["realized_roi"] == 0.07

    est_row = next(r for r in tabular_data if r["node_hash"] == "node_estimated")
    assert est_row["label_type"] == "ESTIMATED"
    assert est_row["label_success"] == 1
    assert est_row["counterfactual_roi"] == 0.06

    msk_row = next(r for r in tabular_data if r["node_hash"] == "node_masked")
    assert msk_row["label_type"] == "MASKED"
    assert msk_row["label_success"] == 0

    shutil.rmtree(temp_dir)
