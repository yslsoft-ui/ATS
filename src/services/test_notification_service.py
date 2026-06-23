# -*- coding: utf-8 -*-

import unittest
import time
import logging
import asyncio
from unittest.mock import Mock, AsyncMock, patch
from src.services.notification_service import NotificationService, NotificationType, NotificationLevel, LEVEL_PRIORITY
from src.engine.utils.telemetry import UIBroadcastHandler

class MockConfigManager:
    def __init__(self, config_dict=None):
        self.config = config_dict or {}

    def get(self, key, default=None):
        keys = key.split('.')
        val = self.config
        for k in keys:
            if isinstance(val, dict) and k in val:
                val = val[k]
            else:
                return default
        return val

class TestNotificationService(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # 모의 레포지토리
        self.repository = AsyncMock()
        self.repository.insert_system_event = AsyncMock()

        # 모의 설정 매니저
        self.config_dict = {
            "system": {
                "notification": {
                    "cooldowns_sec": {
                        "types": {
                            "skip": 30,
                            "error": 10,
                            "system": 5,
                            "trade": 0,
                            "asset": 10
                        },
                        "codes": {
                            "risk.max_position_blocked": 60,
                            "daemon.cleanup_stale": 15
                        }
                    }
                }
            }
        }
        self.config_manager = MockConfigManager(self.config_dict)

        # 브로드캐스트 수신 확인용 리스트
        self.broadcast_payloads = []
        async def mock_broadcast(payload):
            self.broadcast_payloads.append(payload)

        self.service = NotificationService(
            repository=self.repository,
            config_manager=self.config_manager,
            broadcast_callback=mock_broadcast
        )

    async def test_validation_fail_fast(self):
        """유효성 검사 실패 시 즉시 ValueError를 던지는지 검증 (Fail-Fast)"""
        # 1. 알림 타입 에러
        with self.assertRaises(ValueError) as ctx:
            await self.service.publish("wrong_type", "INFO", "test.code", "msg")
        self.assertIn("지원하지 않는 알림 타입", str(ctx.exception))

        # 2. 알림 레벨 에러
        with self.assertRaises(ValueError) as ctx:
            await self.service.publish("system", "WRONG_LEVEL", "test.code", "msg")
        self.assertIn("지원하지 않는 알림 레벨", str(ctx.exception))

        # 3. code 필드 누락 에러
        with self.assertRaises(ValueError) as ctx:
            await self.service.publish("system", "INFO", "", "msg")
        self.assertIn("code 필드는 필수", str(ctx.exception))

        # 4. skip, trade, asset 타입에 대한 target 필수 검증 에러
        for n_type in ("skip", "trade", "asset"):
            with self.assertRaises(ValueError) as ctx:
                await self.service.publish(n_type, "INFO", "test.code", "msg", target=None)
            self.assertIn("target 필드가 필수", str(ctx.exception))

        # 5. system 및 error 타입은 target이 없어도 통과해야 함
        res_system = await self.service.publish("system", "INFO", "test.code", "msg", target=None)
        res_error = await self.service.publish("error", "ERROR", "test.code", "msg", target=None)
        self.assertTrue(res_system)
        self.assertTrue(res_error)

    async def test_cooldown_by_type_and_code(self):
        """설정 기반 타입별/코드별 쿨다운 억제 및 캐시 선갱신 시점 검증"""
        target = "exchange:upbit/symbol:KRW-BTC"
        
        # 1. 특정 코드 쿨다운 (60초 적용 확인)
        # 첫 번째 전송: 성공
        res1 = await self.service.publish("skip", "WARNING", "risk.max_position_blocked", "보류1", target=target)
        self.assertTrue(res1)
        self.assertEqual(len(self.broadcast_payloads), 1)

        # 쿨다운 통과 직후 캐시가 갱신되었는지 확인
        cooldown_key = ("skip", "risk.max_position_blocked", target)
        self.assertIn(cooldown_key, self.service.cooldown_cache)

        # 두 번째 전송 (메시지 내용이 바뀌어도 차단되어야 함): 실패 (False 반환)
        res2 = await self.service.publish("skip", "WARNING", "risk.max_position_blocked", "보류2", target=target)
        self.assertFalse(res2)
        # 브로드캐스트가 추가로 수행되지 않아야 함
        self.assertEqual(len(self.broadcast_payloads), 1)

        # 시간 흐름 시뮬레이션 (캐시의 전송 완료 기록을 61초 전으로 당김)
        self.service.cooldown_cache[cooldown_key] = time.time() - 61.0
        
        # 세 번째 전송: 성공
        res3 = await self.service.publish("skip", "WARNING", "risk.max_position_blocked", "보류3", target=target)
        self.assertTrue(res3)
        self.assertEqual(len(self.broadcast_payloads), 2)

    async def test_db_persistence_conditions(self):
        """DB 영속화 필터 조건 분기 검증 (SYSTEM, ERROR 무조건 저장, SKIP 미저장, ASSET은 WARNING 이상 저장)"""
        target = "exchange:upbit/symbol:KRW-BTC"

        # 1. SYSTEM 타입 -> DB 저장 수행되어야 함
        await self.service.publish("system", "INFO", "sys.start", "시스템 기동", target=target)
        self.repository.insert_system_event.assert_called_once()
        self.repository.insert_system_event.reset_mock()

        # 2. ERROR 타입 -> DB 저장 수행되어야 함
        await self.service.publish("error", "ERROR", "sys.error", "에러 발생", target=target)
        self.repository.insert_system_event.assert_called_once()
        self.repository.insert_system_event.reset_mock()

        # 3. SKIP 타입 -> DB 저장 수행되지 않아야 함 (미저장)
        await self.service.publish("skip", "WARNING", "risk.max", "매매 보류", target=target)
        self.repository.insert_system_event.assert_not_called()

        # 4. ASSET 타입 + INFO 레벨 -> DB 저장 수행되지 않아야 함
        await self.service.publish("asset", "INFO", "asset.list", "신규 종목 조회", target=target)
        self.repository.insert_system_event.assert_not_called()

        # 5. ASSET 타입 + WARNING 레벨 -> DB 저장 수행되어야 함
        await self.service.publish("asset", "WARNING", "asset.delist", "상장 폐지 경고", target=target)
        self.repository.insert_system_event.assert_called_once()

    async def test_isolation_fail_safe(self):
        """DB 저장 오류와 브로드캐스트 오류가 각각 상호 격리되는지 검증 (Fail-Safe)"""
        target = "exchange:upbit/symbol:KRW-BTC"

        # 1. DB 저장이 예외를 발생시켜도 브로드캐스트는 성공해야 함
        self.repository.insert_system_event.side_effect = Exception("DB Connection Timeout")
        self.broadcast_payloads.clear()

        res = await self.service.publish("system", "INFO", "sys.test", "DB오류테스트", target=target)
        # publish()는 I/O의 실패와 무관하게 전송 시도를 정상 완료했으므로 True를 반환해야 함
        self.assertTrue(res)
        self.assertEqual(len(self.broadcast_payloads), 1)
        self.assertEqual(self.broadcast_payloads[0]["message"], "DB오류테스트")

        # 2. 브로드캐스트 전송이 예외를 발생시켜도 DB 저장은 성공해야 함
        self.repository.insert_system_event.side_effect = None
        self.repository.insert_system_event.reset_mock()
        
        async def raising_broadcast(payload):
            raise Exception("WebSocket Connection Broken")
            
        self.service.broadcast_callback = raising_broadcast

        res2 = await self.service.publish("system", "INFO", "sys.test2", "WS오류테스트", target=target)
        self.assertTrue(res2)
        # DB 저장용 insert_system_event가 호출되었는지 검증
        self.repository.insert_system_event.assert_called_once()

    async def test_ui_broadcast_handler_recursion_prevention(self):
        """UIBroadcastHandler가 notification_service 자체 로깅으로 인한 무한 루프를 방어하는지 검증"""
        broadcasts = []
        async def mock_callback(data):
            broadcasts.append(data)

        handler = UIBroadcastHandler(broadcast_callback=mock_callback)
        handler.setFormatter(logging.Formatter("%(message)s"))

        # 1. notification_service 로그 레코드
        record_service = logging.LogRecord(
            name="src.services.notification_service",
            level=logging.WARNING,
            pathname="notification_service.py",
            lineno=100,
            msg="DB 저장 중 에러 발생 (비즈니스 흐름 영향 없음)",
            args=(),
            exc_info=None
        )
        handler.emit(record_service)
        # 알림 서비스 이름으로 발생한 로그는 핸들러 수준에서 완전히 무시되어 전송되지 않아야 함
        self.assertEqual(len(broadcasts), 0)

        # 2. 일반 다른 로거 레코드 (예: strategy_service)
        record_strategy = logging.LogRecord(
            name="src.services.strategy_service",
            level=logging.WARNING,
            pathname="strategy_service.py",
            lineno=200,
            msg="전략 엔진 리로드 경고",
            args=(),
            exc_info=None
        )
        handler.emit(record_strategy)
        
        # 비동기 create_task 대기
        await asyncio.sleep(0.05)

        self.assertEqual(len(broadcasts), 1)
        payload = broadcasts[0]
        self.assertEqual(payload["type"], "notification")
        self.assertEqual(payload["notification_type"], "system")
        self.assertEqual(payload["level"], "WARNING")
        self.assertEqual(payload["code"], "log.warning")
        self.assertEqual(payload["target"], "logger:src.services.strategy_service")
        self.assertEqual(payload["message"], "전략 엔진 리로드 경고")
        self.assertIn("pathname", payload["context"])
        self.assertEqual(payload["context"]["lineno"], 200)

if __name__ == '__main__':
    unittest.main()
