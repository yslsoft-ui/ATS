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
    
    # 임의로 더미 태스크 등록 및 stop() 호출 시 정리되는지 검증
    async def dummy_coro():
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            pass
            
    task = asyncio.create_task(dummy_coro())
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
    
    old_repo_db_path = system.repository.db_path
    system.repository.db_path = temp_db
    
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
    
    # 0. asset_master 및 exchange_assets 테이블에 활성 자산 추가
    async with get_db_conn(temp_db) as db:
        await db.execute(
            "INSERT OR IGNORE INTO asset_master (symbol, korean_name, asset_type) VALUES ('KRW-BTC', '비트코인', 'crypto')"
        )
        await db.execute(
            "INSERT OR IGNORE INTO exchange_assets (exchange_id, symbol, is_active) VALUES ('upbit', 'KRW-BTC', 1)"
        )
        await db.commit()
    
    # ConfigManager를 통해 설정된 실제 임계 시각을 획득
    monitoring_config = system.config_manager.get_monitoring_config()
    detail_stale_ms = monitoring_config["daemon_detail_stale_ms"]
    active_stale_ms = monitoring_config["active_symbols_stale_ms"]
    
    # 1. daemon_detail_stale 유도 (synced_at을 stale_ms + 2초 전으로 주입)
    system.collector_daemon_detail = {
        "type": "collector_daemon_detail",
        "synced_at": now_ms - (detail_stale_ms + 2000),
        "symbols_version": {"upbit": 3},
        "source_pid": 8888,
        "daemon_started_at": now_ms - 50000,
        "exchanges": {
            "upbit": {
                "is_running": True,
                "status": "RUNNING",
                "symbols_count": 10,
                "processed_count": 100,
                "dropped_count": 0,
                "last_tick": None,
                "last_raw": None,
                "last_error": None,
                "operating_hours": "24시간 (연중무휴)",
                "websocket_url": "wss://api.upbit.com/websocket/v1",
                "api_url": "https://api.upbit.com"
            }
        }
    }
    
    # 2. active_symbols_stale 및 mismatch 유도 (synced_at을 stale_ms + 5초 전으로 주입하고 캐시 버전을 1로 설정)
    system.collector_active_symbols = {
        "upbit": {
            "symbols": ["KRW-BTC"],
            "synced_at": now_ms - (active_stale_ms + 5000),
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
        assert stale_status["active_symbols_stale"]["upbit"] is False
        assert stale_status["symbols_version_mismatch"]["upbit"] is False
        assert stale_status["symbols_stale"]["upbit"] is False
        
        # 4. defaultdict 제거 및 dict 형변환 안전성 검증
        assert isinstance(res_data["daemon_detail"]["symbols_version"], dict)
        assert res_data["daemon_detail"]["symbols_version"]["upbit"] == 3
        
        # 5. ConfigManager 기반 공통 API 응답 검증
        assert "monitoring_config" in res_data
        assert res_data["monitoring_config"]["daemon_detail_stale_ms"] == detail_stale_ms
        assert res_data["monitoring_config"]["active_symbols_stale_ms"] == active_stale_ms
        assert res_data["monitoring_config"]["request_symbols_sync_cooldown_ms"] == monitoring_config["request_symbols_sync_cooldown_ms"]
        assert res_data["monitoring_config"]["control_ack_timeout_ms"] == monitoring_config["control_ack_timeout_ms"]
        
        # 6. exchanges 메타데이터 스키마 정합성 검증
        exchanges_info = res_data["daemon_detail"]["exchanges"]
        assert "upbit" in exchanges_info
        upbit_meta = exchanges_info["upbit"]
        assert isinstance(upbit_meta["operating_hours"], str)
        assert isinstance(upbit_meta["websocket_url"], str)
        assert isinstance(upbit_meta["api_url"], str)
        assert upbit_meta["operating_hours"] == "24시간 (연중무휴)"
        assert upbit_meta["websocket_url"] == "wss://api.upbit.com/websocket/v1"
        assert upbit_meta["api_url"] == "https://api.upbit.com"

        # 7. collector_config 전체 설정 데이터 검증
        assert "collector_config" in res_data
        coll_cfg = res_data["collector_config"]
        assert "warmup_enabled" in coll_cfg
        assert "worker_count" in coll_cfg
        assert "db_path" in coll_cfg
        assert "backfill" in coll_cfg
        assert coll_cfg["backfill"]["enabled"] is True
        
    finally:
        system.db_path = old_db_path
        system.repository.db_path = old_repo_db_path
