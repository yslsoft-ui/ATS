import sys
import os
import asyncio

# 프로젝트 루트 경로 추가
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config.manager import ConfigManager
from src.database.repository import SqliteTradingRepository
from src.engine.daemon_supervisor import DaemonSupervisor
from src.engine.daemon_adapters import ZmqEventBus, SysProcessController, SysSignalHandler
from src.services.strategy_service import StrategyService
from src.engine.utils.telemetry import setup_logging

async def main():
    setup_logging(log_file="ats.log")
    
    config_path = "config/settings.yaml"
    config_manager = ConfigManager(config_path)
    db_path = config_manager.get('system.db_path', 'data/backtest.db')

    # SQLite 스키마 초기화 확인
    from src.database.schema import init_db
    await init_db(db_path)

    # 리포지토리 생성
    repository = SqliteTradingRepository(db_path=db_path)

    # 어댑터들 생성
    event_bus = ZmqEventBus()
    process_controller = SysProcessController()
    signal_handler = SysSignalHandler()

    # 서비스 생성
    service = StrategyService(
        config_manager=config_manager,
        event_bus=event_bus
    )

    # Supervisor 생성 및 기동
    supervisor = DaemonSupervisor(
        daemon_name="strategy_daemon",
        service=service,
        event_bus=event_bus,
        process_controller=process_controller,
        signal_handler=signal_handler,
        repository=repository,
        config_manager=config_manager,
        control_topic="strategy_control"
    )

    await supervisor.start()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
