# -*- coding: utf-8 -*-

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, date
from zoneinfo import ZoneInfo
import time

from src.engine.collector_base import BaseCollector

class MockCollector(BaseCollector):
    def __init__(self, processing_queue=None):
        super().__init__(processing_queue=processing_queue)
        self.available_symbols = ['138930']
        self.is_running = True
        self.candle_queue = AsyncMock()

    @property
    def exchange_id(self) -> str:
        return 'kis'

    def get_connection_metadata(self, config):
        return {"operating_hours": "08:00-20:00", "websocket_url": "ws://mock", "api_url": "http://mock"}

    async def _fetch_symbols(self, config):
        return self.available_symbols

    async def _fetch_historical_candles(self, symbol, start_time, end_time):
        self.called_start_time = start_time
        self.called_end_time = end_time
        return []

    def _get_websocket_url(self, config):
        return "ws://mock"

    async def _subscribe(self, ws, config):
        pass

    def _parse_message(self, msg):
        return None

@pytest.mark.asyncio
async def test_kis_backfill_today_limit_and_time_check():
    # 1. 수집기 모의 객체 생성
    processing_queue = asyncio.Queue()
    collector = MockCollector(processing_queue=processing_queue)
    
    # 2. 설정 Mock 세팅 (max_hours = 24)
    config = {
        "db_path": ":memory:",
        "collector": {
            "backfill": {
                "enabled": True,
                "max_hours": 24,
                "delays": {"kis": 0.0}
            }
        }
    }

    kst = ZoneInfo('Asia/Seoul')
    # 현재 시각을 KST 2026-06-17 08:05:00로 고정
    fixed_time = datetime(2026, 6, 17, 8, 5, 0, tzinfo=kst).timestamp()

    # 3. DB 커넥션 Mocking
    # DB 조회 결과: 아무런 캔들도 저장되지 않은 상태 (완벽히 비어 있는 상태)
    mock_cursor = AsyncMock()
    mock_cursor.fetchone = AsyncMock(return_value=(None,))
    mock_cursor.fetchall = AsyncMock(return_value=[])
    
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_cursor)
    
    # patch context managers
    db_conn_mock = MagicMock()
    db_conn_mock.__aenter__ = AsyncMock(return_value=mock_db)
    db_conn_mock.__aexit__ = AsyncMock()

    with patch('src.database.connection.get_db_conn', return_value=db_conn_mock), \
         patch('time.time', return_value=fixed_time):
         
        await collector.backfill_candles(config)
        
    # 검증: 현재 시각이 08:05:00 KST이므로, 오늘 날짜(06-17)의 08:00 ~ 08:04 범위 내의 타임스탬프만 수집 대상으로 조회되어야 합니다.
    assert hasattr(collector, 'called_start_time'), "오늘 오전 08:00 ~ 08:04 백필을 위해 API가 호출되어야 합니다."
    first_start_dt = datetime.fromtimestamp(collector.called_start_time, tz=kst)
    first_end_dt = datetime.fromtimestamp(collector.called_end_time, tz=kst)
    assert first_start_dt.date() == date(2026, 6, 17)
    assert first_end_dt.date() == date(2026, 6, 17)
    assert first_start_dt.hour == 8 and first_start_dt.minute == 0
    assert first_end_dt.hour == 8 and first_end_dt.minute == 4

    # 다음 테스트 단계 진행을 위해 속성 제거
    del collector.called_start_time
    del collector.called_end_time

    # 4. 현재 시각을 장중인 KST 2026-06-17 10:00:00으로 변경하여 테스트
    fixed_time_midday = datetime(2026, 6, 17, 10, 0, 0, tzinfo=kst).timestamp()
    
    with patch('src.database.connection.get_db_conn', return_value=db_conn_mock), \
         patch('time.time', return_value=fixed_time_midday):
         
        await collector.backfill_candles(config)
        
    # 검증: KST 2026-06-17 10:00:00 시점에 기동되면 오늘 날짜인 2026-06-17 08:00:00 ~ 09:59:00 범위가 백필되어야 함.
    # 시작 시간 (08:00:00 KST) = timestamp 1781650800
    # 끝 시간 (09:59:00 KST) = timestamp 1781657940
    assert hasattr(collector, 'called_start_time')
    
    start_dt = datetime.fromtimestamp(collector.called_start_time, tz=kst)
    end_dt = datetime.fromtimestamp(collector.called_end_time, tz=kst)
    
    assert start_dt.date() == date(2026, 6, 17)
    assert end_dt.date() == date(2026, 6, 17)
    
    # 아침 통합장 운영시간 (08:00) 반영에 의해 08:00:00부터 수집 범위가 형성되는지 확인
    assert start_dt.hour == 8
    assert start_dt.minute == 0
    assert end_dt.hour == 9
    assert end_dt.minute == 59
