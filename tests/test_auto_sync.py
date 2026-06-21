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
