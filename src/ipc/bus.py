import os
import zmq
import zmq.asyncio
import json
from src.engine.utils.telemetry import get_logger

logger = get_logger(__name__)

# IPC 소켓 기본 저장소 (프로젝트 작업 공간 내부)
IPC_DIR = "data/ipc"

def ensure_ipc_dir():
    """IPC 소켓용 디렉토리 생성 및 검증"""
    if not os.path.exists(IPC_DIR):
        os.makedirs(IPC_DIR, exist_ok=True)

def cleanup_stale_socket(socket_path: str):
    """과거 비정상 종료 등으로 남아있는 stale 소켓 파일을 안전하게 제거합니다."""
    if socket_path.startswith("ipc://"):
        file_path = socket_path.replace("ipc://", "")
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
                logger.info(f"[IPC cleanup] Removed stale socket file: {file_path}")
            except Exception as e:
                logger.error(f"[IPC cleanup] Failed to remove stale socket file {file_path}: {e}")

class EventBusPublisher:
    """
    이벤트 발행을 처리하는 ZeroMQ IPC Publisher 래퍼입니다.
    ZeroMQ의 세부 구성을 내부로 격리하여 외부 모듈은 ZeroMQ를 직접 알 필요가 없게 설계되었습니다.
    """
    def __init__(self, topic_name: str):
        ensure_ipc_dir()
        self.topic_name = topic_name
        self.socket_addr = f"ipc://{IPC_DIR}/{topic_name}.ipc"
        
        # bind하기 전에 기존의 찌꺼기 파일 청소
        cleanup_stale_socket(self.socket_addr)
        
        self.context = zmq.asyncio.Context()
        self.socket = self.context.socket(zmq.PUB)
        self.socket.bind(self.socket_addr)
        logger.info(f"[IPC Publisher] Bind completed to {self.socket_addr}")

    async def publish(self, topic: str, data: dict):
        """메시지를 지정된 토픽 카테고리로 비동기 발행합니다."""
        try:
            payload = json.dumps(data)
            await self.socket.send_multipart([topic.encode('utf-8'), payload.encode('utf-8')])
        except Exception as e:
            logger.error(f"[IPC Publisher] Failed to publish message on topic {topic}: {e}")

    def close(self):
        """소켓 연결 및 컨텍스트를 정리합니다."""
        try:
            self.socket.close()
            self.context.term()
            # 종료 후 소켓 파일 제거
            cleanup_stale_socket(self.socket_addr)
            logger.info(f"[IPC Publisher] Closed connection to {self.socket_addr}")
        except Exception as e:
            logger.error(f"[IPC Publisher] Error closing connection: {e}")


class EventBusSubscriber:
    """
    이벤트를 비동기로 수신하는 ZeroMQ IPC Subscriber 래퍼입니다.
    """
    def __init__(self, topic_name: str, filter_topics: list = None):
        ensure_ipc_dir()
        self.topic_name = topic_name
        self.socket_addr = f"ipc://{IPC_DIR}/{topic_name}.ipc"
        
        self.context = zmq.asyncio.Context()
        self.socket = self.context.socket(zmq.SUB)
        self.socket.connect(self.socket_addr)
        
        # 구독 필터 설정
        if filter_topics:
            for ft in filter_topics:
                self.socket.setsockopt(zmq.SUBSCRIBE, ft.encode('utf-8'))
        else:
            self.socket.setsockopt(zmq.SUBSCRIBE, b"")  # 전체 수신
            
        logger.info(f"[IPC Subscriber] Connected to {self.socket_addr} with filters {filter_topics}")

    async def receive(self) -> tuple:
        """메시지를 수신하여 (topic, data_dict) 튜플 형태로 비동기 반환합니다."""
        try:
            topic_bytes, payload_bytes = await self.socket.recv_multipart()
            topic = topic_bytes.decode('utf-8')
            data = json.loads(payload_bytes.decode('utf-8'))
            return topic, data
        except Exception as e:
            logger.error(f"[IPC Subscriber] Error receiving message: {e}")
            return "", {}

    def close(self):
        """소켓 및 컨텍스트를 정리합니다."""
        try:
            self.socket.close()
            self.context.term()
            logger.info(f"[IPC Subscriber] Closed subscriber connection to {self.socket_addr}")
        except Exception as e:
            logger.error(f"[IPC Subscriber] Error closing connection: {e}")
