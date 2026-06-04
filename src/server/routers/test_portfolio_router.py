import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import Request, HTTPException
import aiosqlite
from src.server.routers.portfolio import get_upbit_assets
from src.server.routers.market import get_exchange_orders
from src.engine.utils.stock_mapper import stock_mapper

@pytest.mark.asyncio
async def test_get_upbit_assets_liquidated(tmp_path):
    db_file = tmp_path / "test_ats_portfolio.db"
    
    # 1. FastAPI Request 객체 Mocking
    request = MagicMock(spec=Request)
    request.app.state.system = MagicMock()
    request.app.state.system.db_path = str(db_file)
    def mock_get(key, default=None):
        if 'api_url' in key:
            return "https://api.upbit.com"
        return default
    request.app.state.system.config_manager.get.side_effect = mock_get

    # stock_mapper에 테스트용 종목 및 한글명 세팅
    if not hasattr(stock_mapper, '_active_symbols'):
        stock_mapper._active_symbols = {}
    stock_mapper._active_symbols['upbit'] = {'KAVA', 'A', 'VTHO'}
    stock_mapper._mapping = {
        'KAVA': '카바',
        'A': '어거',
        'VTHO': '비체토르'
    }

    # 2. 임시 DB 구축 및 데이터 적재
    async with aiosqlite.connect(str(db_file)) as db:
        # real_orders 테이블 생성
        await db.execute("""
            CREATE TABLE IF NOT EXISTS real_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                exchange TEXT NOT NULL,
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
        
        # KAVA 매수 주문 (체결가 1000, 10개) -> 처분 완료 자산 매각가 쿼리에서 스킵되어야 함
        await db.execute("""
            INSERT INTO real_orders 
            (exchange, uuid, symbol, side, price, volume, executed_volume, fee, state, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, ("upbit", "kava-buy-uuid", "KAVA", "BUY", 1000.0, 10.0, 10.0, 5.0, "done", "2023-05-10T12:00:00+09:00"))
        
        # KAVA 매도 주문 (체결가 1500, 10개) -> 처분 완료 자산 매각가로 확인되어야 함
        await db.execute("""
            INSERT INTO real_orders 
            (exchange, uuid, symbol, side, price, volume, executed_volume, fee, state, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, ("upbit", "kava-sell-uuid", "KAVA", "SELL", 1500.0, 10.0, 10.0, 7.5, "done", "2023-05-10T12:30:00+09:00"))

        # VTHO 최종 분할 매도 주문 1 (이전 시각)
        await db.execute("""
            INSERT INTO real_orders 
            (exchange, uuid, symbol, side, price, volume, executed_volume, fee, state, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, ("upbit", "vtho-sell-uuid-old", "VTHO", "SELL", 5.0, 1000.0, 1000.0, 2.5, "done", "2023-05-10T11:00:00+09:00"))

        # VTHO 최종 매도 주문 2 (최신 시각, 체결가 8.0, 2000개)
        await db.execute("""
            INSERT INTO real_orders 
            (exchange, uuid, symbol, side, price, volume, executed_volume, fee, state, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, ("upbit", "vtho-sell-uuid-new", "VTHO", "SELL", 8.0, 2000.0, 2000.0, 8.0, "done", "2023-05-10T13:00:00+09:00"))

        await db.commit()

    # 3. 업비트 계좌 API Response Mocking
    # KAVA와 VTHO는 balance가 0.0인 상태로 accounts에 전달되어, liquidated 자산에 편입됨
    mock_accounts_response = [
        {"currency": "KRW", "balance": "1000000.0", "locked": "0.0", "avg_buy_price": "0.0"},
        {"currency": "KAVA", "balance": "0.0", "locked": "0.0", "avg_buy_price": "0.0"},
        {"currency": "VTHO", "balance": "0.0", "locked": "0.0", "avg_buy_price": "0.0"}
    ]

    mock_ticker_response = [
        {"market": "KRW-KAVA", "trade_price": 1400.0},
        {"market": "KRW-VTHO", "trade_price": 7.5}
    ]

    class MockClientSession:
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass
        def get(self, url, **kwargs):
            mock_resp = MagicMock()
            mock_resp.status = 200
            
            if "accounts" in url:
                mock_resp.json = AsyncMock(return_value=mock_accounts_response)
            elif "ticker" in url:
                mock_resp.json = AsyncMock(return_value=mock_ticker_response)
            else:
                mock_resp.json = AsyncMock(return_value={})
                
            mock_req = AsyncMock()
            mock_req.__aenter__ = AsyncMock(return_value=mock_resp)
            return mock_req

    # patch 설정 후 API 실행
    with patch('aiohttp.ClientSession', MockClientSession), \
         patch('os.getenv', return_value="dummy_key"), \
         patch('src.database.connection.DB_PATH', str(db_file)):
         
        # liquidated 모드로 API 호출
        result = await get_upbit_assets(request, mode="liquidated", sync=False)
        
        assets = result["assets"]
        # 처분된 자산 KAVA, VTHO 두 종목이 포함되어 있어야 함
        assert len(assets) == 2
        
        # KAVA 검증
        kava = next(x for x in assets if x["currency"] == "KAVA")
        # 현재는 0으로 하드코딩 되어 있으나 개선 후에는 최종 매도 주문(1500) 및 매도 총액(1500 * 10 = 15000)이 나와야 함
        assert kava["avg_buy_price"] == 1500.0
        assert kava["eval_value"] == 15000.0
        assert kava["formatted_eval_value"] == "15,000"

        # VTHO 검증
        vtho = next(x for x in assets if x["currency"] == "VTHO")
        # 분할 매도 중 최신 매도 주문(8.0) 및 매도 총액(8.0 * 2000 = 16000)이 나와야 함
        assert vtho["avg_buy_price"] == 8.0
        assert vtho["eval_value"] == 16000.0
        assert vtho["formatted_eval_value"] == "16,000"


@pytest.mark.asyncio
async def test_sync_real_orders_on_conflict(tmp_path):
    db_file = tmp_path / "test_ats_sync.db"
    
    # 1. 임시 DB 구축 및 초기 wait 상태 레코드 적재
    async with aiosqlite.connect(str(db_file)) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS real_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                exchange TEXT NOT NULL,
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
        # 초기 KAVA 매도 주문 상태는 wait이고 executed_volume = 0.0임
        await db.execute("""
            INSERT INTO real_orders 
            (exchange, uuid, symbol, side, price, volume, executed_volume, fee, state, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, ("upbit", "kava-uuid", "KAVA", "SELL", 0.0, 226.5, 0.0, 0.0, "wait", "2026-06-01T16:09:43+09:00"))
        await db.commit()

    # 2. 동기화 타겟 Mocking
    # 업비트 API에서 체결 완료 상태(done) 및 체결 수량(226.5)으로 반환됨
    mock_api_orders = [
        {
            "uuid": "kava-uuid",
            "side": "ask",
            "avg_price": "82.5587",
            "volume": "226.50056625",
            "executed_volume": "226.50056625",
            "paid_fee": "9.35",
            "state": "done",
            "created_at": "2026-06-01T16:09:43+09:00",
            "market": "KRW-KAVA"
        }
    ]

    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(return_value=mock_api_orders)

    class MockClientSession:
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass
        def get(self, url, **kwargs):
            mock_req = AsyncMock()
            mock_req.__aenter__ = AsyncMock(return_value=mock_resp)
            return mock_req

    from src.server.routers.portfolio import _sync_real_orders

    with patch('aiohttp.ClientSession', MockClientSession), \
         patch('src.database.connection.DB_PATH', str(db_file)):
         
        # sync 실행 (force_sync=True)
        await _sync_real_orders("access", "secret", "https://api.upbit.com", force_sync=True)

        # 3. DB 결과 검증
        async with aiosqlite.connect(str(db_file)) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM real_orders WHERE uuid = 'kava-uuid'") as cursor:
                row = await cursor.fetchone()
                assert row is not None
                assert row["state"] == "done"
                # 핵심 검증: ON CONFLICT 시 executed_volume과 volume이 업데이트되어야 함
                assert row["executed_volume"] == 226.50056625
                assert row["volume"] == 226.50056625
                assert row["price"] == 82.5587


@pytest.mark.asyncio
async def test_sync_real_orders_include_partially_executed_cancel(tmp_path):
    db_file = tmp_path / "test_ats_cancel_sync.db"
    
    # 1. FastAPI Request 객체 Mocking
    request = MagicMock(spec=Request)
    request.app.state.system = MagicMock()
    request.app.state.system.db_path = str(db_file)
    def mock_get(key, default=None):
        if 'api_url' in key:
            return "https://api.upbit.com"
        return default
    request.app.state.system.config_manager.get.side_effect = mock_get

    # stock_mapper에 테스트용 종목 및 한글명 세팅
    if not hasattr(stock_mapper, '_active_symbols'):
        stock_mapper._active_symbols = {}
    stock_mapper._active_symbols['upbit'] = {'ETH'}
    stock_mapper._mapping = {'ETH': '이더리움'}

    # 2. 임시 DB 구축 (빈 상태)
    async with aiosqlite.connect(str(db_file)) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS real_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                exchange TEXT NOT NULL,
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
        await db.commit()

    # 3. API 응답 Mocking
    # 하나는 완전 미체결 취소 주문 (executed_volume = 0.0) -> 스킵되어야 함
    # 다른 하나는 부분 체결 취소 주문 (executed_volume = 1.8) -> DB에 저장되어야 함
    mock_api_orders = [
        {
            "uuid": "eth-cancel-executed",
            "side": "ask",
            "avg_price": "2700000.0",
            "volume": "2.0",
            "executed_volume": "1.8",
            "paid_fee": "2700.0",
            "state": "cancel",
            "created_at": "2026-06-01T15:52:33+09:00",
            "market": "KRW-ETH"
        },
        {
            "uuid": "eth-cancel-unexecuted",
            "side": "ask",
            "avg_price": "0.0",
            "volume": "1.0",
            "executed_volume": "0.0",
            "paid_fee": "0.0",
            "state": "cancel",
            "created_at": "2026-06-01T15:55:00+09:00",
            "market": "KRW-ETH"
        }
    ]

    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(return_value=mock_api_orders)

    # get_upbit_assets 내부의 accounts API 및 ticker API 결과 모킹
    mock_accounts_response = [
        {"currency": "KRW", "balance": "1000000.0", "locked": "0.0", "avg_buy_price": "0.0"},
        {"currency": "ETH", "balance": "0.0", "locked": "0.0", "avg_buy_price": "0.0"}
    ]
    mock_ticker_response = [
        {"market": "KRW-ETH", "trade_price": 2800000.0}
    ]

    class MockClientSession:
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass
        def get(self, url, **kwargs):
            m_resp = MagicMock()
            m_resp.status = 200
            if "accounts" in url:
                m_resp.json = AsyncMock(return_value=mock_accounts_response)
            elif "ticker" in url:
                m_resp.json = AsyncMock(return_value=mock_ticker_response)
            else:
                if "state=cancel" in url and "page=1" in url:
                    m_resp.json = AsyncMock(return_value=mock_api_orders)
                else:
                    m_resp.json = AsyncMock(return_value=[])
                
            mock_req = AsyncMock()
            mock_req.__aenter__ = AsyncMock(return_value=m_resp)
            return mock_req

    with patch('aiohttp.ClientSession', MockClientSession), \
         patch('os.getenv', return_value="dummy_key"), \
         patch('src.database.connection.DB_PATH', str(db_file)):
         
        # liquidated 모드로 API 호출하여 동기화 및 자산 목록 산출 실행
        result = await get_upbit_assets(request, mode="liquidated", sync=False)
        
        # 4. DB 검증
        async with aiosqlite.connect(str(db_file)) as db:
            db.row_factory = aiosqlite.Row
            # 부분 체결된 취소 주문은 DB에 존재해야 함
            async with db.execute("SELECT * FROM real_orders WHERE uuid = 'eth-cancel-executed'") as cursor:
                row = await cursor.fetchone()
                assert row is not None
                assert row["executed_volume"] == 1.8
                assert row["state"] == "cancel"
            
            # 미체결 취소 주문은 DB에 없어야 함
            async with db.execute("SELECT * FROM real_orders WHERE uuid = 'eth-cancel-unexecuted'") as cursor:
                row = await cursor.fetchone()
                assert row is None

        # 5. 자산 목록 검증
        assets = result["assets"]
        # ETH가 처분 완료 자산으로 식별되어 포함되어 있어야 함
        eth = next((x for x in assets if x["currency"] == "ETH"), None)
        assert eth is not None
        assert eth["avg_buy_price"] == 2700000.0
        assert eth["eval_value"] == 2700000.0 * 1.8
        assert eth["formatted_eval_value"] == "4,860,000"

        # 6. market.py 의 get_exchange_orders 함수 검증
        orders = await get_exchange_orders(request, "upbit", "ETH")
        # 부분 체결된 취소 주문 'eth-cancel-executed' 한 건이 반환되어야 함
        assert len(orders) == 1
        eth_order = orders[0]
        assert eth_order["uuid"] == "eth-cancel-executed"
        assert eth_order["executed_volume"] == 1.8
        assert eth_order["state"] == "cancel"


