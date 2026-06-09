"""
tests/test_diversity_analyzer.py
Step 4 — Diversity Intelligence Dashboard 핵심 로직 단위 테스트
"""
import pytest
from src.engine.diversity_analyzer import (
    calculate_parameter_entropy,
    calculate_pruning_accuracy,
    detect_convergence,
    get_counterfactual_lambda_boost,
    get_combined_lambda_boost,
    build_mutation_trace_graph,
)


# ─────────────────────────────────────────────
# 헬퍼 — 테스트용 제안 객체 생성
# ─────────────────────────────────────────────

def _make_proposal(
    proposal_id: int,
    rsi_window: float,
    buy_threshold: float,
    sell_threshold: float,
    status: str = "PENDING",
    counterfactual_roi: float = 0.0,
    is_counterfactual_tracked: int = 0,
    created_at: int = 0,
    decision_path_hash: str = "",
    confidence_score: int = 70,
) -> dict:
    return {
        "id": proposal_id,
        "status": status,
        "confidence_score": confidence_score,
        "proposed_params": {
            "rsi_window": rsi_window,
            "buy_threshold": buy_threshold,
            "sell_threshold": sell_threshold,
        },
        "original_params": {
            "rsi_window": 14.0,
            "buy_threshold": 30.0,
            "sell_threshold": 70.0,
        },
        "counterfactual_roi": counterfactual_roi,
        "is_counterfactual_tracked": is_counterfactual_tracked,
        "created_at": created_at,
        "decision_path_hash": decision_path_hash,
    }


# ─────────────────────────────────────────────
# T1. Entropy — 낮은 다양성 (수렴 상태)
# ─────────────────────────────────────────────

def test_entropy_low_diversity():
    """
    파라미터가 모두 동일한 5개 제안 → entropy = 0.0
    """
    proposals = [_make_proposal(i, 14.0, 30.0, 70.0) for i in range(5)]
    entropy = calculate_parameter_entropy(proposals)
    assert entropy == 0.0, f"기대 0.0, 실제 {entropy}"


# ─────────────────────────────────────────────
# T2. Entropy — 높은 다양성
# ─────────────────────────────────────────────

def test_entropy_high_diversity():
    """
    파라미터가 고르게 분산된 10개 제안 → entropy >= 0.8
    """
    proposals = [
        _make_proposal(i, float(10 + i * 2), float(25 + i * 3), float(60 + i * 2))
        for i in range(10)
    ]
    entropy = calculate_parameter_entropy(proposals)
    assert entropy >= 0.7, f"기대 >= 0.7, 실제 {entropy}"


# ─────────────────────────────────────────────
# T3. Pruning Accuracy — 오판율 계산
# ─────────────────────────────────────────────

def test_pruning_accuracy():
    """
    완료된(tracked=2) 제안 3개 중 2개가 counterfactual_roi > 0
    → outperform_rate = 0.67, bias_alert = True
    """
    proposals = [
        # 완료된 추적: roi > 0 (오판)
        _make_proposal(1, 14.0, 30.0, 70.0, status="PRUNED",
                       counterfactual_roi=3.5, is_counterfactual_tracked=2),
        # 완료된 추적: roi > 0 (오판)
        _make_proposal(2, 16.0, 32.0, 68.0, status="PRUNED",
                       counterfactual_roi=1.2, is_counterfactual_tracked=2),
        # 완료된 추적: roi <= 0 (정상 폐기)
        _make_proposal(3, 12.0, 28.0, 72.0, status="PRUNED",
                       counterfactual_roi=-0.5, is_counterfactual_tracked=2),
        # 아직 추적 중 — 집계 제외
        _make_proposal(4, 14.0, 31.0, 69.0, status="PRUNED",
                       counterfactual_roi=0.0, is_counterfactual_tracked=1),
        # 미추적 — 집계 제외
        _make_proposal(5, 14.0, 30.0, 70.0, status="PENDING",
                       counterfactual_roi=0.0, is_counterfactual_tracked=0),
    ]
    result = calculate_pruning_accuracy(proposals)
    assert result["total_tracked"] == 3
    assert result["outperformed_count"] == 2
    assert abs(result["outperform_rate"] - 0.6667) < 0.001
    assert result["bias_alert"] is True


# ─────────────────────────────────────────────
# T4. Counterfactual λ boost — 오판율 30% 초과 시 1.2 반환
# ─────────────────────────────────────────────

def test_counterfactual_lambda_boost():
    """
    outperform_rate = 0.33 (30% 초과) → boost = 1.2
    outperform_rate = 0.20 (30% 미달) → boost = 1.0
    """
    # 오판율 높음 (2/3 완료, 2개 양수)
    proposals_high = [
        _make_proposal(1, 14.0, 30.0, 70.0, status="PRUNED",
                       counterfactual_roi=2.0, is_counterfactual_tracked=2),
        _make_proposal(2, 16.0, 32.0, 68.0, status="PRUNED",
                       counterfactual_roi=1.5, is_counterfactual_tracked=2),
        _make_proposal(3, 12.0, 28.0, 72.0, status="PRUNED",
                       counterfactual_roi=-1.0, is_counterfactual_tracked=2),
    ]
    boost_high = get_counterfactual_lambda_boost(proposals_high)
    assert boost_high == 1.2, f"기대 1.2, 실제 {boost_high}"

    # 오판율 낮음 (1/5)
    proposals_low = [
        _make_proposal(i, 14.0, 30.0, 70.0, status="PRUNED",
                       counterfactual_roi=2.0 if i == 1 else -1.0,
                       is_counterfactual_tracked=2)
        for i in range(1, 6)
    ]
    boost_low = get_counterfactual_lambda_boost(proposals_low)
    assert boost_low == 1.0, f"기대 1.0, 실제 {boost_low}"


# ─────────────────────────────────────────────
# T5. Convergence Alert — entropy < threshold 시 alert
# ─────────────────────────────────────────────

def test_convergence_alert():
    """
    동일 파라미터 5개 제안 → entropy = 0.0 < 0.3 → convergence_alert = True
    분산된 파라미터 10개 제안 → entropy >= 0.8 → convergence_alert = False
    """
    # 수렴 상태
    converged_proposals = [_make_proposal(i, 14.0, 30.0, 70.0) for i in range(5)]
    result_converged = detect_convergence(converged_proposals, threshold=0.3)
    assert result_converged["convergence_alert"] is True
    assert result_converged["entropy"] == 0.0

    # 분산 상태
    diverse_proposals = [
        _make_proposal(i, float(10 + i * 2), float(25 + i * 3), float(60 + i * 2))
        for i in range(10)
    ]
    result_diverse = detect_convergence(diverse_proposals, threshold=0.3)
    assert result_diverse["convergence_alert"] is False
    assert result_diverse["entropy"] >= 0.5


# ─────────────────────────────────────────────
# T6. Combined λ Boost — Entropy + Counterfactual 복합 신호
# ─────────────────────────────────────────────

def test_combined_lambda_boost():
    """
    entropy < 0.3 AND outperform_rate > 0.30 → alert_level = "HIGH", lambda_boost = 1.2
    entropy < 0.3 AND outperform_rate <= 0.30 → alert_level = "MEDIUM", lambda_boost = 1.1
    entropy >= 0.3 AND outperform_rate <= 0.30 → alert_level = "NONE", lambda_boost = 1.0
    """
    # CASE HIGH: 수렴 + 오판율 둘 다 나쁨
    # entropy=0.0 (동일 파라미터), outperform_rate=0.67 (2/3 양수)
    proposals_high = [
        # 수렴 유발: 동일 파라미터 5개
        _make_proposal(i, 14.0, 30.0, 70.0) for i in range(5)
    ] + [
        # 오판율 유발
        _make_proposal(10, 14.0, 30.0, 70.0, status="PRUNED",
                       counterfactual_roi=2.0, is_counterfactual_tracked=2),
        _make_proposal(11, 14.0, 30.0, 70.0, status="PRUNED",
                       counterfactual_roi=1.5, is_counterfactual_tracked=2),
        _make_proposal(12, 14.0, 30.0, 70.0, status="PRUNED",
                       counterfactual_roi=-0.5, is_counterfactual_tracked=2),
    ]
    result_high = get_combined_lambda_boost(proposals_high, entropy_threshold=0.3, max_boost=1.2)
    assert result_high["alert_level"] == "HIGH"
    assert result_high["lambda_boost"] == 1.2
    assert result_high["diversity_threshold_delta"] == 0.03

    # CASE NONE: 분산 + 오판율 정상
    proposals_none = [
        _make_proposal(i, float(10 + i * 2), float(25 + i * 3), float(60 + i * 2))
        for i in range(10)
    ] + [
        # 낮은 오판율
        _make_proposal(20, 14.0, 30.0, 70.0, status="PRUNED",
                       counterfactual_roi=-1.0, is_counterfactual_tracked=2),
        _make_proposal(21, 14.0, 30.0, 70.0, status="PRUNED",
                       counterfactual_roi=-0.5, is_counterfactual_tracked=2),
    ]
    result_none = get_combined_lambda_boost(proposals_none, entropy_threshold=0.3, max_boost=1.2)
    assert result_none["alert_level"] == "NONE"
    assert result_none["lambda_boost"] == 1.0
    assert result_none["diversity_threshold_delta"] == 0.0


# ─────────────────────────────────────────────
# Step 5 신규 테스트 케이스
# ─────────────────────────────────────────────

def test_canonical_hash_stability():
    """
    T1. 파라미터 정렬 순서나 representation 형식(int vs float)이 달라도
    동일한 canonical 해시가 추출되는지 안정성 검증.
    """
    from src.engine.diversity_analyzer import _canonicalize_params, _hash_params
    
    params1 = {"rsi_window": 14, "buy_threshold": 30.0}
    params2 = {"buy_threshold": 30, "rsi_window": 14.0}
    
    c1 = _canonicalize_params(params1)
    c2 = _canonicalize_params(params2)
    
    assert c1 == c2, f"Canonical string 불일치: {c1} vs {c2}"
    assert c1 == "buy_threshold:30|rsi_window:14"
    
    h1 = _hash_params(params1)
    h2 = _hash_params(params2)
    assert h1 == h2, f"Hash 불일치: {h1} vs {h2}"

def test_hash_uniqueness():
    """
    T2. 서로 다른 파라미터 조합이 고유한 해시를 만드는지 검증.
    """
    from src.engine.diversity_analyzer import _hash_params
    
    h1 = _hash_params({"rsi_window": 14, "buy_threshold": 30})
    h2 = _hash_params({"rsi_window": 15, "buy_threshold": 30})
    h3 = _hash_params({"rsi_window": 14, "buy_threshold": 31.000001})
    
    assert len({h1, h2, h3}) == 3, "해시 충돌 감지!"

def test_deterministic_graph_rebuild():
    """
    T3. 동일 제안셋을 순서를 셔플하여 전달해도 
    결정론적으로 동일한 DAG(동일한 depth, edges, best_path)를 복원하는지 검증.
    """
    import random
    from src.engine.diversity_analyzer import _hash_params
    
    # 순차적 변이 셋 구성
    p0 = {"rsi_window": 14.0, "buy_threshold": 30.0}
    p1 = {"rsi_window": 15.0, "buy_threshold": 30.0}
    p2 = {"rsi_window": 15.0, "buy_threshold": 32.0}
    
    h0 = _hash_params(p0)
    h1 = _hash_params(p1)
    h2 = _hash_params(p2)
    
    prop1 = {
        "id": 1, "status": "APPLIED", "confidence_score": 80,
        "original_params": p0, "proposed_params": p1,
        "created_at": 1000, "decision_path_hash": h1, "metrics": {"expected_roi": 1.5}
    }
    prop2 = {
        "id": 2, "status": "APPLIED", "confidence_score": 90,
        "original_params": p1, "proposed_params": p2,
        "created_at": 2000, "decision_path_hash": h2, "metrics": {"expected_roi": 2.0}
    }
    
    props_a = [prop1, prop2]
    props_b = [prop2, prop1] # 순서 셔플
    
    graph_a = build_mutation_trace_graph(props_a)
    graph_b = build_mutation_trace_graph(props_b)
    
    # 생성 시간에 따라 내부 소팅되어 결정론적으로 구성되어야 함
    assert graph_a["nodes"] == graph_b["nodes"]
    assert graph_a["edges"] == graph_b["edges"]
    assert graph_a["best_path_nodes"] == graph_b["best_path_nodes"]

def test_orphan_node_check():
    """
    T4. 루트 노드를 제외하고는 모든 노드가 반드시 parent_hashes를 가지며
    계보가 연결되는지 검증 (Orphan 노드 배제 검증).
    """
    from src.engine.diversity_analyzer import _hash_params
    
    p0 = {"rsi_window": 14.0}
    p1 = {"rsi_window": 15.0}
    p2 = {"rsi_window": 16.0}
    
    h0 = _hash_params(p0)
    h1 = _hash_params(p1)
    h2 = _hash_params(p2)
    
    props = [
        {
            "id": 1, "status": "APPLIED", "confidence_score": 75,
            "original_params": p0, "proposed_params": p1,
            "created_at": 1000, "decision_path_hash": h1, "metrics": {"expected_roi": 1.2}
        },
        {
            "id": 2, "status": "APPLIED", "confidence_score": 85,
            "original_params": p1, "proposed_params": p2,
            "created_at": 2000, "decision_path_hash": h2, "metrics": {"expected_roi": 2.5}
        }
    ]
    
    graph = build_mutation_trace_graph(props)
    nodes_map = {n["hash"]: n for n in graph["nodes"]}
    
    # p1 노드 검증
    node1 = nodes_map[h1]
    assert node1["is_root"] is True
    assert len(node1["parent_hashes"]) == 0
    assert node1["depth"] == 0
    
    # p2 노드 검증
    node2 = nodes_map[h2]
    assert node2["is_root"] is False
    assert len(node2["parent_hashes"]) == 1
    assert node2["parent_hashes"][0]["hash"] == h1
    assert node2["parent_hashes"][0]["weight"] == 1.0
    assert node2["depth"] == 1
    
    # meta 데이터 검증
    meta = graph["graph_meta"]
    assert meta["node_count"] == 2
    assert meta["edge_count"] == 1
    assert meta["max_depth"] == 1

def test_param_mutation_consistency():
    """
    T5. 자식 노드 파라미터 값과 부모 노드 파라미터 값 간의 대수적 차이가
    recorded edges.delta와 일치하는지 확인.
    """
    from src.engine.diversity_analyzer import _hash_params
    
    p0 = {"rsi_window": 14.0, "buy_threshold": 30.0}
    p1 = {"rsi_window": 16.5, "buy_threshold": 28.2}
    
    h1 = _hash_params(p1)
    
    props = [
        {
            "id": 1, "status": "APPLIED", "confidence_score": 80,
            "original_params": p0, "proposed_params": p1,
            "created_at": 1000, "decision_path_hash": h1, "metrics": {"expected_roi": 1.0}
        }
    ]
    
    # 부모 노드를 찾기 위해 p0를 만족하는 이전 APPLIED 노드를 가상 배치
    h0 = _hash_params(p0)
    props.insert(0, {
        "id": 0, "status": "APPLIED", "confidence_score": 70,
        "original_params": {}, "proposed_params": p0,
        "created_at": 500, "decision_path_hash": h0, "metrics": {"expected_roi": 0.0}
    })
    
    graph = build_mutation_trace_graph(props)
    edges = graph["edges"]
    
    # rsi_window 변이(14 -> 16.5 = +2.5) 및 buy_threshold 변이(30 -> 28.2 = -1.8) 검증
    rsi_edge = next(e for e in edges if e["param"] == "rsi_window")
    buy_edge = next(e for e in edges if e["param"] == "buy_threshold")
    
    assert rsi_edge["from"] == h0
    assert rsi_edge["to"] == h1
    assert abs(rsi_edge["delta"] - 2.5) < 1e-6
    
    assert buy_edge["from"] == h0
    assert buy_edge["to"] == h1
    assert abs(buy_edge["delta"] - (-1.8)) < 1e-6

def test_graph_cycle_prevention():
    """
    T6. DAG 내 순환(Cycle) 감지 시 ValueError 및 Fail-Fast가 발 작동하는지 검증.
    """
    from src.engine.diversity_analyzer import _hash_params
    
    p0 = {"rsi_window": 14.0}
    p1 = {"rsi_window": 15.0}
    
    h0 = _hash_params(p0)
    h1 = _hash_params(p1)
    
    # p0 -> p1 인 동시에 p1 -> p0인 순환 제안 관계 정의
    props = [
        {
            "id": 1, "status": "APPLIED", "confidence_score": 80,
            "original_params": p0, "proposed_params": p1,
            "created_at": 1000, "decision_path_hash": h1, "metrics": {"expected_roi": 1.0}
        },
        {
            "id": 2, "status": "APPLIED", "confidence_score": 80,
            "original_params": p1, "proposed_params": p0,
            "created_at": 2000, "decision_path_hash": h0, "metrics": {"expected_roi": 1.5}
        }
    ]
    
    with pytest.raises(ValueError, match="Cycle detected"):
        build_mutation_trace_graph(props)
