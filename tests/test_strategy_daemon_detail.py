# -*- coding: utf-8 -*-

import os
import tempfile
import time
from unittest.mock import AsyncMock
import pytest
from fastapi.testclient import TestClient

from src.server.main import app
from src.database.schema import init_db

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
async def test_strategy_daemon_detail_api_endpoint(temp_db):
    """
    FastAPI /api/strategies/daemon-detail 엔드포인트 호출 시
    stale 여부 계산 및 응답 스키마 규격 정합성을 검증합니다.
    """
    await init_db(temp_db)
    
    system = app.state.system
    old_db_path = system.db_path
    system.db_path = temp_db
    
    now_ms = int(time.time() * 1000)
    
    # 1. 정상 상태 캐시 직접 주입 (synced_at 기반 판단) - 실제 텔레메트리 계층형 스키마 준수
    system.strategy_daemon_detail = {
        "type": "strategy_daemon_detail",
        "schema_version": 1,
        "synced_at": now_ms - 2000, # 2초 전 (정상)
        "lifecycle": {
            "status": "RUNNING",
            "pid": 9999,
            "started_at": now_ms - 60000,
            "uptime": 60,
            "heartbeat": now_ms - 2000,
            "rss_mb": 64.8,
            "last_error": None
        },
        "engines": {
            "total_engines": 2,
            "active_engines": 2,
            "stale_engines": 0,
            "strategy_stats": {"ShortTermMomentumStrategy": {"active": 2, "total": 2}},
            "exchange_stats": {"upbit": {"active": 2, "total": 2}},
            "engines": [
                {
                    "symbol": "BTC",
                    "strategy_id": "ShortTermMomentumStrategy",
                    "is_active": True,
                    "is_stale": False,
                    "last_tick_received_at": now_ms - 2000,
                    "decision_latency_ms": 1.2
                }
            ]
        },
        "decision_status": {
            "last_tick_at": now_ms - 2000,
            "last_decision_at": now_ms - 3000,
            "decision_latency_ms": 15.2,
            "signal_count_today": 5,
            "order_intent_count_today": 1
        },
        "girs_status": {
            "girs_model_version": "v1.0.0",
            "proposal_count_today": 2,
            "pending": 2,
            "evaluated": 0,
            "failed": 0,
            "rolled_back": 0
        },
        "guardrail_stats": {
            "cooldown": 0,
            "quota": 0,
            "daily_limit": 0,
            "low_stability": 0,
            "data_quality": 0,
            "lazy_replay": 0,
            "champion_cooldown": 0,
            "last_block_reason": None
        },
        "promotion_status": {
            "auto_promotion_enabled": True,
            "promotion_count_today": 0,
            "demotion_count_today": 0,
            "rollback_count_today": 0
        }
    }
    
    try:
        client = TestClient(app)
        
        # 1.1 정상 상태 조회 검증
        response = client.get("/api/strategies/daemon-detail")
        assert response.status_code == 200
        res_data = response.json()
        
        assert res_data["daemon_detail"]["lifecycle"]["pid"] == 9999
        assert res_data["is_stale"] is False
        assert res_data["heartbeat_age_ms"] >= 0
        assert res_data["schema_version"] == 1
        assert res_data["daemon_detail"]["guardrail_stats"]["cooldown"] == 0
        assert len(res_data["daemon_detail"]["engines"]["engines"]) == 1
        assert res_data["daemon_detail"]["engines"]["engines"][0]["symbol"] == "BTC"
        
        # 2. Stale 상태 유도 (마지막 업데이트 시각을 20초 전으로 설정)
        system.strategy_daemon_detail["synced_at"] = now_ms - 20000
        
        response = client.get("/api/strategies/daemon-detail")
        assert response.status_code == 200
        res_data = response.json()
        
        assert res_data["is_stale"] is True
        assert res_data["heartbeat_age_ms"] >= 15000 # stale 임계값 15초 이상
        
    finally:
        system.db_path = old_db_path

@pytest.mark.asyncio
async def test_restart_strategy_daemon_api_endpoint(temp_db):
    """
    FastAPI /api/strategies/restart-daemon API 호출 시
    command_id를 쿼리 파라미터로 받아 정상 응답을 반환하는지 검증합니다.
    """
    await init_db(temp_db)
    
    system = app.state.system
    old_db_path = system.db_path
    system.db_path = temp_db
    
    # dispatcher.dispatch를 Mocking하여 실제 소켓 연결 전송 동작을 회피
    orig_dispatch = system.dispatcher.dispatch
    system.dispatcher.dispatch = AsyncMock()
    
    try:
        client = TestClient(app)
        
        # command_id 포함 호출
        response = client.post("/api/strategies/restart-daemon?command_id=cmd-test-str-123")
        assert response.status_code == 200
        res_data = response.json()
        
        assert "Strategy daemon restart signal published" in res_data["message"]
        assert res_data["command_id"] == "cmd-test-str-123"
        
        # Mock 호출 여부 검증
        system.dispatcher.dispatch.assert_called_once()
        
    finally:
        system.dispatcher.dispatch = orig_dispatch
        system.db_path = old_db_path
