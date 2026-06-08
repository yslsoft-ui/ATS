import asyncio
import time
from dataclasses import asdict
from typing import List, Dict, Any, Optional
from src.engine.daemon_supervisor import DaemonService, EventBus
from src.config.manager import ConfigManager
from src.database.writer import DatabaseWriter
from src.database.repository import SqliteTradingRepository
from src.engine.collector_base import CollectorRegistry
from src.engine.credentials import CredentialProvider
from src.engine.utils.telemetry import get_logger

# 수집기 로드 유도
import src.engine.collector_upbit
import src.engine.collector_kis
import src.engine.collector_bithumb

logger = get_logger("collector_service")

class TickPublishingQueue:
    """틱 수집 데이터를 DBWriter 큐에 넣고, 동시에 ZMQ로 발행하도록 대행하는 프록시 큐"""
    def __init__(self, event_bus: EventBus, db_writer: DatabaseWriter):
        self.event_bus = event_bus
        self.db_writer = db_writer

    async def put(self, item: dict):
        self.db_writer.enqueue_tick(item)
        item_copy = item.copy()
        item_copy['type'] = 'tick'
        await self.event_bus.publish("market_data", item_copy)

    def put_nowait(self, item: dict):
        self.db_writer.enqueue_tick(item)
        item_copy = item.copy()
        item_copy['type'] = 'tick'
        asyncio.create_task(self.event_bus.publish("market_data", item_copy))


class CandlePublishingQueue:
    """완성된 캔들 데이터를 DBWriter 큐에 넣고, 동시에 ZMQ로 발행하도록 대행하는 프록시 큐"""
    def __init__(self, event_bus: EventBus, db_writer: DatabaseWriter):
        self.event_bus = event_bus
        self.db_writer = db_writer

    async def put(self, item):
        self.db_writer.enqueue_candle(item)
        data_dict = asdict(item)
        data_dict['type'] = 'candle'
        await self.event_bus.publish("market_data", data_dict)

    def put_nowait(self, item):
        self.db_writer.enqueue_candle(item)
        data_dict = asdict(item)
        data_dict['type'] = 'candle'
        asyncio.create_task(self.event_bus.publish("market_data", data_dict))


class CollectorService(DaemonService):
    """수집기 실행, 설정 변경에 따른 동적 기동, 상태 전송 등의 도메인 서비스를 구현합니다."""
    def __init__(self, config_manager: ConfigManager, event_bus: EventBus, repository: SqliteTradingRepository):
        self.config_manager = config_manager
        self.event_bus = event_bus
        self.repository = repository
        
        self.db_path = self.config_manager.get('system.db_path', 'data/backtest.db')
        self.db_writer: Optional[DatabaseWriter] = None
        self.collectors: Dict[str, Any] = {}
        self.full_config: Dict[str, Any] = {}
        
        self.last_known_statuses: Dict[str, dict] = {}
        self._status_counter = 0

    async def start(self):
        # 1. DB Writer 기동
        self.db_writer = DatabaseWriter(db_path=self.db_path)
        await self.db_writer.start()

        # 2. 마스터 자산 동기화
        from src.database.sync_assets import sync_exchange_assets
        try:
            logger.info("[CollectorService] Starting boot-time asset synchronization...")
            await sync_exchange_assets(self.db_path)
        except Exception as e:
            logger.error(f"[CollectorService] Failed to run boot-time asset sync: {e}")

        # 3. StockMapper 캐시 기동
        from src.engine.utils.stock_mapper import stock_mapper
        await stock_mapper.load_from_db(self.db_path)

        # 4. 프록시 큐 생성
        tick_queue = TickPublishingQueue(self.event_bus, self.db_writer)
        candle_queue = CandlePublishingQueue(self.event_bus, self.db_writer)

        # 5. 설정 복사 및 CredentialProvider 초기화
        self.full_config = self.config_manager.config.copy()
        self.full_config['db_path'] = self.db_path
        
        CredentialProvider(self.full_config)

        # 수집기 데몬에서는 전략 기동을 차단하기 위해 복사본의 전략 설정을 비활성화함
        if 'strategies' in self.full_config:
            self.full_config['strategies'] = {
                s_id: {**s_conf, 'enabled': False} 
                for s_id, s_conf in self.full_config['strategies'].items()
            }

        # 6. 수집기 인스턴스 등록
        common_kwargs = {
            'processing_queue': asyncio.Queue(),
            'db_queue': tick_queue,
            'candle_queue': candle_queue,
            'repository': None,
            'portfolio_manager': None,
            'on_data_callback': None,
            'on_signal_callback': None,
            'on_status_callback': None
        }

        exchanges_config = self.full_config.get('exchanges', {})
        for exchange_id in exchanges_config.keys():
            collector = CollectorRegistry.create(exchange_id, **common_kwargs)
            if collector:
                self.collectors[exchange_id] = collector
                logger.info(f"[CollectorService] 수집기 인스턴스 등록 완료: {exchange_id}")

        # 7. 기동 가능 수집기 시작
        for exchange_id, collector in self.collectors.items():
            exch_config = self.config_manager.get(f"exchanges.{exchange_id}", {})
            if exch_config.get('enabled', False):
                await collector.start(self.full_config)
                logger.info(f"[CollectorService] 수집기 시작됨: {exchange_id}")
                await self.record_exchange_event('COLLECTOR_START', exchange_id, f"{exchange_id.upper()} 수집기 초기 가동 시작")

    async def stop(self):
        # 1. 수집기 중단
        for exchange_id, collector in self.collectors.items():
            if collector.is_running:
                try:
                    await collector.stop()
                    await self.record_exchange_event('COLLECTOR_STOP', exchange_id, f"{exchange_id.upper()} 수집기 가동 중단 (서비스 종료)")
                except Exception as e:
                    logger.error(f"[CollectorService] 수집기 {exchange_id} 중단 중 예외: {e}")

        # 2. DB Writer 중단
        if self.db_writer:
            await self.db_writer.stop()

    async def handle_config_change(self, new_config: dict):
        exchanges_conf = new_config.get('exchanges', {})
        for exch_id, exch_conf in exchanges_conf.items():
            is_enabled = exch_conf.get('enabled', False)
            collector = self.collectors.get(exch_id)
            if not collector:
                continue

            async def post_start(eid):
                await self.record_exchange_event('COLLECTOR_START', eid, f"{eid.upper()} 수집기 동적 가동 시작")

            async def post_stop(eid):
                await self.record_exchange_event('COLLECTOR_STOP', eid, f"{eid.upper()} 수집기 동적 가동 중단")

            # 활성화 상태 변화에 따른 동적 기동/중지 제어
            if is_enabled and not collector.is_running:
                logger.info(f"[CollectorService] 설정 변경 감지 - {exch_id} 수집기 시작 중...")
                run_config = new_config.copy()
                run_config['db_path'] = self.db_path
                if 'strategies' in run_config:
                    run_config['strategies'] = {
                        s_id: {**s_conf, 'enabled': False} 
                        for s_id, s_conf in run_config['strategies'].items()
                    }
                asyncio.create_task(collector.start(run_config))
                asyncio.create_task(asyncio.sleep(0.5)).add_done_callback(
                    lambda _, eid=exch_id: asyncio.create_task(post_start(eid))
                )
            elif not is_enabled and collector.is_running:
                logger.info(f"[CollectorService] 설정 변경 감지 - {exch_id} 수집기 중단 중...")
                asyncio.create_task(collector.stop())
                asyncio.create_task(asyncio.sleep(0.5)).add_done_callback(
                    lambda _, eid=exch_id: asyncio.create_task(post_stop(eid))
                )

    async def handle_control_message(self, topic: str, data: dict) -> bool:
        if data.get('type') == 'update_symbols':
            exchange = data.get('exchange')
            code = data.get('code')
            is_collected = data.get('is_collected')
            
            # StockMapper 캐시 실시간 리로드
            from src.engine.utils.stock_mapper import stock_mapper
            await stock_mapper.load_from_db(self.db_path)
            
            if exchange == "all":
                for col_id, col_obj in self.collectors.items():
                    if hasattr(col_obj, 'reload_symbols'):
                        await col_obj.reload_symbols(self.full_config)
            else:
                collector = self.collectors.get(exchange)
                if collector and hasattr(collector, 'update_subscription'):
                    await collector.update_subscription(code, is_collected)
            return True
        return False

    def get_status_payloads(self) -> List[tuple[str, dict]]:
        payloads = []
        
        # 1. 1초 주기 큐 상태 취합
        queue_status_payload = {
            "type": "queue_status",
            "processing": sum(c.processing_queue.qsize() for c in self.collectors.values() if hasattr(c, 'processing_queue')),
            "database": self.db_writer.db_queue.qsize() if self.db_writer and hasattr(self.db_writer, 'db_queue') else 0,
            "candle": self.db_writer.candle_queue.qsize() if self.db_writer and hasattr(self.db_writer, 'candle_queue') else 0,
            "total": sum(getattr(c, 'total_processed_count', 0) for c in self.collectors.values())
        }
        payloads.append(("signal_data", queue_status_payload))

        # 2. 5초 주기 거래소 상태 알림
        self._status_counter += 1
        if self._status_counter >= 5:
            self._status_counter = 0
            for exch_id, collector in self.collectors.items():
                err = getattr(collector, 'last_error', None)
                if not err and hasattr(collector, 'cred_provider'):
                    err = getattr(collector.cred_provider, 'last_error', None)

                current_running = collector.is_running
                current_status = getattr(collector, 'status', 'STOPPED')
                current_error = err

                prev = self.last_known_statuses.get(exch_id, {
                    'is_running': False,
                    'status': 'STOPPED',
                    'error': None
                })

                # 상태 변화 기록 (서킷브레이크 및 에러 기록 감지)
                asyncio.create_task(self._detect_and_record_changes(exch_id, prev, current_status, current_error, collector))

                self.last_known_statuses[exch_id] = {
                    'is_running': current_running,
                    'status': current_status,
                    'error': current_error
                }

                status_payload = {
                    "type": "collector_status",
                    "exchange": exch_id,
                    "is_running": collector.is_running,
                    "status": current_status,
                    "status_reason": getattr(collector, 'status_reason', None),
                    "error": err
                }
                payloads.append(("signal_data", status_payload))

        return payloads

    async def _detect_and_record_changes(self, exch_id: str, prev: dict, current_status: str, current_error: Optional[str], collector: Any):
        # 1) 서킷브레이크 진입/해제 감지
        if prev['status'] != current_status:
            if current_status == 'SUSPENDED':
                reason = getattr(collector, 'status_reason', '서킷브레이크 의심')
                await self.record_exchange_event('EXCHANGE_SUSPENDED', exch_id, f"{exch_id.upper()} 거래정지 감지: {reason}")
            elif prev['status'] == 'SUSPENDED' and current_status == 'RUNNING':
                await self.record_exchange_event('EXCHANGE_RESUMED', exch_id, f"{exch_id.upper()} 거래정지 해제 (RUNNING 복구)")

        # 2) 치명적 에러 발생 감지
        if current_error and prev['error'] != current_error:
            await self.record_exchange_event('EXCHANGE_ERROR', exch_id, f"{exch_id.upper()} 치명적 오류 발생: {current_error}")

    async def record_exchange_event(self, event_type: str, exch_id: str, message: str):
        ts = int(time.time() * 1000)
        try:
            if self.repository:
                await self.repository.insert_system_event(event_type, exch_id, message, ts)
        except Exception as e:
            logger.error(f"[CollectorService] EXCHANGE 이벤트 DB 적재 실패: {e}")
        try:
            await self.event_bus.publish("signal_data", {
                "type": "system_event",
                "event_type": event_type,
                "target": exch_id,
                "message": message,
                "timestamp": ts
            })
        except Exception as e:
            logger.error(f"[CollectorService] EXCHANGE 이벤트 버스 발행 실패: {e}")
