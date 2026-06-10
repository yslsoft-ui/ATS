import asyncio
import time
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, Callable
from src.engine.utils.telemetry import get_logger

logger = get_logger("daemon_supervisor")

class DaemonService(ABC):
    """
    DaemonSupervisor가 생명주기를 주관하는 도메인 서비스의 추상 인터페이스입니다.
    """
    @abstractmethod
    async def start(self):
        """서비스 초기화 및 구동"""
        pass

    @abstractmethod
    async def stop(self):
        """서비스 정리 및 안전 종료"""
        pass

    @abstractmethod
    async def handle_config_change(self, new_config: dict):
        """설정 변경 사항을 도메인에 위임"""
        pass

    @abstractmethod
    async def handle_control_message(self, topic: str, data: dict) -> bool:
        """
        ZMQ 제어 명령 수신 시 도메인 서비스 전용 명령 처리.
        True 반환 시 Supervisor의 기본 재기동 명령 처리를 건너뛸 수 있음.
        """
        pass

    @abstractmethod
    def get_status_payloads(self) -> List[tuple[str, dict]]:
        """
        주기적으로 발행해야 할 상태 데이터들의 (토픽, 페이로드) 튜플 리스트를 반환합니다.
        """
        pass


class EventBusSubscriberInterface(ABC):
    """EventBus로부터 메시지를 수신하는 구독자 인터페이스"""
    @abstractmethod
    async def receive(self) -> tuple[Optional[str], Optional[dict]]:
        pass

    @abstractmethod
    def close(self):
        pass


class EventBus(ABC):
    """ZMQ 등 메시지 버스 입출력을 추상화하는 Seam 인터페이스"""
    @abstractmethod
    async def publish(self, topic: str, data: dict):
        pass

    @abstractmethod
    async def subscribe(self, topic: str) -> EventBusSubscriberInterface:
        pass

    @abstractmethod
    def close(self):
        pass


class ProcessController(ABC):
    """OS 프로세스 제어를 추상화하는 Seam 인터페이스"""
    @abstractmethod
    def restart(self):
        """현재 데몬 프로세스를 execv 방식으로 자가 재기동"""
        pass


class SignalHandler(ABC):
    """OS 시그널 처리를 추상화하는 Seam 인터페이스"""
    @abstractmethod
    def register_shutdown_handler(self, callback: Callable[[], None]):
        """종료 시그널 수신 시 실행할 콜백 등록"""
        pass


class DaemonSupervisor:
    """
    데몬의 안전 기동, 시그널 종료, 하트비트 전송, 설정 와칭, 자가 재기동 등의
    공통 시스템 생명주기를 총괄 제어하는 깊은 모듈(Deep Module)입니다.
    """
    def __init__(
        self,
        daemon_name: str,
        service: DaemonService,
        event_bus: EventBus,
        process_controller: ProcessController,
        signal_handler: SignalHandler,
        repository: Any,
        config_manager: Any,
        control_topic: str = "collector_control"
    ):
        self.daemon_name = daemon_name
        self.service = service
        self.event_bus = event_bus
        self.process_controller = process_controller
        self.signal_handler = signal_handler
        self.repository = repository
        self.config_manager = config_manager
        self.control_topic = control_topic

        self.stop_event = asyncio.Event()
        self.restart_requested = False

        self._tasks: List[asyncio.Task] = []

    async def record_system_event(self, event_type: str, message: str):
        ts = int(time.time() * 1000)
        try:
            if self.repository:
                await self.repository.insert_system_event(event_type, self.daemon_name, message, ts)
        except Exception as e:
            logger.error(f"[{self.daemon_name}] DB 시스템 이벤트 적재 실패: {e}")
        try:
            # signal_data 또는 strategy_signal 같은 공통 버스로 시스템 이벤트를 브로드캐스트
            # 기존 규칙에 따라 collector_daemon은 signal_data, strategy_daemon은 strategy_signal 사용
            if "strategy" in self.daemon_name:
                event_topic = "strategy_signal"
            elif "collector" in self.daemon_name:
                event_topic = "collector_signal"
            elif "cleanup" in self.daemon_name:
                event_topic = "cleanup_signal"
            elif "eval" in self.daemon_name:
                event_topic = "evaluation_signal"
            else:
                event_topic = "signal_data"
            await self.event_bus.publish(event_topic, {
                "type": "system_event",
                "event_type": event_type,
                "target": self.daemon_name,
                "message": message,
                "timestamp": ts
            })
        except Exception as e:
            logger.error(f"[{self.daemon_name}] 이벤트 버스 전파 실패: {e}")

    async def _control_listener_loop(self):
        """ZMQ 제어 명령 수신 비동기 루프"""
        subscriber = await self.event_bus.subscribe(self.control_topic)
        logger.info(f"[{self.daemon_name}] Control subscriber connected on '{self.control_topic}'")
        try:
            while not self.stop_event.is_set():
                topic, data = await subscriber.receive()
                if not topic or not data:
                    await asyncio.sleep(0.1)
                    continue

                logger.info(f"[{self.daemon_name}] 제어 신호 수신: topic={topic}, data={data}")

                # 1. 도메인 서비스에 메시지 처리 위임
                handled = await self.service.handle_control_message(topic, data)
                if handled:
                    continue

                # 2. Supervisor 기본 제어 메시지 처리
                if data.get('type') == 'restart_daemon':
                    logger.info(f"[{self.daemon_name}] 자가 재기동(Self-Restart) 요청 감지.")
                    self.restart_requested = True
                    self.stop_event.set()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"[{self.daemon_name}] 제어 루프 예외: {e}")
        finally:
            subscriber.close()
            logger.info(f"[{self.daemon_name}] Control subscriber closed.")

    async def _status_broadcast_loop(self):
        """1초 주기로 상태 폴링 후 버스로 브로드캐스트"""
        while not self.stop_event.is_set():
            try:
                payloads = self.service.get_status_payloads()
                for topic, payload in payloads:
                    await self.event_bus.publish(topic, payload)
            except Exception as e:
                logger.error(f"[{self.daemon_name}] 상태 브로드캐스트 루프 예외: {e}")
            await asyncio.sleep(1.0)

    async def _on_config_changed(self, new_config: dict):
        logger.info(f"[{self.daemon_name}] 설정 파일 변경 감지, 서비스에 변경 사항 전송")
        try:
            await self.service.handle_config_change(new_config)
        except Exception as e:
            logger.error(f"[{self.daemon_name}] 설정 변경 처리 중 예외: {e}")

    def _handle_shutdown_signal(self):
        logger.info(f"[{self.daemon_name}] 종료 시그널 감지. 안전 종료 이벤트를 트리거합니다.")
        self.stop_event.set()

    async def start(self):
        """데몬을 안전하게 기동합니다."""
        logger.info(f"[{self.daemon_name}] Supervisor 구동을 시작합니다.")

        # 1. 크래쉬 검사 및 복구
        if self.repository and hasattr(self.repository, 'check_and_report_previous_crash'):
            try:
                await self.repository.check_and_report_previous_crash(self.daemon_name)
            except Exception as e:
                logger.error(f"[{self.daemon_name}] 기동 전 크래쉬 체크 에러: {e}")

        # 2. 기동 이벤트 로깅
        await self.record_system_event('DAEMON_START', '데몬 기동 완료')

        # 3. 도메인 서비스 기동
        try:
            await self.service.start()
            logger.info(f"[{self.daemon_name}] 도메인 서비스 기동 완료.")
        except Exception as e:
            logger.critical(f"[{self.daemon_name}] 도메인 서비스 기동 중 치명적 예외: {e}")
            await self.record_system_event('DAEMON_CRASHED', f"도메인 서비스 기동 실패: {e}")
            raise e

        # 4. 시그널 핸들러 연결
        self.signal_handler.register_shutdown_handler(self._handle_shutdown_signal)

        # 5. 설정 변경 감시 연동
        if self.config_manager:
            self.config_manager.subscribe(self._on_config_changed)
            await self.config_manager.start_watching()

        # 6. 백그라운드 태스크 기동
        self._tasks.append(asyncio.create_task(self._control_listener_loop()))
        self._tasks.append(asyncio.create_task(self._status_broadcast_loop()))

        # 7. 종료 대기
        await self.stop_event.wait()

        # 8. 안전 종료 수행
        await self.stop()

    async def stop(self):
        """데몬을 안전하게 종료하고 필요한 경우 자가 재기동을 요청합니다."""
        logger.info(f"[{self.daemon_name}] 안전 종료 절차를 시작합니다...")

        # 1. 설정 감시 중단
        if self.config_manager:
            await self.config_manager.stop_watching()

        # 2. 백그라운드 태스크 정리
        for task in self._tasks:
            if not task.done():
                task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
            self._tasks.clear()

        # 3. 도메인 서비스 중단
        try:
            await self.service.stop()
            logger.info(f"[{self.daemon_name}] 도메인 서비스 중단 완료.")
        except Exception as e:
            logger.error(f"[{self.daemon_name}] 도메인 서비스 중단 중 예외 발생: {e}")

        # 4. 종료 이벤트 로깅
        stop_event_type = 'DAEMON_STOP' if not self.restart_requested else 'DAEMON_STOP_RESTART'
        stop_message = '데몬 안전 종료 완료' if not self.restart_requested else '데몬 자가 재기동을 위한 종료'
        await self.record_system_event(stop_event_type, stop_message)

        # 5. 이벤트 버스 정리
        self.event_bus.close()

        # 0.8초 플러시 대기
        await asyncio.sleep(0.8)

        if self.restart_requested:
            logger.info(f"[{self.daemon_name}] 자가 재기동을 실행합니다.")
            self.process_controller.restart()
        else:
            logger.info(f"[{self.daemon_name}] 안전 종료 절차가 완료되었습니다.")
