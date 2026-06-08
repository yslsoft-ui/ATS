from fastapi import APIRouter, Request, HTTPException
from typing import Dict
from src.engine.strategy import StrategyRegistry
from src.engine.utils.telemetry import get_logger
from src.engine.command import UserCommand

logger = get_logger(__name__)
router = APIRouter()

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
            
        results.append({
            "id": s_id,
            "name": meta['name'],
            "type": meta['type'],
            "description": meta['description'],
            "enabled": config.get('enabled', False),
            "params": params_with_values
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
