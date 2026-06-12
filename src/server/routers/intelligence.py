"""
src/server/routers/intelligence.py
Step 4 — AI 내부 감시 시스템 전용 API 라우터

엔드포인트:
  GET /api/intelligence/diversity          — Diversity Map + Convergence Alert + Decision Drift
  GET /api/intelligence/counterfactual-summary — Counterfactual 추적 현황 집계
"""
import json
import time
from fastapi import APIRouter, Request, HTTPException
from src.engine.utils.telemetry import get_logger
from src.engine.diversity_analyzer import (
    detect_convergence,
    calculate_pruning_accuracy,
    build_mutation_trace_graph,
    get_combined_lambda_boost,
)

logger = get_logger(__name__)
router = APIRouter()


async def _load_proposals(system, strategy_id: str = None, days: int = 30) -> list:
    """
    최근 N일간의 제안 목록을 DB에서 조회하여 반환합니다.
    proposed_params / original_params 는 dict로 역직렬화합니다.
    """
    cutoff = int(time.time() * 1000) - days * 24 * 3600 * 1000
    proposals = await system.repository.get_active_proposals(strategy_id, status=None)
    # 기간 필터 + PRUNED/DEFERRED 포함 전체 조회
    all_proposals = [p for p in proposals if (p.get("created_at") or 0) > cutoff]

    for prop in all_proposals:
        for field in ("proposed_params", "original_params", "metrics"):
            if isinstance(prop.get(field), str):
                try:
                    prop[field] = json.loads(prop[field])
                except Exception:
                    prop[field] = {}

    return all_proposals


async def _load_entropy_timeline(system, strategy_id: str = None, days: int = 7) -> list:
    """
    하루 단위로 끊어서 Entropy 시계열을 구성합니다.
    """
    now_ms = int(time.time() * 1000)
    one_day_ms = 24 * 3600 * 1000
    timeline = []

    all_proposals = await _load_proposals(system, strategy_id, days=days + 1)

    for day_offset in range(days, 0, -1):
        end_ts = now_ms - (day_offset - 1) * one_day_ms
        start_ts = end_ts - one_day_ms
        day_props = [
            p for p in all_proposals
            if start_ts <= (p.get("created_at") or 0) < end_ts
        ]
        # 해당 날까지 누적 제안 기준
        cumulative = [
            p for p in all_proposals
            if (p.get("created_at") or 0) < end_ts
        ]
        from src.engine.diversity_analyzer import calculate_parameter_entropy
        entropy = calculate_parameter_entropy(cumulative) if cumulative else 1.0
        timeline.append({
            "ts": end_ts,
            "entropy": round(entropy, 4),
            "proposal_count": len(day_props),
        })

    return timeline


@router.get("/api/intelligence/diversity")
async def get_diversity_map(request: Request, strategy_id: str = None):
    """
    현재 전략 파라미터 공간의 다양성 상태를 반환합니다.

    - entropy: 파라미터 공간 다양성 지수 (0=완전수렴, 1=완전분산)
    - convergence_alert: entropy < 0.3 일 때 True
    - param_distributions: 파라미터별 mean/std/values
    - pruning_accuracy: PRUNED 오판율 집계
    - combined_boost: 복합 λ 보정 신호 (alert_level, lambda_boost 등)
    - decision_drift: Entropy 시계열 (최근 7일)
    - mutation_graph: 파라미터 변이 계보
    - replay_status: 비동기 랭킹 정정/승격 차단 상태
    """
    system = request.app.state.system
    try:
        proposals = await _load_proposals(system, strategy_id, days=30)

        # 수렴 분석
        convergence_result = detect_convergence(proposals, threshold=0.3)

        # 오판율
        pruning_acc = calculate_pruning_accuracy(proposals)

        # 복합 λ 신호
        combined = get_combined_lambda_boost(proposals, entropy_threshold=0.3, max_boost=1.2)

        # Entropy 시계열
        entropy_timeline = await _load_entropy_timeline(system, strategy_id, days=7)

        # Mutation Trace Graph
        mutation_graph = build_mutation_trace_graph(proposals)

        # Promotion Queue 상태 뷰 리빌드 및 리플레이 지표 로드
        from src.engine.promotion_queue import PromotionQueue
        pq = PromotionQueue(db_path=system.db_path)
        await pq.init_table()
        await pq.rebuild_materialized_view()

        return {
            "strategy_id": strategy_id,
            "entropy": convergence_result["entropy"],
            "convergence_alert": convergence_result["convergence_alert"],
            "param_distributions": convergence_result["param_distributions"],
            "pruning_accuracy": pruning_acc,
            "combined_boost": combined,
            "decision_drift": {
                "entropy_timeline": entropy_timeline,
            },
            "mutation_graph": mutation_graph,
            "replay_status": {
                "correction_active": pq.correction_active,
                "rank_drift": pq.rank_drift,
                "last_replay_corrected_at": pq.last_replay_corrected_at,
                "promotion_block_reason": pq.promotion_block_reason
            }
        }
    except Exception as e:
        logger.error(f"[Intelligence] /diversity 오류: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/intelligence/counterfactual-summary")
async def get_counterfactual_summary(request: Request, strategy_id: str = None):
    """
    Counterfactual 추적 현황 집계를 반환합니다.

    - total_tracked: 추적 중 + 완료 합계
    - completed: 7일 관찰 완료 건수
    - in_progress: 추적 중 건수
    - outperformed_live: 실거래보다 가상 ROI가 높았던 건수
    - outperform_rate: 오판율
    - avg_counterfactual_roi: 완료 제안의 평균 가상 ROI
    - items: 상세 목록
    """
    system = request.app.state.system
    try:
        proposals = await _load_proposals(system, strategy_id, days=30)

        tracked = [p for p in proposals if (p.get("is_counterfactual_tracked") or 0) > 0]
        completed = [p for p in tracked if p.get("is_counterfactual_tracked") == 2]
        in_progress = [p for p in tracked if p.get("is_counterfactual_tracked") == 1]

        outperformed = [p for p in completed if (p.get("counterfactual_roi") or 0.0) > 0.0]
        outperform_rate = len(outperformed) / len(completed) if completed else 0.0

        now_ms = int(time.time() * 1000)
        items = []
        for p in tracked:
            created_at = p.get("created_at") or now_ms
            days_observed = round((now_ms - created_at) / (24 * 3600 * 1000), 1)
            items.append({
                "proposal_id": p.get("id"),
                "strategy_id": p.get("strategy_id"),
                "confidence_score": p.get("confidence_score"),
                "status": p.get("status"),
                "counterfactual_roi": round(p.get("counterfactual_roi") or 0.0, 4),
                "counterfactual_mdd": round(p.get("counterfactual_mdd") or 0.0, 4),
                "is_tracked": p.get("is_counterfactual_tracked"),
                "days_observed": days_observed,
            })

        avg_cf_roi = (
            sum(p.get("counterfactual_roi") or 0.0 for p in completed) / len(completed)
            if completed else 0.0
        )

        return {
            "total_tracked": len(tracked),
            "completed": len(completed),
            "in_progress": len(in_progress),
            "outperformed_live": len(outperformed),
            "outperform_rate": round(outperform_rate, 4),
            "avg_counterfactual_roi": round(avg_cf_roi, 4),
            "items": sorted(items, key=lambda x: x["days_observed"], reverse=True),
        }
    except Exception as e:
        logger.error(f"[Intelligence] /counterfactual-summary 오류: {e}")
        raise HTTPException(status_code=500, detail=str(e))
