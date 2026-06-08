import os
import unittest
import time
from src.database.repository import SqliteTradingRepository, InMemoryTradingRepository
from src.database.schema import init_db

class TestSystemEvents(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # 테스트용 임시 SQLite DB 경로
        self.db_path = os.path.join(os.getcwd(), 'data', 'test_system_events.db')
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        
        # DB 스키마 초기화
        await init_db(self.db_path)
        self.sqlite_repo = SqliteTradingRepository(db_path=self.db_path)
        self.in_memory_repo = InMemoryTradingRepository()

    async def asyncTearDown(self):
        # 테스트용 임시 SQLite DB 삭제
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    async def test_in_memory_repository(self):
        """InMemoryTradingRepository에서 시스템 이벤트 기록 및 조회 검증"""
        # 1. 이벤트 삽입
        await self.in_memory_repo.insert_system_event('DAEMON_START', 'web_server', '웹 API 서버 기동', 1000)
        await self.in_memory_repo.insert_system_event('EXCHANGE_SUSPENDED', 'kis', 'KIS 거래정지', 2000)
        await self.in_memory_repo.insert_system_event('EXCHANGE_RESUMED', 'kis', 'KIS 거래정지 해제', 1500)

        # 2. 조회 및 정렬 검증 (timestamp DESC 순서여야 함: 2000 -> 1500 -> 1000)
        events = await self.in_memory_repo.get_system_events(limit=2)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]['event_type'], 'EXCHANGE_SUSPENDED')
        self.assertEqual(events[0]['timestamp'], 2000)
        self.assertEqual(events[1]['event_type'], 'EXCHANGE_RESUMED')
        self.assertEqual(events[1]['timestamp'], 1500)

        # 3. limit 검증
        all_events = await self.in_memory_repo.get_system_events(limit=10)
        self.assertEqual(len(all_events), 3)

    async def test_sqlite_repository(self):
        """SqliteTradingRepository에서 시스템 이벤트 기록 및 영속화 조회 검증"""
        # 1. 이벤트 삽입
        await self.sqlite_repo.insert_system_event('DAEMON_START', 'collector_daemon', '수집기 데몬 기동', 1000)
        await self.sqlite_repo.insert_system_event('EXCHANGE_ERROR', 'upbit', '업비트 API 에러', 3000)
        await self.sqlite_repo.insert_system_event('STRATEGY_SESSION_LOAD', 'strategy_daemon', '전략 로드', 2000)

        # 2. 조회 및 정렬 검증 (timestamp DESC 순서여야 함: 3000 -> 2000 -> 1000)
        events = await self.sqlite_repo.get_system_events(limit=2)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]['event_type'], 'EXCHANGE_ERROR')
        self.assertEqual(events[0]['timestamp'], 3000)
        self.assertEqual(events[0]['target'], 'upbit')
        
        self.assertEqual(events[1]['event_type'], 'STRATEGY_SESSION_LOAD')
        self.assertEqual(events[1]['timestamp'], 2000)

        # 3. limit 검증
        all_events = await self.sqlite_repo.get_system_events(limit=10)
        self.assertEqual(len(all_events), 3)

    async def test_crash_detection_sqlite(self):
        """SqliteTradingRepository에서 크래쉬 감지 로직 검증"""
        target = "test_daemon"
        
        # 1. 초기상태 (기록 없음) -> 크래쉬 감지 안 됨
        await self.sqlite_repo.check_and_report_previous_crash(target)
        events = await self.sqlite_repo.get_system_events()
        self.assertEqual(len(events), 0)

        # 2. START만 기록
        await self.sqlite_repo.insert_system_event('DAEMON_START', target, '기동', 1000000)
        await self.sqlite_repo.check_and_report_previous_crash(target)
        
        events = await self.sqlite_repo.get_system_events()
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]['event_type'], 'DAEMON_CRASHED')
        self.assertEqual(events[0]['target'], target)
        self.assertIn("비정상 종료(크래쉬)되었음을 감지", events[0]['message'])

        # 3. 정상 종료 기록 (START -> STOP)
        await self.sqlite_repo.insert_system_event('DAEMON_START', target, '기동', 2000000)
        await self.sqlite_repo.insert_system_event('DAEMON_STOP', target, '정상종료', 3000000)
        
        # STOP 상태이므로 크래쉬 감지 안 됨
        await self.sqlite_repo.check_and_report_previous_crash(target)
        events = await self.sqlite_repo.get_system_events()
        # 이전 크래쉬(1) + START(1) + START(1) + STOP(1) = 총 4개여야 함 (새 크래쉬 감지 안 됨)
        self.assertEqual(len(events), 4)
        self.assertNotEqual(events[0]['event_type'], 'DAEMON_CRASHED')

    async def test_crash_detection_in_memory(self):
        """InMemoryTradingRepository에서 크래쉬 감지 로직 검증"""
        target = "test_daemon"
        
        # 1. 초기상태 (기록 없음) -> 크래쉬 감지 안 됨
        await self.in_memory_repo.check_and_report_previous_crash(target)
        events = await self.in_memory_repo.get_system_events()
        self.assertEqual(len(events), 0)

        # 2. START만 기록
        await self.in_memory_repo.insert_system_event('DAEMON_START', target, '기동', 1000000)
        await self.in_memory_repo.check_and_report_previous_crash(target)
        
        events = await self.in_memory_repo.get_system_events()
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]['event_type'], 'DAEMON_CRASHED')
        self.assertEqual(events[0]['target'], target)

        # 3. 정상 종료 기록 (START -> STOP)
        await self.in_memory_repo.insert_system_event('DAEMON_START', target, '기동', 2000000)
        await self.in_memory_repo.insert_system_event('DAEMON_STOP', target, '정상종료', 3000000)
        
        await self.in_memory_repo.check_and_report_previous_crash(target)
        events = await self.in_memory_repo.get_system_events()
        self.assertEqual(len(events), 4)
        self.assertNotEqual(events[0]['event_type'], 'DAEMON_CRASHED')

if __name__ == '__main__':
    unittest.main()
