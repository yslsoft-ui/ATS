from fastapi import APIRouter, Request, HTTPException
from typing import Dict
from src.engine.strategy import StrategyRegistry
from src.engine.utils.telemetry import get_logger
from src.engine.command import UserCommand

logger = get_logger(__name__)
router = APIRouter()

async def _get_active_evaluations_count(system, strategy_id: str) -> int:
    """해당 전략에 대해 shadow_eval_daemon이 현재 진행 중(PENDING)인 평가 건수를 조회합니다."""
    from src.database.connection import get_db_conn
    from src.engine.strategy import StrategyRegistry
    
    strat_cls = StrategyRegistry.get_strategy_class(strategy_id)
    official_name = strat_cls.__name__ if strat_cls else strategy_id
    
    try:
        async with get_db_conn(system.db_path) as db:
            async with db.execute(
                "SELECT COUNT(*) FROM proposal_evaluations pe "
                "JOIN strategy_proposals sp ON pe.proposal_id = sp.id "
                "WHERE (sp.strategy_id = ? OR sp.strategy_id = ?) AND pe.evaluation_status = 'PENDING'",
                (strategy_id, official_name)
            ) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else 0
    except Exception as e:
        logger.error(f"Error getting active evaluations count for {strategy_id}: {e}")
        return 0

async def _determine_strategy_status(system, strategy_id: str) -> str:
    """활성 포트폴리오와 보유 잔고 상태를 연산하여 전략의 실시간 동작 상태를 판별합니다."""
    active_p = system.portfolio_manager.get_active_simulation_portfolio()
    if not active_p:
        active_p = system.portfolio_manager.portfolios.get('1')
        
    if not active_p or not getattr(active_p, 'strategy_info', None):
        return "DISABLED"
        
    try:
        import json
        meta = json.loads(active_p.strategy_info)
        applied = meta.get("applied_strategies", {})
        
        from src.engine.strategy import StrategyRegistry
        strat_cls = StrategyRegistry.get_strategy_class(strategy_id)
        official_name = strat_cls.__name__ if strat_cls else strategy_id
        
        if official_name not in applied:
            return "DISABLED"
            
        s_conf = applied[official_name]
        is_enabled = s_conf.get("enabled", False)
        if is_enabled:
            return "RUNNING"
            
        # 비활성화 상태인 경우 미청산 잔고가 남아있는지 대조
        from src.database.connection import get_db_conn
        async with get_db_conn(system.db_path) as db:
            async with db.execute(
                "SELECT SUM(CASE WHEN side = 'BUY' THEN quantity ELSE -quantity END) as qty "
                "FROM orders_history WHERE portfolio_id = ? AND strategy_id = ? GROUP BY symbol",
                (active_p.id, official_name)
            ) as cursor:
                rows = await cursor.fetchall()
                if any(row[0] > 0.000001 for row in rows if row[0] is not None):
                    return "EXIT_ONLY"
    except Exception as e:
        logger.error(f"Error determining strategy runtime status for {strategy_id}: {e}")
        
    return "DISABLED"

@router.get("/api/strategies")
async def list_strategies(request: Request):
    """사용 가능한 모든 전략 목록과 메타데이터를 반환합니다."""
    system = request.app.state.system
    all_meta = StrategyRegistry.get_all_metadata()
    configs = system.strategy_configs
    
    results = []
    for meta in all_meta:
        s_id = meta['id']
        config = configs.get(s_id, {"enabled": False, "params": {}})
        
        params_with_values = {}
        for p_name, p_info in meta['params'].items():
            current_val = config.get('params', {}).get(p_name, p_info.get('default'))
            params_with_values[p_name] = {
                **p_info,
                "current": current_val
            }
            
        status = await _determine_strategy_status(system, s_id)
        eval_count = await _get_active_evaluations_count(system, s_id)
            
        results.append({
            "id": s_id,
            "name": meta['name'],
            "type": meta['type'],
            "description": meta['description'],
            "enabled": config.get('enabled', False),
            "params": params_with_values,
            "status": status,
            "active_eval_count": eval_count
        })
        
    return results

@router.put("/api/strategies/{strategy_id}")
async def update_strategy_params(strategy_id: str, params: Dict, request: Request):
    """특정 전략의 파라미터를 업데이트하고 파일에 저장합니다."""
    system = request.app.state.system
    try:
        await system.dispatcher.dispatch(
            UserCommand.STRATEGY_UPDATE_PARAMS,
            {"strategy_id": strategy_id, "params": params}
        )
        await system._on_config_changed(system.config_manager.config)
        return {"message": f"Strategy {strategy_id} updated and saved", "params": params}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/api/strategies/{strategy_id}")
async def disable_strategy(strategy_id: str, request: Request):
    """특정 전략을 비활성화하고 파일에 저장합니다."""
    system = request.app.state.system
    try:
        await system.dispatcher.dispatch(
            UserCommand.STRATEGY_DISABLE,
            {"strategy_id": strategy_id}
        )
        await system._on_config_changed(system.config_manager.config)
        return {"message": f"Strategy {strategy_id} disabled and saved"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/api/strategies/{strategy_id}/enable")
async def enable_strategy(strategy_id: str, request: Request):
    """특정 전략을 활성화하고 파일에 저장합니다."""
    system = request.app.state.system
    try:
        await system.dispatcher.dispatch(
            UserCommand.STRATEGY_ENABLE,
            {"strategy_id": strategy_id}
        )
        await system._on_config_changed(system.config_manager.config)
        return {"message": f"Strategy {strategy_id} enabled and saved"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/api/strategies/restart-daemon")
async def restart_strategy_daemon(request: Request):
    """전략 엔진 데몬 프로세스 자체를 자가 재기동시킵니다."""
    system = request.app.state.system
    try:
        await system.dispatcher.dispatch(
            UserCommand.STRATEGY_RESTART_DAEMON,
            {"target": "strategy_daemon"}
        )
        return {"message": "Strategy daemon restart signal published successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/api/proposals")
async def list_proposals(request: Request, strategy_id: str = None, status: str = None, include_pruned: bool = False):
    """전략 파라미터 제안 목록을 조회합니다."""
    system = request.app.state.system
    try:
        proposals = await system.repository.get_active_proposals(strategy_id, status)
        if not include_pruned and status is None:
            proposals = [p for p in proposals if p["status"] not in ("PRUNED", "DEFERRED")]
        return proposals
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/api/proposals/{proposal_id}/approve")
async def approve_proposal(proposal_id: int, request: Request):
    """제안을 승인하고 실시간 적용을 개시합니다."""
    system = request.app.state.system
    import time
    import asyncio
    try:
        applied_ts = int(time.time() * 1000)
        
        # Atomic 트랜잭션 호출
        res = await system.repository.approve_proposal_atomic(proposal_id, applied_ts)
        
        strategy_id = res["strategy_id"]
        portfolio_id = res["portfolio_id"]
        new_version_id = res["new_version_id"]
        proposed_params = res["proposed_params"]
        snapshot_id = res["snapshot_id"]
        
        # 1. 비동기 백그라운드 태스크로 ROI, MDD 등 성과 지표 계산 및 적재 (Async Enrichment)
        asyncio.create_task(system.repository.enrich_snapshot_metrics_async(snapshot_id, portfolio_id))
        
        # 2. ZMQ 전송을 통한 실시간 전략 엔진 동적 갱신(Dynamic Apply) 적용
        if hasattr(request.app.state, 'strategy_control_publisher'):
            await request.app.state.strategy_control_publisher.publish("strategy_control", {
                "type": "apply_params",
                "strategy_id": strategy_id,
                "version_id": new_version_id,
                "params": proposed_params,
                "reason": "PROPOSAL_APPLY"
            })
            
        # 3. 감사용 시스템 이벤트 저장
        msg = f"사용자 승인으로 제안 #{proposal_id} 적용 완료: 전략 {strategy_id.upper()} 버전 {new_version_id} 갱신"
        asyncio.create_task(system.repository.insert_system_event(
            event_type="PROPOSAL_APPROVED",
            target=strategy_id,
            message=msg,
            context=f'{{"proposal_id": {proposal_id}, "version_id": {new_version_id}}}'
        ))
        
        return {"message": "Proposal approved and applied successfully", "version_id": new_version_id}
    except ValueError as val_err:
        raise HTTPException(status_code=400, detail=str(val_err))
    except Exception as e:
        logger.error(f"Failed to approve proposal: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/api/strategies/{strategy_id}/rollback/{version_id}")
async def rollback_strategy(strategy_id: str, version_id: int, request: Request):
    """지정 버전으로 원클릭 롤백을 실행합니다."""
    system = request.app.state.system
    import time
    import asyncio
    try:
        applied_ts = int(time.time() * 1000)
        
        # Atomic 트랜잭션 호출
        res = await system.repository.rollback_strategy_atomic(strategy_id, version_id, applied_ts)
        
        new_version_id = res["new_version_id"]
        target_params = res["target_params"]
        prop_id = res["associated_proposal_id"]
        snapshot_id = res["snapshot_id"]
        
        # 1. 비동기 백그라운드 태스크로 ROI, MDD 등 성과 지표 계산 및 적재
        # 롤백은 특정 포트폴리오 ID가 명시되지 않으므로 디폴트 시뮬레이션 포트폴리오를 탐색해 처리
        portfolio_id = "loop_test_port"
        for pid, p in system.portfolio_manager.portfolios.items():
            if strategy_id in str(p.strategy_info or "") or strategy_id in getattr(p, 'strategies', []):
                portfolio_id = pid
                break
        
        asyncio.create_task(system.repository.enrich_snapshot_metrics_async(snapshot_id, portfolio_id))
        
        # 2. ZMQ 전송을 통한 실시간 전략 엔진 동적 갱신(Dynamic Apply) 적용
        if hasattr(request.app.state, 'strategy_control_publisher'):
            await request.app.state.strategy_control_publisher.publish("strategy_control", {
                "type": "apply_params",
                "strategy_id": strategy_id,
                "version_id": new_version_id,
                "params": target_params,
                "reason": "ROLLBACK"
            })
            
        # 3. 수동 롤백 감지 시 AI 자동제안 전역 차단 장치 트리거
        if hasattr(request.app.state, 'scheduler'):
            await request.app.state.scheduler.handle_manual_rollback(strategy_id)

        # 4. 감사용 시스템 이벤트 저장
        msg = f"사용자 요청으로 전략 {strategy_id.upper()} 롤백 적용 완료: 버전 {new_version_id} 갱신 (V{version_id}로 복구)"
        asyncio.create_task(system.repository.insert_system_event(
            event_type="STRATEGY_ROLLBACK",
            target=strategy_id,
            message=msg,
            context=f'{{"to_version": {version_id}, "new_version": {new_version_id}}}'
        ))
        
        return {
            "message": "Strategy rolled back successfully",
            "from_version": new_version_id - 1,
            "to_version": version_id,
            "new_version_id": new_version_id
        }
    except ValueError as val_err:
        raise HTTPException(status_code=400, detail=str(val_err))
    except Exception as e:
        logger.error(f"Failed to rollback strategy: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/api/strategies/{strategy_id}")
async def get_strategy_detail(strategy_id: str, request: Request):
    """특정 전략의 상세 상태(활성 버전, 파라미터, 작동 여부 등)를 조회합니다."""
    system = request.app.state.system
    import time
    try:
        # 1. DB에서 현재 활성화된 전략 버전 정보 조회
        ver = await system.repository.get_strategy_version(strategy_id)
        
        # 2. 시스템 설정에서 해당 전략의 기동 상태 조회
        config = system.strategy_configs.get(strategy_id, {"enabled": False, "params": {}})
        
        # 3. 전략 레지스트리에서 메타데이터 획득
        meta = next((m for m in StrategyRegistry.get_all_metadata() if m['id'] == strategy_id), None)
        
        if not ver:
            # DB 버전 레코드가 아직 없는 경우 최초 가동 버전 모사 반환
            default_params = meta.get('params', {}) if meta else {}
            params_formatted = {k: v.get('default') for k, v in default_params.items()}
            ver = {
                "strategy_id": strategy_id,
                "current_version_id": 1,
                "current_params": params_formatted,
                "rollback_source_version": None,
                "applied_at": int(time.time() * 1000)
            }
            
        status = await _determine_strategy_status(system, strategy_id)
        eval_count = await _get_active_evaluations_count(system, strategy_id)
            
        return {
            "strategy_id": strategy_id,
            "name": meta.get('name', strategy_id) if meta else strategy_id,
            "enabled": config.get('enabled', False),
            "current_version_id": ver["current_version_id"],
            "current_params": ver["current_params"],
            "rollback_source_version": ver["rollback_source_version"],
            "applied_at": ver["applied_at"],
            "description": meta.get('description', '') if meta else '',
            "status": status,
            "active_eval_count": eval_count
        }
    except Exception as e:
        logger.error(f"Failed to fetch strategy detail: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/api/strategies/{strategy_id}/snapshots")
async def get_strategy_snapshots(strategy_id: str, request: Request, version_id: int = None, limit: int = 100):
    """특정 전략의 성과 스냅샷 목록을 조회합니다."""
    system = request.app.state.system
    try:
        snapshots = await system.repository.get_strategy_performance_snapshots(strategy_id, version_id, limit)
        return snapshots
    except Exception as e:
        logger.error(f"Failed to fetch strategy snapshots: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/api/strategies/{strategy_id}/history")
async def get_strategy_history(strategy_id: str, request: Request, limit: int = 50):
    """특정 전략의 파라미터 변경 이력 목록을 조회합니다."""
    system = request.app.state.system
    import json
    try:
        history = await system.repository.get_strategy_parameter_history(strategy_id, limit)
        for h in history:
            if isinstance(h.get("old_params"), str):
                try:
                    h["old_params"] = json.loads(h["old_params"])
                except:
                    pass
            if isinstance(h.get("new_params"), str):
                try:
                    h["new_params"] = json.loads(h["new_params"])
                except:
                    pass
        return history
    except Exception as e:
        logger.error(f"Failed to fetch strategy history: {e}")
        raise HTTPException(status_code=500, detail=str(e))

