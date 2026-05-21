import sys
import os
import asyncio
import signal
from dataclasses import asdict

# 프로젝트 루트 경로 추가
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config.manager import ConfigManager
from src.database.writer import DatabaseWriter
from src.ipc.bus import EventBusPublisher
from src.engine.collector_base import CollectorRegistry
# 각 거래소 수집기가 Registry에 자동 등록되도록 import 수행
import src.engine.collector
import src.engine.collector_kis
import src.engine.collector_bithumb
from src.engine.utils.telemetry import get_logger

logger = get_logger("collector_daemon")

class TickPublishingQueue:
    """
    틱 수집 데이터를 DBWriter 큐에 넣고, 동시에 ZeroMQ market_data 채널로 발행합니다.
    """
    def __init__(self, publisher: EventBusPublisher, db_writer: DatabaseWriter):
        self.publisher = publisher
        self.db_writer = db_writer

    async def put(self, item: dict):
        # DBWriter 틱 큐에 적재
        self.db_writer.enqueue_tick(item)
        # ZMQ 퍼블리시
        item_copy = item.copy()
        item_copy['type'] = 'tick'
        await self.publisher.publish("market_data", item_copy)

    def put_nowait(self, item: dict):
        self.db_writer.enqueue_tick(item)
        item_copy = item.copy()
        item_copy['type'] = 'tick'
        asyncio.create_task(self.publisher.publish("market_data", item_copy))


class CandlePublishingQueue:
    """
    완성된 캔들 데이터를 DBWriter 큐에 넣고, 동시에 ZeroMQ market_data 채널로 발행합니다.
    """
    def __init__(self, publisher: EventBusPublisher, db_writer: DatabaseWriter):
        self.publisher = publisher
        self.db_writer = db_writer

    async def put(self, item):
        # DBWriter 캔들 큐에 적재
        self.db_writer.enqueue_candle(item)
        # ZMQ 퍼블리시 (Dataclass -> Dict)
        data_dict = asdict(item)
        data_dict['type'] = 'candle'
        await self.publisher.publish("market_data", data_dict)

    def put_nowait(self, item):
        self.db_writer.enqueue_candle(item)
        data_dict = asdict(item)
        data_dict['type'] = 'candle'
        asyncio.create_task(self.publisher.publish("market_data", data_dict))


async def main():
    logger.info("=========================================")
    logger.info("실시간 데이터 수집기 데몬(Collector Daemon) 기동 시작")
    logger.info("=========================================")

    # 1. 설정 로드
    config_path = "config/settings.yaml"
    config_manager = ConfigManager(config_path)
    
    # DB 경로와 설정 로드
    db_path = config_manager.get('system.db_path', 'data/backtest.db')
    
    # 2. SQLite 스키마 초기화 확인
    from src.database.schema import init_db
    await init_db(db_path)
    
    # 3. DB Writer 기동
    db_writer = DatabaseWriter(db_path=db_path)
    await db_writer.start()

    # 4. ZeroMQ Publisher 기동
    publisher = EventBusPublisher("market_data")

    # 5. 프록시 큐 정의 및 생성
    tick_queue = TickPublishingQueue(publisher, db_writer)
    candle_queue = CandlePublishingQueue(publisher, db_writer)

    # 6. 설정 내 전략들을 전부 강제 비활성화한 복사본 작성
    full_config = config_manager.config.copy()
    full_config['db_path'] = db_path
    
    if 'strategies' in full_config:
        full_config['strategies'] = {
            s_id: {**s_conf, 'enabled': False} 
            for s_id, s_conf in full_config['strategies'].items()
        }

    # 7. 수집기 초기화 및 기동
    collectors = []
    common_kwargs = {
        'processing_queue': asyncio.Queue(),  # 템플릿 대응용 더미 큐
        'db_queue': tick_queue,
        'candle_queue': candle_queue,
        'repository': None,
        'portfolio_manager': None,
        'on_data_callback': None,
        'on_signal_callback': None,
        'on_status_callback': None
    }

    exchanges_config = full_config.get('exchanges', {})
    for exchange_id, exch_config in exchanges_config.items():
        if not exch_config.get('enabled', True):
            continue
        collector = CollectorRegistry.create(exchange_id, **common_kwargs)
        if collector:
            collectors.append(collector)
            logger.info(f"[Collector Daemon] 수집기 등록 완료: {exchange_id}")

    # 수집기 병렬 기동
    for collector in collectors:
        await collector.start(full_config)
        logger.info(f"[Collector Daemon] 수집기 시작됨: {collector.exchange}")

    # 8. 종료 처리용 시그널 핸들링
    stop_event = asyncio.Event()

    def handle_shutdown():
        logger.info("[Collector Daemon] 종료 시그널 감지. 자원 정리 및 안전 종료 절차를 진행합니다...")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, handle_shutdown)
        except NotImplementedError:
            pass

    # 종료 이벤트 대기
    await stop_event.wait()

    # 9. 종료 절차 (Graceful Shutdown)
    logger.info("[Collector Daemon] 수집기 중단 중...")
    for collector in collectors:
        try:
            await collector.stop()
        except Exception as e:
            logger.error(f"[Collector Daemon] 수집기 {collector.exchange} 중단 중 예외 발생: {e}")

    logger.info("[Collector Daemon] DB Writer 중단 및 잔여 큐 플러시 중...")
    await db_writer.stop()

    logger.info("[Collector Daemon] ZeroMQ IPC 소켓 정리 중...")
    publisher.close()

    logger.info("=========================================")
    logger.info("[Collector Daemon] 실시간 데이터 수집기 데몬 안전 종료 완료")
    logger.info("=========================================")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
