import sys
import os
import asyncio

# 프로젝트 루트 경로 추가
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config.manager import ConfigManager
from src.database.repository import SqliteTradingRepository
from src.engine.daemon_supervisor import DaemonSupervisor
from src.engine.daemon_adapters import ZmqEventBus, SysProcessController, SysSignalHandler
from src.services.collector_service import CollectorService
from src.engine.utils.telemetry import setup_logging

async def main():
    setup_logging(log_file="ats.log")
    
    config_path = os.getenv("ATS_CONFIG", "config/settings.yaml")
    config_manager = ConfigManager(config_path)
    db_path = config_manager.get('system.db_path', 'data/backtest.db')

    # SQLite 스키마 초기화 확인
    from src.database.schema import init_db
    await init_db(db_path)

    # 리포지토리 생성
    repository = SqliteTradingRepository(db_path=db_path)

    # 설정 로드 이벤트 기록
    import subprocess
    import json
    git_commit = "unknown"
    try:
        git_commit = subprocess.check_output(["git", "rev-parse", "HEAD"]).strip().decode("utf-8")
    except Exception:
        pass
    payload = {
        "daemon": "collector_daemon",
        "pid": os.getpid(),
        "config_path": config_path,
        "config_sha256": getattr(config_manager, "config_sha256", "unknown"),
        "config_modified_at": getattr(config_manager, "last_mtime", 0.0),
        "git_commit": git_commit,
        "operation_mode": config_manager.get("system.operation_mode"),
        "girs_shadow_mode": config_manager.get("system.girs_shadow_mode"),
        "live_trading_enabled": config_manager.get("system.live_trading_enabled"),
        "auto_strategy_promotion_enabled": config_manager.get("system.auto_strategy_promotion_enabled")
    }
    await repository.insert_system_event(
        event_type="CONFIG_LOADED",
        target="collector_daemon",
        message=f"Loaded configuration from {config_path}",
        context=json.dumps(payload)
    )

    # 어댑터들 생성
    event_bus = ZmqEventBus()
    process_controller = SysProcessController()
    signal_handler = SysSignalHandler()

    # 서비스 생성
    service = CollectorService(
        config_manager=config_manager,
        event_bus=event_bus,
        repository=repository
    )

    # Supervisor 생성 및 기동
    supervisor = DaemonSupervisor(
        daemon_name="collector_daemon",
        service=service,
        event_bus=event_bus,
        process_controller=process_controller,
        signal_handler=signal_handler,
        repository=repository,
        config_manager=config_manager,
        control_topic="collector_control"
    )

    await supervisor.start()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
