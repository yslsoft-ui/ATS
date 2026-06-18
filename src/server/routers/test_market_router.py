import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import Request
from src.server.routers.market import fetch_ranking
from src.engine.utils.stock_mapper import stock_mapper

@pytest.mark.asyncio
@pytest.mark.parametrize("tr_id", ["FHPST01820000", "FHPST01700000", "FHPST01790000", "FHPST02340000"])
async def test_kis_ranking_is_collected(tr_id):
    # 1. stock_mapper에 KIS 활성 종목 수동으로 주입
    if not hasattr(stock_mapper, '_active_symbols'):
        stock_mapper._active_symbols = {}
    stock_mapper._active_symbols['kis'] = {'005930'}  # 삼성전자 수집 중으로 설정
    stock_mapper._mapping['005930'] = '삼성전자'

    # 2. FastAPI Request 객체 Mocking
    request = MagicMock(spec=Request)
    request.app.state.system = MagicMock()
    request.app.state.system.db_path = "dummy_db"
    
    # CredentialProvider Mocking
    async_get_token = AsyncMock(return_value="dummy_token")
    request.app.state.system.cred_provider.get_kis_access_token = async_get_token
    
    def config_mock_get(key, default=None):
        if key == 'exchanges.kis':
            return {
                'app_key': 'key',
                'app_secret': 'secret',
                'api_url': 'https://api.dummy'
            }
        return default
        
    request.app.state.system.config_manager.get.side_effect = config_mock_get

    # 3. KIS OpenAPI Response Mocking (aiohttp.ClientSession.get)
    # output 배열에 stck_shrn_iscd + mksc_shrn_iscd 두 필드를 모두 포함하여
    # 서로 다른 TR_ID (FHPST01700000→stck_shrn_iscd, FHPST01790000→mksc_shrn_iscd)를
    # 하나의 mock으로 커버할 수 있도록 함
    mock_kis_response = {
        "rt_cd": "0",
        "output": [
            {
                "stck_shrn_iscd": "005930",
                "mksc_shrn_iscd": "005930",
                "hts_kor_isnm": "삼성전자",
                "stck_prpr": "70000"
            },
            {
                "stck_shrn_iscd": "000660",
                "mksc_shrn_iscd": "000660",
                "hts_kor_isnm": "SK하이닉스",
                "stck_prpr": "120000"
            }
        ],
        "output2": [
            {
                "mksc_shrn_iscd": "005930",
                "stck_shrn_iscd": "005930",
                "hts_kor_isnm": "삼성전자",
                "stck_prpr": "70000"
            },
            {
                "mksc_shrn_iscd": "000660",
                "stck_shrn_iscd": "000660",
                "hts_kor_isnm": "SK하이닉스",
                "stck_prpr": "120000"
            }
        ]
    }

    # aiohttp ClientSession Mocking
    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.json.return_value = mock_kis_response

    class MockClientSession:
        def __init__(self, *args, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass
        def get(self, url, headers=None, params=None):
            mock_req = AsyncMock()
            mock_req.__aenter__ = AsyncMock(return_value=mock_resp)
            return mock_req

    # 4. API 실행 및 결과 검증
    with patch('aiohttp.ClientSession', MockClientSession), \
         patch('src.engine.utils.stock_mapper.stock_mapper.add_mapping_async', AsyncMock()):
        
        result = await fetch_ranking(request, tr_id=tr_id)
        
        data = result["data"]
        assert len(data) == 2
        
        # 삼성전자 (005930)는 active_symbols에 있으므로 is_collected가 True여야 함
        samsung = next(x for x in data if x["code"] == "005930")
        assert samsung["is_collected"] is True, f"삼성전자는 수집 중이어야 합니다 (tr_id: {tr_id})"
        
        # SK하이닉스 (000660)는 active_symbols에 없으므로 is_collected가 False여야 함
        hynix = next(x for x in data if x["code"] == "000660")
        assert hynix["is_collected"] is False, f"SK하이닉스는 수집 중이 아니어야 합니다 (tr_id: {tr_id})"


from src.server.routers.market import get_exchange_orderbook, place_exchange_order, RealOrderRequest

@pytest.mark.asyncio
async def test_get_exchange_orderbook():
    request = MagicMock(spec=Request)
    request.app.state.system = MagicMock()
    
    mock_orderbook_response = [
        {
            "market": "KRW-BTC",
            "timestamp": 123456789,
            "total_ask_size": 1.0,
            "total_bid_size": 2.0,
            "orderbook_units": [
                {"ask_price": 50000000, "bid_price": 49000000, "ask_size": 0.1, "bid_size": 0.2}
            ]
        }
    ]
    
    mock_ticker_response = [
        {
            "market": "KRW-BTC",
            "trade_price": 49500000.0,
            "signed_change_rate": 0.01,
            "signed_change_price": 500000.0
        }
    ]

    class MockClientSession:
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass
        def get(self, url, **kwargs):
            mock_resp = AsyncMock()
            mock_resp.status = 200
            if "orderbook" in url:
                mock_resp.json.return_value = mock_orderbook_response
            else:
                mock_resp.json.return_value = mock_ticker_response
                
            mock_req = AsyncMock()
            mock_req.__aenter__ = AsyncMock(return_value=mock_resp)
            return mock_req

    with patch('aiohttp.ClientSession', MockClientSession):
        res = await get_exchange_orderbook(request, exchange_id="upbit", symbol="BTC")
        assert res["orderbook"]["market"] == "KRW-BTC"
        assert res["trade_price"] == 49500000.0
        assert len(res["orderbook"]["orderbook_units"]) == 1

@pytest.mark.asyncio
async def test_place_exchange_order_limit():
    request = MagicMock(spec=Request)
    request.app.state.system = MagicMock()
    
    # stock_mapper에 Upbit 활성 종목 추가
    if not hasattr(stock_mapper, '_active_symbols'):
        stock_mapper._active_symbols = {}
    stock_mapper._active_symbols['upbit'] = {'BTC'}

    body = RealOrderRequest(
        symbol="BTC",
        side="BUY",
        price=50000000.0,
        volume=0.001,
        order_type="limit"
    )

    mock_order_response = {
        "uuid": "dummy-uuid",
        "side": "bid",
        "ord_type": "limit",
        "price": "50000000.0",
        "volume": "0.001",
        "state": "wait"
    }

    mock_resp = AsyncMock()
    mock_resp.status = 201
    mock_resp.json.return_value = mock_order_response

    class MockClientSession:
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass
        def post(self, url, json=None, headers=None, **kwargs):
            mock_req = AsyncMock()
            mock_req.__aenter__ = AsyncMock(return_value=mock_resp)
            return mock_req

    with patch('aiohttp.ClientSession', MockClientSession), \
         patch('os.getenv', return_value="dummy_key"):
        res = await place_exchange_order(request, exchange_id="upbit", body=body)
        assert res["uuid"] == "dummy-uuid"
        assert res["ord_type"] == "limit"

from src.server.routers.market import get_exchange_orders

@pytest.mark.asyncio
async def test_get_exchange_orders(tmp_path):
    db_file = tmp_path / "test_ats.db"
    
    request = MagicMock(spec=Request)
    request.app.state.system = MagicMock()
    request.app.state.system.db_path = str(db_file)
    request.app.state.system.config_manager.get.return_value = 100
    
    # 임시 DB에 real_orders 테이블 생성 및 테스트용 모의 거래 기록 적재
    import aiosqlite
    async with aiosqlite.connect(str(db_file)) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS real_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                exchange_id TEXT NOT NULL,
                uuid TEXT UNIQUE NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                price REAL DEFAULT 0.0,
                volume REAL DEFAULT 0.0,
                executed_volume REAL DEFAULT 0.0,
                fee REAL DEFAULT 0.0,
                state TEXT NOT NULL,
                created_at DATETIME,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            INSERT INTO real_orders 
            (exchange_id, uuid, symbol, side, price, volume, executed_volume, fee, state, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            "upbit",
            "dummy-order-uuid",
            "BTC",
            "BUY",
            50000000.0,
            0.001,
            0.001,
            0.0,
            "done",
            "2023-05-10T12:00:00+09:00"
        ))
        await db.commit()
        
    res = await get_exchange_orders(request, exchange_id="upbit", symbol="BTC")
    assert len(res) == 1
    assert res[0]["uuid"] == "dummy-order-uuid"
    assert res[0]["side"] == "BUY"
    assert res[0]["state"] == "done"


from src.server.routers.market import get_kis_symbol_detail

@pytest.mark.asyncio
async def test_get_kis_symbol_detail_from_db(tmp_path):
    db_file = tmp_path / "test_ats.db"
    request = MagicMock(spec=Request)
    request.app.state.system = MagicMock()
    request.app.state.system.db_path = str(db_file)

    # 임시 DB에 테이블 생성 및 미리 메타데이터 적재
    import aiosqlite
    async with aiosqlite.connect(str(db_file)) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS kis_stock_info (
                symbol TEXT PRIMARY KEY,
                prdt_name TEXT,
                prdt_abrv_name TEXT,
                mket_id_cd TEXT,
                scty_grp_id_cd TEXT,
                excg_dvsn_cd TEXT,
                lstg_stqt INTEGER,
                lstg_cptl_amt INTEGER,
                cpta INTEGER,
                papr REAL,
                issu_pric REAL,
                kospi200_item_yn TEXT,
                scts_mket_lstg_dt TEXT,
                kosdaq_mket_lstg_dt TEXT,
                lstg_abol_dt TEXT,
                std_pdno TEXT,
                prdt_eng_name TEXT,
                tr_stop_yn TEXT,
                admn_item_yn TEXT,
                thdt_clpr REAL,
                bfdy_clpr REAL,
                std_idst_clsf_cd_name TEXT,
                idx_bztp_lcls_cd_name TEXT,
                idx_bztp_mcls_cd_name TEXT,
                idx_bztp_scls_cd_name TEXT,
                cptt_trad_tr_psbl_yn TEXT,
                nxt_tr_stop_yn TEXT,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            INSERT INTO kis_stock_info (symbol, prdt_name, prdt_abrv_name, cptt_trad_tr_psbl_yn, nxt_tr_stop_yn)
            VALUES (?, ?, ?, ?, ?)
        """, ("047040", "대우건설보통주", "대우건설", "N", "N"))
        await db.commit()

    res = await get_kis_symbol_detail(request, symbol="047040")
    assert res["symbol"] == "047040"
    assert res["prdt_abrv_name"] == "대우건설"
    assert res["cptt_trad_tr_psbl_yn"] == "N"


@pytest.mark.asyncio
async def test_get_kis_symbol_detail_from_api_fallback(tmp_path):
    db_file = tmp_path / "test_ats.db"
    request = MagicMock(spec=Request)
    request.app.state.system = MagicMock()
    request.app.state.system.db_path = str(db_file)
    
    # CredentialProvider Mocking
    request.app.state.system.cred_provider.get_kis_access_token = AsyncMock(return_value="mock_token")
    request.app.state.system.config_manager.get.return_value = {
        'app_key': 'mock_app_key',
        'app_secret': 'mock_app_secret',
        'api_url': 'https://api.dummy'
    }

    # 임시 DB에 테이블 생성만 함 (데이터는 없음)
    import aiosqlite
    async with aiosqlite.connect(str(db_file)) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS kis_stock_info (
                symbol TEXT PRIMARY KEY,
                prdt_name TEXT,
                prdt_abrv_name TEXT,
                mket_id_cd TEXT,
                scty_grp_id_cd TEXT,
                excg_dvsn_cd TEXT,
                lstg_stqt INTEGER,
                lstg_cptl_amt INTEGER,
                cpta INTEGER,
                papr REAL,
                issu_pric REAL,
                kospi200_item_yn TEXT,
                scts_mket_lstg_dt TEXT,
                kosdaq_mket_lstg_dt TEXT,
                lstg_abol_dt TEXT,
                std_pdno TEXT,
                prdt_eng_name TEXT,
                tr_stop_yn TEXT,
                admn_item_yn TEXT,
                thdt_clpr REAL,
                bfdy_clpr REAL,
                std_idst_clsf_cd_name TEXT,
                idx_bztp_lcls_cd_name TEXT,
                idx_bztp_mcls_cd_name TEXT,
                idx_bztp_scls_cd_name TEXT,
                cptt_trad_tr_psbl_yn TEXT,
                nxt_tr_stop_yn TEXT,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.commit()

    # KIS API Mock 응답
    mock_api_response = {
        "rt_cd": "0",
        "output": {
            "pdno": "005930",
            "prdt_name": "삼성전자보통주",
            "prdt_abrv_name": "삼성전자",
            "cptt_trad_tr_psbl_yn": "Y",
            "nxt_tr_stop_yn": "N"
        },
        "msg1": "정상처리"
    }

    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.json.return_value = mock_api_response

    class MockClientSession:
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass
        def get(self, url, **kwargs):
            mock_req = AsyncMock()
            mock_req.__aenter__ = AsyncMock(return_value=mock_resp)
            return mock_req

    with patch('aiohttp.ClientSession', MockClientSession):
        res = await get_kis_symbol_detail(request, symbol="005930")
        assert res["prdt_abrv_name"] == "삼성전자"
        assert res["cptt_trad_tr_psbl_yn"] == "Y"

        # DB에 캐싱이 완료되었는지 확인
        async with aiosqlite.connect(str(db_file)) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM kis_stock_info WHERE symbol = ?", ("005930",)) as cursor:
                row = await cursor.fetchone()
                assert row is not None
                assert row["prdt_abrv_name"] == "삼성전자"
                assert row["cptt_trad_tr_psbl_yn"] == "Y"


from src.server.routers.market import get_exchange_outstanding_orders, cancel_exchange_order, CancelOrderRequest

@pytest.mark.asyncio
async def test_get_exchange_outstanding_orders_upbit():
    request = MagicMock(spec=Request)
    request.app.state.system = MagicMock()
    
    mock_outstanding_response = [
        {
            "uuid": "upbit-order-uuid",
            "market": "KRW-BTC",
            "side": "bid",
            "price": "50000000.0",
            "volume": "0.01",
            "remaining_volume": "0.01",
            "executed_volume": "0.0",
            "created_at": "2026-06-18T23:17:00+09:00"
        }
    ]
    
    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.json.return_value = mock_outstanding_response
    
    class MockClientSession:
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass
        def get(self, url, **kwargs):
            mock_req = AsyncMock()
            mock_req.__aenter__ = AsyncMock(return_value=mock_resp)
            return mock_req
            
    with patch('aiohttp.ClientSession', MockClientSession), \
         patch('os.getenv', return_value="dummy_key"):
        res = await get_exchange_outstanding_orders(request, exchange_id="upbit", symbol="BTC")
        assert len(res) == 1
        assert res[0]["uuid"] == "upbit-order-uuid"
        assert res[0]["side"] == "BUY"
        assert res[0]["remaining_volume"] == 0.01
        assert res[0]["is_reservation"] is False
        assert res[0]["state"] == "wait"


@pytest.mark.asyncio
async def test_get_exchange_outstanding_orders_kis():
    request = MagicMock(spec=Request)
    request.app.state.system = MagicMock()
    request.app.state.system.cred_provider.get_kis_access_token = AsyncMock(return_value="mock_token")
    request.app.state.system.config_manager.get.side_effect = lambda key, default=None: {
        'app_key': 'mock_key',
        'app_secret': 'mock_secret',
        'account_no': '12345678-01',
        'api_url': 'https://openapi.koreainvestment.com:9443'
    } if key == 'exchanges.kis' else default

    mock_daily_ccld = {
        "rt_cd": "0",
        "output1": [
            {
                "odno": "kis-normal-odno",
                "pdno": "005930",
                "sll_buy_dvsn_cd": "02",
                "ord_unpr": "75000",
                "ord_qty": "10",
                "rmn_qty": "10",
                "tot_ccld_qty": "0",
                "ord_dt": "20260618",
                "ord_tmd": "133000",
                "excg_id_dvsn_cd": "KRX"
            }
        ]
    }
    
    mock_resv_ccnl = {
        "rt_cd": "0",
        "output": [
            {
                "rsvn_ord_seq": "kis-resv-seq",
                "pdno": "005930",
                "sll_buy_dvsn_cd": "02",
                "ord_rsvn_unpr": "74000",
                "ord_rsvn_qty": "5",
                "rsvn_ord_rcit_dt": "20260618",
                "rsvn_ord_rcit_tmd": "203000",
                "rsvn_ord_ord_dt": "20260619"
            }
        ]
    }

    class MockClientSession:
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass
        def get(self, url, **kwargs):
            mock_resp = AsyncMock()
            mock_resp.status = 200
            if "inquire-daily-ccld" in url:
                mock_resp.json.return_value = mock_daily_ccld
            elif "order-resv-ccnl" in url:
                mock_resp.json.return_value = mock_resv_ccnl
            
            mock_req = AsyncMock()
            mock_req.__aenter__ = AsyncMock(return_value=mock_resp)
            return mock_req

    with patch('aiohttp.ClientSession', MockClientSession):
        res = await get_exchange_outstanding_orders(request, exchange_id="kis", symbol="005930")
        assert len(res) == 2
        
        normal = next(r for r in res if not r["is_reservation"])
        assert normal["uuid"] == "kis-normal-odno"
        assert normal["remaining_volume"] == 10.0
        assert normal["state"] == "wait"
        
        resv = next(r for r in res if r["is_reservation"])
        assert resv["uuid"] == "kis-resv-seq"
        assert resv["remaining_volume"] == 5.0
        assert resv["rsvn_ord_ord_dt"] == "20260619"
        assert resv["state"] == "wait"


@pytest.mark.asyncio
async def test_get_exchange_outstanding_orders_with_cancelled(tmp_path):
    db_file = tmp_path / "test_ats.db"
    request = MagicMock(spec=Request)
    request.app.state.system = MagicMock()
    request.app.state.system.db_path = str(db_file)
    
    # Setup database with cancelled order
    import aiosqlite
    async with aiosqlite.connect(str(db_file)) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS real_orders (
                exchange_id TEXT,
                uuid TEXT PRIMARY KEY,
                symbol TEXT,
                side TEXT,
                price REAL,
                volume REAL,
                executed_volume REAL,
                fee REAL,
                state TEXT,
                created_at TEXT,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            INSERT INTO real_orders (exchange_id, uuid, symbol, side, price, volume, executed_volume, fee, state, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, ("upbit", "cancelled-uuid", "BTC", "SELL", 52000000.0, 0.02, 0.0, 0.0, "cancel", "2026-06-18 23:20:00"))
        await db.commit()

    mock_outstanding_response = [
        {
            "uuid": "upbit-active-uuid",
            "market": "KRW-BTC",
            "side": "bid",
            "price": "50000000.0",
            "volume": "0.01",
            "remaining_volume": "0.01",
            "executed_volume": "0.0",
            "created_at": "2026-06-18T23:17:00+09:00"
        }
    ]
    
    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.json.return_value = mock_outstanding_response
    
    class MockClientSession:
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass
        def get(self, url, **kwargs):
            mock_req = AsyncMock()
            mock_req.__aenter__ = AsyncMock(return_value=mock_resp)
            return mock_req
            
    with patch('aiohttp.ClientSession', MockClientSession), \
         patch('os.getenv', return_value="dummy_key"):
        res = await get_exchange_outstanding_orders(request, exchange_id="upbit", symbol="BTC")
        # Should return both active and cancelled order
        assert len(res) == 2
        
        active = next(o for o in res if o["state"] == "wait")
        assert active["uuid"] == "upbit-active-uuid"
        
        cancelled = next(o for o in res if o["state"] == "cancel")
        assert cancelled["uuid"] == "cancelled-uuid"
        assert cancelled["remaining_volume"] == 0.0


@pytest.mark.asyncio
async def test_cancel_exchange_order_upbit(tmp_path):
    db_file = tmp_path / "test_ats.db"
    
    request = MagicMock(spec=Request)
    request.app.state.system = MagicMock()
    request.app.state.system.db_path = str(db_file)
    
    import aiosqlite
    async with aiosqlite.connect(str(db_file)) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS real_orders (
                exchange_id TEXT,
                uuid TEXT PRIMARY KEY,
                symbol TEXT,
                side TEXT,
                price REAL,
                volume REAL,
                executed_volume REAL,
                fee REAL,
                state TEXT,
                created_at TEXT
            )
        """)
        await db.execute("INSERT INTO real_orders VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         ("upbit", "cancel-uuid", "BTC", "BUY", 50000000.0, 0.01, 0.0, 0.0, "wait", "2026-06-18"))
        await db.commit()

    body = CancelOrderRequest(
        uuid="cancel-uuid",
        symbol="BTC",
        is_reservation=False
    )
    
    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.json.return_value = {"uuid": "cancel-uuid", "state": "cancel"}
    
    class MockClientSession:
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass
        def delete(self, url, **kwargs):
            mock_req = AsyncMock()
            mock_req.__aenter__ = AsyncMock(return_value=mock_resp)
            return mock_req

    with patch('aiohttp.ClientSession', MockClientSession), \
         patch('os.getenv', return_value="dummy_key"):
        res = await cancel_exchange_order(request, exchange_id="upbit", body=body)
        assert res["uuid"] == "cancel-uuid"
        
        async with aiosqlite.connect(str(db_file)) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT state FROM real_orders WHERE uuid = ?", ("cancel-uuid",)) as cursor:
                row = await cursor.fetchone()
                assert row is not None
                assert row["state"] == "cancel"


@pytest.mark.asyncio
async def test_cancel_exchange_order_kis(tmp_path):
    db_file = tmp_path / "test_ats.db"
    
    request = MagicMock(spec=Request)
    request.app.state.system = MagicMock()
    request.app.state.system.db_path = str(db_file)
    request.app.state.system.cred_provider.get_kis_access_token = AsyncMock(return_value="mock_token")
    request.app.state.system.config_manager.get.side_effect = lambda key, default=None: {
        'app_key': 'mock_key',
        'app_secret': 'mock_secret',
        'account_no': '12345678-01',
        'api_url': 'https://openapi.koreainvestment.com:9443'
    } if key == 'exchanges.kis' else default

    import aiosqlite
    async with aiosqlite.connect(str(db_file)) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS real_orders (
                exchange_id TEXT,
                uuid TEXT PRIMARY KEY,
                symbol TEXT,
                side TEXT,
                price REAL,
                volume REAL,
                executed_volume REAL,
                fee REAL,
                state TEXT,
                created_at TEXT
            )
        """)
        await db.execute("INSERT OR IGNORE INTO real_orders VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         ("kis", "kis-cancel-uuid", "005930", "BUY", 75000.0, 10.0, 0.0, 0.0, "wait", "2026-06-18"))
        await db.commit()

    body = CancelOrderRequest(
        uuid="kis-cancel-uuid",
        symbol="005930",
        is_reservation=False,
        excg_id_dvsn_cd="KRX"
    )
    
    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.json.return_value = {"rt_cd": "0", "msg1": "정상처리", "output": {"ODNO": "kis-cancel-uuid"}}
    
    class MockClientSession:
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass
        def post(self, url, json=None, headers=None, **kwargs):
            mock_req = AsyncMock()
            mock_req.__aenter__ = AsyncMock(return_value=mock_resp)
            return mock_req

    with patch('aiohttp.ClientSession', MockClientSession):
        res = await cancel_exchange_order(request, exchange_id="kis", body=body)
        assert res["rt_cd"] == "0"
        
        async with aiosqlite.connect(str(db_file)) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT state FROM real_orders WHERE uuid = ?", ("kis-cancel-uuid",)) as cursor:
                row = await cursor.fetchone()
                assert row is not None
                assert row["state"] == "cancel"


