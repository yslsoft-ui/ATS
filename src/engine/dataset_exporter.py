import os
import json
import time
import shutil
import tempfile
import threading
from typing import List, Dict, Any, Optional, Tuple, Set
from collections import OrderedDict

# =====================================================================
# Versioned Event Priority Registry
# =====================================================================
VERSIONED_PRIORITY_REGISTRY = {
    1: {
        "APPLIED": 100,
        "PARAM_CHANGE": 80,
        "PROPOSAL_CREATED": 50,
        "ROLLBACK": 10
    }
}

DEFAULT_PRIORITY_VERSION = 1


# =====================================================================
# Atomic Write Helper
# =====================================================================
def safe_atomic_write(dest_path: str, content: str):
    """
    Staging & Replace 패턴을 적용한 원자적 파일 쓰기 헬퍼입니다.
    - 동일 디렉토리 내 임시 파일(.tmp) 생성
    - fsync 강제
    - os.replace (rename) 시도
    - Cross-device fail 시 Copy-Verify-Delete fallback 작동
    """
    dest_dir = os.path.dirname(dest_path)
    if dest_dir:
        os.makedirs(dest_dir, exist_ok=True)
    
    # 1. 동일 디렉토리에 임시 파일 작성
    fd, temp_path = tempfile.mkstemp(dir=dest_dir if dest_dir else ".", suffix=".tmp")
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(content)
            f.flush()
            # Disk flush 보장
            try:
                os.fsync(fd)
            except OSError:
                pass
        
        # 2. 원자적 rename 시도
        try:
            os.replace(temp_path, dest_path)
        except OSError:
            # 3. Cross-device filesystem 이동 시의 Fallback (copy-verify-delete)
            shutil.copy2(temp_path, dest_path)
            # 파일 크기 검증
            if os.path.getsize(temp_path) != os.path.getsize(dest_path):
                raise IOError(f"Size verification failed during cross-device fallback copy: {temp_path} -> {dest_path}")
            # 해시 혹은 추가 검증 필요시 보장
            os.remove(temp_path)
    except Exception as e:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass
        raise e


# =====================================================================
# 1. Event Buffer (Ingestion Layer)
# =====================================================================
class EventBuffer:
    """
    실시간 전략 변이 이벤트를 스레드 세이프하게 버퍼링하는 유닛입니다.
    - timestamp lock 및 monotonic clock 보완 적용
    - micro-sequence entropy injection 추가로 완전한 Total Ordering 보장
    """
    def __init__(self):
        self._lock = threading.Lock()
        self._events: List[Dict[str, Any]] = []
        self._last_commit_time: int = 0
        self._local_increment_counter: int = 0
        self._global_seq_counter: int = 0

    def put_event(self, event_type: str, node_hash: str, params: Dict[str, Any], created_at: int, 
                  parent_hashes: Optional[List[Dict[str, Any]]] = None, depth: int = 0, 
                  metrics: Optional[Dict[str, Any]] = None, labels: Optional[Dict[str, Any]] = None, 
                  context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        이벤트를 인메모리 버퍼에 락을 획득하여 단조성(Monotonicity)을 유지하며 적재합니다.
        """
        with self._lock:
            # 1. Monotonic Timestamp & Micro-sequence Entropy Injection 계산
            system_time_ms = int(time.time() * 1000)
            target_ts = max(system_time_ms, self._last_commit_time + 1)
            
            if target_ts == self._last_commit_time:
                self._local_increment_counter += 1
            else:
                self._last_commit_time = target_ts
                self._local_increment_counter = 0

            # 2. Global Monotonic Sequence Allocation
            self._global_seq_counter += 1
            global_id = self._global_seq_counter

            # 3. 정합성 있는 이벤트 구조 생성
            event_record = {
                "global_monotonic_id": global_id,
                "node_hash": node_hash,
                "event_type": event_type,
                "created_at": created_at, # 논리적 생성 시점 (DB/API 상의 시각)
                "commit_timestamp": (target_ts, self._local_increment_counter), # 컴파일러 수신 시점
                "params": params,
                "parent_hashes": parent_hashes or [],
                "depth": depth,
                "metrics": metrics or {"expected_roi": 0.0, "realized_roi": None, "mdd": 0.0},
                "labels": labels or {"success": 0, "failure": 0, "label_type": "MASKED"},
                "context": context or {}
            }
            self._events.append(event_record)
            return event_record

    def flush(self) -> List[Dict[str, Any]]:
        """
        버퍼를 안전하게 락 획득 하에 비우고(flush), 수집된 목록을 반환합니다.
        """
        with self._lock:
            flushed_events = list(self._events)
            self._events.clear()
            return flushed_events

    def size(self) -> int:
        with self._lock:
            return len(self._events)


# =====================================================================
# 2. Dataset Exporter (Compiler & Persistence Layer)
# =====================================================================
class DatasetExporter:
    """
    Raw Storage (.jsonl) 및 Graph Snapshot을 동기화하는 컴파일러 엑스포터입니다.
    - 4단계 Dual Ordering 규칙 강제 정렬
    - 복합 키 (Composite Key) 기반 멱등 병합
    - 임시 파일 쓰기 및 fsync, cross-device replace atomic 보장
    """
    def __init__(self, output_dir: str = "data/dataset", priority_version: int = DEFAULT_PRIORITY_VERSION):
        self.output_dir = output_dir
        self.graph_path = os.path.join(output_dir, "mutation_graph.jsonl")
        self.snapshot_path = os.path.join(output_dir, "mutation_graph_snapshot.jsonl")
        self.meta_path = os.path.join(output_dir, "dataset_meta.json")
        self.priority_table = VERSIONED_PRIORITY_REGISTRY.get(priority_version, VERSIONED_PRIORITY_REGISTRY[1])
        self.priority_version = priority_version
        
        os.makedirs(self.output_dir, exist_ok=True)

    def _get_event_priority(self, event_type: str) -> int:
        """
        설정된 Versioned Priority Table에 따라 이벤트 우선순위 점수를 리턴합니다.
        알 수 없는 이벤트 타입은 최하위(0) 점수를 리턴해 붕괴를 막습니다.
        """
        return self.priority_table.get(event_type, 0)

    def _sort_events(self, events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        4단계 Dual Ordering 적용 정렬:
        1) created_at (논리적 시간)
        2) event_priority (의사결정 우선순위, 높은 우선순위 우선)
        3) global_monotonic_id (전역 시퀀스 번호)
        4) commit_timestamp (하이브리드 단조 증가 타임스탬프 튜플)
        """
        def sort_key(e):
            priority = self._get_event_priority(e["event_type"])
            return (
                e.get("created_at") or 0,
                -priority,
                e.get("global_monotonic_id") or 0,
                e.get("commit_timestamp") or (0, 0)
            )
        return sorted(events, key=sort_key)

    def export_batch(self, new_events: List[Dict[str, Any]]):
        """
        신규 유입된 이벤트를 정렬 및 멱등성 병합하여 JSONL 및 Snapshot을 갱신합니다.
        (Atomic Transaction Unit 구현)
        """
        if not new_events:
            return

        # 1. 파일 시스템에 보관된 기존 데이터 로딩
        existing_records = self.load_graph_records()
        
        # 2. 복합 키(Composite Key) 기반 멱등 병합을 위한 딕셔너리 구성
        # Composite Key: (node_hash, event_type, timestamp_bucket)
        # 1분(60초) 단위 버킷 처리로 불필요한 마이크로 중복 방지
        merged_map: Dict[Tuple[str, str, int], Dict[str, Any]] = {}
        
        for rec in existing_records:
            node_hash = rec["node_hash"]
            event_type = rec["event_type"]
            created_at = rec.get("created_at") or 0
            ts_bucket = (created_at // 60) * 60
            key = (node_hash, event_type, ts_bucket)
            merged_map[key] = rec

        # 3. 신규 유입 이벤트 멱등 병합
        for ev in new_events:
            node_hash = ev["node_hash"]
            event_type = ev["event_type"]
            created_at = ev.get("created_at") or 0
            ts_bucket = (created_at // 60) * 60
            key = (node_hash, event_type, ts_bucket)
            
            # 기존 레코드가 있다면 병합, 신규 레코드면 추가
            if key in merged_map:
                existing = merged_map[key]
                # 최신 갱신 데이터 병합 (metrics, labels, context 등)
                existing.update({
                    "params": ev["params"],
                    "parent_hashes": ev["parent_hashes"],
                    "depth": ev["depth"],
                    "metrics": ev["metrics"],
                    "labels": ev["labels"],
                    "context": ev["context"],
                    "global_monotonic_id": max(existing.get("global_monotonic_id") or 0, ev.get("global_monotonic_id") or 0),
                    "commit_timestamp": max(existing.get("commit_timestamp") or (0, 0), ev.get("commit_timestamp") or (0, 0))
                })
            else:
                merged_map[key] = ev

        # 4. 4단계 Dual Ordering 규칙에 맞춘 최종 레코드 정렬
        sorted_records = self._sort_events(list(merged_map.values()))

        # 5. Graph Meta 연산 및 스냅샷 구성
        node_count = len(sorted_records)
        edges = []
        best_path_nodes = []
        max_depth = 0
        pruned_count = 0

        # 임시 인접 구조 파악
        for r in sorted_records:
            max_depth = max(max_depth, r.get("depth", 0))
            if r.get("labels", {}).get("label_type") == "ESTIMATED" and r.get("labels", {}).get("success") == 0:
                pruned_count += 1
            
            node_hash = r["node_hash"]
            parents = r.get("parent_hashes") or []
            for p in parents:
                p_hash = p.get("hash")
                if p_hash:
                    edges.append({
                        "from": p_hash,
                        "to": node_hash,
                        "weight": p.get("weight", 1.0)
                    })

        # SSOT Snapshot ID 생성 (timestamp + hash(state))
        import hashlib
        snapshot_time = int(time.time() * 1000)
        state_str = "".join([r["node_hash"] for r in sorted_records])
        state_hash = hashlib.sha256(state_str.encode("utf-8")).hexdigest()[:16]
        dataset_snapshot_id = f"snap_{snapshot_time}_{state_hash}"

        snapshot_info = {
            "snapshot_id": dataset_snapshot_id,
            "timestamp": snapshot_time,
            "node_count": node_count,
            "edge_count": len(edges),
            "max_depth": max_depth,
            "pruned_count": pruned_count,
            "best_path_nodes": best_path_nodes,
            "graph_meta": {
                "schema_version": "1.0",
                "priority_version": self.priority_version,
                "density": round(len(edges) / (node_count ** 2) if node_count > 0 else 0, 6)
            }
        }

        # Meta 파일 구성
        meta_info = {
            "schema_version": "1.0",
            "last_updated": snapshot_time,
            "latest_snapshot_id": dataset_snapshot_id,
            "feature_list": ["parent_expected_roi", "parent_realized_roi", "historical_roi_trend", "parent_param_deltas"],
            "label_definition": {
                "success_threshold": 0.05,
                "failure_threshold": -0.03
            }
        }

        # 6. Write-Ahead Staging & Replace 패턴을 적용하여 파일 일괄 원자적 기록
        # JSONL 레코드 파일 빌드
        jsonl_lines = [json.dumps(r) for r in sorted_records]
        jsonl_content = "\n".join(jsonl_lines) + ("\n" if jsonl_lines else "")
        
        # Snapshot JSONL 파일 빌드 (Append-only snapshot history)
        snapshot_records = self.load_snapshot_history()
        snapshot_records.append(snapshot_info)
        snapshot_lines = [json.dumps(s) for s in snapshot_records]
        snapshot_content = "\n".join(snapshot_lines) + ("\n" if snapshot_lines else "")

        # 메타 JSON 파일 빌드
        meta_content = json.dumps(meta_info, indent=2)

        # 쓰기 트랜잭션 수행
        try:
            safe_atomic_write(self.graph_path, jsonl_content)
            safe_atomic_write(self.snapshot_path, snapshot_content)
            safe_atomic_write(self.meta_path, meta_content)
        except Exception as e:
            # 원자성 보장 실패 시 예외 전파 (Fail-Fast)
            raise IOError(f"Failed to commit atomic batch export: {e}")

    def load_graph_records(self) -> List[Dict[str, Any]]:
        """기존 JSONL 레코드 파일을 안전하게 파싱하여 리스트로 복원합니다."""
        if not os.path.exists(self.graph_path):
            return []
        records = []
        with open(self.graph_path, 'r', encoding='utf-8') as f:
            for line in f:
                line_str = line.strip()
                if line_str:
                    records.append(json.loads(line_str))
        return records

    def load_snapshot_history(self) -> List[Dict[str, Any]]:
        """기존 스냅샷 이력을 로딩합니다."""
        if not os.path.exists(self.snapshot_path):
            return []
        records = []
        with open(self.snapshot_path, 'r', encoding='utf-8') as f:
            for line in f:
                line_str = line.strip()
                if line_str:
                    records.append(json.loads(line_str))
        return records

    def load_meta(self) -> Dict[str, Any]:
        """메타데이터 파일을 로딩합니다."""
        if not os.path.exists(self.meta_path):
            return {}
        with open(self.meta_path, 'r', encoding='utf-8') as f:
            return json.load(f)


# =====================================================================
# 3. Feature Builder (Representation Layer)
# =====================================================================
class VersionedLRUCache:
    """
    그래프 최종 스냅샷 버전을 기반으로 작동하는 LRU 캐시입니다.
    - Key: (node_hash, graph_version)
    - graph_version (dataset_snapshot_id)이 변하면 자동으로 stale 캐시가 미매칭으로 처리되어 Invalidation
    """
    def __init__(self, capacity: int = 1000):
        self.capacity = capacity
        self.cache = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: Tuple[str, str]) -> Optional[Dict[str, Any]]:
        with self._lock:
            if key in self.cache:
                self.cache.move_to_end(key)
                return self.cache[key]
            return None

    def set(self, key: Tuple[str, str], value: Dict[str, Any]):
        with self._lock:
            if key in self.cache:
                self.cache.move_to_end(key)
            self.cache[key] = value
            if len(self.cache) > self.capacity:
                self.cache.popitem(last=False)

    def clear(self):
        with self._lock:
            self.cache.clear()


class FeatureBuilder:
    """
    Raw JSONL 데이터를 분석하여 온디맨드로 파생 피처(Derived Features)를 계산하는 피처 컴파일러입니다.
    - O(N) parent traversal 및 LRU Cache 최적화
    - graph_version (dataset_snapshot_id) 일치 기반 Version Invalidation
    - cycle-safe DFS guard 탑재 (visited 체크)
    """
    def __init__(self, cache_capacity: int = 1000):
        self.cache = VersionedLRUCache(capacity=cache_capacity)

    def build_features(self, node_hash: str, records: List[Dict[str, Any]], 
                       graph_version: str) -> Dict[str, Any]:
        """
        특정 노드의 파생 피처 맵을 계산 및 캐싱하여 반환합니다.
        """
        cache_key = (node_hash, graph_version)
        cached = self.cache.get(cache_key)
        if cached is not None:
            return cached

        # 레코드를 빠른 룩업을 위한 딕셔너리로 변환
        record_map = {r["node_hash"]: r for r in records}
        
        # DFS Cycle 탐지를 위한 visited 셋 생성 및 가드 탑재
        visited: Set[str] = set()
        
        features = self._compute_recursive(node_hash, record_map, graph_version, visited)
        self.cache.set(cache_key, features)
        return features

    def _compute_recursive(self, node_hash: str, record_map: Dict[str, Dict[str, Any]], 
                           graph_version: str, visited: Set[str]) -> Dict[str, Any]:
        """부모 트래버설을 수행하여 파생 피처(N-hop context, delta, trend)를 계산합니다."""
        # 1. Cycle Detection DFS Guard
        if node_hash in visited:
            raise ValueError(f"Cycle detected at FeatureBuilder level during graph traversal. Loop node: {node_hash}")
        
        visited.add(node_hash)

        curr = record_map.get(node_hash)
        if not curr:
            visited.remove(node_hash)
            return {
                "parent_expected_roi": 0.0,
                "parent_realized_roi": None,
                "historical_roi_trend": [],
                "parent_param_deltas": {}
            }

        parents = curr.get("parent_hashes") or []
        if not parents:
            # 루트 노드 케이스
            visited.remove(node_hash)
            return {
                "parent_expected_roi": 0.0,
                "parent_realized_roi": None,
                "historical_roi_trend": [],
                "parent_param_deltas": {}
            }

        # 주 부모 노드 (첫 번째 부모) 기준 연산
        primary_parent_hash = parents[0]["hash"]
        parent_rec = record_map.get(primary_parent_hash)
        
        if not parent_rec:
            visited.remove(node_hash)
            return {
                "parent_expected_roi": 0.0,
                "parent_realized_roi": None,
                "historical_roi_trend": [],
                "parent_param_deltas": {}
            }

        # 부모 노드의 파생 피처 계산 (재귀 호출)
        parent_features = self._compute_recursive(primary_parent_hash, record_map, graph_version, visited)

        # 2. 피처 연산
        # 2-1) parent roi
        parent_expected = parent_rec.get("metrics", {}).get("expected_roi") or 0.0
        parent_realized = parent_rec.get("metrics", {}).get("realized_roi")
        if parent_realized is None:
            # 만약 실거래 적용 안 된 estimated 노드인 경우 counterfactual 활용
            parent_realized = parent_rec.get("metrics", {}).get("counterfactual_roi")

        # 2-2) 파라미터 델타 (현재 - 부모)
        curr_params = curr.get("params") or {}
        parent_params = parent_rec.get("params") or {}
        deltas = {}
        for k, v in curr_params.items():
            if isinstance(v, (int, float)):
                p_v = parent_params.get(k)
                if p_v is not None and isinstance(p_v, (int, float)):
                    deltas[k] = round(float(v) - float(p_v), 6)

        # 2-3) historical roi trend
        # 부모의 트렌드 이력 리스트에 부모의 roi(realized 우선, 없으면 expected)를 어펜드
        p_roi = parent_realized if parent_realized is not None else parent_expected
        trend = list(parent_features.get("historical_roi_trend") or [])
        trend.append(p_roi)

        visited.remove(node_hash)
        
        return {
            "parent_expected_roi": parent_expected,
            "parent_realized_roi": parent_realized,
            "historical_roi_trend": trend[-5:],  # 최근 최대 5개 이력 제한
            "parent_param_deltas": deltas
        }


# =====================================================================
# 4. Dataset Loader (Consumption Layer)
# =====================================================================
class DatasetLoader:
    """
    FeatureBuilder로 계산된 피처를 최종 모델 뷰(Tabular/Sequence/Graph)로 변환해주는 로더입니다.
    """
    def __init__(self, exporter: DatasetExporter, cache_capacity: int = 1000):
        self.exporter = exporter
        self.feature_builder = FeatureBuilder(cache_capacity=cache_capacity)

    def load_as_tabular(self) -> List[Dict[str, Any]]:
        """
        데이터셋을 Scikit-learn, XGBoost 등에서 즉시 학습할 수 있는 Tabular(Flat DataFrame-ready) 포맷으로 변환합니다.
        """
        records = self.exporter.load_graph_records()
        meta = self.exporter.load_meta()
        graph_version = meta.get("latest_snapshot_id", "default_version")

        tabular_data = []
        for r in records:
            node_hash = r["node_hash"]
            # 온디맨드 피처 빌딩
            derived = self.feature_builder.build_features(node_hash, records, graph_version)
            
            flat_rec = {
                "node_hash": node_hash,
                "created_at": r.get("created_at"),
                "depth": r.get("depth", 0),
                "label_type": r.get("labels", {}).get("label_type", "MASKED"),
                "label_success": r.get("labels", {}).get("success", 0),
                "label_failure": r.get("labels", {}).get("failure", 0),
                "realized_roi": r.get("metrics", {}).get("realized_roi"),
                "counterfactual_roi": r.get("metrics", {}).get("counterfactual_roi"),
                "expected_roi": r.get("metrics", {}).get("expected_roi"),
                "mdd": r.get("metrics", {}).get("mdd", 0.0)
            }
            
            # 파라미터 임베딩
            for p_k, p_v in (r.get("params") or {}).items():
                if isinstance(p_v, (int, float)):
                    flat_rec[f"param_{p_k}"] = p_v

            # 파생 피처 병합
            flat_rec.update({
                "feature_parent_expected_roi": derived["parent_expected_roi"],
                "feature_parent_realized_roi": derived["parent_realized_roi"],
                # trend의 평균값과 마지막값을 스칼라 피처로 평탄화
                "feature_historical_roi_mean": sum(derived["historical_roi_trend"])/len(derived["historical_roi_trend"]) if derived["historical_roi_trend"] else 0.0,
                "feature_historical_roi_last": derived["historical_roi_trend"][-1] if derived["historical_roi_trend"] else 0.0
            })
            
            # 파라미터 델타 병합
            for d_k, d_v in derived["parent_param_deltas"].items():
                flat_rec[f"delta_param_{d_k}"] = d_v

            tabular_data.append(flat_rec)
        return tabular_data

    def load_as_graph(self) -> Dict[str, Any]:
        """
        데이터셋을 PyTorch Geometric(PyG) 및 NetworkX 등에서 즉시 읽을 수 있는 노드/엣지 리스트 딕셔너리로 반환합니다.
        """
        records = self.exporter.load_graph_records()
        meta = self.exporter.load_meta()
        graph_version = meta.get("latest_snapshot_id", "default_version")

        nodes = []
        edges = []

        for r in records:
            node_hash = r["node_hash"]
            derived = self.feature_builder.build_features(node_hash, records, graph_version)
            
            # 노드 피처 수치 수집
            node_features = {
                "depth": r.get("depth", 0),
                "expected_roi": r.get("metrics", {}).get("expected_roi", 0.0),
                "parent_expected_roi": derived["parent_expected_roi"],
                "parent_realized_roi": derived["parent_realized_roi"] or 0.0,
                "historical_roi_mean": sum(derived["historical_roi_trend"])/len(derived["historical_roi_trend"]) if derived["historical_roi_trend"] else 0.0
            }

            nodes.append({
                "id": node_hash,
                "features": node_features,
                "label_type": r.get("labels", {}).get("label_type", "MASKED"),
                "label_success": r.get("labels", {}).get("success", 0),
                "label_failure": r.get("labels", {}).get("failure", 0)
            })

            for p in r.get("parent_hashes") or []:
                p_hash = p.get("hash")
                if p_hash:
                    edges.append({
                        "source": p_hash,
                        "target": node_hash,
                        "weight": p.get("weight", 1.0)
                    })

        return {
            "nodes": nodes,
            "edges": edges,
            "snapshot_id": graph_version
        }
