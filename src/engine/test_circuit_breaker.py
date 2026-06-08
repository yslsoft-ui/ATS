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

    # 1. 거래정지 메시지 파싱 테스트
    # tr_id = H0UNMKO0 (국내주식 장운영정보 통합)
    # 포맷: header|tr_id|data_cnt|symbol^trht_yn^susp_reason^mkop_cls_code^...
    # trht_yn = Y
    msg_data = "0|H0UNMKO0|1|005930^Y^정리매매^1"
    msg_mock = MagicMock()
    msg_mock.type = MagicMock()
    msg_mock.type = 1 # text (aiohttp.WSMsgType.TEXT)
    # 실제 aiohttp.WSMsgType.TEXT는 enum 값 혹은 클래스 속성이나 _parse_message는 msg.type != aiohttp.WSMsgType.TEXT 비교 수행.
    # aiohttp.WSMsgType.TEXT는 string이거나 enum 멤버이므로, aiohttp.WSMsgType.TEXT 자체를 사용하거나 모킹.
    import aiohttp
    msg_mock.type = aiohttp.WSMsgType.TEXT
    msg_mock.data = msg_data

    collector._parse_message(msg_mock)
    assert collector.status == "SUSPENDED"
    assert "거래정지: 정리매매" in collector.status_reason

    # 2. VI 발동 메시지 파싱 테스트
    # trht_yn = N, vi_cls_code = Y (9번째 필드, index 8)
    msg_data_vi = "0|H0UNMKO0|1|005930^N^정상^1^^^^^Y"
    msg_mock_vi = MagicMock()
    msg_mock_vi.type = aiohttp.WSMsgType.TEXT
    msg_mock_vi.data = msg_data_vi

    collector._parse_message(msg_mock_vi)
    assert collector.status == "SUSPENDED"
    assert "VI 발동" in collector.status_reason

    # 3. 정상 해제 메시지 파싱 테스트
    msg_data_normal = "0|H0UNMKO0|1|005930^N^정상^1^^^^^N"
    msg_mock_normal = MagicMock()
    msg_mock_normal.type = aiohttp.WSMsgType.TEXT
    msg_mock_normal.data = msg_data_normal

    collector._parse_message(msg_mock_normal)
    assert collector.status == "RUNNING"
    assert collector.status_reason is None


