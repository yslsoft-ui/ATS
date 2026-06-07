import sys
import os
import asyncio
import signal
from dataclasses import asdict

# 프로젝트 루트 경로 추가
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config.manager import ConfigManager
from src.database.writer import DatabaseWriter
from src.ipc.bus import EventBusPublisher, EventBusSubscriber
from src.engine.collector_base import CollectorRegistry
from src.engine.credentials import CredentialProvider
# 각 거래소 수집기가 Registry에 자동 등록되도록 import 수행
import src.engine.collector_upbit
import src.engine.collector_kis
import src.engine.collector_bithumb
from src.engine.utils.telemetry import get_logger, setup_logging

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
    setup_logging(log_file="ats.log")
    logger.info("=========================================")
    logger.info("실시간 데이터 수집기 데몬(Collector Daemon) 기동 시작")
    logger.info("=========================================")

    restart_requested = False


    # 1. 설정 로드
    config_path = "config/settings.yaml"
    config_manager = ConfigManager(config_path)
    
    # 마켓 운영 시간 사전 파싱 유효성 검사 (Fail-Fast)
    from src.engine.utils.market_hours import MarketHours
    exchanges_config = config_manager.get('exchanges', {})
    for exch_id, exch_conf in exchanges_config.items():
        if exch_conf.get('enabled', False) and 'market_hours' in exch_conf:
            hours = exch_conf['market_hours']
            start_time = hours.get('start_time')
            end_time = hours.get('end_time')
            if start_time is not None:
                MarketHours._parse_time(start_time, f"exchanges.{exch_id}.market_hours.start_time")
            if end_time is not None:
                MarketHours._parse_time(end_time, f"exchanges.{exch_id}.market_hours.end_time")

    # DB 경로와 설정 로드
    db_path = config_manager.get('system.db_path', 'data/backtest.db')
    
    # 2. SQLite 스키마 초기화 확인
    from src.database.schema import init_db
    await init_db(db_path)

    # [추가] 기동 전 거래소 마스터 자산 동기화 1회 수행
    from src.database.sync_assets import sync_exchange_assets
    try:
        logger.info("[Collector Daemon] Starting boot-time asset synchronization...")
        await sync_exchange_assets(db_path)
        logger.info("[Collector Daemon] Boot-time asset synchronization completed successfully.")
    except Exception as e:
        logger.error(f"[Collector Daemon] Failed to run boot-time asset sync: {e}")

    # 2.5 StockMapper 메모리 캐시 적재
    from src.engine.utils.stock_mapper import stock_mapper
    await stock_mapper.load_from_db(db_path)
    
    # 3. DB Writer 기동
    db_writer = DatabaseWriter(db_path=db_path)
    await db_writer.start()

    # 4. ZeroMQ Publisher 기동 (market_data: 틱/캔들용, signal_data: 상태보고용)
    publisher = EventBusPublisher("market_data")
    signal_publisher = EventBusPublisher("signal_data")

    # 5. 프록시 큐 정의 및 생성
    tick_queue = TickPublishingQueue(publisher, db_writer)
    candle_queue = CandlePublishingQueue(publisher, db_writer)

    # 6. 설정 내 전략들을 전부 강제 비활성화한 복사본 작성
    full_config = config_manager.config.copy()
    full_config['db_path'] = db_path
    
    # 6.5. 싱글톤 CredentialProvider에 설정 정보를 주입하여 초기화
    CredentialProvider(full_config)
    
    if 'strategies' in full_config:
        full_config['strategies'] = {
            s_id: {**s_conf, 'enabled': False} 
            for s_id, s_conf in full_config['strategies'].items()
        }

    # 7. 수집기 초기화 (딕셔너리로 관리하여 동적 기동/중지 지원)
    collectors = {}
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
    for exchange_id in exchanges_config.keys():
        collector = CollectorRegistry.create(exchange_id, **common_kwargs)
        if collector:
            collectors[exchange_id] = collector
            logger.info(f"[Collector Daemon] 수집기 인스턴스 등록 완료: {exchange_id}")

    # 8. 종료 처리용 시그널 핸들링
    stop_event = asyncio.Event()

    # 실시간 상태 전송 헬퍼 함수
    async def publish_status(exch_id):
        collector = collectors.get(exch_id)
        if not collector:
            return
        err = getattr(collector, 'last_error', None)
        if not err and hasattr(collector, 'cred_provider'):
            err = getattr(collector.cred_provider, 'last_error', None)
        status_payload = {
            "type": "collector_status",
            "exchange": exch_id,
            "is_running": collector.is_running,
            "error": err
        }
        await signal_publisher.publish("signal_data", status_payload)

    # ZMQ 제어 소켓 리스너 루프
    async def control_listener_loop():
        control_sub = EventBusSubscriber("collector_control")
        logger.info("[Collector Daemon] ZMQ control subscriber connected.")
        nonlocal restart_requested
        while not stop_event.is_set():
            try:
                topic, data = await control_sub.receive()
                if not topic or not data:
                    continue

                logger.info(f"[Collector Daemon] IPC 제어 신호 수신: topic={topic}, data={data}")
                
                if data.get('type') == 'update_symbols':
                    exchange = data.get('exchange')
                    code = data.get('code')
                    is_collected = data.get('is_collected')
                    
                    # 1. DB의 변경 사항을 반영하여 StockMapper 캐시를 실시간 리로드
                    from src.engine.utils.stock_mapper import stock_mapper
                    await stock_mapper.load_from_db(db_path)
                    
                    # 2. 수집기에 통지
                    if exchange == "all":
                        # 전체 동기화 신호 수신 시, 모든 구동 중인 수집기를 DB 기준으로 실시간 리로드
                        for col_id, col_obj in collectors.items():
                            if hasattr(col_obj, 'reload_symbols'):
                                await col_obj.reload_symbols(full_config)
                    else:
                        collector = collectors.get(exchange)
                        if collector and hasattr(collector, 'update_subscription'):
                            await collector.update_subscription(code, is_collected)
                elif data.get('type') == 'restart_daemon':
                    logger.info("[Collector Daemon] 자가 재기동(Self-Restart) 요청 수신. 안전 종료를 시작합니다.")
                    restart_requested = True
                    stop_event.set()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[Collector Daemon] control_listener_loop error: {e}")
                await asyncio.sleep(0.5)
        control_sub.close()
        logger.info("[Collector Daemon] ZMQ control subscriber closed.")

    # 5초 간격 실시간 상태 주기적 퍼블리시 루프
    async def status_broadcast_loop():
        while not stop_event.is_set():
            try:
                for exch_id in collectors.keys():
                    await publish_status(exch_id)
            except Exception as e:
                logger.error(f"[Collector Daemon] 상태 브로드캐스트 에러: {e}")
            await asyncio.sleep(5.0)

    # 1초 간격 큐 상태 주기적 퍼블리시 루프
    async def queue_broadcast_loop():
        while not stop_event.is_set():
            try:
                queue_status_payload = {
                    "type": "queue_status",
                    "processing": sum(c.processing_queue.qsize() for c in collectors.values() if hasattr(c, 'processing_queue')),
                    "database": db_writer.db_queue.qsize() if hasattr(db_writer, 'db_queue') else 0,
                    "candle": db_writer.candle_queue.qsize() if hasattr(db_writer, 'candle_queue') else 0,
                    "total": sum(getattr(c, 'total_processed_count', 0) for c in collectors.values())
                }
                await signal_publisher.publish("signal_data", queue_status_payload)
            except Exception as e:
                logger.error(f"[Collector Daemon] 큐 상태 브로드캐스트 에러: {e}")
            await asyncio.sleep(1.0)

    # 초기 기동 대상 수집기 시작
    for exchange_id, collector in collectors.items():
        exch_config = config_manager.get(f"exchanges.{exchange_id}", {})
        if exch_config.get('enabled', False):
            await collector.start(full_config)
            logger.info(f"[Collector Daemon] 수집기 시작됨: {exchange_id}")
            await publish_status(exchange_id)

    # 9. 설정 변경 실시간 감시 및 동적 수집기 기동 제어
    async def on_config_changed(new_config: dict):
        exchanges_conf = new_config.get('exchanges', {})
        for exch_id, exch_conf in exchanges_conf.items():
            is_enabled = exch_conf.get('enabled', False)
            collector = collectors.get(exch_id)
            if not collector:
                continue

            # 활성화 상태가 변했을 때 동적으로 기동/중지 제어
            if is_enabled and not collector.is_running:
                logger.info(f"[Collector Daemon] 설정 변경 감지 - {exch_id} 수집기 시작 중...")
                run_config = new_config.copy()
                run_config['db_path'] = db_path
                if 'strategies' in run_config:
                    run_config['strategies'] = {
                        s_id: {**s_conf, 'enabled': False} 
                        for s_id, s_conf in run_config['strategies'].items()
                    }
                asyncio.create_task(collector.start(run_config))
                # 기동 비동기 완료 후 상태 즉시 방출
                asyncio.create_task(asyncio.sleep(0.5)).add_done_callback(lambda _: asyncio.create_task(publish_status(exch_id)))
            elif not is_enabled and collector.is_running:
                logger.info(f"[Collector Daemon] 설정 변경 감지 - {exch_id} 수집기 중단 중...")
                asyncio.create_task(collector.stop())
                # 중단 비동기 완료 후 상태 즉시 방출
                asyncio.create_task(asyncio.sleep(0.5)).add_done_callback(lambda _: asyncio.create_task(publish_status(exch_id)))

    config_manager.subscribe(on_config_changed)
    await config_manager.start_watching()

    # 루프 및 리스너 태스크 기동
    broadcast_task = asyncio.create_task(status_broadcast_loop())
    queue_task = asyncio.create_task(queue_broadcast_loop())
    control_task = asyncio.create_task(control_listener_loop())

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

    # 10. 종료 절차 (Graceful Shutdown)
    await config_manager.stop_watching()
    
    logger.info("[Collector Daemon] 상태 퍼블리시 루프 및 제어 리스너 취소 중...")
    broadcast_task.cancel()
    queue_task.cancel()
    control_task.cancel()
    try:
        await asyncio.gather(broadcast_task, queue_task, control_task, return_exceptions=True)
    except asyncio.CancelledError:
        pass

    logger.info("[Collector Daemon] 수집기 중단 중...")
    for exchange_id, collector in collectors.items():
        if collector.is_running:
            try:
                await collector.stop()
                await publish_status(exchange_id)
            except Exception as e:
                logger.error(f"[Collector Daemon] 수집기 {exchange_id} 중단 중 예외 발생: {e}")

    logger.info("[Collector Daemon] DB Writer 중단 및 잔여 큐 플러시 중...")
    await db_writer.stop()

    logger.info("[Collector Daemon] ZeroMQ IPC 소켓 정리 중...")
    publisher.close()
    
    # 최종 중단된 상태 전송 시도 후 닫기
    for exchange_id in collectors.keys():
        try:
            await publish_status(exchange_id)
        except Exception:
            pass
    signal_publisher.close()

    logger.info("=========================================")
    logger.info("[Collector Daemon] 실시간 데이터 수집기 데몬 안전 종료 완료")
    logger.info("=========================================")

    if restart_requested:
        logger.info("[Collector Daemon] Self-restarting process via execv...")
        import sys
        import os
        os.execv(sys.executable, [sys.executable] + sys.argv)



if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass

