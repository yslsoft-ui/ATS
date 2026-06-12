# -*- coding: utf-8 -*-

import json
import time
import uuid
import hashlib
from typing import Dict, Any, List, Optional
from fastapi import APIRouter, Request, HTTPException
from src.engine.utils.telemetry import get_logger
from src.database.connection import get_db_conn
from src.engine.strategy import StrategyRegistry

logger = get_logger("decision_console_router")
router = APIRouter()

@router.get("/api/decision-console/summary")
async def get_summary(request: Request):
    """의사결정 콘솔의 상단 상태 요약 정보를 반환합니다."""
    system = request.app.state.system
    
    # 1. 설정 정보 로드
    config = system.config_manager.config if hasattr(system, 'config_manager') else {}
    system_cfg = config.get("system", {})
    
    op_mode = system_cfg.get("operation_mode", "shadow")
    live_trading = system_cfg.get("live_trading_enabled", False)
    auto_proposal = system_cfg.get("enable_auto_proposal", False)
    auto_promotion = system_cfg.get("auto_strategy_promotion_enabled", False)
    
    # 2. DB 정보 쿼리
    active_strategies_count = 0
    champion_strategies_count = 0
    pending_proposals_count = 0
    blocked_proposals_count = 0
    recent_promotion_time = "-"
    data_quality_status = "정상"
    girs_stability = 1.0
    
    try:
        # 활성 전략 수 판별 (설정 기반)
        active_strategies_count = len([s for s in system.strategy_configs.values() if s.get("enabled", False)])
        
        async with get_db_conn(system.db_path) as db:
            # 챔피언 수 쿼리 (strategy_versions)
            async with db.execute("SELECT COUNT(*) as cnt FROM strategy_versions") as cur:
                row = await cur.fetchone()
                champion_strategies_count = row["cnt"] if row else 0
                
            # 대기 제안 수 (PENDING)
            async with db.execute("SELECT COUNT(*) as cnt FROM strategy_proposals WHERE status = 'PENDING'") as cur:
                row = await cur.fetchone()
                pending_proposals_count = row["cnt"] if row else 0
                
            # 차단/폐기 제안 수 (PRUNED, DEFERRED, REJECTED)
            async with db.execute("SELECT COUNT(*) as cnt FROM strategy_proposals WHERE status IN ('PRUNED', 'DEFERRED', 'REJECTED')") as cur:
                row = await cur.fetchone()
                blocked_proposals_count = row["cnt"] if row else 0
                
            # 최근 승격 시각 (APPLIED)
            async with db.execute("SELECT MAX(applied_at) as max_applied_at FROM strategy_proposals WHERE status = 'APPLIED'") as cur:
                row = await cur.fetchone()
                if row and row["max_applied_at"]:
                    recent_promotion_time = format_timestamp(row["max_applied_at"])
                    
            # 데이터 품질 & GIRS 안정성 조회 (최근 girs_shadow_metrics 기준)
            async with db.execute("SELECT is_fresh, final_promotion_score, replay_drift, stale_reason FROM girs_shadow_metrics ORDER BY timestamp DESC LIMIT 1") as cur:
                row = await cur.fetchone()
                if row:
                    is_fresh = row["is_fresh"]
                    final_promotion_score = row["final_promotion_score"]
                    stale_reason = row["stale_reason"]
                    
                    if is_fresh == 0:
                        data_quality_status = f"차단 ({stale_reason or 'Stale'})"
                    else:
                        data_quality_status = "정상"
                        
                    if final_promotion_score is not None:
                        girs_stability = round(final_promotion_score, 2)
                        
    except Exception as e:
        logger.error(f"[Summary API] Error loading summary metrics: {e}")
        
    return {
        "operation_mode": op_mode,
        "live_trading_enabled": live_trading,
        "enable_auto_proposal": auto_proposal,
        "auto_strategy_promotion_enabled": auto_promotion,
        "active_strategies_count": active_strategies_count,
        "champion_strategies_count": champion_strategies_count,
        "pending_proposals_count": pending_proposals_count,
        "blocked_proposals_count": blocked_proposals_count,
        "recent_promotion_time": recent_promotion_time,
        "data_quality_status": data_quality_status,
        "girs_stability": girs_stability
    }

@router.get("/api/decision-console/strategies")
async def get_strategies(request: Request):
    """모든 전략의 상태 불일치 및 챔피언 누락 감지를 포함한 목록을 반환합니다."""
    system = request.app.state.system
    all_meta = StrategyRegistry.get_all_metadata()
    configs = system.strategy_configs
    
    results = []
    try:
        async with get_db_conn(system.db_path) as db:
            # DB 챔피언 정보 로드
            async with db.execute("SELECT strategy_id, current_version_id, current_params FROM strategy_versions") as cur:
                db_versions = {r["strategy_id"]: {"version": r["current_version_id"], "params": r["current_params"]} for r in await cur.fetchall()}
                
        # 실제 데몬/엔진 활성화 상태 조회
        active_p = system.portfolio_manager.get_active_simulation_portfolio() or system.portfolio_manager.portfolios.get('live')
        applied_in_engine = {}
        if active_p and hasattr(active_p, 'strategy_info') and active_p.strategy_info:
            try:
                meta = json.loads(active_p.strategy_info)
                applied_in_engine = meta.get("applied_strategies", {})
            except:
                pass
                
        for meta in all_meta:
            s_id = meta['id']
            config = configs.get(s_id, {"enabled": False, "params": {}})
            
            # 1. 설정 활성 상태
            settings_enabled = config.get('enabled', False)
            
            # 2. DB 챔피언 버전 정보
            db_champ = db_versions.get(s_id)
            db_version_id = db_champ["version"] if db_champ else None
            
            # 3. 데몬 로딩 상태 & 실제 엔진 적용 버전
            engine_info = applied_in_engine.get(s_id) or applied_in_engine.get(meta['name'])
            engine_enabled = False
            engine_version = None
            if engine_info:
                engine_enabled = engine_info.get("enabled", False)
                engine_version = engine_info.get("version_id")
                
            # 일치성 여부 판단
            is_synced = True
            mismatch_reason = []
            
            if settings_enabled != engine_enabled:
                is_synced = False
                mismatch_reason.append(f"설정 파일 활성화({settings_enabled})와 실제 엔진 기동({engine_enabled}) 불일치")
                
            if settings_enabled and not db_version_id:
                is_synced = False
                mismatch_reason.append("활성 전략이나 DB에 등극된 챔피언 버전이 없습니다 (신규 시작 시 누락 위험)")
                
            results.append({
                "id": s_id,
                "name": meta['name'],
                "type": meta['type'],
                "description": meta['description'],
                "settings_enabled": settings_enabled,
                "db_champion_version": f"V{db_version_id}" if db_version_id else "None",
                "engine_enabled": engine_enabled,
                "engine_version": f"V{engine_version}" if engine_version else "None",
                "is_synced": is_synced,
                "mismatch_reason": ", ".join(mismatch_reason) if mismatch_reason else None
            })
    except Exception as e:
        logger.error(f"[Strategies API] Error loading strategies list: {e}")
        raise HTTPException(status_code=500, detail=str(e))
        
    return results

@router.get("/api/decision-console/strategies/{strategy_id}/trace")
async def get_strategy_trace(strategy_id: str, request: Request):
    """특정 전략의 일치성, 파라미터 변동 이력, 최근 성과 스냅샷 및 의사결정 타임라인을 반환합니다."""
    system = request.app.state.system
    
    try:
        async with get_db_conn(system.db_path) as db:
            # 1. 일치성 검사 상세 조회
            # DB 챔피언
            async with db.execute("SELECT current_version_id, current_params, applied_at FROM strategy_versions WHERE strategy_id = ?", (strategy_id,)) as cur:
                db_row = await cur.fetchone()
                
            db_version = db_row["current_version_id"] if db_row else None
            db_params = json.loads(db_row["current_params"]) if db_row and db_row["current_params"] else {}
            applied_at = db_row["applied_at"] if db_row else None
            
            # 설정
            config = system.strategy_configs.get(strategy_id, {"enabled": False, "params": {}})
            settings_enabled = config.get("enabled", False)
            
            # 엔진 상태
            active_p = system.portfolio_manager.get_active_simulation_portfolio() or system.portfolio_manager.portfolios.get('live')
            engine_enabled = False
            engine_version = None
            if active_p and hasattr(active_p, 'strategy_info') and active_p.strategy_info:
                try:
                    meta = json.loads(active_p.strategy_info)
                    applied_in_engine = meta.get("applied_strategies", {})
                    engine_info = applied_in_engine.get(strategy_id) or applied_in_engine.get(StrategyRegistry.get_strategy_class(strategy_id).__name__ if StrategyRegistry.get_strategy_class(strategy_id) else strategy_id)
                    if engine_info:
                        engine_enabled = engine_info.get("enabled", False)
                        engine_version = engine_info.get("version_id")
                except:
                    pass
            
            # 2. 파라미터 분석 (현재값 vs 이전값)
            # 최근 2건의 parameter_history 쿼리
            async with db.execute(
                "SELECT version_id, new_params, old_params, change_reason, changed_by, created_at "
                "FROM strategy_parameter_history WHERE strategy_id = ? ORDER BY version_id DESC LIMIT 2",
                (strategy_id,)
            ) as cur:
                hist_rows = await cur.fetchall()
                
            current_params_diff = []
            if hist_rows:
                latest = hist_rows[0]
                latest_new = json.loads(latest["new_params"]) if isinstance(latest["new_params"], str) else latest["new_params"] or {}
                latest_old = json.loads(latest["old_params"]) if isinstance(latest["old_params"], str) else latest["old_params"] or {}
                
                for k, v in latest_new.items():
                    current_params_diff.append({
                        "name": k,
                        "current": v,
                        "previous": latest_old.get(k, "-"),
                        "changed_at": format_timestamp(latest["created_at"]) if isinstance(latest["created_at"], (int, float)) else latest["created_at"],
                        "change_reason": latest["change_reason"],
                        "changed_by": latest["changed_by"]
                    })
            
            # 3. 최근 성과 지표 (snapshots 쿼리)
            async with db.execute(
                "SELECT version_id, snapshot_type, timestamp, roi, mdd, profit_factor, win_rate, trade_count "
                "FROM strategy_performance_snapshots WHERE strategy_id = ? ORDER BY timestamp DESC LIMIT 10",
                (strategy_id,)
            ) as cur:
                snapshot_rows = await cur.fetchall()
                
            snapshots = []
            for r in snapshot_rows:
                snapshots.append({
                    "version_id": r["version_id"],
                    "snapshot_type": r["snapshot_type"],
                    "timestamp": r["timestamp"],
                    "roi": r["roi"],
                    "mdd": r["mdd"],
                    "profit_factor": r["profit_factor"],
                    "win_rate": r["win_rate"],
                    "trade_count": r["trade_count"]
                })
                
            # 4. 의사결정 타임라인 구성
            # Proposals, Version History, System_events 결합
            timeline = []
            
            # A. Proposals
            async with db.execute(
                "SELECT id, status, created_at, updated_at, confidence_score "
                "FROM strategy_proposals WHERE strategy_id = ? ORDER BY created_at DESC LIMIT 10",
                (strategy_id,)
            ) as cur:
                prop_rows = await cur.fetchall()
                for r in prop_rows:
                    timeline.append({
                        "type": "PROPOSAL",
                        "id": r["id"],
                        "title": f"Proposal #{r['id']} ({r['status']}) 생성",
                        "timestamp": r["created_at"],
                        "description": f"신뢰도 점수: {r['confidence_score']}점"
                    })
                    
            # B. Parameter History
            for h in hist_rows:
                timeline.append({
                    "type": "VERSION",
                    "id": h["version_id"],
                    "title": f"버전 V{h['version_id']} 적용 완료",
                    "timestamp": h["created_at"],
                    "description": f"원인: {h['change_reason']} (주체: {h['changed_by']})"
                })
                
            # C. System Events (Strategy 관련 필터)
            async with db.execute(
                "SELECT event_type, message, timestamp "
                "FROM system_events WHERE target = ? OR message LIKE ? ORDER BY timestamp DESC LIMIT 10",
                (strategy_id, f"%{strategy_id}%")
            ) as cur:
                evt_rows = await cur.fetchall()
                for r in evt_rows:
                    timeline.append({
                        "type": "SYSTEM_EVENT",
                        "id": r["event_type"],
                        "title": f"시스템 이벤트: {r['event_type']}",
                        "timestamp": r["timestamp"],
                        "description": r["message"]
                    })
                    
            # 타임라인 시간순 정렬
            # 데이터베이스의 timestamp 형식이 섞여 있으므로 정규화 처리
            for item in timeline:
                if isinstance(item["timestamp"], str):
                    try:
                        item["timestamp_normalized"] = int(float(item["timestamp"]))
                    except:
                        item["timestamp_normalized"] = 0
                else:
                    item["timestamp_normalized"] = int(item["timestamp"])
            timeline.sort(key=lambda x: x["timestamp_normalized"], reverse=True)
            
            # 최종 정렬 및 15개 슬라이싱
            timeline = timeline[:15]
            
            # 일치성 요약 경고 생성
            is_synced = (settings_enabled == engine_enabled) and (not settings_enabled or db_version is not None)
            alert_msg = None
            if not is_synced:
                if settings_enabled != engine_enabled:
                    alert_msg = f"설정 활성화와 실제 엔진 구동 불일치 (설정: {settings_enabled}, 엔진: {engine_enabled})"
                elif not db_version:
                    alert_msg = "활성 전략이나 DB에 등극된 챔피언 버전이 없습니다 (신규 시작 시 누락 위험)"
            
            return {
                "strategy_id": strategy_id,
                "settings_enabled": settings_enabled,
                "db_champion_version": db_version,
                "engine_enabled": engine_enabled,
                "engine_version": engine_version,
                "applied_at": applied_at,
                "is_synced": is_synced,
                "sync_alert_message": alert_msg,
                "params_diff": current_params_diff,
                "snapshots": snapshots,
                "timeline": timeline
            }
            
    except Exception as e:
        logger.error(f"[Strategy Trace API] Error tracing strategy {strategy_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/api/decision-console/proposals")
async def get_proposals(request: Request, strategy_id: str = None, status: str = None):
    """지정된 필터 조건에 따른 제안 목록을 반환합니다."""
    system = request.app.state.system
    
    query = "SELECT * FROM strategy_proposals"
    params = []
    conditions = []
    
    if strategy_id:
        conditions.append("strategy_id = ?")
        params.append(strategy_id)
    if status:
        if status == "PRUNED_DEFERRED":
            conditions.append("status IN ('PRUNED', 'DEFERRED', 'REJECTED')")
        else:
            conditions.append("status = ?")
            params.append(status)
            
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
        
    query += " ORDER BY created_at DESC LIMIT 50"
    
    try:
        async with get_db_conn(system.db_path) as db:
            async with db.execute(query, params) as cur:
                rows = await cur.fetchall()
                results = []
                for r in rows:
                    p = dict(r)
                    # JSON 문자열 역직렬화
                    for field in ("proposed_params", "original_params", "metrics", "mutation_trace", "audit_log_json"):
                        if field in p and isinstance(p[field], str) and p[field]:
                            try:
                                p[field] = json.loads(p[field])
                            except:
                                pass
                    results.append(p)
                return results
    except Exception as e:
        logger.error(f"[Proposals API] Error fetching proposals: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/api/decision-console/proposals/{proposal_id}/trace")
async def get_proposal_trace(proposal_id: int, request: Request):
    """특정 제안의 디버그를 위한 10대 백데이터 패키지를 수집하여 반환합니다."""
    system = request.app.state.system
    
    try:
        async with get_db_conn(system.db_path) as db:
            # 1. 원본 Proposal 로드
            async with db.execute("SELECT * FROM strategy_proposals WHERE id = ?", (proposal_id,)) as cur:
                prop_row = await cur.fetchone()
                
            if not prop_row:
                raise HTTPException(status_code=404, detail=f"Proposal #{proposal_id} not found")
                
            proposal = dict(prop_row)
            for field in ("proposed_params", "original_params", "metrics", "mutation_trace", "audit_log_json"):
                if field in proposal and isinstance(proposal[field], str) and proposal[field]:
                    try:
                        proposal[field] = json.loads(proposal[field])
                    except:
                        pass
                        
            strategy_id = proposal["strategy_id"]
            
            # 2. 관련 전략 정보 로드
            async with db.execute("SELECT * FROM strategy_versions WHERE strategy_id = ?", (strategy_id,)) as cur:
                strat_row = await cur.fetchone()
            strategy_version = dict(strat_row) if strat_row else {}
            if "current_params" in strategy_version and isinstance(strategy_version["current_params"], str):
                try:
                    strategy_version["current_params"] = json.loads(strategy_version["current_params"])
                except:
                    pass
            
            # 3. FSM 생명주기 타임라인 (promotion_event_log 로드)
            async with db.execute(
                "SELECT event_type, payload, timestamp, feature_snapshot, model_version, scaler_version "
                "FROM promotion_event_log WHERE proposal_id = ? ORDER BY sequence_no ASC",
                (str(proposal_id),)
            ) as cur:
                event_rows = await cur.fetchall()
                
            fsm_timeline = []
            feature_snapshot = None
            model_version = None
            scaler_version = None
            
            for r in event_rows:
                evt_type = r["event_type"]
                payload = json.loads(r["payload"]) if r["payload"] else {}
                ts = r["timestamp"]
                
                # 피처 스냅샷 추출 (첫 번째 feature_snapshot이 있는 것 기준)
                if r["feature_snapshot"] and not feature_snapshot:
                    try:
                        feature_snapshot = json.loads(r["feature_snapshot"])
                        model_version = r["model_version"]
                        scaler_version = r["scaler_version"]
                    except:
                        pass
                        
                fsm_timeline.append({
                    "state": evt_type.replace("STATE_CHANGED_", ""),
                    "timestamp": ts,
                    "payload": payload,
                    "success": True
                })
                
            # 4. GIRS 점수 상세 정보 수집 (girs_shadow_metrics 테이블 쿼리)
            async with db.execute(
                "SELECT model_risk_score, fallback_risk_score, final_promotion_score, replay_drift, correction_active, blocked_reason, timestamp "
                "FROM girs_shadow_metrics WHERE proposal_id = ? ORDER BY timestamp DESC LIMIT 1",
                (str(proposal_id),)
            ) as cur:
                girs_row = await cur.fetchone()
                
            girs_score = {}
            if girs_row:
                girs_score = {
                    "model_risk_score": girs_row["model_risk_score"],
                    "fallback_risk_score": girs_row["fallback_risk_score"],
                    "final_promotion_score": girs_row["final_promotion_score"],
                    "replay_drift": girs_row["replay_drift"],
                    "correction_active": girs_row["correction_active"] == 1,
                    "blocked_reason": girs_row["blocked_reason"],
                    "timestamp": girs_row["timestamp"]
                }
                
            # 5. Counterfactual Simulation & Horizon 결과 로드
            async with db.execute(
                "SELECT horizon_name, candidate_roi, champion_roi, roi_gap, candidate_mdd, champion_mdd, virtual_rollback, evaluation_status, last_error "
                "FROM proposal_evaluations WHERE proposal_id = ?",
                (proposal_id,)
            ) as cur:
                eval_rows = await cur.fetchall()
                
            evaluations = []
            for r in eval_rows:
                evaluations.append({
                    "horizon_name": r["horizon_name"],
                    "candidate_roi": r["candidate_roi"],
                    "champion_roi": r["champion_roi"],
                    "roi_gap": r["roi_gap"],
                    "candidate_mdd": r["candidate_mdd"],
                    "champion_mdd": r["champion_mdd"],
                    "virtual_rollback": r["virtual_rollback"] == 1,
                    "status": r["evaluation_status"],
                    "error": r["last_error"]
                })
                
            # 6. Promotion Queue 상태 및 가드 상태 획득
            # (FSM 상태, Cooldown, Guard 체크)
            # 설정 값 읽어오기
            config = system.config_manager.config if hasattr(system, 'config_manager') else {}
            system_cfg = config.get("system", {})
            auto_promotion = system_cfg.get("auto_strategy_promotion_enabled", False)
            live_trading = system_cfg.get("live_trading_enabled", False)
            
            guards = [
                {"name": "자동 승격 활성화 (auto_strategy_promotion_enabled)", "status": "PASS" if auto_promotion else "BLOCKED", "reason": None if auto_promotion else "auto_strategy_promotion_enabled=false"},
                {"name": "실거래 비상 차단 스위치 비활성화 (live_trading_enabled)", "status": "PASS" if not live_trading else "WARN", "reason": None if not live_trading else "실거래 모드 활성화로 추가 안정성 가드 적용 중"},
            ]
            
            if girs_score:
                stability = girs_score.get("final_promotion_score") or 1.0
                guards.append({
                    "name": "GIRS 안정성 임계치 통과 (stability_score > 0.2)",
                    "status": "PASS" if stability > 0.2 else "BLOCKED",
                    "reason": None if stability > 0.2 else f"stability_score {stability} <= 0.2 로 자동 승격 원천 차단"
                })
                
            # Cooldown 체크
            curr_ver = await db.execute("SELECT applied_at FROM strategy_versions WHERE strategy_id = ?", (strategy_id,))
            ver_row = await curr_ver.fetchone()
            if ver_row and ver_row["applied_at"]:
                applied_ts = ver_row["applied_at"]
                elapsed_days = (time.time() * 1000 - applied_ts) / (24 * 3600 * 1000)
                cooldown_days = system_cfg.get("champion_cooldown_days", 7.0)
                
                status_cooldown = "PASS" if elapsed_days >= cooldown_days else "BLOCKED"
                guards.append({
                    "name": f"챔피언 교체 Cooldown 검사 (>= {cooldown_days}일 경과)",
                    "status": status_cooldown,
                    "reason": None if status_cooldown == "PASS" else f"마지막 승격 이후 {elapsed_days:.2f}일만 경과 (최소 {cooldown_days}일 대기 필요)"
                })
                
            # 7. 관련 주문 내역 조회 (orders_history)
            async with db.execute(
                "SELECT id, side, symbol, price, quantity, fee, timestamp, reason, market "
                "FROM orders_history WHERE strategy_id = ? ORDER BY timestamp DESC LIMIT 20",
                (strategy_id,)
            ) as cur:
                order_rows = await cur.fetchall()
                
            related_orders = []
            for r in order_rows:
                related_orders.append({
                    "id": r["id"],
                    "side": r["side"],
                    "symbol": r["symbol"],
                    "price": r["price"],
                    "quantity": r["quantity"],
                    "fee": r["fee"],
                    "timestamp": r["timestamp"],
                    "reason": r["reason"],
                    "market": r["market"]
                })
                
            # 8. 관련 시스템 감사 이벤트 로그 수집 (system_events)
            async with db.execute(
                "SELECT event_type, message, timestamp, context "
                "FROM system_events WHERE (target = ? OR message LIKE ? OR context LIKE ?) ORDER BY timestamp DESC LIMIT 20",
                (str(proposal_id), f"%Proposal #{proposal_id}%", f"%{proposal_id}%")
            ) as cur:
                evt_rows = await cur.fetchall()
                
            related_events = []
            for r in evt_rows:
                context_data = {}
                if r["context"]:
                    try:
                        context_data = json.loads(r["context"])
                    except:
                        pass
                related_events.append({
                    "event_type": r["event_type"],
                    "message": r["message"],
                    "timestamp": r["timestamp"],
                    "context": context_data
                })
                
            # 9. 수동 재평가 Job 이력 정보 로드
            async with db.execute(
                "SELECT job_id, status, requested_at, finished_at, error_message "
                "FROM proposal_reevaluation_jobs WHERE proposal_id = ? ORDER BY requested_at DESC",
                (proposal_id,)
            ) as cur:
                job_rows = await cur.fetchall()
            reeval_jobs = []
            for r in job_rows:
                reeval_jobs.append({
                    "job_id": r["job_id"],
                    "status": r["status"],
                    "requested_at": r["requested_at"],
                    "finished_at": r["finished_at"],
                    "error_message": r["error_message"]
                })
                
            return {
                "proposal": proposal,
                "strategy": strategy_version,
                "fsm_timeline": fsm_timeline,
                "girs_score": girs_score,
                "feature_snapshot": feature_snapshot,
                "model_version": model_version,
                "scaler_version": scaler_version,
                "evaluations": evaluations,
                "guards": guards,
                "related_orders": related_orders,
                "related_events": related_events,
                "reeval_jobs": reeval_jobs
            }
    except Exception as e:
        logger.error(f"[Proposal Trace API] Error tracing proposal #{proposal_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/api/decision-console/proposals/{proposal_id}/reevaluate")
async def request_reevaluation(proposal_id: int, request: Request):
    """특정 제안에 대한 수동 재평가를 Job Queue에 비동기 등록합니다."""
    system = request.app.state.system
    now_ms = int(time.time() * 1000)
    
    try:
        async with get_db_conn(system.db_path) as db:
            # 1. 이미 RUNNING 이나 QUEUED 상태의 동일 proposal Job이 있는지 검증
            async with db.execute(
                "SELECT job_id, status, requested_at FROM proposal_reevaluation_jobs "
                "WHERE proposal_id = ? AND status IN ('QUEUED', 'RUNNING') LIMIT 1",
                (proposal_id,)
            ) as cur:
                exist_row = await cur.fetchone()
                
            if exist_row:
                return {
                    "accepted": False,
                    "job_id": exist_row["job_id"],
                    "proposal_id": proposal_id,
                    "status": exist_row["status"],
                    "message": "이미 동일 제안에 대해 진행 중이거나 대기 중인 재평가 작업이 존재합니다."
                }
                
            # 2. proposal의 input_snapshot_id (또는 feature_snapshot 데이터 정보) 조회
            # promotion_event_log의 PROPOSAL_ENTERED 시점 row id 등
            async with db.execute(
                "SELECT global_sequence_no FROM promotion_event_log WHERE proposal_id = ? AND event_type = 'PROPOSAL_ENTERED' LIMIT 1",
                (str(proposal_id),)
            ) as cur:
                seq_row = await cur.fetchone()
            input_snapshot_id = seq_row["global_sequence_no"] if seq_row else None
            
            # 3. Job 등록
            cursor = await db.execute(
                "INSERT INTO proposal_reevaluation_jobs "
                "(proposal_id, status, requested_at, requested_by, mode, input_snapshot_id) "
                "VALUES (?, 'QUEUED', ?, 'USER', 'shadow_revaluation', ?)",
                (proposal_id, now_ms, input_snapshot_id)
            )
            job_id = cursor.lastrowid
            await db.commit()
            
            # 4. 감사 로그 system_events 등록
            await db.execute(
                "INSERT INTO system_events (event_type, target, message, timestamp, context) "
                "VALUES ('PROPOSAL_REEVALUATION_REQUESTED', ?, ?, ?, ?)",
                (
                    str(proposal_id),
                    f"제안 #{proposal_id}에 대한 수동 재평가 Job #{job_id} 등록 완료",
                    now_ms,
                    json.dumps({"job_id": job_id, "proposal_id": proposal_id, "mode": "shadow_revaluation"})
                )
            )
            await db.commit()
            
        # 5. ZMQ 전송을 통한 shadow_eval_daemon 깨우기 (Wake-up) 트리거 송출
        if hasattr(request.app.state, 'strategy_control_publisher'):
            await request.app.state.strategy_control_publisher.publish("shadow_eval_control", {
                "type": "reevaluate_trigger",
                "job_id": job_id,
                "proposal_id": proposal_id
            })
            logger.info(f"[Re-evaluation API] ZMQ shadow_eval_control wake-up signal published for Job #{job_id}")
            
        return {
            "accepted": True,
            "job_id": job_id,
            "proposal_id": proposal_id,
            "status": "QUEUED",
            "mode": "shadow_revaluation",
            "side_effects_allowed": False
        }
    except Exception as e:
        logger.error(f"[Re-evaluation API] Failed to request re-evaluation for proposal #{proposal_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/api/decision-console/proposals/{proposal_id}/reevaluation-jobs")
async def get_reevaluation_jobs(proposal_id: int, request: Request):
    """특정 제안의 수동 재평가 작업 이력 조회를 반환합니다."""
    system = request.app.state.system
    try:
        async with get_db_conn(system.db_path) as db:
            async with db.execute(
                "SELECT job_id, status, requested_at, started_at, finished_at, error_message, worker_id "
                "FROM proposal_reevaluation_jobs WHERE proposal_id = ? ORDER BY requested_at DESC",
                (proposal_id,)
            ) as cur:
                rows = await cur.fetchall()
                results = []
                for r in rows:
                    results.append({
                        "job_id": r["job_id"],
                        "status": r["status"],
                        "requested_at": format_timestamp(r["requested_at"]) if r["requested_at"] else "-",
                        "started_at": format_timestamp(r["started_at"]) if r["started_at"] else "-",
                        "finished_at": format_timestamp(r["finished_at"]) if r["finished_at"] else "-",
                        "error_message": r["error_message"],
                        "worker_id": r["worker_id"]
                    })
                return results
    except Exception as e:
        logger.error(f"[Reevaluation Jobs API] Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/api/decision-console/events")
async def get_console_events(request: Request, event_type: str = None, target: str = None, limit: int = 50):
    """의사결정 전용 감사 로그 필터링 조회를 반환합니다."""
    system = request.app.state.system
    
    query = "SELECT event_type, target, message, timestamp, context FROM system_events"
    conditions = []
    params = []
    
    # 의사결정 관련 필터 강제 또는 명시적 필터
    if event_type:
        conditions.append("event_type = ?")
        params.append(event_type)
    else:
        # 기본적으로 의사결정 관련 주요 감사 로그 필터링
        decision_types = (
            "PROPOSAL_APPROVED", "STRATEGY_ROLLBACK", "PROPOSAL_REEVALUATION_REQUESTED",
            "PROPOSAL_REEVALUATION_STARTED", "PROPOSAL_REEVALUATION_COMPLETED", "PROPOSAL_REEVALUATION_FAILED",
            "UNIVERSE_PROMOTION", "UNIVERSE_DEMOTION", "PROMOTION_COOLDOWN_BLOCKED", "PROMOTION_LIMIT_BLOCKED"
        )
        conditions.append("event_type IN (" + ",".join("?" for _ in decision_types) + ")")
        params.extend(decision_types)
        
    if target:
        conditions.append("target = ?")
        params.append(target)
        
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
        
    query += " ORDER BY timestamp DESC LIMIT ?"
    params.append(limit)
    
    try:
        async with get_db_conn(system.db_path) as db:
            async with db.execute(query, params) as cur:
                rows = await cur.fetchall()
                results = []
                for r in rows:
                    context_data = {}
                    if r["context"]:
                        try:
                            context_data = json.loads(r["context"])
                        except:
                            pass
                    results.append({
                        "event_type": r["event_type"],
                        "target": r["target"],
                        "message": r["message"],
                        "timestamp": format_timestamp(r["timestamp"]) if r["timestamp"] else "-",
                        "context": context_data
                    })
                return results
    except Exception as e:
        logger.error(f"[Console Events API] Error fetching events: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/api/decision-console/raw/{object_type}/{object_id}")
async def get_raw_data(object_type: str, object_id: str, request: Request):
    """각 객체의 원본 데이터베이스 레코드를 JSON으로 그대로 반환합니다."""
    system = request.app.state.system
    
    table_map = {
        "proposal": ("strategy_proposals", "id"),
        "snapshot": ("strategy_performance_snapshots", "id"),
        "event": ("system_events", "id"),
        "history": ("strategy_parameter_history", "id"),
        "job": ("proposal_reevaluation_jobs", "job_id"),
        "run": ("proposal_evaluation_runs", "evaluation_run_id")
    }
    
    if object_type not in table_map:
        raise HTTPException(status_code=400, detail=f"Unsupported object type '{object_type}'")
        
    table_name, pk_col = table_map[object_type]
    
    try:
        # object_id를 타입에 따라 변환 시도
        parsed_id = int(object_id) if object_id.isdigit() else object_id
    except:
        parsed_id = object_id
        
    try:
        async with get_db_conn(system.db_path) as db:
            async with db.execute(f"SELECT * FROM {table_name} WHERE {pk_col} = ?", (parsed_id,)) as cur:
                row = await cur.fetchone()
                
            if not row:
                raise HTTPException(status_code=404, detail=f"Object '{object_type}' with ID {object_id} not found")
                
            p = dict(row)
            # JSON 직렬화된 필드는 자동 역직렬화하여 깔끔한 JSONTree 구성 유도
            for k, v in list(p.items()):
                if isinstance(v, str) and v.strip().startswith(("{", "[")):
                    try:
                        p[k] = json.loads(v)
                    except:
                        pass
            return p
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"[Raw Data API] Error fetching raw {object_type} #{object_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ─────────────────────────────────────────────
# 헬퍼 함수 정의
# ─────────────────────────────────────────────

def format_timestamp(ts: Any) -> str:
    """밀리초/초 단위를 포맷팅된 문자열 날짜로 변환합니다."""
    if ts is None:
        return "-"
    try:
        ts_val = float(ts)
        # 밀리초 단위 보정
        if ts_val > 1000000000000:
            ts_val = ts_val / 1000.0
        return time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(ts_val))
    except:
        return str(ts)

def aiosqlite_row_factory(cursor, row):
    """aiosqlite용 dict row 팩토리"""
    return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}
