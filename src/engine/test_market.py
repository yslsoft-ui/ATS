import pytest
import aiohttp
from unittest.mock import AsyncMock, MagicMock, patch
from src.engine.market.dto import MarketTickerDTO
from src.engine.market.upbit import UpbitMarketAdapter
from src.engine.market.bithumb import BithumbMarketAdapter
from src.engine.market.kis import KisMarketAdapter

@pytest.mark.asyncio
async def test_upbit_market_adapter():
    adapter = UpbitMarketAdapter()
    
    # aiohttp session mock
    session_mock = MagicMock(spec=aiohttp.ClientSession)
    
    # Mocking Upbit api responses
    mock_market_all = [
        {"market": "KRW-BTC", "korean_name": "비트코인"},
        {"market": "BTC-ETH", "korean_name": "이더리움"} # KRW로 시작 안함
    ]
    mock_tickers = [
        {
            "market": "KRW-BTC",
            "trade_price": 50000000.0,
            "signed_change_rate": 0.05,
            "acc_trade_price_24h": 1000000000.0,
            "high_price": 51000000.0,
            "low_price": 49000000.0
        }
    ]
    
    resp_market_all = AsyncMock()
    resp_market_all.status = 200
    resp_market_all.json.return_value = mock_market_all
    
    resp_ticker = AsyncMock()
    resp_ticker.status = 200
    resp_ticker.json.return_value = mock_tickers
    
    # sequential get
    session_mock.get.side_effect = [
        AsyncMock(__aenter__=AsyncMock(return_value=resp_market_all)),
        AsyncMock(__aenter__=AsyncMock(return_value=resp_ticker))
    ]
    
    system_mock = MagicMock()
    
    dtos = await adapter.fetch_market_data(session_mock, system_mock)
    
    assert len(dtos) == 1
    dto = dtos[0]
    assert isinstance(dto, MarketTickerDTO)
    assert dto.exchange == "upbit"
    assert dto.market == "BTC"
    assert dto.korean_name == "비트코인"
    assert dto.trade_price == 50000000.0
    assert dto.signed_change_rate == 0.05

@pytest.mark.asyncio
async def test_bithumb_market_adapter():
    adapter = BithumbMarketAdapter()
    
    session_mock = MagicMock(spec=aiohttp.ClientSession)
    
    mock_market_all = [
        {"market": "KRW-ETH", "korean_name": "이더리움"}
    ]
    mock_tickers = [
        {
            "market": "KRW-ETH",
            "trade_price": 3000000.0,
            "signed_change_rate": -0.02,
            "acc_trade_price_24h": 500000000.0,
            "high_price": 3100000.0,
            "low_price": 2950000.0,
            "timestamp": 123456789
        }
    ]
    
    resp_market_all = AsyncMock()
    resp_market_all.status = 200
    resp_market_all.json.return_value = mock_market_all
    
    resp_ticker = AsyncMock()
    resp_ticker.status = 200
    resp_ticker.json.return_value = mock_tickers
    
    session_mock.get.side_effect = [
        AsyncMock(__aenter__=AsyncMock(return_value=resp_market_all)),
        AsyncMock(__aenter__=AsyncMock(return_value=resp_ticker))
    ]
    
    system_mock = MagicMock()
    def mock_get(key, default=None):
        if key == 'exchanges.bithumb':
            return {"api_url": "https://api.bithumb.com/v1"}
        return default
    system_mock.config_manager.get.side_effect = mock_get
    system_mock.latest_prices = {}
    system_mock.get_latest_price.return_value = {
        'trade_price': 3000000.0,
        'signed_change_rate': -0.02,
        'acc_trade_price_24h': 500000000.0,
        'high_price': 3100000.0,
        'low_price': 2950000.0
    }
    
    dtos = await adapter.fetch_market_data(session_mock, system_mock)
    
    assert len(dtos) >= 1
    eth_dtos = [d for d in dtos if d.market == "ETH"]
    assert len(eth_dtos) == 1
    dto = eth_dtos[0]
    assert isinstance(dto, MarketTickerDTO)
    assert dto.exchange == "bithumb"
    assert dto.market == "ETH"
    assert dto.trade_price == 3000000.0


@pytest.mark.asyncio
async def test_kis_market_adapter():
    adapter = KisMarketAdapter()
    
    session_mock = MagicMock(spec=aiohttp.ClientSession)
    system_mock = MagicMock()
    system_mock.config_manager.get.return_value = ["005930"]
    system_mock.latest_prices = {}
    system_mock.get_latest_price.return_value = {
        'trade_price': 70000.0,
        'signed_change_rate': 0.01,
        'acc_trade_price_24h': 10000000000.0,
        'high_price': 71000.0,
        'low_price': 69500.0
    }
    
    with patch('src.engine.utils.stock_mapper.stock_mapper.fetch_and_add_kis_symbol', return_value="삼성전자"):
        dtos = await adapter.fetch_market_data(session_mock, system_mock)
        
    assert len(dtos) >= 1
    kis_dtos = [d for d in dtos if d.market == "005930"]
    assert len(kis_dtos) == 1
    dto = kis_dtos[0]
    assert isinstance(dto, MarketTickerDTO)
    assert dto.exchange == "kis"
    assert dto.market == "005930"
    assert dto.korean_name == "삼성전자"
    assert dto.trade_price == 70000.0

