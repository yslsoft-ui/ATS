import pytest
import asyncio
from typing import List, Dict, Tuple, Optional, Callable
from src.engine.daemon_supervisor import DaemonSupervisor, DaemonService, EventBus, EventBusSubscriberInterface, ProcessController, SignalHandler
from src.database.repository import InMemoryTradingRepository

class FakeSubscriber(EventBusSubscriberInterface):
    def __init__(self):
        self.queue = asyncio.Queue()
        self.is_closed = False

    async def receive(self) -> tuple[Optional[str], Optional[dict]]:
        try:
            return await self.queue.get()
        except asyncio.CancelledError:
            return None, None

    def close(self):
        self.is_closed = True


class FakeEventBus(EventBus):
    def __init__(self):
        self.published_messages: List[Tuple[str, dict]] = []
        self.subscribers: Dict[str, List[FakeSubscriber]] = {}
        self.is_closed = False

    async def publish(self, topic: str, data: dict):
        self.published_messages.append((topic, data))
        if topic in self.subscribers:
            for sub in self.subscribers[topic]:
                if not sub.is_closed:
                    sub.queue.put_nowait((topic, data))

    async def subscribe(self, topic: str) -> EventBusSubscriberInterface:
        sub = FakeSubscriber()
        self.subscribers.setdefault(topic, []).append(sub)
        return sub

    def close(self):
        self.is_closed = True
        for subs in self.subscribers.values():
            for sub in subs:
                sub.close()


class FakeProcessController(ProcessController):
    def __init__(self):
        self.restart_called = False

    def restart(self):
        self.restart_called = True


class FakeSignalHandler(SignalHandler):
    def __init__(self):
        self.shutdown_callback: Optional[Callable[[], None]] = None

    def register_shutdown_handler(self, callback: Callable[[], None]):
        self.shutdown_callback = callback

    def trigger_shutdown(self):
        if self.shutdown_callback:
            self.shutdown_callback()


class FakeDaemonService(DaemonService):
    def __init__(self):
        self.start_called = False
        self.stop_called = False
        self.config_changes: List[dict] = []
        self.control_messages: List[Tuple[str, dict]] = []
        self.status_payloads = [("test_topic", {"status": "ok"})]

    async def start(self):
        self.start_called = True

    async def stop(self):
        self.stop_called = True

    async def handle_config_change(self, new_config: dict):
        self.config_changes.append(new_config)

    async def handle_control_message(self, topic: str, data: dict) -> bool:
        self.control_messages.append((topic, data))
        # 특정 테스트용 제어 메시지에 대해서는 True를 반환해 supervisor 동작 스킵
        if data.get('type') == 'domain_only':
            return True
        return False

    def get_status_payloads(self) -> List[tuple[str, dict]]:
        return self.status_payloads


class FakeConfigManager:
    def __init__(self):
        self.callbacks: List[Callable[[dict], None]] = []
        self.is_watching = False

    def subscribe(self, callback: Callable[[dict], None]):
        self.callbacks.append(callback)

    async def start_watching(self):
        self.is_watching = True

    async def stop_watching(self):
        self.is_watching = False

    def trigger_change(self, new_config: dict):
        for cb in self.callbacks:
            if asyncio.iscoroutinefunction(cb):
                asyncio.create_task(cb(new_config))
            else:
                cb(new_config)


@pytest.mark.asyncio
async def test_supervisor_lifecycle_start_stop():
    repository = InMemoryTradingRepository()
    event_bus = FakeEventBus()
    process_controller = FakeProcessController()
    signal_handler = FakeSignalHandler()
    service = FakeDaemonService()
    config_manager = FakeConfigManager()

    supervisor = DaemonSupervisor(
        daemon_name="test_daemon",
        service=service,
        event_bus=event_bus,
        process_controller=process_controller,
        signal_handler=signal_handler,
        repository=repository,
        config_manager=config_manager
    )

    # 비동기로 start 실행
    start_task = asyncio.create_task(supervisor.start())

    # 기동이 진행될 시간을 살짝 대기
    await asyncio.sleep(0.05)

    assert service.start_called is True
    assert config_manager.is_watching is True
    assert signal_handler.shutdown_callback is not None

    # 기동 이벤트 로깅 검증
    # DB 검증
    assert len(repository.events) == 1
    assert repository.events[0]['event_type'] == 'DAEMON_START'
    assert repository.events[0]['target'] == 'test_daemon'

    # ZMQ 퍼블리시 검증 (strategy가 안 들어가있으므로 signal_data 채널)
    assert len(event_bus.published_messages) >= 1
    start_msg = [m for m in event_bus.published_messages if m[1].get('event_type') == 'DAEMON_START']
    assert len(start_msg) == 1
    assert start_msg[0][0] == 'signal_data'

    # 시그널 입력을 흉내내어 안전 종료 트리거
    signal_handler.trigger_shutdown()

    # start 태스크 대기
    await start_task

    assert service.stop_called is True
    assert config_manager.is_watching is False
    assert event_bus.is_closed is True
    assert process_controller.restart_called is False

    # 종료 이벤트 검증
    assert len(repository.events) == 2
    assert repository.events[1]['event_type'] == 'DAEMON_STOP'


@pytest.mark.asyncio
async def test_supervisor_restart_control():
    repository = InMemoryTradingRepository()
    event_bus = FakeEventBus()
    process_controller = FakeProcessController()
    signal_handler = FakeSignalHandler()
    service = FakeDaemonService()
    config_manager = FakeConfigManager()

    supervisor = DaemonSupervisor(
        daemon_name="test_daemon",
        service=service,
        event_bus=event_bus,
        process_controller=process_controller,
        signal_handler=signal_handler,
        repository=repository,
        config_manager=config_manager,
        control_topic="test_control"
    )

    start_task = asyncio.create_task(supervisor.start())
    await asyncio.sleep(0.05)

    # restart_daemon 제어 신호를 퍼블리시하여 자가 재기동 트리거
    await event_bus.publish("test_control", {"type": "restart_daemon"})

    await start_task

    assert supervisor.restart_requested is True
    assert process_controller.restart_called is True
    assert service.stop_called is True
    
    # 종료 이벤트 타입이 DAEMON_STOP_RESTART인지 검증
    assert repository.events[1]['event_type'] == 'DAEMON_STOP_RESTART'


@pytest.mark.asyncio
async def test_supervisor_config_change():
    repository = InMemoryTradingRepository()
    event_bus = FakeEventBus()
    process_controller = FakeProcessController()
    signal_handler = FakeSignalHandler()
    service = FakeDaemonService()
    config_manager = FakeConfigManager()

    supervisor = DaemonSupervisor(
        daemon_name="test_daemon",
        service=service,
        event_bus=event_bus,
        process_controller=process_controller,
        signal_handler=signal_handler,
        repository=repository,
        config_manager=config_manager
    )

    start_task = asyncio.create_task(supervisor.start())
    await asyncio.sleep(0.05)

    # 설정 변경 유발
    config_manager.trigger_change({"test_key": "new_value"})
    await asyncio.sleep(0.05)

    assert len(service.config_changes) == 1
    assert service.config_changes[0] == {"test_key": "new_value"}

    signal_handler.trigger_shutdown()
    await start_task


@pytest.mark.asyncio
async def test_supervisor_status_broadcast():
    repository = InMemoryTradingRepository()
    event_bus = FakeEventBus()
    process_controller = FakeProcessController()
    signal_handler = FakeSignalHandler()
    service = FakeDaemonService()
    config_manager = FakeConfigManager()

    supervisor = DaemonSupervisor(
        daemon_name="test_daemon",
        service=service,
        event_bus=event_bus,
        process_controller=process_controller,
        signal_handler=signal_handler,
        repository=repository,
        config_manager=config_manager
    )

    service.status_payloads = [("test_topic", {"status": "broadcast_ok"})]

    start_task = asyncio.create_task(supervisor.start())
    
    # 브로드캐스트 대기 (1초 주기이므로 1.1초 대기)
    await asyncio.sleep(1.1)

    # ZMQ에 상태 페이로드가 발행되었는지 확인
    status_msgs = [m for m in event_bus.published_messages if m[0] == 'test_topic']
    assert len(status_msgs) >= 1
    assert status_msgs[0][1] == {"status": "broadcast_ok"}

    signal_handler.trigger_shutdown()
    await start_task
