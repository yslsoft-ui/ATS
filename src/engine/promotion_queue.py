# -*- coding: utf-8 -*-

import json
import math
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any
import aiosqlite
from src.engine.girs_types import CandidateProposal, FeatureSnapshot
from src.engine.utils.telemetry import get_logger

logger = get_logger("promotion_queue")

class Clock:
    def __init__(self, start_time: Optional[float] = None):
        self._current_time = start_time if start_time is not None else time.time()
        
    def now(self) -> float:
        return self._current_time
        
    def sleep(self, seconds: float):
        self._current_time += seconds
        
    def set_time(self, t: float):
        self._current_time = t

# FSM 전이표 정의
ALLOWED_TRANSITIONS = {
    "CANDIDATE": ["SCORED", "EXPIRED"],
    "SCORED": ["RANKED", "PROMOTION_PENDING", "EXPIRED"],
    "RANKED": ["SCORED", "PROMOTION_PENDING", "EXPIRED"],
    "PROMOTION_PENDING": ["PROMOTION_LOCKED", "PROMOTION_REJECTED"],
    "PROMOTION_LOCKED": ["PROMOTION_EXECUTED", "PROMOTION_REJECTED"],
    "PROMOTION_REJECTED": ["SCORED", "EXPIRED"],
    "PROMOTION_EXECUTED": [],  # Terminal
    "EXPIRED": []              # Terminal
}

@dataclass
class ProposalStateView:
    proposal_id: str
    source_strategy_id: str
    status: str
    sequence_no: int
    last_updated_at: float
    first_entered_at: float
    features: Optional[FeatureSnapshot] = None
    graph_embedding: Optional[List[float]] = None
    model_version: Optional[str] = None
    scaler_version: Optional[str] = None
    final_promotion_score: float = 0.0
    backtest_result: Dict[str, Any] = field(default_factory=dict)

class PromotionQueue:
    def __init__(
        self,
        db_path: str,
        clock: Optional[Clock] = None,
        proposal_ttl: float = 3600.0,
        lock_timeout: float = 60.0,
        rejected_max_age: float = 1800.0,
        cooldown_period: float = 300.0
    ):
        self.db_path = db_path
        self.clock = clock if clock is not None else Clock()
        self.proposal_ttl = proposal_ttl
        self.lock_timeout = lock_timeout
        self.rejected_max_age = rejected_max_age
        self.cooldown_period = cooldown_period
        self.eps = 1e-9
        
        # Hysteresis state
        self.correction_active = False
        self.system_event_seq = 0
        
        self.rank_drift = 0.0
        self.last_replay_corrected_at = 0.0
        self.promotion_block_reason = None
        
        # 인메모리 Materialized View 캐시
        self.materialized_views: Dict[str, ProposalStateView] = {}

    async def init_table(self):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA foreign_keys=ON")
            # Raw Event Log 테이블 생성
            # event_id UNIQUE, (proposal_id, sequence_no) UNIQUE 제약
            # replay 순서 보장을 위해 global_sequence_no INTEGER PRIMARY KEY AUTOINCREMENT
            await db.execute("""
                CREATE TABLE IF NOT EXISTS promotion_event_log (
                    global_sequence_no INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT UNIQUE NOT NULL,
                    proposal_id TEXT NOT NULL,
                    sequence_no INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    payload TEXT,
                    timestamp REAL NOT NULL,
                    feature_snapshot TEXT,
                    graph_embedding TEXT,
                    model_version TEXT,
                    scaler_version TEXT,
                    UNIQUE(proposal_id, sequence_no)
                )
            """)
            await db.commit()

    async def ingest_proposal(self, proposal: CandidateProposal, event_id: str) -> bool:
        proposal_id = proposal.proposal_id
        
        # 1. 중복 인입 방어 (인메모리 캐시에서 먼저 확인)
        if proposal_id in self.materialized_views:
            logger.warning(f"Proposal {proposal_id} already ingested.")
            return False

        now_time = self.clock.now()
        
        # 피처 스냅샷 직렬화
        feature_snap_json = None
        if proposal.features:
            feature_snap_json = json.dumps({
                "price_features": proposal.features.price_features,
                "liquidity_features": proposal.features.liquidity_features,
                "regime_features": proposal.features.regime_features,
                "schema_version": proposal.features.schema_version,
                "feature_hash": proposal.features.feature_hash,
                "generated_at": proposal.features.generated_at
            })

        graph_embedding_json = json.dumps(proposal.graph_embedding) if proposal.graph_embedding else None

        payload = {
            "source_strategy_id": proposal.source_strategy_id,
            "backtest_result": proposal.backtest_result
        }
        payload_json = json.dumps(payload)

        # 2. DB 적재
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("""
                    INSERT INTO promotion_event_log (
                        event_id, proposal_id, sequence_no, event_type, payload,
                        timestamp, feature_snapshot, graph_embedding,
                        model_version, scaler_version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    event_id, proposal_id, 1, "PROPOSAL_ENTERED", payload_json,
                    now_time, feature_snap_json, graph_embedding_json,
                    proposal.model_version, proposal.scaler_version
                ))
                await db.commit()
        except aiosqlite.IntegrityError as e:
            logger.error(f"Idempotency violation for proposal ingest {proposal_id}: {e}")
            return False

        # 3. Materialized View 갱신
        self.materialized_views[proposal_id] = ProposalStateView(
            proposal_id=proposal_id,
            source_strategy_id=proposal.source_strategy_id,
            status="CANDIDATE",
            sequence_no=1,
            last_updated_at=now_time,
            first_entered_at=now_time,
            features=proposal.features,
            graph_embedding=proposal.graph_embedding,
            model_version=proposal.model_version,
            scaler_version=proposal.scaler_version,
            backtest_result=proposal.backtest_result
        )
        return True

    async def transition_state(
        self,
        proposal_id: str,
        to_state: str,
        event_id: str,
        payload: Optional[Dict[str, Any]] = None
    ) -> bool:
        if proposal_id not in self.materialized_views:
            logger.error(f"Transition rejected: Proposal {proposal_id} does not exist.")
            return False

        view = self.materialized_views[proposal_id]
        from_state = view.status

        # 1. ALLOWED_TRANSITIONS 체크
        if to_state not in ALLOWED_TRANSITIONS.get(from_state, []):
            logger.error(f"Transition rejected: {from_state} -> {to_state} is not allowed.")
            return False

        # correction_active가 True일 때 실전 승격 전이 차단
        if self.correction_active and to_state in ["PROMOTION_PENDING", "PROMOTION_LOCKED", "PROMOTION_EXECUTED"]:
            self.promotion_block_reason = "REPLAY_CORRECTION_ACTIVE"
            logger.warning(f"Transition to {to_state} blocked for proposal {proposal_id} due to active replay correction (drift: {self.rank_drift:.4f}).")
            
            # DB에 차단 이벤트 기록
            now_time = self.clock.now()
            try:
                next_seq = view.sequence_no + 1
                async with aiosqlite.connect(self.db_path) as db:
                    await db.execute("""
                        INSERT INTO promotion_event_log (
                            event_id, proposal_id, sequence_no, event_type, payload,
                            timestamp, model_version, scaler_version
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        str(uuid.uuid4()), proposal_id, next_seq, "PROMOTION_BLOCKED_BY_REPLAY_CORRECTION",
                        json.dumps({"drift": self.rank_drift, "target_state": to_state}),
                        now_time, view.model_version, view.scaler_version
                    ))
                    await db.commit()
                view.sequence_no = next_seq
                view.last_updated_at = now_time
            except Exception as e:
                logger.error(f"Failed to write PROMOTION_BLOCKED_BY_REPLAY_CORRECTION event: {e}")
            return False

        # 2. Cooldown 및 기타 제약 체크
        now_time = self.clock.now()
        
        # PROMOTION_REJECTED -> SCORED 전이 시 cooldown 기간 만족 여부 체크
        if from_state == "PROMOTION_REJECTED" and to_state == "SCORED":
            elapsed = now_time - view.last_updated_at
            if elapsed < self.cooldown_period:
                logger.error(f"Transition rejected: Cooldown in progress for proposal {proposal_id}. Elapsed: {elapsed:.1f}s, Required: {self.cooldown_period}s")
                return False

        # 3. DB 적재 시 sequence_no 원자적 증가 적용
        next_seq = view.sequence_no + 1
        
        feature_snap_json = None
        if view.features:
            feature_snap_json = json.dumps({
                "price_features": view.features.price_features,
                "liquidity_features": view.features.liquidity_features,
                "regime_features": view.features.regime_features,
                "schema_version": view.features.schema_version,
                "feature_hash": view.features.feature_hash,
                "generated_at": view.features.generated_at
            })
        graph_embedding_json = json.dumps(view.graph_embedding) if view.graph_embedding else None

        payload_str = json.dumps(payload) if payload else None

        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("""
                    INSERT INTO promotion_event_log (
                        event_id, proposal_id, sequence_no, event_type, payload,
                        timestamp, feature_snapshot, graph_embedding,
                        model_version, scaler_version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    event_id, proposal_id, next_seq, f"STATE_CHANGED_{to_state}", payload_str,
                    now_time, feature_snap_json, graph_embedding_json,
                    view.model_version, view.scaler_version
                ))
                await db.commit()
        except aiosqlite.IntegrityError as e:
            logger.error(f"Idempotency violation for proposal {proposal_id} transition to {to_state}: {e}")
            return False

        # 4. Materialized View 갱신
        view.status = to_state
        view.sequence_no = next_seq
        view.last_updated_at = now_time
        if payload and "final_promotion_score" in payload:
            view.final_promotion_score = float(payload["final_promotion_score"])

        return True

    async def check_lifecycle_and_timeouts(self) -> List[str]:
        now_time = self.clock.now()
        triggered_proposals = []

        views_copy = list(self.materialized_views.values())

        for view in views_copy:
            proposal_id = view.proposal_id
            status = view.status

            # 1. TTL 체크 (Candidate, Scored, Ranked 대상)
            if status in ["CANDIDATE", "SCORED", "RANKED"]:
                first_entered_at = getattr(view, "first_entered_at", view.last_updated_at)
                if first_entered_at + self.proposal_ttl < now_time:
                    logger.info(f"Proposal {proposal_id} TTL expired. Initiating transition to EXPIRED.")
                    evt_id = str(uuid.uuid4())
                    success = await self.transition_state(proposal_id, "EXPIRED", evt_id, {"reason": "TTL_EXPIRED"})
                    if success:
                        triggered_proposals.append(proposal_id)

            # 2. PromotionLocked Timeout 체크
            elif status == "PROMOTION_LOCKED":
                if view.last_updated_at + self.lock_timeout < now_time:
                    logger.info(f"Proposal {proposal_id} lock timeout. Initiating transition to PROMOTION_REJECTED.")
                    evt_id = str(uuid.uuid4())
                    success = await self.transition_state(proposal_id, "PROMOTION_REJECTED", evt_id, {"reason": "LOCK_TIMEOUT"})
                    if success:
                        triggered_proposals.append(proposal_id)

            # 3. PromotionRejected Max Age 체크
            elif status == "PROMOTION_REJECTED":
                if view.last_updated_at + self.rejected_max_age < now_time:
                    logger.info(f"Proposal {proposal_id} rejected max age exceeded. Initiating transition to EXPIRED.")
                    evt_id = str(uuid.uuid4())
                    success = await self.transition_state(proposal_id, "EXPIRED", evt_id, {"reason": "REJECTED_MAX_AGE_EXCEEDED"})
                    if success:
                        triggered_proposals.append(proposal_id)

        return triggered_proposals

    async def rebuild_materialized_view(self) -> None:
        new_views: Dict[str, ProposalStateView] = {}
        
        # Reset hysteresis and block reason variables to prevent stale data
        self.correction_active = False
        self.system_event_seq = 0
        self.rank_drift = 0.0
        self.last_replay_corrected_at = 0.0
        self.promotion_block_reason = None
        
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM promotion_event_log ORDER BY global_sequence_no ASC")
            rows = await cursor.fetchall()

        for row in rows:
            event_type = row["event_type"]
            proposal_id = row["proposal_id"]
            seq = row["sequence_no"]
            ts = row["timestamp"]
            payload_str = row["payload"]
            payload = json.loads(payload_str) if payload_str else {}
            
            if proposal_id == "SYSTEM":
                if seq > self.system_event_seq:
                    self.system_event_seq = seq
                if event_type == "REPLAY_CORRECTION_ENABLED":
                    self.correction_active = True
                    self.promotion_block_reason = "REPLAY_CORRECTION_ACTIVE"
                    if payload and "drift" in payload:
                        self.rank_drift = payload["drift"]
                    self.last_replay_corrected_at = ts
                elif event_type == "REPLAY_CORRECTION_DISABLED":
                    self.correction_active = False
                    self.promotion_block_reason = None
                    if payload and "drift" in payload:
                        self.rank_drift = payload["drift"]
                    self.last_replay_corrected_at = ts
                continue
                
            if event_type == "PROMOTION_BLOCKED_BY_REPLAY_CORRECTION":
                if proposal_id in new_views:
                    view = new_views[proposal_id]
                    view.sequence_no = seq
                    view.last_updated_at = ts
                continue
            
            feat_str = row["feature_snapshot"]
            features = None
            if feat_str:
                feat_dict = json.loads(feat_str)
                features = FeatureSnapshot(
                    price_features=feat_dict["price_features"],
                    liquidity_features=feat_dict["liquidity_features"],
                    regime_features=feat_dict["regime_features"],
                    schema_version=feat_dict["schema_version"],
                    feature_hash=feat_dict["feature_hash"],
                    generated_at=feat_dict["generated_at"]
                )

            graph_emb_str = row["graph_embedding"]
            graph_embedding = json.loads(graph_emb_str) if graph_emb_str else None

            if event_type == "PROPOSAL_ENTERED":
                new_views[proposal_id] = ProposalStateView(
                    proposal_id=proposal_id,
                    source_strategy_id=payload["source_strategy_id"],
                    status="CANDIDATE",
                    sequence_no=seq,
                    last_updated_at=ts,
                    first_entered_at=ts,
                    features=features,
                    graph_embedding=graph_embedding,
                    model_version=row["model_version"],
                    scaler_version=row["scaler_version"],
                    backtest_result=payload["backtest_result"]
                )
            elif event_type.startswith("STATE_CHANGED_"):
                to_state = event_type.replace("STATE_CHANGED_", "")
                if proposal_id in new_views:
                    view = new_views[proposal_id]
                    view.status = to_state
                    view.sequence_no = seq
                    view.last_updated_at = ts
                    if payload and "final_promotion_score" in payload:
                        view.final_promotion_score = float(payload["final_promotion_score"])

        # Atomic Swap
        self.materialized_views = new_views

    async def run_replay_correction(
        self,
        fast_ranks: Dict[str, int],
        replay_ranks: Dict[str, int]
    ) -> Tuple[float, str]:
        candidates = set(fast_ranks.keys()).union(set(replay_ranks.keys()))
        N = len(candidates)
        
        if N == 0:
            return 0.0, "NOOP"

        missing_rank = N + 1
        sum_weights = 0.0
        weighted_drift_sum = 0.0
        
        for p_id in candidates:
            fast_r = fast_ranks.get(p_id, missing_rank)
            replay_r = replay_ranks.get(p_id, missing_rank)
            
            # 1. rank_diff_i = abs(fast_rank - replay_rank) / max(1, N)
            # 2. clip[0, 1] 처리
            rank_diff_i = min(max(abs(fast_r - replay_r) / max(1, N), 0.0), 1.0)
            
            min_r = min(fast_r, replay_r)
            weight_i = 1.0 / math.log(min_r + 2.0)  # k = 2.0
            
            sum_weights += weight_i
            weighted_drift_sum += weight_i * rank_diff_i

        if sum_weights <= self.eps:
            return 0.0, "NOOP"

        drift = weighted_drift_sum / sum_weights
        
        # Hysteresis Rule
        action = "KEEP_STATE"
        prev_active = self.correction_active
        
        self.rank_drift = drift
        self.last_replay_corrected_at = self.clock.now()
        
        if drift >= 0.3:  # T_high
            self.correction_active = True
            self.promotion_block_reason = "REPLAY_CORRECTION_ACTIVE"
            action = "CORRECTION_ACTIVE"
        elif drift <= 0.1:  # T_low
            self.correction_active = False
            self.promotion_block_reason = None
            action = "CORRECTION_INACTIVE"
            
        # 상태 전환 이벤트 로깅
        if self.correction_active != prev_active:
            now_time = self.clock.now()
            
            if self.correction_active:
                events = ["REPLAY_DRIFT_HIGH", "REPLAY_CORRECTION_ENABLED"]
            else:
                events = ["REPLAY_DRIFT_LOW", "REPLAY_CORRECTION_DISABLED"]
                
            async with aiosqlite.connect(self.db_path) as db:
                for evt_type in events:
                    self.system_event_seq += 1
                    evt_id = str(uuid.uuid4())
                    await db.execute("""
                        INSERT INTO promotion_event_log (
                            event_id, proposal_id, sequence_no, event_type, payload,
                            timestamp
                        ) VALUES (?, ?, ?, ?, ?, ?)
                    """, (
                        evt_id, "SYSTEM", self.system_event_seq, evt_type,
                        json.dumps({"drift": drift}), now_time
                    ))
                await db.commit()
                
            # GIRS Shadow Metrics에 Replay Drift 단독 로깅
            from src.config.manager import ConfigManager
            config_manager = ConfigManager("config/settings.yaml")
            op_mode = config_manager.get("system.operation_mode", "shadow")
            
            try:
                async with aiosqlite.connect(self.db_path) as db:
                    await db.execute("""
                        INSERT INTO girs_shadow_metrics (
                            timestamp, proposal_id, strategy_id, model_risk_score, fallback_risk_score,
                            final_promotion_score, shadow_risk_score, replay_drift, correction_active,
                            operation_mode, model_version, scaler_version, strategy_version_id,
                            simulation_session_id, decision_type, blocked_reason
                        ) VALUES (?, 'SYSTEM', 'SYSTEM', NULL, NULL, NULL, NULL, ?, ?, ?, NULL, NULL, NULL, NULL, 'REPLAY', NULL)
                    """, (
                        now_time, drift, 1 if self.correction_active else 0, op_mode
                    ))
                    await db.commit()
            except aiosqlite.OperationalError as oe:
                # 테이블이 없는 임시 DB 테스트 환경의 경우 예외를 삼키고 로깅만 남김
                logger.warning(f"[PromotionQueue] girs_shadow_metrics table not found. Skipping drift log. Details: {oe}")
            except Exception as ex:
                logger.error(f"[PromotionQueue] Failed to log drift metrics: {ex}")
                
        return drift, action

    def get_ranked_proposals(self) -> List[Dict[str, Any]]:
        active_states = ["CANDIDATE", "SCORED", "RANKED", "PROMOTION_PENDING", "PROMOTION_LOCKED"]
        active_views = [
            v for v in self.materialized_views.values()
            if v.status in active_states
        ]
        
        sorted_views = sorted(active_views, key=lambda x: x.final_promotion_score, reverse=True)
        
        result = []
        for view in sorted_views:
            result.append({
                "proposal_id": view.proposal_id,
                "source_strategy_id": view.source_strategy_id,
                "status": view.status,
                "final_promotion_score": view.final_promotion_score,
                "backtest_result": view.backtest_result
            })
        return result
