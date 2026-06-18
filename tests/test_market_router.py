# -*- coding: utf-8 -*-

import os
import tempfile
import time
import datetime
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from fastapi.testclient import TestClient
from fastapi import HTTPException
from src.server.main import app
from src.database.schema import init_db
from src.database.connection import get_db_conn

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
async def test_kis_reservation_order_routing_and_validation(temp_db):
    # 1. 임시 데이터베이스 초기화
    await init_db(temp_db)
    
    # 2. 테스트용 KIS 종목 정보 적재
    async with get_db_conn(temp_db) as db:
        # NXT 지원 종목 (삼성전자: 005930)
        await db.execute(
            "INSERT OR REPLACE INTO kis_stock_info (symbol, prdt_name, cptt_trad_tr_psbl_yn, nxt_tr_stop_yn) "
            "VALUES ('005930', '삼성전자', 'Y', 'N')"
        )
        # NXT 미지원 종목 (임의 종목: 123450)
        await db.execute(
            "INSERT OR REPLACE INTO kis_stock_info (symbol, prdt_name, cptt_trad_tr_psbl_yn, nxt_tr_stop_yn) "
            "VALUES ('123450', '우선주', 'N', 'N')"
        )
        await db.commit()

    # 3. FastAPI app state 및 Mocking 설정
    old_db_path = app.state.system.db_path
    app.state.system.db_path = temp_db
    
    # KIS API Key Mocking
    os.environ["KIS_APP_KEY"] = "mock_key"
    os.environ["KIS_APP_SECRET"] = "mock_secret"
    os.environ["KIS_ACCOUNT_NO"] = "12345678-01"
    
    # Credential Provider Mocking (Access Token)
    old_cred_provider = app.state.system.cred_provider
    mock_cred = MagicMock()
    mock_cred.get_kis_access_token = AsyncMock(return_value="mock_token")
    app.state.system.cred_provider = mock_cred
    
    client = TestClient(app)

    try:
        # [A] 평일 오전 10:00 (영업시간) - 예약 주문 신청 시 Fail-Fast 에러 반환 검증
        # KST 평일 오전 10:00 -> UTC 평일 01:00
        mock_now_utc = datetime.datetime(2026, 6, 15, 1, 0, 0, tzinfo=datetime.timezone.utc) # 2026-06-15(월) 10:00 KST
        with patch("src.server.routers.market.datetime.datetime") as mock_dt:
            mock_dt.now.return_value = mock_now_utc
            
            # 예약 주문 요청 (is_reservation=True) -> 400 에러 발생해야 함
            response = client.post(
                "/api/exchanges/kis/order",
                json={
                    "symbol": "005930",
                    "side": "BUY",
                    "order_type": "limit",
                    "price": 70000.0,
                    "volume": 10.0,
                    "excg_id_dvsn_cd": "KRX",
                    "is_reservation": True
                }
            )
            assert response.status_code == 400
            assert "현재 예약 주문 가능 시간이 아닙니다" in response.json()["detail"]

        # [B] 평일 오후 17:00 (NXT 지원 종목: 16:00~20:00 사이에는 예약 주문 비활성화 구간)
        # KST 평일 17:00 -> UTC 평일 08:00
        mock_now_utc = datetime.datetime(2026, 6, 15, 8, 0, 0, tzinfo=datetime.timezone.utc) # 2026-06-15(월) 17:00 KST
        with patch("src.server.routers.market.datetime.datetime") as mock_dt:
            mock_dt.now.return_value = mock_now_utc
            
            # NXT 지원 종목 (005930)에 대해 예약 주문 요청 -> 16:00~20:00은 예약 주문 불가하므로 400 에러
            response = client.post(
                "/api/exchanges/kis/order",
                json={
                    "symbol": "005930",
                    "side": "BUY",
                    "order_type": "limit",
                    "price": 70000.0,
                    "volume": 10.0,
                    "excg_id_dvsn_cd": "KRX",
                    "is_reservation": True
                }
            )
            assert response.status_code == 400
            assert "현재 예약 주문 가능 시간이 아닙니다" in response.json()["detail"]

            # 반면 NXT 미지원 종목 (123450)은 16:00부터 예약 가능하므로 KIS API 호출 시도까지 진행되어야 함 (여기서는 API 401 혹은 세션 에러 등이 발생하거나 Mock 호출 성공)
            with patch("aiohttp.ClientSession.post") as mock_post:
                # Mock aiohttp response
                mock_resp = MagicMock()
                mock_resp.status = 200
                mock_resp.json = AsyncMock(return_value={
                    "rt_cd": "0",
                    "output": {"RSVN_ORD_SEQ": "rsvn_12345"}
                })
                mock_post.return_value.__aenter__.return_value = mock_resp
                
                response = client.post(
                    "/api/exchanges/kis/order",
                    json={
                        "symbol": "123450",
                        "side": "BUY",
                        "order_type": "limit",
                        "price": 10000.0,
                        "volume": 5.0,
                        "excg_id_dvsn_cd": "KRX",
                        "is_reservation": True
                    }
                )
                assert response.status_code == 200
                assert response.json()["uuid"] == "rsvn_12345"

        # [C] 평일 오후 21:00 (NXT 지원 종목 포함 전체 예약 주문 가동 구간)
        # KST 평일 21:00 -> UTC 평일 12:00
        mock_now_utc = datetime.datetime(2026, 6, 15, 12, 0, 0, tzinfo=datetime.timezone.utc) # 2026-06-15(월) 21:00 KST
        with patch("src.server.routers.market.datetime.datetime") as mock_dt:
            mock_dt.now.return_value = mock_now_utc
            
            with patch("aiohttp.ClientSession.post") as mock_post:
                mock_resp = MagicMock()
                mock_resp.status = 200
                mock_resp.json = AsyncMock(return_value={
                    "rt_cd": "0",
                    "output": {"RSVN_ORD_SEQ": "rsvn_9999"}
                })
                mock_post.return_value.__aenter__.return_value = mock_resp
                
                # NXT 지원 종목 (005930)에 대해 예약 주문 요청 -> 성공해야 함
                response = client.post(
                    "/api/exchanges/kis/order",
                    json={
                        "symbol": "005930",
                        "side": "BUY",
                        "order_type": "limit",
                        "price": 70000.0,
                        "volume": 10.0,
                        "excg_id_dvsn_cd": "KRX",
                        "is_reservation": True
                    }
                )
                assert response.status_code == 200
                assert response.json()["uuid"] == "rsvn_9999"

    finally:
        app.state.system.db_path = old_db_path
        app.state.system.cred_provider = old_cred_provider
