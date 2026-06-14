from fastapi import WebSocket
from typing import Set, Dict, Tuple
import json
import asyncio
from src.engine.utils.telemetry import get_logger

logger = get_logger(__name__)

class ConnectionManager:
    """WebSocket 연결 및 종목별 구독을 관리합니다."""
    def __init__(self):
        self.active_connections: Set[WebSocket] = set()
        # (exchange, symbol) -> Set[WebSocket] 형태의 O(1) 구독 조회 사전
        self.subscriptions: Dict[Tuple[str, str], Set[WebSocket]] = {}
        # WebSocket -> (exchange, symbol) 형태의 빠른 역조회 사전
        self.ws_to_sub: Dict[WebSocket, Tuple[str, str]] = {}

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.add(websocket)
        logger.debug(f"New WebSocket client connected. Active: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        self.active_connections.discard(websocket)
        # 역조회 사전을 이용해 O(1)로 기존 구독을 해제
        sub = self.ws_to_sub.pop(websocket, None)
        if sub and sub in self.subscriptions:
            self.subscriptions[sub].discard(websocket)
            if not self.subscriptions[sub]:
                self.subscriptions.pop(sub)
        logger.debug(f"WebSocket client disconnected. Active: {len(self.active_connections)}")

    def subscribe(self, websocket: WebSocket, exchange: str, symbol: str):
        """클라이언트가 특정 시장의 종목을 구독합니다."""
        # 1. 기존 구독 정보가 있다면 먼저 해제
        prev_sub = self.ws_to_sub.get(websocket)
        if prev_sub and prev_sub in self.subscriptions:
            self.subscriptions[prev_sub].discard(websocket)
            if not self.subscriptions[prev_sub]:
                self.subscriptions.pop(prev_sub)
                
        # 2. 신규 구독 등록
        new_sub = (exchange, symbol)
        self.ws_to_sub[websocket] = new_sub
        if new_sub not in self.subscriptions:
            self.subscriptions[new_sub] = set()
        self.subscriptions[new_sub].add(websocket)
        logger.debug(f"Client subscribed to {exchange}:{symbol}")

    async def broadcast(self, message: dict):
        """해당 종목을 구독 중인 클라이언트에게만 O(1) 속도로 선별 전송합니다."""
        symbol = message.get('code', '')
        exchange = message.get('exchange_id', message.get('exchange', 'upbit'))
        key = (exchange, symbol)
        
        targets = self.subscriptions.get(key, set())
        if targets:
            msg_str = json.dumps(message)
            await asyncio.gather(
                *[ws.send_text(msg_str) for ws in targets],
                return_exceptions=True
            )

    async def broadcast_alert(self, message: dict):
        """시스템 경고 또는 전체 이벤트 로그를 구독 상태와 무관하게 전역 전송합니다."""
        msg_str = json.dumps(message)
        if self.active_connections:
            await asyncio.gather(
                *[ws.send_text(msg_str) for ws in self.active_connections],
                return_exceptions=True
            )

manager = ConnectionManager()
