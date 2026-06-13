# -*- coding: utf-8 -*-

import os
import tempfile
import time
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from fastapi.testclient import TestClient

from src.server.main import app
from src.database.schema import init_db
from src.database.connection import get_db_conn
from src.services.collector_service import CollectorService
from src.engine.daemon_supervisor import EventBus
from src.config.manager import ConfigManager
from src.database.repository import SqliteTradingRepository

@pytest.fixture
def temp_db():
    fd, path = tempfile.mkstemp()
    os.close(fd)
    yield path
    if os.path.exists(path):
        try:
            os.remove(path)
        except Exception:
            pass

@pytest.mark.asyncio
async def test_collector_service_stop_cleanup(temp_db):
    """
    CollectorService의 stop() 시 정기 동기화 백그라운드 태스크가 취소되고 정상적으로 리소스가 소멸되는지 검증합니다.
    """
    await init_db(temp_db)
    
    config = ConfigManager()
    config.config = {"system": {"db_path": temp_db}, "exchanges": {}}
    
    repository = SqliteTradingRepository(db_path=temp_db)
    event_bus = MagicMock(spec=EventBus)
    event_bus.publish = AsyncMock()
    
    service = CollectorService(config, event_bus, repository)
    service.collectors = {}
    
    # 임의로 _periodic_symbols_sync_loop 태스크 등록 및 stop() 호출 시 정리되는지 검증
    task = asyncio.create_task(service._periodic_symbols_sync_loop())
    service._tasks.append(task)
    
    await service.stop()
    
    assert task.cancelled() or task.done()
    assert len(service._tasks) == 0

@pytest.mark.asyncio
async def test_collector_invalid_exchange_prevention(temp_db):
    """
    ZMQ를 통해 무효 거래소 식별자가 인입될 때 제어가 거부되고 SYSTEM_WARNING 감사 로그가 적재되는지 검증합니다.
    """
    await init_db(temp_db)
    
    config = ConfigManager()
    config.config = {"system": {"db_path": temp_db}, "exchanges": {}}
    
    repository = SqliteTradingRepository(db_path=temp_db)
    event_bus = MagicMock(spec=EventBus)
    event_bus.publish = AsyncMock()
    
    service = CollectorService(config, event_bus, repository)
    # upbit만 수집기로 등록
    service.collectors = {"upbit": MagicMock()}
    
    # 1. 무효 거래소 인입 제어 메시지 (start)
    invalid_data = {
        "type": "collector_start",
        "exchange": "bithumb_invalid",
        "command_id": "cmd-test-999"
    }
    
    res = await service.handle_control_message("collector_control", invalid_data)
    
    # 2. 실행 거부 검증
    assert res is False
    
    # 3. DB에 SYSTEM_WARNING 감사 로그 기록 여부 확인
    async with get_db_conn(temp_db) as db:
        async with db.execute("SELECT * FROM system_events WHERE event_type = 'SYSTEM_WARNING'") as cursor:
            row = await cursor.fetchone()
            assert row is not None
            assert "bithumb_invalid" in row["message"]

@pytest.mark.asyncio
async def test_daemon_detail_api_endpoint(temp_db):
    """
    FastAPI /collector/daemon-detail 엔드포인트 호출 시 stale 및 mismatch 로직이 정상 처리되어 dict 형태로 반환되는지 검증합니다.
    """
    await init_db(temp_db)
    
    system = app.state.system
    old_db_path = system.db_path
    system.db_path = temp_db
    
    # 테스트 전용 상태 캐시 직접 주입
    now_ms = int(time.time() * 1000)
    system.collector_statuses = {
        "upbit": {
            "is_running": True,
            "status": "RUNNING",
            "status_reason": None,
            "error": None
        }
    }
    
    # 1. daemon_detail_stale 유도 (synced_at을 17초 전으로 주입해 15초 기준 stale하게 만듦)
    system.collector_daemon_detail = {
        "type": "collector_daemon_detail",
        "synced_at": now_ms - 17000,
        "symbols_version": {"upbit": 3},
        "source_pid": 8888,
        "daemon_started_at": now_ms - 50000
    }
    
    # 2. active_symbols_stale 및 mismatch 유도 (synced_at을 80초 전으로 주입하고 캐시 버전을 1로 설정하여 데몬 버전 3과 다르게 유도)
    system.collector_active_symbols = {
        "upbit": {
            "symbols": ["KRW-BTC"],
            "synced_at": now_ms - 80000,
            "symbols_version": 1,
            "source_pid": 8888,
            "daemon_started_at": now_ms - 50000
        }
    }
    
    try:
        client = TestClient(app)
        response = client.get("/collector/daemon-detail")
        assert response.status_code == 200
        
        res_data = response.json()
        
        # 3. stale 및 mismatch 계산 무결성 검증
        stale_status = res_data["stale_status"]
        assert stale_status["daemon_detail_stale"]["upbit"] is True
        assert stale_status["active_symbols_stale"]["upbit"] is True
        assert stale_status["symbols_version_mismatch"]["upbit"] is True
        assert stale_status["symbols_stale"]["upbit"] is True
        
        # 4. defaultdict 제거 및 dict 형변환 안전성 검증
        assert isinstance(res_data["daemon_detail"]["symbols_version"], dict)
        assert res_data["daemon_detail"]["symbols_version"]["upbit"] == 3
        
    finally:
        system.db_path = old_db_path
