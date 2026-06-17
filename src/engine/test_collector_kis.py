import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from src.engine.collector_kis import KisCollector

@pytest.mark.asyncio
async def test_kis_collector_historical_candles_active_date_filtering():
    # Mock Credential Provider
    mock_cred = MagicMock()
    mock_cred.get_kis_access_token = AsyncMock(return_value="mock_token")
    
    # Mock KIS API response (output2)
    # The first batch returns a mix of dates. 
    # Suppose we queried hour '154000'. The latest candle returned is output2[0] because it is sorted.
    # In output2, let's say the candles are:
    # 1. 20260617 154000 -> date '20260617' (active_date will be set to '20260617')
    # 2. 20260616 153000 -> date '20260616' (different date, should be skipped)
    mock_output2 = [
        {
            "stck_bsop_date": "20260616",
            "stck_cntg_hour": "153000",
            "stck_oprc": "100000",
            "stck_hgpr": "100000",
            "stck_lwpr": "100000",
            "stck_prpr": "100000",
            "cntg_vol": "100"
        },
        {
            "stck_bsop_date": "20260617",
            "stck_cntg_hour": "154000",
            "stck_oprc": "101000",
            "stck_hgpr": "101000",
            "stck_lwpr": "101000",
            "stck_prpr": "101000",
            "cntg_vol": "200"
        }
    ]
    
    # Mock aiohttp client session
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(return_value={"output2": mock_output2})
    
    mock_session = MagicMock()
    mock_session.closed = False
    mock_session.get = MagicMock(return_value=mock_resp)
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=None)
    
    with patch('aiohttp.ClientSession', return_value=mock_session):
        collector = KisCollector(processing_queue=asyncio.Queue(), cred_provider=mock_cred)
        collector.config = {
            'exchanges': {
                'kis': {
                    'app_key': 'mock_key',
                    'app_secret': 'mock_secret',
                    'api_url': 'http://mockapi'
                }
            }
        }
        collector.session = mock_session
        collector.symbol_market_map = {"005930": "UN"}

        # 2026-06-17 15:00:00 KST to 15:45:00 KST
        start_ts = 1781676000
        end_ts = 1781678700
        
        candles = await collector._fetch_historical_candles(
            symbol="005930",
            start_time=start_ts,
            end_time=end_ts
        )
        
        # Only the candle from '20260617' should be kept.
        # The candle from '20260616' has a different date, so it must be skipped.
        assert len(candles) == 1
        assert candles[0].symbol == "005930"
        assert candles[0].open == 101000.0
        assert candles[0].close == 101000.0
