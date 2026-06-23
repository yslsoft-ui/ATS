# -*- coding: utf-8 -*-

import time
import json
from enum import Enum
from typing import Dict, Any, List, Optional, Callable, Awaitable
from src.engine.utils.telemetry import get_logger

logger = get_logger("notification_service")

class NotificationType(str, Enum):
    SKIP = "skip"
    ERROR = "error"
    SYSTEM = "system"
    TRADE = "trade"
    ASSET = "asset"

class NotificationLevel(str, Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    SUCCESS = "SUCCESS"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"

LEVEL_PRIORITY = {
    "DEBUG": 10,
    "INFO": 20,
    "SUCCESS": 25,
    "WARNING": 30,
    "ERROR": 40,
    "CRITICAL": 50
}

class NotificationService:
    """
    도메인 이벤트를 표준 알림/감사 로그로 변환하는 통합 알림 관리 서비스입니다.
    유효성 검사, 쿨다운 정책 제어, DB 영속화, WebSocket 실시간 브로드캐스트 전송을 처리합니다.
    """
    def __init__(
        self,
        repository: Any,
        config_manager: Any,
        broadcast_callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None
    ):
        self.repository = repository
        self.config = config_manager
        self.broadcast_callback = broadcast_callback
        self.cooldown_cache: Dict[tuple, float] = {}

    def _validate(self, notification_type: str, level: str, code: str, target: Optional[str]) -> None:
        """
        알림 페이로드 규격을 엄격히 검사하는 유효성 검증 메서드입니다. (Fail-Fast)
        """
        # 1. 알림 타입 검증
        if notification_type not in [t.value for t in NotificationType]:
            raise ValueError(f"지원하지 않는 알림 타입입니다: {notification_type}")

        # 2. 알림 레벨 검증
        if level not in LEVEL_PRIORITY:
            raise ValueError(f"지원하지 않는 알림 레벨입니다: {level}")

        # 3. code 필드 필수 검증
        if not code or not code.strip():
            raise ValueError("code 필드는 필수값입니다.")

        # 4. skip, trade, asset 타입에 대한 target 필수 검증
        if notification_type in (NotificationType.SKIP.value, NotificationType.TRADE.value, NotificationType.ASSET.value):
            if not target or not target.strip():
                raise ValueError(f"'{notification_type}' 타입 알림에는 target 필드가 필수적입니다.")

    async def publish(
        self,
        notification_type: str,
        level: str,
        code: str,
        message: str,
        target: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
        created_at_ms: Optional[int] = None
    ) -> bool:
        """
        알림을 발행합니다.
        
        유효성 오류 시 Fail-Fast 예외를 발생시키고, 
        쿨다운 조건 통과 후 DB 저장 및 브로드캐스트(WebSocket)를 각각 개별 격리하여 Fail-Safe로 실행합니다.
        
        반환값:
            - True: 쿨다운을 통과하여 전송 시도를 정상 완료한 경우
            - False: 쿨다운 정책에 의해 전송이 차단(Throttled)된 경우
        """
        # 1. 유효성 검사 (Fail-Fast)
        self._validate(notification_type, level, code, target)

        current_time = time.time()

        # 2. 쿨다운 설정 조회 및 검사
        cooldowns_cfg = self.config.get("system.notification.cooldowns_sec", {})
        codes_cfg = cooldowns_cfg.get("codes", {})
        types_cfg = cooldowns_cfg.get("types", {})

        # 코드 매칭 우선 -> 타입 매칭 -> 기본값 0
        cooldown_sec = codes_cfg.get(code)
        if cooldown_sec is None:
            cooldown_sec = types_cfg.get(notification_type, 0)

        cooldown_key = (notification_type, code, target)

        if cooldown_sec > 0:
            if cooldown_key in self.cooldown_cache:
                last_published = self.cooldown_cache[cooldown_key]
                if current_time - last_published < cooldown_sec:
                    # 쿨다운 제한에 걸린 경우 차단
                    return False

            # 쿨다운 통과 직후 캐시를 먼저 갱신하여 예외 발생 시의 반복 I/O 및 재귀 스패밍 방지
            self.cooldown_cache[cooldown_key] = current_time

        # 3. DB 영속화 (개별 격리 - Fail-Safe)
        # SYSTEM/ERROR 타입이거나, ASSET_EVENT 중 level이 WARNING 이상인 경우만 DB 저장
        should_persist = (
            notification_type in (NotificationType.SYSTEM.value, NotificationType.ERROR.value)
            or (
                notification_type == NotificationType.ASSET.value
                and LEVEL_PRIORITY[level] >= LEVEL_PRIORITY["WARNING"]
            )
        )

        if should_persist:
            try:
                db_timestamp = created_at_ms if created_at_ms is not None else int(current_time * 1000)
                context_str = json.dumps(context, ensure_ascii=False) if context is not None else None
                # repository.insert_system_event() 호출
                await self.repository.insert_system_event(
                    event_type=code,
                    target=target or "system",
                    message=message,
                    timestamp=db_timestamp,
                    context=context_str
                )
            except Exception as e:
                logger.exception(f"알림 DB 저장 중 예외 발생 (비즈니스 흐름 영향 없음): {e}")

        # 4. 실시간 웹소켓/EventBus 브로드캐스트 (개별 격리 - Fail-Safe)
        try:
            payload = {
                "type": "notification",
                "notification_type": notification_type,
                "level": level,
                "code": code,
                "target": target,
                "message": message,
                "context": context or {},
                "created_at_ms": created_at_ms if created_at_ms is not None else int(current_time * 1000)
            }
            if self.broadcast_callback:
                await self.broadcast_callback(payload)
        except Exception as e:
            logger.exception(f"알림 브로드캐스트 전송 중 예외 발생 (비즈니스 흐름 영향 없음): {e}")

        return True
