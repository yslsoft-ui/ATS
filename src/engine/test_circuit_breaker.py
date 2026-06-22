import pytest
import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch
from src.engine.collector_kis import KisCollector

class DummyQueue:
    def put_nowait(self, item):
        pass
    async def put(self, item):
        pass

@pytest.mark.asyncio
async def test_kis_collector_suspended_by_msg():
    # KisCollector 생성 (mock 큐 전달)
    collector = KisCollector(
        processing_queue=asyncio.Queue(),
        db_queue=DummyQueue(),
        candle_queue=DummyQueue()
    )
    collector.available_symbols = ["005930"]
    collector.status = "RUNNING"

    import aiohttp

    # 1. 개별 종목 거래정지 메시지 파싱 테스트
    # tr_id = H0UNMKO0 (국내주식 장운영정보 통합)
    # 포맷: header|tr_id|data_cnt|symbol^trht_yn^susp_reason^mkop_cls_code^...
    # trht_yn = Y (개별 거래 정지)
    msg_data = "0|H0UNMKO0|1|005930^Y^정리매매^1"
    msg_mock = MagicMock()
    msg_mock.type = aiohttp.WSMsgType.TEXT
    msg_mock.data = msg_data

    collector._parse_message(msg_mock)
    # 개별 거래정지는 수집기 전역 상태(status)를 SUSPENDED로 만들지 않고 suspended_symbols에 등록해야 함.
    assert collector.status == "RUNNING"
    assert "005930" in collector.suspended_symbols

    # 2. 개별 종목 VI 발동 메시지 파싱 테스트
    # trht_yn = N, vi_cls_code = Y (9번째 필드, index 8)
    msg_data_vi = "0|H0UNMKO0|1|005930^N^정상^1^^^^^Y"
    msg_mock_vi = MagicMock()
    msg_mock_vi.type = aiohttp.WSMsgType.TEXT
    msg_mock_vi.data = msg_data_vi

    collector._parse_message(msg_mock_vi)
    assert collector.status == "RUNNING"
    assert "005930" in collector.vi_active_symbols

    # 3. 시장 전체 정지 / 서킷브레이커 발동 테스트
    # mkop_cls_code = 164 (서킷브레이커)
    msg_data_cb = "0|H0UNMKO0|1|005930^N^정상^164^^^^^N"
    msg_mock_cb = MagicMock()
    msg_mock_cb.type = aiohttp.WSMsgType.TEXT
    msg_mock_cb.data = msg_data_cb

    collector._parse_message(msg_mock_cb)
    assert collector.status == "SUSPENDED"
    assert "시장 전체 정지 / 서킷브레이커 발동" in collector.status_reason

    # 4. 시장 전체 정지 해제 테스트
    msg_data_resume = "0|H0UNMKO0|1|005930^N^정상^1^^^^^N"
    msg_mock_resume = MagicMock()
    msg_mock_resume.type = aiohttp.WSMsgType.TEXT
    msg_mock_resume.data = msg_data_resume

    collector._parse_message(msg_mock_resume)
    assert collector.status == "RUNNING"
    assert collector.status_reason is None
