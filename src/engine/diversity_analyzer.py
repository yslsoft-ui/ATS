"""
Diversity Analyzer — 전략 파라미터 공간 다양성 분석 모듈

책임 범위:
- 파라미터 집합의 Entropy(다양성 지수) 산출
- Pruning Accuracy(채점 오판율) 계산
- 수렴 경고(Convergence Alert) 감지
- Mutation Trace Graph 빌드
- Counterfactual 기반 λ 보정 계수(boost) 반환

이 모듈은 순수 계산 함수만 포함합니다 (DB 접근 없음).
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional


# ─────────────────────────────────────────────
# 1. Entropy 계산
# ─────────────────────────────────────────────

def _normalize_params(proposals: List[Dict[str, Any]]) -> Dict[str, List[float]]:
    """
    제안 리스트에서 수치형 파라미터별 값 목록을 추출합니다.
    문자열·None 값은 무시합니다.
    """
    param_map: Dict[str, List[float]] = {}
    for prop in proposals:
        raw = prop.get("proposed_params") or {}
        if isinstance(raw, str):
            import json
            try:
                raw = json.loads(raw)
            except Exception:
                raw = {}
        for k, v in raw.items():
            if isinstance(v, (int, float)):
                param_map.setdefault(k, []).append(float(v))
    return param_map


def _bin_entropy(values: List[float], bins: int = 5) -> float:
    """
    값 목록을 bins개 구간으로 히스토그램화한 뒤 정규화된 Shannon Entropy를 반환합니다.
    반환 범위: 0.0 (완전 수렴) ~ 1.0 (완전 분산)
    """
    if len(values) < 2:
        return 0.0

    vmin, vmax = min(values), max(values)
    if vmax == vmin:
        return 0.0  # 모든 값이 동일

    bin_size = (vmax - vmin) / bins
    counts: Dict[int, int] = {}
    for v in values:
        b = int((v - vmin) / bin_size)
        b = min(b, bins - 1)  # 최댓값은 마지막 bin에 포함
        counts[b] = counts.get(b, 0) + 1

    n = len(values)
    entropy = 0.0
    for c in counts.values():
        p = c / n
        if p > 0:
            entropy -= p * math.log2(p)

    max_entropy = math.log2(bins)
    return entropy / max_entropy if max_entropy > 0 else 0.0


def calculate_parameter_entropy(proposals: List[Dict[str, Any]], bins: int = 5) -> float:
    """
    제안 목록 전체의 파라미터 공간 다양성 지수(Entropy)를 반환합니다.
    
    각 파라미터의 bin entropy를 산출한 뒤 평균을 반환합니다.
    반환 범위: 0.0 (완전 수렴) ~ 1.0 (완전 분산)
    """
    param_map = _normalize_params(proposals)
    if not param_map:
        return 0.0

    entropies = [_bin_entropy(vals, bins) for vals in param_map.values()]
    return sum(entropies) / len(entropies)


# ─────────────────────────────────────────────
# 2. Pruning Accuracy (채점 오판율)
# ─────────────────────────────────────────────

def calculate_pruning_accuracy(proposals: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    완료된 Counterfactual 추적(is_counterfactual_tracked=2) 제안 중
    counterfactual_roi가 양수인 비율(오판율)을 반환합니다.

    반환 형식:
      {
        "total_tracked": int,
        "outperformed_count": int,   # counterfactual_roi > 0
        "outperform_rate": float,    # 0.0 ~ 1.0
        "bias_alert": bool           # outperform_rate > 0.30
      }
    """
    completed = [
        p for p in proposals
        if p.get("is_counterfactual_tracked") == 2
    ]
    total = len(completed)
    if total == 0:
        return {
            "total_tracked": 0,
            "outperformed_count": 0,
            "outperform_rate": 0.0,
            "bias_alert": False,
        }

    outperformed = sum(
        1 for p in completed
        if (p.get("counterfactual_roi") or 0.0) > 0.0
    )
    rate = outperformed / total
    return {
        "total_tracked": total,
        "outperformed_count": outperformed,
        "outperform_rate": round(rate, 4),
        "bias_alert": rate > 0.30,
    }


# ─────────────────────────────────────────────
# 3. Convergence Alert
# ─────────────────────────────────────────────

def detect_convergence(
    proposals: List[Dict[str, Any]],
    threshold: float = 0.3,
    bins: int = 5,
) -> Dict[str, Any]:
    """
    현재 파라미터 공간 Entropy를 계산하고 수렴 경고 여부를 반환합니다.

    반환 형식:
      {
        "entropy": float,
        "threshold": float,
        "convergence_alert": bool,
        "param_distributions": { param_name: {"mean": ..., "std": ..., "values": [...]} }
      }
    """
    param_map = _normalize_params(proposals)
    entropy = calculate_parameter_entropy(proposals, bins)

    distributions: Dict[str, Any] = {}
    for k, vals in param_map.items():
        mean = sum(vals) / len(vals) if vals else 0.0
        variance = sum((v - mean) ** 2 for v in vals) / len(vals) if len(vals) > 1 else 0.0
        distributions[k] = {
            "mean": round(mean, 4),
            "std": round(math.sqrt(variance), 4),
            "values": vals,
        }

    return {
        "entropy": round(entropy, 4),
        "threshold": threshold,
        "convergence_alert": entropy < threshold,
        "param_distributions": distributions,
    }


# ─────────────────────────────────────────────
# 4. Mutation Trace Graph & Canonicalization
# ─────────────────────────────────────────────

def _canonicalize_params(params: dict) -> str:
    """
    엄격한 5단계 파라미터 정규화 규칙을 적용하여 canonical string을 반환합니다:
    1. Type Coercion: 모든 int, float 값을 float으로 강제 변환 (Type Guard 포함)
    2. Rounding: round(v, 6)
    3. Formatting: f"{v:.6f}".rstrip('0').rstrip('.') 포맷팅
    4. Sorting: 키 사전 순 정렬
    5. Joining: '|' 구분자로 결합
    """
    if not params:
        return ""
    
    parts = []
    for k in sorted(params.keys()):
        v = params[k]
        if isinstance(v, (int, float)):
            v_float = float(v)
            v_rounded = round(v_float, 6)
            v_str = f"{v_rounded:.6f}".rstrip('0').rstrip('.')
        else:
            v_str = str(v).strip()
        parts.append(f"{k}:{v_str}")
    
    return "|".join(parts)


def _hash_params(params: dict) -> str:
    """
    정규화된 canonical 파라미터 문자열의 SHA-256 해시를 반환합니다.
    """
    import hashlib
    canonical = _canonicalize_params(params)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_mutation_trace_graph(proposals: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    제안 목록에서 파라미터 변이 계보(Mutation Trace Graph)를 구성합니다.

    반환 형식:
      {
        "nodes": [{"id": proposal_id, "hash": hash, "parent_hashes": [{"hash": ..., "weight": 1.0}], ...}, ...],
        "edges": [{"from": parent_hash, "to": child_hash, "param": key, "delta": value}, ...],
        "best_path_nodes": [hash1, hash2, ...],
        "param_trend": { param_name: [{"ts": ..., "value": ...}, ...] },
        "graph_meta": { "node_count": int, "edge_count": int, "max_depth": int, "pruned_count": int, ... }
      }
    """
    nodes = []
    edges = []
    param_trend: Dict[str, List[Dict[str, Any]]] = {}

    # 1. 생성 시간 순 정렬
    sorted_props = sorted(proposals, key=lambda p: p.get("created_at") or 0)

    # 2. 해시 및 파라미터 셋 매핑 생성
    proposal_by_hash: Dict[str, Dict[str, Any]] = {}
    param_key_to_proposal: Dict[str, Dict[str, Any]] = {}

    # 중복 제거를 위해 노드 고유성 확보
    unique_props = []
    seen_hashes = set()
    for prop in sorted_props:
        p_id = prop.get("id")
        raw_proposed = prop.get("proposed_params") or {}
        if isinstance(raw_proposed, str):
            import json
            try: raw_proposed = json.loads(raw_proposed)
            except Exception: raw_proposed = {}
            
        p_hash = prop.get("decision_path_hash") or _hash_params(raw_proposed)
        if p_hash in seen_hashes:
            continue
        seen_hashes.add(p_hash)
        unique_props.append((prop, p_hash, raw_proposed))
        
        # 1단계: 모든 제안의 proposed_params 키와 해시를 맵에 선등록
        prop_key = _canonicalize_params(raw_proposed)
        param_key_to_proposal[prop_key] = {"hash": p_hash, "id": p_id}

    # 3. 노드 정보 추출 및 부모 관계 매핑 (2단계 루프)
    adj_list: Dict[str, List[str]] = {}  # parent -> children
    
    for prop, p_hash, raw_proposed in unique_props:
        p_id = prop.get("id")
        raw_original = prop.get("original_params") or {}
        if isinstance(raw_original, str):
            import json
            try: raw_original = json.loads(raw_original)
            except Exception: raw_original = {}

        metrics = prop.get("metrics") or {}
        expected_roi = float(metrics.get("expected_roi") or metrics.get("roi_7d") or 0.0)
        
        # 부모 노드 찾기
        orig_key = _canonicalize_params(raw_original)
        parent_prop = param_key_to_proposal.get(orig_key)
        
        parent_hashes = []
        is_root = True
        
        if parent_prop and parent_prop["hash"] != p_hash:
            parent_hash = parent_prop["hash"]
            parent_hashes.append({"hash": parent_hash, "weight": 1.0})
            is_root = False
            
            # 인접 리스트 추가 (parent -> child)
            adj_list.setdefault(parent_hash, []).append(p_hash)

        node_info = {
            "id": p_id,
            "hash": p_hash,
            "parent_hashes": parent_hashes,
            "is_root": is_root,
            "depth": 0,  # 6.5단계에서 최종 계산
            "score": prop.get("confidence_score") or 50,
            "status": prop.get("status"),
            "created_at": prop.get("created_at"),
            "proposed_params": raw_proposed,
            "original_params": raw_original,
            "expected_roi": expected_roi,
            "counterfactual_roi": prop.get("counterfactual_roi") or 0.0,
        }
        nodes.append(node_info)
        proposal_by_hash[p_hash] = node_info

        # 4. 에지 생성 및 Delta 연산 (Option A 고정: proposed - original)
        if parent_prop and parent_prop["hash"] != p_hash:
            parent_hash = parent_prop["hash"]
            for k, v in raw_proposed.items():
                if isinstance(v, (int, float)):
                    orig_v = raw_original.get(k)
                    if orig_v is not None and orig_v != v:
                        edges.append({
                            "from": parent_hash,
                            "to": p_hash,
                            "param": k,
                            "delta": round(float(v) - float(orig_v), 6),
                        })

        # 5. 파라미터 시계열 트렌드 수집
        ts = prop.get("created_at") or 0
        for k, v in raw_proposed.items():
            if isinstance(v, (int, float)):
                param_trend.setdefault(k, []).append({"ts": ts, "value": float(v)})

    # 6. Cycle Detection (Fail-Fast)
    visited = set()
    rec_stack = set()
    
    def dfs_detect_cycle(u: str):
        visited.add(u)
        rec_stack.add(u)
        for v in adj_list.get(u, []):
            if v not in visited:
                if dfs_detect_cycle(v):
                    return True
            elif v in rec_stack:
                return True
        rec_stack.remove(u)
        return False

    for node in nodes:
        u = node["hash"]
        if u not in visited:
            if dfs_detect_cycle(u):
                raise ValueError("Cycle detected in strategy mutation DAG")

    # 6.5. Depth 동적 결정 (BFS 레벨 탐색)
    from collections import deque
    queue = deque()
    
    roots = [n["hash"] for n in nodes if n["is_root"]]
    for r_hash in roots:
        proposal_by_hash[r_hash]["depth"] = 0
        queue.append(r_hash)
        
    while queue:
        curr_hash = queue.popleft()
        curr_node = proposal_by_hash[curr_hash]
        curr_depth = curr_node["depth"]
        
        for child_hash in adj_list.get(curr_hash, []):
            child_node = proposal_by_hash[child_hash]
            child_node["depth"] = max(child_node["depth"], curr_depth + 1)
            queue.append(child_hash)

    # 7. Memoized DFS 기반 Best Path 산출 (sum expected_roi 최대 경로)
    # 캐시: dp[node_hash] = (max_cumulative_roi, path_list)
    dp_cache: Dict[str, tuple] = {}

    def get_best_path_from(node_hash: str) -> tuple:
        if node_hash in dp_cache:
            return dp_cache[node_hash]

        node = proposal_by_hash[node_hash]
        current_roi = node["expected_roi"]
        
        children = adj_list.get(node_hash, [])
        if not children:
            res = (current_roi, [node_hash])
            dp_cache[node_hash] = res
            return res
            
        best_child_roi = -float("inf")
        best_child_path = []
        
        for child_hash in children:
            child_roi, child_path = get_best_path_from(child_hash)
            if child_roi > best_child_roi:
                best_child_roi = child_roi
                best_child_path = child_path
                
        res = (current_roi + best_child_roi, [node_hash] + best_child_path)
        dp_cache[node_hash] = res
        return res

    best_path_nodes = []
    best_total_roi = -float("inf")
    
    # 루트 노드들로부터 탐색 시작
    for root_hash in roots:
        cum_roi, path = get_best_path_from(root_hash)
        if cum_roi > best_total_roi:
            best_total_roi = cum_roi
            best_path_nodes = path

    # 8. graph_meta 계산 (structural metrics only)
    node_count = len(nodes)
    edge_count = len(edges)
    max_depth = max([n["depth"] for n in nodes]) if nodes else 0
    pruned_count = sum(1 for n in nodes if n["status"] == "PRUNED")
    avg_depth = sum(n["depth"] for n in nodes) / node_count if node_count > 0 else 0.0
    branching_factor = edge_count / node_count if node_count > 0 else 0.0
    density = edge_count / (node_count ** 2) if node_count > 0 else 0.0

    graph_meta = {
        "node_count": node_count,
        "edge_count": edge_count,
        "max_depth": max_depth,
        "pruned_count": pruned_count,
        "avg_depth": round(avg_depth, 4),
        "branching_factor": round(branching_factor, 4),
        "density": round(density, 6),
    }

    return {
        "nodes": nodes,
        "edges": edges,
        "best_path_nodes": best_path_nodes,
        "param_trend": param_trend,
        "graph_meta": graph_meta,
    }


# ─────────────────────────────────────────────
# 5. Counterfactual λ 보정 계수
# ─────────────────────────────────────────────

def get_counterfactual_lambda_boost(
    proposals: List[Dict[str, Any]],
    max_boost: float = 1.2,
) -> float:
    """
    완료된 Counterfactual 추적 기록의 오판율이 0.30 초과이면
    λ 보정 계수 max_boost(기본 1.2)를 반환합니다.
    미달이면 1.0(보정 없음)을 반환합니다.

    이 함수는 ShadowBacktestEngine이 base_lambda를 결정할 때 호출됩니다.
    """
    acc = calculate_pruning_accuracy(proposals)
    if acc["outperform_rate"] > 0.30:
        return max_boost
    return 1.0


# ─────────────────────────────────────────────
# 6. Entropy Drift Alert → λ 복합 트리거
# ─────────────────────────────────────────────

def get_combined_lambda_boost(
    proposals: List[Dict[str, Any]],
    entropy_threshold: float = 0.3,
    max_boost: float = 1.2,
    bins: int = 5,
) -> Dict[str, Any]:
    """
    Entropy Drift + Counterfactual Bias 두 신호를 결합하여
    최종 λ 보정 계수와 diversity threshold 조정 값을 반환합니다.

    신호 조합 규칙:
      - entropy < threshold  AND  outperform_rate > 0.30  → 강한 경고, λ_boost = max_boost
      - entropy < threshold  OR   outperform_rate > 0.30  → 약한 경고, λ_boost = (1 + max_boost) / 2
      - 둘 다 정상                                        → λ_boost = 1.0

    반환 형식:
      {
        "lambda_boost": float,
        "diversity_threshold_delta": float,  # 기본 임계치에 더할 조정량 (+0.02 ~ 0)
        "entropy": float,
        "outperform_rate": float,
        "alert_level": "HIGH" | "MEDIUM" | "NONE"
      }
    """
    entropy = calculate_parameter_entropy(proposals, bins)
    acc = calculate_pruning_accuracy(proposals)
    outperform_rate = acc["outperform_rate"]

    entropy_alert = entropy < entropy_threshold
    bias_alert = outperform_rate > 0.30

    if entropy_alert and bias_alert:
        lambda_boost = max_boost
        threshold_delta = 0.03  # 다양성 임계치를 3% 포인트 상향
        alert_level = "HIGH"
    elif entropy_alert or bias_alert:
        lambda_boost = round((1.0 + max_boost) / 2, 3)
        threshold_delta = 0.01
        alert_level = "MEDIUM"
    else:
        lambda_boost = 1.0
        threshold_delta = 0.0
        alert_level = "NONE"

    return {
        "lambda_boost": lambda_boost,
        "diversity_threshold_delta": threshold_delta,
        "entropy": round(entropy, 4),
        "outperform_rate": round(outperform_rate, 4),
        "alert_level": alert_level,
    }
