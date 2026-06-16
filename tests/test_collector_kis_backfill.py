# -*- coding: utf-8 -*-

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime
from zoneinfo import ZoneInfo

from src.engine.collector_kis import KisCollector
from src.engine.candles import Candle

@pytest.mark.asyncio
async def test_kis_collector_backfill_clipping_and_validation():
    # 1. 수집기 모의 초기화
    processing_queue = asyncio.Queue()
    collector = KisCollector(processing_queue=processing_queue)
    collector.symbol_market_map = {"005930": "UN"}
    
    # 설정Mock 세팅
    collector.config = {
        "exchanges": {
            "kis": {
                "api_url": "https://mock-openapi.koreainvestment.com",
                "app_key": "mock_app_key",
                "app_secret": "mock_app_secret"
            }
        },
        "collector": {
            "backfill": {
                "delays": {"kis": 0.0}
            }
        }
    }
    
    # 인증 토큰 Mock 세팅
    collector.cred_provider = MagicMock()
    collector.cred_provider.get_kis_access_token = AsyncMock(return_value="mock_token")
    
    # 2. Mock aiohttp ClientSession 및 GET 응답 정의
    kst = ZoneInfo('Asia/Seoul')
    mock_session = AsyncMock()
    mock_session.closed = False
    
    # KIS API 응답 모의 데이터 (누적 거래대금 acml_tr_pbmn 확인)
    # stck_cntg_hour가 15:30:00인 진짜 캔들과 15:31:00인 가짜 캔들 (거래대금 변화 없음)
    mock_api_response = {
        "output1": {},
        "output2": [
            {
                "stck_bsop_date": "20260616",
                "stck_cntg_hour": "153000",
                "stck_oprc": "50000.0",
                "stck_hgpr": "50500.0",
                "stck_lwpr": "49900.0",
                "stck_prpr": "50000.0",
                "cntg_vol": "1000",
                "acml_tr_pbmn": "50000000"  # 누적 거래대금 5000만 원 (진짜 거래)
            },
            {
                "stck_bsop_date": "20260616",
                "stck_cntg_hour": "153100",
                "stck_oprc": "50000.0",
                "stck_hgpr": "50000.0",
                "stck_lwpr": "50000.0",
                "stck_prpr": "50000.0",
                "cntg_vol": "1000",
                "acml_tr_pbmn": "50000000"  # 누적 거래대금 5000만 원 (동일함 ➡️ 가짜 캔들)
            }
        ],
        "rt_cd": "0",
        "msg_cd": "MCA00000",
        "msg1": "정상처리 되었습니다!"
    }
    
    # Mock Response Mocking
    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(return_value=mock_api_response)
    
    # aiohttp ClientSession.get() 비동기 컨텍스트 매니저 모킹
    mock_session.get = MagicMock()
    mock_session.get.return_value.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_session.get.return_value.__aexit__ = AsyncMock()
    
    collector.session = mock_session
    
    # 3. KST 저녁 9시 30분(21:30:00)의 타임스탬프 설정 (20:00:00 클리핑 유도)
    test_dt = datetime(2026, 6, 16, 21, 30, 0, tzinfo=kst)
    start_dt = datetime(2026, 6, 16, 15, 0, 0, tzinfo=kst)
    
    end_time_ts = int(test_dt.timestamp())
    start_time_ts = int(start_dt.timestamp())
    
    # 4. _fetch_historical_candles 실행
    candles = await collector._fetch_historical_candles(
        symbol="005930",
        start_time=start_time_ts,
        end_time=end_time_ts
    )
    
    # 5. 검증 1: to_time이 저녁 8시(20:00:00)로 보정되어 쿼리가 200000으로 나갔는지 확인
    # mock_session.get.call_args_list에서 FID_INPUT_HOUR_1와 FID_COND_MRKT_DIV_CODE 검증
    assert mock_session.get.call_count > 0
    call_args = mock_session.get.call_args
    params_sent = call_args[1]["params"]
    
    # 저녁 8시(20:00:00)로 클리핑되었는지 확인
    assert params_sent["FID_INPUT_HOUR_1"] == "200000"
    
    # 검증 2: 시장 코드가 "UN"(통합)으로 나갔는지 확인
    assert params_sent["FID_COND_MRKT_DIV_CODE"] == "UN"
    
    # 검증 3: 누적 거래대금 델타 필터링에 의해 가짜 캔들(15:31:00)은 탈락하고 진짜 캔들(15:30:00) 1개만 수집되었는지 확인
    # stck_cntg_hour 가 153000인 것만 들어있어야 함
    assert len(candles) == 1
    assert candles[0].timestamp == int(datetime(2026, 6, 16, 15, 30, 0, tzinfo=kst).timestamp())
    assert candles[0].volume == 1000.0
