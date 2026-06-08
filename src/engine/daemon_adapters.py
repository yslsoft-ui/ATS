import os
import sys
import signal
import asyncio
from typing import Dict, Optional, Callable
from src.engine.daemon_supervisor import EventBus, EventBusSubscriberInterface, ProcessController, SignalHandler
from src.ipc.bus import EventBusPublisher, EventBusSubscriber
from src.engine.utils.telemetry import get_logger

logger = get_logger("daemon_adapters")

class ZmqEventBusSubscriber(EventBusSubscriberInterface):
    """EventBusSubscriber를 감싸는 ZMQ용 구독자 어댑터"""
    def __init__(self, zmq_sub: EventBusSubscriber):
        self.zmq_sub = zmq_sub

    async def receive(self) -> tuple[Optional[str], Optional[dict]]:
        topic, data = await self.zmq_sub.receive()
        if not topic:
            return None, None
        return topic, data

    def close(self):
        self.zmq_sub.close()


class ZmqEventBus(EventBus):
    """ZMQ IPC 버스와 연동하는 이벤트 버스 어댑터"""
    def __init__(self):
        self.publishers: Dict[str, EventBusPublisher] = {}

    async def publish(self, topic: str, data: dict):
        # 발행 토픽별로 퍼블리셔 인스턴스를 캐싱하여 재사용
        if topic not in self.publishers:
            try:
                self.publishers[topic] = EventBusPublisher(topic)
            except Exception as e:
                logger.error(f"[ZmqEventBus] Publisher 생성 실패 (topic={topic}): {e}")
                return
        
        try:
            await self.publishers[topic].publish(topic, data)
        except Exception as e:
            logger.error(f"[ZmqEventBus] 메시지 발행 실패 (topic={topic}): {e}")

    async def subscribe(self, topic: str) -> EventBusSubscriberInterface:
        try:
            zmq_sub = EventBusSubscriber(topic)
            return ZmqEventBusSubscriber(zmq_sub)
        except Exception as e:
            logger.error(f"[ZmqEventBus] Subscriber 생성 실패 (topic={topic}): {e}")
            raise e

    def close(self):
        for topic, pub in list(self.publishers.items()):
            try:
                pub.close()
            except Exception as e:
                logger.error(f"[ZmqEventBus] Publisher 닫기 실패 (topic={topic}): {e}")
        self.publishers.clear()


class SysProcessController(ProcessController):
    """sys.executable 및 os.execv를 활용하는 OS 프로세스 제어 어댑터"""
    def restart(self):
        logger.info("[SysProcessController] execv를 통한 프로세스 재시작 실행")
        try:
            os.execv(sys.executable, [sys.executable] + sys.argv)
        except Exception as e:
            logger.error(f"[SysProcessController] execv 재시작 실패: {e}")
            sys.exit(1)


class SysSignalHandler(SignalHandler):
    """asyncio 루프의 add_signal_handler를 활용하는 시그널 제어 어댑터"""
    def register_shutdown_handler(self, callback: Callable[[], None]):
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, callback)
                logger.info(f"[SysSignalHandler] 시그널 핸들러 등록 완료: {sig}")
            except NotImplementedError:
                # Windows 등 시그널 핸들러 등록 미지원 환경 대응
                pass
