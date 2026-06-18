import asyncio
import pytest
import aiohttp
from unittest.mock import AsyncMock, MagicMock
from src.engine.collector_kis import KisCollector
from src.engine.market.upbit import UpbitMarketAdapter
from src.engine.market.bithumb import BithumbMarketAdapter
from src.engine.market.dto import MarketTickerDTO

class MockRepository:
    def __init__(self):
        self.system_events = []

    async def insert_system_event(self, event_type: str, target: str, message: str, timestamp=None, context=None):
        self.system_events.append({
            "event_type": event_type,
            "target": target,
            "message": message
        })

@pytest.mark.asyncio
async def test_kis_collector_circuit_breaker_vs_stock_halt():
    # 1. Mock dependencies
    proc_queue = asyncio.Queue()
    repo = MockRepository()
    signals = []
    
    def on_signal(sig):
        signals.append(sig)

    collector = KisCollector(
        processing_queue=proc_queue,
        repository=repo,
        on_signal_callback=on_signal
    )
    
    # Initialize basic states
    collector.status = "RUNNING"
    
    # 2. Simulate individual stock halt (trht_yn == 'Y')
    # message: "0|H0STMKO0|0001|005930^Y^Mock Halt Reason^0000^^^^N^"
    msg_halt = MagicMock()
    msg_halt.type = aiohttp.WSMsgType.TEXT
    msg_halt.data = "0|H0STMKO0|1|005930^Y^Mock Halt Reason^0000^^^^N"
    
    collector._parse_message(msg_halt)
    await asyncio.sleep(0.02) # Allow async task to run
    
    # Check that collector global status remains RUNNING (does not go to SUSPENDED)
    assert collector.status == "RUNNING"
    assert len(repo.system_events) == 1
    assert repo.system_events[0]["event_type"] == "STOCK_SUSPENDED"
    assert repo.system_events[0]["target"] == "005930"
    assert "Mock Halt Reason" in repo.system_events[0]["message"]
    
    assert len(signals) == 1
    assert signals[0]["event_type"] == "STOCK_SUSPENDED"
    
    # 3. Simulate individual stock VI activation (vi_cls_code == '1')
    # This also resumes the stock halt because trht_yn transitions from 'Y' to 'N'
    # There should be 5 carets after 0000 to place '1' at index 8 of the split list
    msg_vi = MagicMock()
    msg_vi.type = aiohttp.WSMsgType.TEXT
    msg_vi.data = "0|H0STMKO0|1|005930^N^Mock Reason^0000^^^^^1"
    
    collector._parse_message(msg_vi)
    await asyncio.sleep(0.02)
    
    assert collector.status == "RUNNING"
    # Should record: 1. STOCK_RESUMED, 2. STOCK_VI_ACTIVATED
    assert len(repo.system_events) == 3
    assert repo.system_events[1]["event_type"] == "STOCK_RESUMED"
    assert repo.system_events[2]["event_type"] == "STOCK_VI_ACTIVATED"
    assert repo.system_events[2]["target"] == "005930"
    
    # 4. Simulate global market halt / Circuit Breaker (mkop_cls_code == '174')
    msg_cb = MagicMock()
    msg_cb.type = aiohttp.WSMsgType.TEXT
    msg_cb.data = "0|H0STMKO0|1|005930^N^Mock Reason^174^^^^N"
    
    collector._parse_message(msg_cb)
    await asyncio.sleep(0.02)
    
    # Global status should now be SUSPENDED
    assert collector.status == "SUSPENDED"
    
    # 5. Simulate global market resumed (mkop_cls_code == '0000' and no halt/vi)
    msg_resume = MagicMock()
    msg_resume.type = aiohttp.WSMsgType.TEXT
    msg_resume.data = "0|H0STMKO0|1|005930^N^Mock Reason^0000^^^^N"
    
    collector._parse_message(msg_resume)
    await asyncio.sleep(0.02)
    
    assert collector.status == "RUNNING"


@pytest.mark.asyncio
async def test_upbit_adapter_caution_alert_parsing():
    adapter = UpbitMarketAdapter()
    
    mock_session = MagicMock()
    
    # Mock /market/all?is_details=true response
    mock_markets_resp = MagicMock()
    mock_markets_resp.status = 200
    mock_markets_resp.json = AsyncMock(return_value=[
        {
            "market": "KRW-BTC",
            "korean_name": "비트코인",
            "english_name": "Bitcoin",
            "market_event": {
                "warning": True,
                "caution": {
                    "PRICE_FLUCTUATIONS": False,
                    "TRADING_VOLUME_SOARING": False,
                    "DEPOSIT_AMOUNT_SOARING": False,
                    "GLOBAL_PRICE_DIFFERENCES": False,
                    "CONCENTRATION_OF_SMALL_ACCOUNTS": False
                }
            }
        },
        {
            "market": "KRW-ETH",
            "korean_name": "이더리움",
            "english_name": "Ethereum",
            "market_event": {
                "warning": False,
                "caution": {
                    "PRICE_FLUCTUATIONS": True,
                    "TRADING_VOLUME_SOARING": False,
                    "DEPOSIT_AMOUNT_SOARING": False,
                    "GLOBAL_PRICE_DIFFERENCES": True,
                    "CONCENTRATION_OF_SMALL_ACCOUNTS": False
                }
            }
        }
    ])
    mock_markets_resp.__aenter__ = AsyncMock(return_value=mock_markets_resp)
    mock_markets_resp.__aexit__ = AsyncMock(return_value=None)
    
    # Mock /ticker response
    mock_ticker_resp = MagicMock()
    mock_ticker_resp.status = 200
    mock_ticker_resp.json = AsyncMock(return_value=[
        {"market": "KRW-BTC", "trade_price": 90000000.0},
        {"market": "KRW-ETH", "trade_price": 5000000.0}
    ])
    mock_ticker_resp.__aenter__ = AsyncMock(return_value=mock_ticker_resp)
    mock_ticker_resp.__aexit__ = AsyncMock(return_value=None)
    
    mock_session.get.side_effect = [mock_markets_resp, mock_ticker_resp]
    
    # Mock stock_mapper
    from src.engine.utils.stock_mapper import stock_mapper
    stock_mapper.get_active_symbols = MagicMock(return_value=["BTC", "ETH"])
    
    dtos = await adapter.fetch_market_data(mock_session, None)
    
    assert len(dtos) == 2
    
    btc = next(d for d in dtos if d.market == "BTC")
    assert btc.is_caution is True
    assert btc.is_alert is False
    assert btc.caution_reasons == ["투자유의"]
    
    eth = next(d for d in dtos if d.market == "ETH")
    assert eth.is_caution is False
    assert eth.is_alert is True
    assert set(eth.caution_reasons) == {"가격 급등락", "글로벌 시세 차이"}


@pytest.mark.asyncio
async def test_bithumb_adapter_caution_alert_parsing():
    adapter = BithumbMarketAdapter()
    
    mock_session = MagicMock()
    mock_system = MagicMock()
    mock_system.config_manager.get.return_value = {"api_url": "https://api.bithumb.com/v1"}
    mock_system.latest_prices = {}
    mock_system.get_latest_price.return_value = {
        "trade_price": 5000000.0,
        "signed_change_rate": 0.01,
        "change_price": 50000.0,
        "acc_trade_price_24h": 1000000.0,
        "high_price": 5100000.0,
        "low_price": 4900000.0
    }
    
    # Mock /market/all?isDetails=true
    mock_markets_resp = MagicMock()
    mock_markets_resp.status = 200
    mock_markets_resp.json = AsyncMock(return_value=[
        {
            "market": "KRW-BTC",
            "korean_name": "비트코인",
            "market_warning": "CAUTION"
        },
        {
            "market": "KRW-ETH",
            "korean_name": "이더리움",
            "market_warning": "NONE"
        }
    ])
    mock_markets_resp.__aenter__ = AsyncMock(return_value=mock_markets_resp)
    mock_markets_resp.__aexit__ = AsyncMock(return_value=None)
    
    # Mock /market/virtual_asset_warning
    mock_warning_resp = MagicMock()
    mock_warning_resp.status = 200
    mock_warning_resp.json = AsyncMock(return_value=[
        {
            "market": "KRW-ETH",
            "warning_type": "PRICE_SUDDEN_FLUCTUATION"
        },
        {
            "market": "KRW-ETH",
            "warning_type": "PRICE_DIFFERENCE_HIGH"
        }
    ])
    mock_warning_resp.__aenter__ = AsyncMock(return_value=mock_warning_resp)
    mock_warning_resp.__aexit__ = AsyncMock(return_value=None)
    
    # Mock /ticker
    mock_ticker_resp = MagicMock()
    mock_ticker_resp.status = 200
    mock_ticker_resp.json = AsyncMock(return_value=[
        {"market": "KRW-BTC", "trade_price": 90000000.0},
        {"market": "KRW-ETH", "trade_price": 5000000.0}
    ])
    mock_ticker_resp.__aenter__ = AsyncMock(return_value=mock_ticker_resp)
    mock_ticker_resp.__aexit__ = AsyncMock(return_value=None)
    
    mock_session.get.side_effect = [mock_markets_resp, mock_warning_resp, mock_ticker_resp]
    
    # Mock stock_mapper
    from src.engine.utils.stock_mapper import stock_mapper
    stock_mapper.get_active_symbols = MagicMock(return_value=["BTC", "ETH"])
    stock_mapper.get_name = MagicMock(return_value="이더리움")
    stock_mapper.add_mapping_async = AsyncMock()
    
    dtos = await adapter.fetch_market_data(mock_session, mock_system)
    
    assert len(dtos) == 2
    
    btc = next(d for d in dtos if d.market == "BTC")
    assert btc.is_caution is True
    assert btc.is_alert is False
    assert btc.caution_reasons == ["투자유의"]
    
    eth = next(d for d in dtos if d.market == "ETH")
    assert eth.is_caution is False
    assert eth.is_alert is True
    assert set(eth.caution_reasons) == {"가격 급등락", "글로벌 시세 차이"}

