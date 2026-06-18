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
        if getattr(item, 'is_backfill', False):
            data_dict['is_backfill'] = True
        await self.event_bus.publish("market_data", data_dict)

    def put_nowait(self, item):
        self.db_writer.enqueue_candle(item)
        data_dict = asdict(item)
        data_dict['type'] = 'candle'
        if getattr(item, 'is_backfill', False):
            data_dict['is_backfill'] = True
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
        self.processors: Dict[str, Any] = {}  # [NEW] 거래소별 프로세서 관리 사전
        self.full_config: Dict[str, Any] = {}
        
        self.last_known_statuses: Dict[str, dict] = {}
        self._status_counter = 0
        self._tasks: List[asyncio.Task] = []

        # [NEW] 수집기 종목 버전 및 메타데이터 동적 관리를 위한 변수 초기화
        self.symbols_version: Dict[str, int] = {}
        import os
        self.source_pid = os.getpid()
        self.daemon_started_at = int(time.time() * 1000)

    async def start(self):
        # 1. DB Writer 기동
        self.db_writer = DatabaseWriter(db_path=self.db_path)
        await self.db_writer.start()

        # 2. 마스터 자산 동기화 (기동 시 스킵, 웹 UI 수동 동기화만 지원)
        logger.info("[CollectorService] Skipping boot-time asset synchronization. Manual synchronization is required via the Web UI.")

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

        # 6. 수집기 및 프로세서 인스턴스 등록
        exchanges_config = self.full_config.get('exchanges', {})
        for exchange_id in exchanges_config.keys():
            # Drop Oldest 정책이 안전하게 구동되도록 격리된 maxsize 큐 생성
            proc_queue = asyncio.Queue(maxsize=5000)

            # 가공 프로세서 생성
            from src.engine.market_data_processor import MarketDataProcessor
            processor = MarketDataProcessor(
                exchange_id=exchange_id,
                processing_queue=proc_queue,
                db_queue=tick_queue,
                candle_queue=candle_queue
            )
            self.processors[exchange_id] = processor

            # 수집기 생성
            common_kwargs = {
                'processing_queue': proc_queue,
                'db_queue': tick_queue,
                'candle_queue': candle_queue,
                'repository': self.repository,
                'portfolio_manager': None,
                'on_data_callback': None,
                'on_signal_callback': lambda sig: asyncio.create_task(self.event_bus.publish("collector_signal", sig)),
                'on_status_callback': None
            }
            collector = CollectorRegistry.create(exchange_id, **common_kwargs)
            if collector:
                self.collectors[exchange_id] = collector
                logger.info(f"[CollectorService] 수집기 인스턴스 등록 완료: {exchange_id}")

        # 7. 기동 가능 수집기 및 프로세서 시작
        for exchange_id, collector in self.collectors.items():
            exch_config = self.config_manager.get(f"exchanges.{exchange_id}", {})
            if exch_config.get('enabled', False):
                # 수집 종목 사전 획득 및 프로세서/수집기 공유
                try:
                    symbols = await collector._fetch_symbols(self.full_config)
                    collector.available_symbols = symbols
                    processor = self.processors.get(exchange_id)
                    if processor:
                        processor.available_symbols = symbols
                        await processor.start(self.full_config)
                except Exception as e:
                    logger.error(f"[CollectorService] {exchange_id} 종목 조회 및 프로세서 시작 중 오류: {e}")

                await collector.start(self.full_config)
                logger.info(f"[CollectorService] 수집기 시작됨: {exchange_id}")
                await self.record_exchange_event('COLLECTOR_START', exchange_id, f"{exchange_id.upper()} 수집기 초기 가동 시작")

        # [NEW] symbols_version 명시적 초기화 (활성 수집기 목록 기반)
        for exch_id in self.collectors.keys():
            self.symbols_version[exch_id] = 1

        # [NEW] 기동 직후 최초 1회 즉각 동기화 신호 전송
        for exch_id in self.collectors.keys():
            await self.publish_symbols_sync(exch_id)

        # [NEW] 30초 주기 저빈도 동기화 루프 기동
        self._tasks.append(asyncio.create_task(self._periodic_symbols_sync_loop()))

        # [V2] 시장 상태 요약 수집 루프 백그라운드 태스크 기동
        self._tasks.append(asyncio.create_task(self._periodic_market_regime_summarizer_loop()))

    async def stop(self):
        # 0. 백그라운드 태스크 정리 (periodic_symbols_sync_loop 포함 안전하게 cancel & gather)
        for task in self._tasks:
            if not task.done():
                task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
            self._tasks.clear()

        # 1. 수집기 중단
        for exchange_id, collector in self.collectors.items():
            if collector.is_running:
                try:
                    await collector.stop()
                    await self.record_exchange_event('COLLECTOR_STOP', exchange_id, f"{exchange_id.upper()} 수집기 가동 중단 (서비스 종료)")
                except Exception as e:
                    logger.error(f"[CollectorService] 수집기 {exchange_id} 중단 중 예외: {e}")

        # 2. 프로세서 중단
        for exchange_id, processor in self.processors.items():
            if processor.is_running:
                try:
                    await processor.stop()
                except Exception as e:
                    logger.error(f"[CollectorService] 프로세서 {exchange_id} 중단 중 예외: {e}")

        # 3. DB Writer 중단
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
                logger.info(f"[CollectorService] 설정 변경 감지 - {exch_id} 수집기 및 프로세서 시작 중...")
                run_config = new_config.copy()
                run_config['db_path'] = self.db_path
                if 'strategies' in run_config:
                    run_config['strategies'] = {
                        s_id: {**s_conf, 'enabled': False} 
                        for s_id, s_conf in run_config['strategies'].items()
                    }
                
                async def start_pair(eid, col, rconf):
                    try:
                        syms = await col._fetch_symbols(rconf)
                        col.available_symbols = syms
                        proc = self.processors.get(eid)
                        if proc:
                            proc.available_symbols = syms
                            await proc.start(rconf)
                    except Exception as e:
                        logger.error(f"[CollectorService] {eid} 동적 기동 중 오류: {e}")
                    await col.start(rconf)

                asyncio.create_task(start_pair(exch_id, collector, run_config))
                asyncio.create_task(asyncio.sleep(0.5)).add_done_callback(
                    lambda _, eid=exch_id: asyncio.create_task(post_start(eid))
                )
            elif not is_enabled and collector.is_running:
                logger.info(f"[CollectorService] 설정 변경 감지 - {exch_id} 수집기 및 프로세서 중단 중...")
                
                async def stop_pair(eid, col):
                    await col.stop()
                    proc = self.processors.get(eid)
                    if proc:
                        await proc.stop()

                asyncio.create_task(stop_pair(exch_id, collector))
                asyncio.create_task(asyncio.sleep(0.5)).add_done_callback(
                    lambda _, eid=exch_id: asyncio.create_task(post_stop(eid))
                )

    async def handle_control_message(self, topic: str, data: dict) -> bool:
        # [NEW] 공통 무효 거래소 검증 및 예외 없는 무시/SYSTEM_WARNING 감사 로그 적재
        exchange = data.get('exchange')
        if exchange is not None:
            if exchange != "all" and exchange not in self.collectors:
                logger.warning(f"[CollectorService] Received invalid exchange in control message: {exchange}")
                await self.record_exchange_event(
                    event_type="SYSTEM_WARNING",
                    exch_id="system",
                    message=f"Received control command with invalid exchange: {exchange}"
                )
                # command_id가 있다면 FAILED ACK 전송
                command_id = data.get('command_id')
                if command_id:
                    result_payload = {
                        "type": "collector_command_result",
                        "command_id": command_id,
                        "exchange": exchange,
                        "status": "FAILED",
                        "error": f"Invalid exchange: {exchange}",
                        "timestamp": int(time.time() * 1000)
                    }
                    await self.event_bus.publish("collector_signal", result_payload)
                return False

        if data.get('type') == 'update_symbols':
            code = data.get('code')
            is_collected = data.get('is_collected')
            
            # StockMapper 캐시 실시간 리로드
            from src.engine.utils.stock_mapper import stock_mapper
            await stock_mapper.load_from_db(self.db_path)
            
            if exchange == "all":
                for col_id, col_obj in self.collectors.items():
                    if hasattr(col_obj, 'reload_symbols'):
                        await col_obj.reload_symbols(self.full_config)
                        # 프로세서 종목 리로드 호출
                        processor = self.processors.get(col_id)
                        if processor:
                            await processor.reload_symbols(self.full_config, col_obj.available_symbols)
                        # 버전 증가 및 1회성 동기화 전송
                        self.symbols_version[col_id] = self.symbols_version.get(col_id, 1) + 1
                        await self.publish_symbols_sync(col_id)
            else:
                collector = self.collectors.get(exchange)
                if collector and hasattr(collector, 'update_subscription'):
                    await collector.update_subscription(code, is_collected)
                    # 버전 증가 및 1회성 동기화 전송
                    self.symbols_version[exchange] = self.symbols_version.setdefault(exchange, 1) + 1
                    await self.publish_symbols_sync(exchange)
            return True

        # [NEW] 저빈도 동기화 보강용 강제 재동기화 신호 처리
        elif data.get('type') == 'request_symbols_sync':
            if exchange == "all":
                for col_id in self.collectors.keys():
                    await self.publish_symbols_sync(col_id)
            else:
                await self.publish_symbols_sync(exchange)
            return True

        # [NEW] command_id 기반 비동기 시작/정지 제어 명령 완결 처리
        elif data.get('type') in ['collector_start', 'collector_stop']:
            cmd_type = data.get('type')
            command_id = data.get('command_id')
            target_running = (cmd_type == 'collector_start')
            
            exchanges_to_check = self.collectors.keys() if exchange == "all" else [exchange]
            
            success = True
            error_msg = None
            
            # 설정 감지 및 상태 기동/정지 대기 (최대 1.5초 대기)
            for _ in range(15):
                await asyncio.sleep(0.1)
                all_matched = True
                for exch in exchanges_to_check:
                    col = self.collectors.get(exch)
                    if col and col.is_running != target_running:
                        all_matched = False
                        break
                if all_matched:
                    break
            else:
                success = False
                error_msg = "Timeout waiting for collector state change"
                
            # 결과 퍼블리싱
            if command_id:
                result_payload = {
                    "type": "collector_command_result",
                    "command_id": command_id,
                    "exchange": exchange,
                    "status": "SUCCESS" if success else "FAILED",
                    "error": error_msg,
                    "timestamp": int(time.time() * 1000)
                }
                await self.event_bus.publish("collector_signal", result_payload)
            return True

        # [NEW] restart_daemon 즉각 ACK 응답 전송 및 Supervisor로 위임
        elif data.get('type') == 'restart_daemon':
            command_id = data.get('command_id')
            if command_id:
                result_payload = {
                    "type": "collector_command_result",
                    "command_id": command_id,
                    "exchange": "all",
                    "status": "SUCCESS",
                    "error": None,
                    "timestamp": int(time.time() * 1000)
                }
                await self.event_bus.publish("collector_signal", result_payload)
            return False # Supervisor가 실제 자가 재기동 처리를 하도록 False 리턴

        return False

    def get_status_payloads(self) -> List[tuple[str, dict]]:
        payloads = []
        
        # 1. 큐 메트릭 동적 산출 (max_size 참조 및 usage_pct, level 계산)
        # 1.1. Processing Queue
        proc_qsize = sum(c.processing_queue.qsize() for c in self.collectors.values() if hasattr(c, 'processing_queue'))
        proc_max_size = sum(c.processing_queue.maxsize if (hasattr(c, 'processing_queue') and getattr(c.processing_queue, 'maxsize', 0) > 0) else 5000 for c in self.collectors.values())
        proc_usage = round((proc_qsize / proc_max_size * 100), 2) if proc_max_size > 0 else 0.0
        proc_level = "CRITICAL" if proc_usage >= 85 else ("WARNING" if proc_usage >= 50 else "NORMAL")

        # 1.2. Database Queue
        db_qsize = self.db_writer.db_queue.qsize() if self.db_writer and hasattr(self.db_writer, 'db_queue') else 0
        db_max_size = self.db_writer.db_queue.maxsize if (self.db_writer and hasattr(self.db_writer, 'db_queue') and getattr(self.db_writer.db_queue, 'maxsize', 0) > 0) else 1000
        db_usage = round((db_qsize / db_max_size * 100), 2) if db_max_size > 0 else 0.0
        db_level = "CRITICAL" if db_usage >= 85 else ("WARNING" if db_usage >= 50 else "NORMAL")

        # 1.3. Candle Queue
        cnd_qsize = self.db_writer.candle_queue.qsize() if self.db_writer and hasattr(self.db_writer, 'candle_queue') else 0
        cnd_max_size = self.db_writer.candle_queue.maxsize if (self.db_writer and hasattr(self.db_writer, 'candle_queue') and getattr(self.db_writer.candle_queue, 'maxsize', 0) > 0) else 1000
        cnd_usage = round((cnd_qsize / cnd_max_size * 100), 2) if cnd_max_size > 0 else 0.0
        cnd_level = "CRITICAL" if cnd_usage >= 85 else ("WARNING" if cnd_usage >= 50 else "NORMAL")

        total_processed = sum(getattr(c, 'total_processed_count', 0) for c in self.collectors.values())
        total_dropped = sum(getattr(c, 'total_dropped_count', 0) for c in self.collectors.values())

        queue_status_payload = {
            "type": "queue_status",
            "processing": proc_qsize,
            "database": db_qsize,
            "candle": cnd_qsize,
            "total": total_processed
        }
        payloads.append(("collector_signal", queue_status_payload))

        # [NEW] 5초 주기 수집기 데몬 상세 정보 취합 및 브로드캐스트용 페이로드 생성
        self._detail_status_counter = getattr(self, '_detail_status_counter', 0) + 1
        if self._detail_status_counter >= 5:
            self._detail_status_counter = 0
            
            from src.engine.utils.stock_mapper import stock_mapper
            
            exchanges_data = {}
            for exch_id, collector in self.collectors.items():
                err = getattr(collector, 'last_error', None)
                if not err and hasattr(collector, 'cred_provider'):
                    err = getattr(collector.cred_provider, 'last_error', None)
                
                # 거래소별 설정 및 운영 시간 가공
                try:
                    metadata = collector.get_connection_metadata(self.full_config)
                except Exception:
                    logger.exception(f"[CollectorService] Failed to get connection metadata for {exch_id}")
                    metadata = {
                        "operating_hours": "오류 (조회 실패)",
                        "websocket_url": "",
                        "api_url": ""
                    }

                exchanges_data[exch_id] = {
                    "is_running": collector.is_running,
                    "status": getattr(collector, 'status', 'STOPPED'),
                    "symbols_count": len(getattr(collector, 'available_symbols', [])),
                    "processed_count": getattr(collector, 'total_processed_count', 0),
                    "dropped_count": getattr(collector, 'total_dropped_count', 0),
                    "last_tick": getattr(collector, 'last_tick', None),
                    "last_raw": getattr(collector, 'last_raw', None),
                    "last_error": err,
                    "operating_hours": metadata.get("operating_hours", ""),
                    "websocket_url": metadata.get("websocket_url", ""),
                    "api_url": metadata.get("api_url", "")
                }

            detail_payload = {
                "type": "collector_daemon_detail",
                "queues": {
                    "processing": {"qsize": proc_qsize, "max_size": proc_max_size, "usage_pct": proc_usage, "level": proc_level},
                    "database": {"qsize": db_qsize, "max_size": db_max_size, "usage_pct": db_usage, "level": db_level},
                    "candle": {"qsize": cnd_qsize, "max_size": cnd_max_size, "usage_pct": cnd_usage, "level": cnd_level},
                    "total_processed": total_processed,
                    "total_dropped": total_dropped
                },
                "exchanges": exchanges_data,
                "memory": {
                    "rss_mb": self._get_rss_memory(),
                    "stock_mapper_cache_count": len(getattr(stock_mapper, '_mapping', {}))
                },
                "symbols_version": dict(self.symbols_version),  # [NEW] defaultdict 방지 일반 dict 변환 리턴
                "daemon_started_at": self.daemon_started_at,
                "source_pid": self.source_pid
            }
            payloads.append(("collector_signal", detail_payload))

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
                payloads.append(("collector_signal", status_payload))

        return payloads

    # --- [NEW] 저빈도/구독 변경 강제 동기화 헬퍼 및 비동기 루프 메서드 ---

    async def publish_symbols_sync(self, exchange: str):
        """특정 거래소의 실제 구독 종목 목록을 동기화하기 위한 ZMQ 이벤트를 퍼블리시합니다."""
        collector = self.collectors.get(exchange)
        if not collector:
            return
        
        # 런타임에 동적으로 검증된 exchange 등록 처리 (setdefault)
        version = self.symbols_version.setdefault(exchange, 1)
        symbols = getattr(collector, 'available_symbols', [])
        
        sync_payload = {
            "type": "collector_symbols_sync",
            "exchange": exchange,
            "symbols": list(symbols),
            "symbols_version": version,
            "source_pid": self.source_pid,
            "daemon_started_at": self.daemon_started_at
        }
        try:
            await self.event_bus.publish("collector_signal", sync_payload)
            logger.debug(f"[CollectorService] {exchange} symbols sync published. (version: {version}, count: {len(symbols)})")
        except Exception as e:
            logger.error(f"[CollectorService] Failed to publish symbols sync for {exchange}: {e}")

    async def _periodic_symbols_sync_loop(self):
        """30초 주기로 모든 거래소의 종목 동기화 이벤트를 ZMQ로 저빈도 재전송합니다."""
        try:
            # 기동 초기 지연 (기동 시 즉각 발행은 start 메서드 하단에서 1회 수행됨)
            await asyncio.sleep(30)
            while True:
                for exch_id in self.collectors.keys():
                    await self.publish_symbols_sync(exch_id)
                await asyncio.sleep(30)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"[CollectorService] periodic symbols sync loop error: {e}")

    def _get_rss_memory(self) -> float:
        """/proc/self/status 파일에서 데몬 프로세스의 현재 RSS 메모리(MB)를 안전하게 파싱합니다."""
        try:
            with open("/proc/self/status", "r") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        parts = line.split()
                        if len(parts) >= 2:
                            kb = float(parts[1])
                            return round(kb / 1024.0, 2)
        except Exception:
            pass
        return 0.0

    async def _detect_and_record_changes(self, exch_id: str, prev: dict, current_status: str, current_error: Optional[str], collector: Any):
        # 1) 서킷브레이크 진입/해제 감지
        if prev['status'] != current_status:
            if current_status == 'SUSPENDED':
                reason = getattr(collector, 'status_reason', '서킷브레이크 의심')
                await self.record_exchange_event('EXCHANGE_SUSPENDED', exch_id, f"{exch_id.upper()} 거래정지 감지: {reason}")
            elif prev['status'] == 'SUSPENDED' and current_status == 'RUNNING':
                last_symbol = getattr(collector, 'last_event_symbol', None)
                if last_symbol:
                    from src.engine.utils.stock_mapper import stock_mapper
                    korean_name = stock_mapper.get_name(exch_id, last_symbol)
                    msg = f"{exch_id.upper()} 거래정지 해제 (RUNNING 복구): [{last_symbol}] {korean_name}"
                else:
                    msg = f"{exch_id.upper()} 거래정지 해제 (RUNNING 복구)"
                await self.record_exchange_event('EXCHANGE_RESUMED', exch_id, msg)

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
            await self.event_bus.publish("collector_signal", {
                "type": "system_event",
                "event_type": event_type,
                "target": exch_id,
                "message": message,
                "timestamp": ts
            })
        except Exception as e:
            logger.error(f"[CollectorService] EXCHANGE 이벤트 버스 발행 실패: {e}")

    async def _periodic_market_regime_summarizer_loop(self):
        """1분마다 활성 종목들의 시장 상태 피처를 계산하여 market_regime_summaries에 적재합니다."""
        logger.info("[CollectorService] 시장 Regime 요약 수집 루프 기동")
        from src.database.connection import get_db_conn
        try:
            # 매 분 5초 시점에 기동되도록 정렬 (캔들이 확정적으로 DB에 저장된 직후)
            await asyncio.sleep(60 - (time.time() % 60) + 5)
            while True:
                start_time = time.time()
                current_bucket = int((time.time() // 60) * 60 * 1000) # ms 버킷
                
                try:
                    # 1. 활성 종목 리스트 획득
                    active_symbols = []
                    async with get_db_conn(self.db_path) as db:
                        async with db.execute(
                            "SELECT exchange_id, symbol FROM exchange_assets WHERE is_active = 1"
                        ) as cursor:
                            rows = await cursor.fetchall()
                            active_symbols = [(r["exchange_id"], r["symbol"]) for r in rows]
                            
                    # 2. 종목별 피처 계산 및 적재
                    for ex, sym in active_symbols:
                        # 25개 캔들을 가져와 RSI 14 및 변동성 20 계산에 활용
                        c_rows = []
                        async with get_db_conn(self.db_path) as db:
                            async with db.execute(
                                "SELECT open, high, low, close, volume, timestamp FROM candles "
                                "WHERE exchange_id = ? AND symbol = ? AND interval = 60 "
                                "ORDER BY timestamp DESC LIMIT 25",
                                (ex, sym)
                            ) as cursor:
                                c_rows = await cursor.fetchall()
                                
                        if len(c_rows) < 15:
                            continue
                            
                        # 시간 오름차순 정렬
                        candles = sorted([dict(r) for r in c_rows], key=lambda x: x['timestamp'])
                        
                        # rsi 계산
                        closes = [c['close'] for c in candles]
                        rsi_val = 50.0
                        if len(closes) >= 15:
                            diffs = [closes[i] - closes[i-1] for i in range(1, len(closes))]
                            gains = [d if d > 0 else 0.0 for d in diffs[-14:]]
                            losses = [-d if d < 0 else 0.0 for d in diffs[-14:]]
                            avg_gain = sum(gains) / 14
                            avg_loss = sum(losses) / 14
                            if avg_loss == 0:
                                rsi_val = 100.0 if avg_gain > 0 else 50.0
                            else:
                                rs = avg_gain / avg_loss
                                rsi_val = 100.0 - (100.0 / (1.0 + rs))
                                
                        # volatility 계산 (최근 20개 분봉의 close 표준편차 비율)
                        vol_val = 0.0
                        if len(closes) >= 20:
                            target_closes = closes[-20:]
                            mean_close = sum(target_closes) / 20.0
                            variance = sum((x - mean_close) ** 2 for x in target_closes) / 20.0
                            vol_val = (variance ** 0.5) / mean_close if mean_close > 0 else 0.0
                            
                        # volume_ratio (최근 20분 평균 대비 직전 1분 거래량 비율)
                        vols = [c['volume'] for c in candles]
                        vol_ratio = 1.0
                        if len(vols) >= 20:
                            last_vol = vols[-1]
                            mean_vol = sum(vols[-20:]) / 20.0
                            vol_ratio = (last_vol / mean_vol) if mean_vol > 0 else 1.0
                            
                        # 최근 1분 틱 데이터를 통한 spread 및 imbalance 계산
                        bucket_start_ms = current_bucket - 60000
                        bucket_end_ms = current_bucket
                        
                        t_rows = []
                        async with get_db_conn(self.db_path) as db:
                            async with db.execute(
                                "SELECT trade_price, trade_volume, ask_bid FROM trades "
                                "WHERE exchange_id = ? AND symbol = ? AND trade_timestamp BETWEEN ? AND ?",
                                (ex, sym, bucket_start_ms, bucket_end_ms)
                            ) as cursor:
                                t_rows = await cursor.fetchall()
                                
                        spread_val = 0.0005
                        imbalance_val = 0.0
                        
                        if t_rows:
                            ask_vol = sum(r['trade_volume'] for r in t_rows if r['ask_bid'] == 'ASK')
                            bid_vol = sum(r['trade_volume'] for r in t_rows if r['ask_bid'] == 'BID')
                            tot_vol = ask_vol + bid_vol
                            if tot_vol > 0:
                                imbalance_val = (ask_vol - bid_vol) / tot_vol
                                
                            prices = [r['trade_price'] for r in t_rows]
                            max_p = max(prices)
                            min_p = min(prices)
                            avg_p = sum(prices) / len(prices)
                            if avg_p > 0:
                                spread_val = (max_p - min_p) / avg_p
                                
                        # DB 적재
                        async with get_db_conn(self.db_path) as db:
                            await db.execute('''
                                INSERT INTO market_regime_summaries 
                                (timestamp, symbol, volatility, rsi, volume_ratio, spread, orderbook_imbalance)
                                VALUES (?, ?, ?, ?, ?, ?, ?)
                            ''', (current_bucket, f"{ex}:{sym}", vol_val, rsi_val, vol_ratio, spread_val, imbalance_val))
                            await db.commit()
                            
                    logger.debug(f"[CollectorService] 시장 Regime 피처 적재 완료 (종목 수: {len(active_symbols)})")
                except Exception as e:
                    logger.error(f"[CollectorService] 시장 Regime 수집 중 예외 발생: {e}")
                    
                elapsed = time.time() - start_time
                sleep_time = max(0.1, 60.0 - (time.time() % 60.0) + 5.0)
                await asyncio.sleep(sleep_time)
        except asyncio.CancelledError:
            pass
