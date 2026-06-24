import asyncio
import pytest
from src.engine.utils.performance import calculate_performance_metrics
from src.engine.portfolio import Position
from src.engine.daemon_supervisor import DaemonSupervisor, DaemonService, EventBus, ProcessController, SignalHandler

# 1. 포지션 가격 격리 테스트
def test_calculate_performance_metrics_isolation():
    # upbit와 bithumb 각각에 BTC 포지션 정의
    positions = {
        ("upbit", "BTC"): Position(exchange_id="upbit", symbol="BTC", quantity=1.0, avg_price=50000.0),
        ("bithumb", "BTC"): Position(exchange_id="bithumb", symbol="BTC", quantity=2.0, avg_price=49000.0)
    }
    
    # 각 거래소별 BTC의 최신 가격 다르게 설정
    current_prices = {
        ("upbit", "BTC"): 60000.0,
        ("bithumb", "BTC"): 55000.0
    }
    
    trades = []
    initial_cash = 100000.0
    current_cash = 1000.0
    
    metrics = calculate_performance_metrics(
        history=trades,
        initial_cash=initial_cash,
        current_cash=current_cash,
        positions=positions,
        current_prices=current_prices
    )
    
    # 기대 자산 평가액 = current_cash (1000) + 1.0 * 60000 (upbit BTC) + 2.0 * 55000 (bithumb BTC)
    #                   = 1000 + 60000 + 110000 = 171000
    # ROI = ((171000 - 100000) / 100000) * 100 = 71.0%
    assert metrics["roi"] == 71.0


# 2. exchange_id 누락 시 ValueError 발생 테스트
def test_calculate_performance_metrics_missing_exchange_id():
    # exchange_id가 누락되어 복합 키 복원이 불가능한 포지션 정의
    positions = {
        (None, "BTC"): Position(exchange_id=None, symbol="BTC", quantity=1.0, avg_price=50000.0)
    }
    current_prices = {
        ("upbit", "BTC"): 60000.0
    }
    
    with pytest.raises(ValueError, match="exchange_id or symbol is missing"):
        calculate_performance_metrics(
            history=[],
            initial_cash=100000.0,
            current_cash=10000.0,
            positions=positions,
            current_prices=current_prices
        )


# 3. 가격 데이터 누락 시 KeyError 발생 테스트
def test_calculate_performance_metrics_missing_price():
    positions = {
        ("upbit", "BTC"): Position(exchange_id="upbit", symbol="BTC", quantity=1.0, avg_price=50000.0)
    }
    # current_prices에 upbit BTC 가격이 빠져있음
    current_prices = {
        ("bithumb", "BTC"): 60000.0
    }
    
    with pytest.raises(KeyError):
        calculate_performance_metrics(
            history=[],
            initial_cash=100000.0,
            current_cash=10000.0,
            positions=positions,
            current_prices=current_prices
        )


# 모의 도구(Mock) 정의
class MockSubscriber:
    async def receive(self):
        # 무한 대기
        await asyncio.sleep(3600)
        return None, None
    def close(self):
        pass

class MockDaemonService(DaemonService):
    def __init__(self):
        self._tasks = []
        self.critical_tasks = []
    async def start(self): pass
    async def stop(self): pass
    async def handle_config_change(self, new_config: dict): pass
    async def handle_control_message(self, topic: str, data: dict): return False
    def get_status_payloads(self): return []

class MockEventBus(EventBus):
    async def publish(self, topic: str, data: dict): pass
    async def subscribe(self, topic: str): 
        return MockSubscriber()
    def close(self): pass

class MockProcessController(ProcessController):
    def restart(self): pass

class MockSignalHandler(SignalHandler):
    def register_shutdown_handler(self, callback): pass


# 4. non-critical 태스크 정상 완료 시 종료되지 않음 검증
@pytest.mark.asyncio
async def test_daemon_supervisor_non_critical_task_complete():
    service = MockDaemonService()
    supervisor = DaemonSupervisor(
        daemon_name="test_daemon",
        service=service,
        event_bus=MockEventBus(),
        process_controller=MockProcessController(),
        signal_handler=MockSignalHandler(),
        repository=None,
        config_manager=None
    )
    
    # 단발성(non-critical) 태스크 등록
    async def quick_task():
        await asyncio.sleep(0.1)
        
    task = asyncio.create_task(quick_task())
    service._tasks.append(task)
    
    # Supervisor 구동
    supervisor_task = asyncio.create_task(supervisor.start())
    
    await asyncio.sleep(0.5)
    
    # 단발성 태스크가 종료되어도 stop_event는 set되지 않고 Supervisor는 계속 구동 중이어야 함
    assert not supervisor.stop_event.is_set()
    assert not supervisor_task.done()
    
    # 정상 종료 처리
    supervisor.stop_event.set()
    await supervisor_task


# 5. critical_task가 예외 없이 조기 종료 시 RuntimeError 발생 검증
@pytest.mark.asyncio
async def test_daemon_supervisor_critical_task_silent_exit():
    service = MockDaemonService()
    supervisor = DaemonSupervisor(
        daemon_name="test_daemon",
        service=service,
        event_bus=MockEventBus(),
        process_controller=MockProcessController(),
        signal_handler=MockSignalHandler(),
        repository=None,
        config_manager=None
    )
    
    async def critical_loop_exit_early():
        await asyncio.sleep(0.1) # 예외 없이 정상 리턴해 버림
        
    task = asyncio.create_task(critical_loop_exit_early())
    service._tasks.append(task)
    service.critical_tasks.append(task)
    
    # supervisor.start() 실행
    supervisor_task = asyncio.create_task(supervisor.start())
    
    await asyncio.sleep(0.5)
    
    # 조기 종료를 감지하여 supervisor가 RuntimeError를 던지고 정지했어야 함
    assert supervisor_task.done()
    with pytest.raises(RuntimeError, match="정합성 오류: 핵심 백그라운드 태스크"):
        await supervisor_task


# 6. stop_event set 상태에서 CancelledError 발생 시 정상 종료로 처리됨 검증
@pytest.mark.asyncio
async def test_daemon_supervisor_cancellation_under_stop_event():
    service = MockDaemonService()
    supervisor = DaemonSupervisor(
        daemon_name="test_daemon",
        service=service,
        event_bus=MockEventBus(),
        process_controller=MockProcessController(),
        signal_handler=MockSignalHandler(),
        repository=None,
        config_manager=None
    )
    
    async def loop():
        while True:
            await asyncio.sleep(0.1)
            
    task = asyncio.create_task(loop())
    service._tasks.append(task)
    service.critical_tasks.append(task)
    
    supervisor_task = asyncio.create_task(supervisor.start())
    await asyncio.sleep(0.3)
    
    # stop_event를 설정하고 취소 발생 유도
    supervisor.stop_event.set()
    
    await supervisor_task
    # 예외 없이 정상 완료되어야 함 (CancelledError를 삼킴)


# 7. stop_event가 set되지 않은 상태에서 CancelledError 발생 시 비정상 취소로 판단하여 예외 전파 검증
@pytest.mark.asyncio
async def test_daemon_supervisor_cancellation_without_stop_event():
    service = MockDaemonService()
    supervisor = DaemonSupervisor(
        daemon_name="test_daemon",
        service=service,
        event_bus=MockEventBus(),
        process_controller=MockProcessController(),
        signal_handler=MockSignalHandler(),
        repository=None,
        config_manager=None
    )
    
    async def loop():
        while True:
            await asyncio.sleep(0.1)
            
    task = asyncio.create_task(loop())
    service._tasks.append(task)
    service.critical_tasks.append(task)
    
    supervisor_task = asyncio.create_task(supervisor.start())
    await asyncio.sleep(0.3)
    
    # stop_event set 없이 태스크 강제 취소
    task.cancel()
    
    await asyncio.sleep(0.5)
    assert supervisor_task.done()
    
    # 비정상 취소이므로 CancelledError가 전파되어 터져있어야 함
    with pytest.raises(asyncio.CancelledError):
        await supervisor_task


# 8. stop_event set 이후 종료 정리 단계에서 일반 예외 발생 시 수거하여 raise 검증
@pytest.mark.asyncio
async def test_daemon_supervisor_stop_gathers_general_exception():
    service = MockDaemonService()
    supervisor = DaemonSupervisor(
        daemon_name="test_daemon",
        service=service,
        event_bus=MockEventBus(),
        process_controller=MockProcessController(),
        signal_handler=MockSignalHandler(),
        repository=None,
        config_manager=None
    )
    
    async def loop_with_error():
        try:
            while True:
                await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            # cancel되었을 때 일부러 ValueError를 던짐
            raise ValueError("Error during cleanup")
            
    task = asyncio.create_task(loop_with_error())
    supervisor._tasks.append(task)
    supervisor.critical_tasks.append(task)
    
    supervisor_task = asyncio.create_task(supervisor.start())
    await asyncio.sleep(0.3)
    
    # stop_event를 설정하여 종료 단계 유도
    supervisor.stop_event.set()
    
    await asyncio.sleep(0.5)
    assert supervisor_task.done()
    
    # 종료 과정에서 ValueError가 수거되어 raise 되어야 함
    with pytest.raises(ValueError, match="Error during cleanup"):
        await supervisor_task
