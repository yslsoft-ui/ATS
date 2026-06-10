import os
import pytest
import asyncio
from src.database.schema import init_db
from src.engine.utils.stock_mapper import StockMapper

@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test_stock_mapper.db")

@pytest.mark.asyncio
async def test_stock_mapper_db_flow(db_path):
    # 1. DB 초기화
    await init_db(db_path)
    
    # 2. StockMapper 인스턴스 생성
    mapper = StockMapper()
    # 캐시 비우기 (1차원 딕셔너리 구조에 맞게 초기화)
    mapper._mapping = {}
    
    # 3. add_mapping_async 테스트
    await mapper.add_mapping_async("upbit", "BTC", "비트코인", db_path)
    await mapper.add_mapping_async("kis", "005930", "삼성전자", db_path)
    
    # 4. 메모리 맵 검증
    assert mapper.get_name("upbit", "BTC") == "비트코인"
    assert mapper.get_name("kis", "005930") == "삼성전자"
    
    # 5. DB 로드 검증을 위해 DB에 데이터 강제 삽입
    from src.database.connection import get_db_conn
    async with get_db_conn(db_path) as db:
        await db.execute(
            "INSERT INTO asset_master (symbol, korean_name, asset_type) VALUES (?, ?, ?)", 
            ("ETH", "이더리움", "crypto")
        )
        await db.execute(
            "INSERT INTO exchange_assets (exchange, symbol, is_active, is_delisted) VALUES (?, ?, 1, 0)",
            ("upbit", "ETH")
        )
        await db.commit()
        
    # 다시 비우고
    mapper._mapping = {}
    
    # DB 로드
    await mapper.load_from_db(db_path)
    
    # 캐싱 검증
    assert mapper.get_name("upbit", "ETH") == "이더리움"
    assert mapper.get_active_symbols("upbit") == {"ETH"}
    assert mapper.get_name("kis", "UNKNOWN") == "UNKNOWN" # 없는 종목은 그대로 반환
