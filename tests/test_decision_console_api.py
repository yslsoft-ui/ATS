# -*- coding: utf-8 -*-

import os
import tempfile
import time
import pytest
from fastapi.testclient import TestClient
from src.server.main import app
from src.database.schema import init_db
from src.database.connection import get_db_conn
from src.engine.strategy import BaseStrategy, StrategyRegistry

class RSIStrategy(BaseStrategy):
    """RSI Strategy for testing"""
    default_params = {"rsi_window": 14}
    def on_update(self, context):
        return None

# 테스트용 전략 등록
StrategyRegistry.register(RSIStrategy)

@pytest.fixture
def temp_db():
    fd, path = tempfile.mkstemp()
    # 임시 파일 디스크립터를 닫아서 SQLite가 접근할 수 있도록 함
    os.close(fd)
    yield path
    if os.path.exists(path):
        try:
            os.remove(path)
        except Exception:
            pass

@pytest.mark.asyncio
async def test_decision_console_endpoints(temp_db):
    """
    의사결정 콘솔용 백엔드 API 라우터의 모든 엔드포인트와 중복 재평가 요청 가드가 정상 작동하는지 검증합니다.
    """
    # 1. 임시 데이터베이스 초기화
    await init_db(temp_db)
    
    # 2. 테스트용 뼈대 데이터 적재
    now_ms = int(time.time() * 1000)
    async with get_db_conn(temp_db) as db:
        # 의사결정 제안 (Proposal)
        await db.execute(
            "INSERT INTO strategy_proposals (id, strategy_id, status, outcome, confidence_score, proposed_params, original_params, created_at, updated_at) "
            "VALUES (102, 'RSIStrategy', 'PENDING', 'RUNNING', 85, '{\"rsi_window\": 16}', '{\"rsi_window\": 14}', ?, ?)",
            (now_ms, now_ms)
        )
        
        # 전략 버전 정보
        await db.execute(
            "INSERT INTO strategy_versions (strategy_id, current_version_id, current_params, applied_at) "
            "VALUES ('RSIStrategy', 3, '{\"rsi_window\": 14}', ?)",
            (now_ms,)
        )
        
        # FSM 타임라인 로그
        await db.execute(
            "INSERT INTO promotion_event_log (event_id, proposal_id, event_type, payload, timestamp, sequence_no, feature_snapshot) "
            "VALUES ('evt_102_entered', '102', 'PROPOSAL_ENTERED', '{}', ?, 1, '{\"price_close\": 50000.0, \"price_sma\": 49500.0}')",
            (now_ms,)
        )
        
        # 시스템 감사 이벤트 로그
        await db.execute(
            "INSERT INTO system_events (event_type, target, message, timestamp) "
            "VALUES ('PROPOSAL_APPROVED', '102', '제안 #102 승인 및 대기열 등록', ?)",
            (now_ms,)
        )
        
        await db.commit()

    # 3. 전역 FastAPI app state의 db_path를 임시 DB 경로로 Mocking
    old_db_path = app.state.system.db_path
    app.state.system.db_path = temp_db
    
    # 전략 설정 모킹 추가
    old_configs = app.state.system.strategy_configs
    app.state.system.strategy_configs = {
        "RSIStrategy": {"enabled": True, "params": {"rsi_window": 14}}
    }
    
    try:
        client = TestClient(app)
        
        # [A] 요약 API 검증
        response = client.get("/api/decision-console/summary")
        assert response.status_code == 200
        summary = response.json()
        assert summary["pending_proposals_count"] == 1
        assert summary["operation_mode"] is not None
        
        # [B] 전략 목록 API 검증
        response = client.get("/api/decision-console/strategies")
        assert response.status_code == 200
        strategies = response.json()
        assert len(strategies) > 0
        
        # [C] 특정 전략 상세 추적 API 검증
        response = client.get("/api/decision-console/strategies/RSIStrategy/trace")
        assert response.status_code == 200
        trace = response.json()
        assert trace["strategy_id"] == "RSIStrategy"
        
        # [D] 제안 목록 API 검증
        response = client.get("/api/decision-console/proposals?status=PENDING")
        assert response.status_code == 200
        proposals = response.json()
        assert len(proposals) == 1
        assert proposals[0]["id"] == 102
        
        # [E] 특정 제안 심층 추적 API 검증 (10대 탭 데이터 패키지)
        response = client.get("/api/decision-console/proposals/102/trace")
        assert response.status_code == 200
        prop_trace = response.json()
        assert prop_trace["proposal"]["id"] == 102
        assert prop_trace["feature_snapshot"]["price_close"] == 50000.0
        
        # [F] 수동 재평가 Job 요청 등록 검증 (POST)
        response = client.post("/api/decision-console/proposals/102/reevaluate")
        assert response.status_code == 200
        reeval = response.json()
        assert reeval["accepted"] is True
        assert reeval["status"] == "QUEUED"
        
        # [G] 동일 Proposal에 대한 중복 재평가 요청 차단 가드 검증
        response_dup = client.post("/api/decision-console/proposals/102/reevaluate")
        assert response_dup.status_code == 200
        reeval_dup = response_dup.json()
        assert reeval_dup["accepted"] is False
        assert "이미" in reeval_dup["message"]
        
        # [H] 수동 재평가 Job 이력 API 검증
        response = client.get("/api/decision-console/proposals/102/reevaluation-jobs")
        assert response.status_code == 200
        jobs = response.json()
        assert len(jobs) == 1
        assert jobs[0]["status"] == "QUEUED"
        
        # [I] 의사결정 이벤트 로그 API 검증
        response = client.get("/api/decision-console/events")
        assert response.status_code == 200
        events = response.json()
        assert len(events) > 0
        
        # [J] 데이터 신선도/정합성 조회를 위한 Raw JSON 조회 API 검증
        response = client.get("/api/decision-console/raw/proposal/102")
        assert response.status_code == 200
        raw_proposal = response.json()
        assert raw_proposal["id"] == 102

    finally:
        # 4. db_path 및 설정 복구
        app.state.system.db_path = old_db_path
        app.state.system.strategy_configs = old_configs


@pytest.mark.asyncio
async def test_system_settings_endpoints(temp_db):
    """
    시스템 설정 API (GET, POST)가 정상 작동하는지 검증합니다.
    """
    # 1. 임시 데이터베이스 초기화
    await init_db(temp_db)
    
    # 2. 전역 FastAPI app state의 db_path를 임시 DB 경로로 Mocking
    old_db_path = app.state.system.db_path
    app.state.system.db_path = temp_db
    
    try:
        client = TestClient(app)
        
        # [A] 미등록 설정 조회 검증
        response = client.get("/api/system/settings/non_existent_key")
        assert response.status_code == 200
        data = response.json()
        assert data["key"] == "non_existent_key"
        assert data["value"] is None
        
        # [B] 설정 저장 검증 (POST)
        response = client.post(
            "/api/system/settings/test_dismissed_events",
            json={"value": "[1, 2, 3]"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["key"] == "test_dismissed_events"
        assert data["value"] == "[1, 2, 3]"
        
        # [C] 저장된 설정 조회 검증 (GET)
        response = client.get("/api/system/settings/test_dismissed_events")
        assert response.status_code == 200
        data = response.json()
        assert data["key"] == "test_dismissed_events"
        assert data["value"] == "[1, 2, 3]"
        
        # [D] 설정 업데이트 검증 (POST)
        response = client.post(
            "/api/system/settings/test_dismissed_events",
            json={"value": "[1, 2, 3, 4]"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["key"] == "test_dismissed_events"
        assert data["value"] == "[1, 2, 3, 4]"
        
        # [E] 업데이트된 설정 조회 검증 (GET)
        response = client.get("/api/system/settings/test_dismissed_events")
        assert response.status_code == 200
        data = response.json()
        assert data["key"] == "test_dismissed_events"
        assert data["value"] == "[1, 2, 3, 4]"
        
    finally:
        # 3. db_path 복구
        app.state.system.db_path = old_db_path

