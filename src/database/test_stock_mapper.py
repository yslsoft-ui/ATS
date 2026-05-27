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
    
    # 2. StockMapper 인스턴스 생성 (싱글톤이 아닌 독립 인스턴스로 테스트하기 위해 직접 클래스 생성)
    # 다만 StockMapper가 싱글톤이므로 _instance를 리셋하거나 직접 조작해야 할 수 있음.
    # 안전하게 StockMapper 내부의 _mapping을 직접 비우고 테스트를 수행.
    mapper = StockMapper()
    # 캐시 비우기
    mapper._mapping = {"upbit": {}, "kis": {}, "bithumb": {}}
    
    # 3. add_mapping_async 테스트
    await mapper.add_mapping_async("upbit", "BTC", "비트코인", db_path)
    await mapper.add_mapping_async("kis", "005930", "삼성전자", db_path)
    
    # 4. load_from_db 테스트
    # 다시 비우고
    mapper._mapping = {"upbit": {}, "kis": {}, "bithumb": {}}
    await mapper.load_from_db(db_path)
    
    # 5. 캐싱 검증
    assert mapper.get_name("upbit", "BTC") == "비트코인"
    assert mapper.get_name("kis", "005930") == "삼성전자"
    
    # 6. get_name 하위호환 및 fallback 검증
    assert mapper.get_name("bithumb", "BTC") == "비트코인" # 업비트 매핑 연동
    assert mapper.get_name("kis", "UNKNOWN") == "UNKNOWN" # 없는 종목은 그대로 반환
