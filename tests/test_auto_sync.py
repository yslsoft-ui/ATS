# -*- coding: utf-8 -*-
import pytest
import datetime
import re
from typing import Optional
from src.database.repository import SqliteTradingRepository, InMemoryTradingRepository
from src.database.schema import init_db

def parse_bithumb_time(text: str) -> Optional[str]:
    m1 = re.search(r'(\d{4})\.\s*(\d{2})\.\s*(\d{2})\s*\(.*?\)\s*(\d{1,2})시', text)
    if m1:
        year, month, day, hour = m1.groups()
        return f"{year}-{month.zfill(2)}-{day.zfill(2)} {hour.zfill(2)}:00:00"
    m2 = re.search(r'(\d{4})\.\s*(\d{2})\.\s*(\d{2})\s*\(.*?\)\s*(오전|오후)\s*(\d{1,2}):(\d{2})', text)
    if m2:
        year, month, day, ampm, hour_str, minute = m2.groups()
        hour = int(hour_str)
        if ampm == "오후" and hour < 12:
            hour += 12
        elif ampm == "오전" and hour == 12:
            hour = 0
        return f"{year}-{month.zfill(2)}-{day.zfill(2)} {str(hour).zfill(2)}:{minute.zfill(2)}:00"
    return None

def parse_bithumb_symbol(title: str) -> Optional[str]:
    match = re.search(r'\(([A-Za-z0-9/_-]+)\)', title)
    if match:
        return match.group(1).upper()
    return None

def test_bithumb_notice_parsing():
    # 1. 상장 공지 테스트
    listing_title = "리프로토콜(RE) 원화 마켓 추가"
    listing_body = "거래 개시 시점 : 2026.06.19(금) 오후 3:00 예정"
    assert parse_bithumb_symbol(listing_title) == "RE"
    assert parse_bithumb_time(listing_body) == "2026-06-19 15:00:00"

    # 2. 상폐 공지 테스트
    delisting_title = "이클립스(ES) 거래지원종료"
    delisting_body = "ㆍ거래(매수/매도) 종료 일시: 2026. 07. 20 (월) 15시 예정"
    assert parse_bithumb_symbol(delisting_title) == "ES"
    assert parse_bithumb_time(delisting_body) == "2026-07-20 15:00:00"


@pytest.mark.asyncio
async def test_in_memory_planned_events_flow():
    repo = InMemoryTradingRepository()
    
    now = datetime.datetime.now()
    scheduled_at = (now + datetime.timedelta(minutes=20)).strftime('%Y-%m-%d %H:%M:%S')
    
    # 1. 이벤트 등록
    event_id = await repo.insert_planned_asset_event(
        exchange_id='bithumb',
        symbol='RE',
        event_type='listing',
        scheduled_at=scheduled_at,
        notice_url='https://feed.bithumb.com/notice/1653785'
    )
    assert event_id > 0

    # 2. 중복 등록 방지 검증
    duplicate_id = await repo.insert_planned_asset_event(
        exchange_id='bithumb',
        symbol='RE',
        event_type='listing',
        scheduled_at=scheduled_at,
        notice_url='https://feed.bithumb.com/notice/1653785'
    )
    assert duplicate_id == 0

    # 3. 이벤트 조회
    events = await repo.get_planned_asset_events(status='PLANNED')
    assert len(events) == 1
    assert events[0]['symbol'] == 'RE'

    # 4. 실행 예정 이벤트 (30분 전) 조회 검증
    executables = await repo.get_executable_planned_events(before_minutes=30)
    assert len(executables) == 1
    assert executables[0]['symbol'] == 'RE'

    executables_10m = await repo.get_executable_planned_events(before_minutes=10)
    assert len(executables_10m) == 0

    # 5. 상태 변경 검증
    success = await repo.update_planned_event_status(event_id, 'EXECUTED')
    assert success is True
    
    events_after = await repo.get_planned_asset_events(status='PLANNED')
    assert len(events_after) == 0

    events_executed = await repo.get_planned_asset_events(status='EXECUTED')
    assert len(events_executed) == 1
    assert events_executed[0]['status'] == 'EXECUTED'

    # 6. 예정 이벤트 삭제(Delete) 검증
    test_del_id = await repo.insert_planned_asset_event(
        exchange_id='bithumb',
        symbol='TEST_DEL',
        event_type='listing',
        scheduled_at=scheduled_at,
        notice_url='https://feed.bithumb.com/notice/1653787'
    )
    assert test_del_id > 0
    
    active_events = await repo.get_planned_asset_events(status='PLANNED')
    assert any(ev['id'] == test_del_id for ev in active_events)
    
    del_success = await repo.delete_planned_event(test_del_id)
    assert del_success is True
    
    active_events_after = await repo.get_planned_asset_events(status='PLANNED')
    assert not any(ev['id'] == test_del_id for ev in active_events_after)
    
    del_fail = await repo.delete_planned_event(99999)
    assert del_fail is False


@pytest.mark.asyncio
async def test_sqlite_planned_events_flow(tmp_path):
    db_file = str(tmp_path / "test_backtest.db")
    await init_db(db_file)
    
    repo = SqliteTradingRepository(db_path=db_file)
    
    now = datetime.datetime.now()
    scheduled_at = (now + datetime.timedelta(minutes=20)).strftime('%Y-%m-%d %H:%M:%S')
    
    # 1. 이벤트 등록
    event_id = await repo.insert_planned_asset_event(
        exchange_id='bithumb',
        symbol='RE',
        event_type='listing',
        scheduled_at=scheduled_at,
        notice_url='https://feed.bithumb.com/notice/1653785'
    )
    assert event_id > 0

    # 2. 중복 등록 방지 검증
    duplicate_id = await repo.insert_planned_asset_event(
        exchange_id='bithumb',
        symbol='RE',
        event_type='listing',
        scheduled_at=scheduled_at,
        notice_url='https://feed.bithumb.com/notice/1653785'
    )
    assert duplicate_id == 0

    # 3. 이벤트 조회
    events = await repo.get_planned_asset_events(status='PLANNED')
    assert len(events) == 1
    assert events[0]['symbol'] == 'RE'

    # 4. 실행 예정 이벤트 (30분 전) 조회 검증
    executables = await repo.get_executable_planned_events(before_minutes=30)
    assert len(executables) == 1
    assert executables[0]['symbol'] == 'RE'

    # 5. 상태 변경 검증
    success = await repo.update_planned_event_status(event_id, 'EXECUTED')
    assert success is True
    
    events_after = await repo.get_planned_asset_events(status='PLANNED')
    assert len(events_after) == 0

    # 6. 예정 이벤트 삭제(Delete) 검증
    test_del_id = await repo.insert_planned_asset_event(
        exchange_id='bithumb',
        symbol='TEST_DEL',
        event_type='listing',
        scheduled_at=scheduled_at,
        notice_url='https://feed.bithumb.com/notice/1653787'
    )
    assert test_del_id > 0
    
    active_events = await repo.get_planned_asset_events(status='PLANNED')
    assert any(ev['id'] == test_del_id for ev in active_events)
    
    del_success = await repo.delete_planned_event(test_del_id)
    assert del_success is True
    
    active_events_after = await repo.get_planned_asset_events(status='PLANNED')
    assert not any(ev['id'] == test_del_id for ev in active_events_after)
    
    del_fail = await repo.delete_planned_event(99999)
    assert del_fail is False


def test_kis_schedule_calculation():
    # 07:50 KST 타겟 스케줄 시간 계산 검증
    # 1. 07:50 이전인 경우 (예: 당일 06:00) -> 당일 07:50이 타겟이어야 함
    now = datetime.datetime(2026, 6, 22, 6, 0, 0)
    target_time = now.replace(hour=7, minute=50, second=0, microsecond=0)
    if now >= target_time:
        target_time += datetime.timedelta(days=1)
    
    assert target_time == datetime.datetime(2026, 6, 22, 7, 50, 0)
    sleep_seconds = (target_time - now).total_seconds()
    assert sleep_seconds == 110 * 60 # 110분 = 6600초

    # 2. 07:50 이후인 경우 (예: 당일 09:00) -> 익일 07:50이 타겟이어야 함
    now = datetime.datetime(2026, 6, 22, 9, 0, 0)
    target_time = now.replace(hour=7, minute=50, second=0, microsecond=0)
    if now >= target_time:
        target_time += datetime.timedelta(days=1)
        
    assert target_time == datetime.datetime(2026, 6, 23, 7, 50, 0)
    sleep_seconds = (target_time - now).total_seconds()
    assert sleep_seconds == (22 * 60 + 50) * 60 # 22시간 50분 = 82200초


def test_schedule_calculator():
    from src.services.collector_service import ScheduleCalculator
    
    # 1. parse_hours_to_seconds 검증
    # 정상 변환 (1시간 -> 3600초)
    assert ScheduleCalculator.parse_hours_to_seconds("test_key", 1) == 3600
    assert ScheduleCalculator.parse_hours_to_seconds("test_key", 12) == 43200
    
    # 누락된 설정
    with pytest.raises(ValueError) as excinfo:
        ScheduleCalculator.parse_hours_to_seconds("test_key", None)
    assert "설정이 누락되었습니다" in str(excinfo.value)
    
    # 음수/0
    with pytest.raises(ValueError) as excinfo:
        ScheduleCalculator.parse_hours_to_seconds("test_key", 0)
    assert "올바르지 않은 설정값" in str(excinfo.value)
    with pytest.raises(ValueError) as excinfo:
        ScheduleCalculator.parse_hours_to_seconds("test_key", -5)
    assert "올바르지 않은 설정값" in str(excinfo.value)
    
    # 문자열 등 잘못된 타입
    with pytest.raises(ValueError) as excinfo:
        ScheduleCalculator.parse_hours_to_seconds("test_key", "1")
    assert "올바르지 않은 설정값" in str(excinfo.value)

    # 2. parse_sync_time 검증
    # 정상
    hour, minute = ScheduleCalculator.parse_sync_time("test_key", "07:50")
    assert hour == 7
    assert minute == 50
    
    # 누락
    with pytest.raises(ValueError) as excinfo:
        ScheduleCalculator.parse_sync_time("test_key", None)
    assert "설정이 누락되었습니다" in str(excinfo.value)
    with pytest.raises(ValueError) as excinfo:
        ScheduleCalculator.parse_sync_time("test_key", "")
    assert "설정이 누락되었습니다" in str(excinfo.value)
    
    # 비정상 포맷
    for invalid in ["7:50", "07:5", "07:500", "invalid", "24:00", "12:60"]:
        with pytest.raises(ValueError) as excinfo:
            ScheduleCalculator.parse_sync_time("test_key", invalid)
        assert "올바르지 않은 동기화 시각 설정값" in str(excinfo.value)

    # 3. calculate_time_delay 검증
    # 07:50 타겟, 현재 06:00 -> 110분 (6600초) 대기
    now = datetime.datetime(2026, 6, 22, 6, 0, 0)
    delay = ScheduleCalculator.calculate_time_delay("test_key", "07:50", now)
    assert delay == 6600.0
    
    # 07:50 타겟, 현재 09:00 -> 다음날 07:50 대기 (22시간 50분 = 82200초)
    now = datetime.datetime(2026, 6, 22, 9, 0, 0)
    delay = ScheduleCalculator.calculate_time_delay("test_key", "07:50", now)
    assert delay == 82200.0


class MockConfigManager:
    def __init__(self, config_dict):
        self.config_dict = config_dict
        
    def get(self, key, default=None):
        return self.config_dict.get(key, default)


class MockLogger:
    def __init__(self):
        self.criticals = []
        self.infos = []
        self.exceptions = []
        
    def info(self, msg):
        self.infos.append(msg)
        
    def critical(self, msg):
        self.criticals.append(msg)
        
    def exception(self, msg):
        self.exceptions.append(msg)


@pytest.mark.asyncio
async def test_scheduler_trigger_success():
    from src.services.collector_service import SchedulerTrigger
    import asyncio
    
    # 1. run_interval_loop 정상 작동 검증
    config = MockConfigManager({"test_interval": 1})
    logger = MockLogger()
    trigger = SchedulerTrigger(config, logger)
    
    call_count = 0
    async def mock_action():
        nonlocal call_count
        call_count += 1
        if call_count >= 2:
            raise asyncio.CancelledError()
            
    # asyncio.sleep을 패치하여 sleep 대기 없이 즉시 기동하도록 함
    async def dummy_sleep(delay):
        pass
        
    original_sleep = asyncio.sleep
    asyncio.sleep = dummy_sleep
    try:
        with pytest.raises(asyncio.CancelledError):
            await trigger.run_interval_loop("test_interval", "테스트태스크", mock_action)
    finally:
        asyncio.sleep = original_sleep
        
    assert call_count == 2
    assert len(logger.infos) > 0
    assert len(logger.criticals) == 0


@pytest.mark.asyncio
async def test_scheduler_trigger_fail_fast():
    from src.services.collector_service import SchedulerTrigger
    import asyncio
    
    # 설정이 잘못된 경우(ValueError) 루프가 즉시 폭사(Raise)하는지 검증 (Fail-Fast)
    config = MockConfigManager({"test_interval": -5})
    logger = MockLogger()
    trigger = SchedulerTrigger(config, logger)
    
    async def mock_action():
        pass
        
    with pytest.raises(ValueError):
        await trigger.run_interval_loop("test_interval", "테스트태스크", mock_action)
        
    assert len(logger.criticals) == 1
    assert "설정 유효성 검사 실패" in logger.criticals[0]


@pytest.mark.asyncio
async def test_kis_holiday_checking_and_sync_skip():
    from src.engine.credentials import CredentialProvider
    from unittest.mock import AsyncMock, patch, MagicMock

    # 1. CredentialProvider check_kis_open_day 및 캐시 동작 테스트
    config = {
        "exchanges": {
            "kis": {
                "enabled": True,
                "app_key": "mock_key",
                "app_secret": "mock_secret",
                "api_url": "https://mockapi.com"
            }
        }
    }
    
    provider = CredentialProvider(config)
    provider.kis_open_day_cache.clear()
    
    # get_kis_access_token 모킹
    provider.get_kis_access_token = AsyncMock(return_value="mock_token")
    
    # aiohttp session get 모킹
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(return_value={
        "rt_cd": "0",
        "msg1": "SUCCESS",
        "output": [{"opnd_yn": "N"}]  # 휴장일
    })
    
    mock_context = MagicMock()
    mock_context.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_context.__aexit__ = AsyncMock(return_value=None)
    
    provider.session = MagicMock()
    provider.session.get = MagicMock(return_value=mock_context)
    
    # 휴장일 조회 및 캐시 적용 확인
    is_open = await provider.check_kis_open_day("20260622")
    assert is_open is False
    assert "20260622" in provider.kis_open_day_cache
    assert provider.kis_open_day_cache["20260622"] is False
    
    # 캐시 작동 확인: session.get이 두 번째에는 호출되지 않아야 함
    provider.session.get.reset_mock()
    is_open_cached = await provider.check_kis_open_day("20260622")
    assert is_open_cached is False
    provider.session.get.assert_not_called()

    # API 실패 시 Fail-Fast(ValueError) 검증
    provider.kis_open_day_cache.clear()
    mock_resp_fail = MagicMock()
    mock_resp_fail.status = 500
    mock_resp_fail.text = AsyncMock(return_value="Internal Server Error")
    
    mock_context_fail = MagicMock()
    mock_context_fail.__aenter__ = AsyncMock(return_value=mock_resp_fail)
    mock_context_fail.__aexit__ = AsyncMock(return_value=None)
    
    provider.session.get = MagicMock(return_value=mock_context_fail)
    
    with pytest.raises(ValueError) as excinfo:
        await provider.check_kis_open_day("20260623")
    assert "HTTP 에러 (500)" in str(excinfo.value)


@pytest.mark.asyncio
async def test_kis_collector_holiday_pre_connect_check():
    from src.engine.collector_kis import KisCollector
    from unittest.mock import AsyncMock, patch, MagicMock
    import asyncio
    
    proc_queue = asyncio.Queue()
    collector = KisCollector(processing_queue=proc_queue)
    
    collector.config = {
        "exchanges": {
            "kis": {
                "enabled": True,
                "market_hours": {
                    "start_time": "08:30",
                    "end_time": "18:10"
                }
            }
        }
    }
    
    # MarketHours.is_krx_open가 항상 True를 리턴하게 모킹 (평일 영업 시간 조건)
    with patch("src.engine.utils.market_hours.MarketHours.is_krx_open", return_value=True):
        # CredentialProvider.check_kis_open_day가 False(휴장일)를 리턴하게 모킹
        collector.cred_provider = MagicMock()
        collector.cred_provider.check_kis_open_day = AsyncMock(return_value=False)
        
        wait_sec = await collector._pre_connect_check()
        # 휴장일이므로 자정(00:00)까지 대기하게 되므로 wait_sec > 0 이며 하루 이내(86400)여야 함
        assert wait_sec > 0
        assert wait_sec <= 86400
        
        # API 오류가 발생하여 예외가 터질 때 수집기가 60초 대기를 반환하는지 검증
        collector.cred_provider.check_kis_open_day = AsyncMock(side_effect=Exception("API Error"))
        wait_sec_err = await collector._pre_connect_check()
        assert wait_sec_err == 60.0


@pytest.mark.asyncio
async def test_kis_master_sync_skip_on_holiday():
    from src.services.collector_service import CollectorService
    from unittest.mock import AsyncMock, patch, MagicMock
    
    config_manager = MagicMock()
    config_manager.get = MagicMock(side_effect=lambda key, default=None: {
        "exchanges.kis": {"enabled": True},
        "system.db_path": "data/backtest.db"
    }.get(key, default))
    
    event_bus = MagicMock()
    repository = MagicMock()
    
    service = CollectorService(config_manager, event_bus, repository)
    service.db_path = "data/backtest.db"
    
    # cred_provider 모킹
    service.cred_provider = MagicMock()
    service.cred_provider.check_kis_open_day = AsyncMock(return_value=False) # 오늘 휴장일
    
    # sync_exchange_assets 모킹 및 호출 여부 검증
    with patch("src.database.sync_assets.sync_exchange_assets") as mock_sync:
        await service._sync_kis_master_assets()
        mock_sync.assert_not_called()  # 휴장일이므로 동기화 함수가 호출되지 않아야 함

