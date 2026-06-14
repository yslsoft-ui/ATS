# -*- coding: utf-8 -*-

import os
import time
import json
import asyncio
import math
import pytest
import aiosqlite
from src.database.repository import SqliteTradingRepository, InMemoryTradingRepository, ChampionCooldownBlockedError
from src.engine.auto_scheduler import HybridAutoApplyScheduler
from src.engine.portfolio import Portfolio, PortfolioManager, OrderExecutor
from src.engine.girs_types import CandidateProposal

DB_FILE = "data/test_champion_cooldown.db"

@pytest.fixture(autouse=True)
def setup_teardown_db():
    if os.path.exists(DB_FILE):
        try:
            os.remove(DB_FILE)
        except OSError:
            pass
    yield
    if os.path.exists(DB_FILE):
        try:
            os.remove(DB_FILE)
        except OSError:
            pass

@pytest.mark.asyncio
async def test_champion_cooldown_sqlite():
    # Sqlite 리포지토리로 쿨다운 테스트
    # champion_cooldown_days = 2.0일, trades = 5건 설정
    repo = SqliteTradingRepository(
        db_path=DB_FILE,
        girs_shadow_mode_override=False,
        auto_strategy_promotion_enabled_override=True,
        champion_cooldown_days=2.0,
        champion_cooldown_trades=5
    )
    
    # DB 스키마 초기화
    from src.database.schema import init_db
    await init_db(DB_FILE)
    
    # 정수 초 단위 밀리초 타임스탬프로 고정하여 오차 제거
    now_ms = int(time.time()) * 1000
    
    # 1. 초기 챔피언 생성 (ACTIVE)
    # proposal 생성 및 approve
    async with aiosqlite.connect(DB_FILE) as db:
        # PENDING proposal 추가
        await db.execute("""
            INSERT INTO strategy_proposals (id, strategy_id, portfolio_id, status, outcome, proposed_params, original_params, confidence_score)
            VALUES (1, 'RSIStrategy', 'p1', 'PENDING', 'NONE', '{"rsi_window": 14}', '{"rsi_window": 15}', 85)
        """)
        await db.commit()
        
    # 첫 기용은 기존 챔피언이 없으므로 무조건 성공해야 함
    res = await repo.approve_proposal_atomic(1, now_ms)
    assert res["new_version_id"] == 1
    
    # 2. 두 번째 제안 승격 시도 (쿨다운 차단 확인 - 2일 미경과 & 5건 미거래 상태)
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("""
            INSERT INTO strategy_proposals (id, strategy_id, portfolio_id, status, outcome, proposed_params, original_params, confidence_score)
            VALUES (2, 'RSIStrategy', 'p1', 'PENDING', 'NONE', '{"rsi_window": 12}', '{"rsi_window": 14}', 90)
        """)
        await db.commit()
        
    # 승격 시도 -> ChampionCooldownBlockedError 발생해야 함 (1초 경과 시점)
    with pytest.raises(ChampionCooldownBlockedError) as excinfo:
        await repo.approve_proposal_atomic(2, now_ms + 1000)
    assert "cooldown" in str(excinfo.value)
    
    # 3. 2일 미만 경과했으나 거래량이 충족된 경우 (차단)
    # 5건 거래 완료 처리 (시점은 now_ms / 1000.0 이후로 보장)
    async with aiosqlite.connect(DB_FILE) as db:
        for i in range(5):
            await db.execute("""
                INSERT INTO orders_history (portfolio_id, exchange_id, market, strategy_id, symbol, side, price, quantity, fee, timestamp)
                VALUES ('p1', 'upbit', 'KRW', 'RSIStrategy', 'BTC', 'BUY', 50000000, 0.1, 2500, ?)
            """, (int(now_ms / 1000.0) + 1 + i,))
        await db.commit()
        
    with pytest.raises(ChampionCooldownBlockedError) as excinfo:
        await repo.approve_proposal_atomic(2, now_ms + 1000) # 거래량은 채웠으나 시간 미달 (1초 경과)
    assert "cooldown" in str(excinfo.value)
    
    # 4. 2일 이상 경과했으나 거래량이 부족한 경우 (차단)
    # 2일 = 172800초 경과로 시뮬레이션
    two_days_later_ms = now_ms + 2 * 24 * 3600 * 1000 + 1000
    
    # 거래 내역을 삭제한 뒤, 격리 및 정상 체결 배제 검증용 주문 적재
    # - p2 포트폴리오 주문 10건 (포트폴리오 격리 검증)
    # - p1 포트폴리오 주문 중 price = 0.0 인 미체결성 주문 2건 (체결 조건 보강 검증)
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("DELETE FROM orders_history")
        
        # 타 포트폴리오 p2의 주문 10건 추가
        for i in range(10):
            await db.execute("""
                INSERT INTO orders_history (portfolio_id, exchange_id, market, strategy_id, symbol, side, price, quantity, fee, timestamp)
                VALUES ('p2', 'upbit', 'KRW', 'RSIStrategy', 'BTC', 'BUY', 50000000, 0.1, 2500, ?)
            """, (int(now_ms / 1000.0) + 1 + i,))
            
        # target 포트폴리오 p1에 price가 0.0인 주문 2건 추가
        for i in range(2):
            await db.execute("""
                INSERT INTO orders_history (portfolio_id, exchange_id, market, strategy_id, symbol, side, price, quantity, fee, timestamp)
                VALUES ('p1', 'upbit', 'KRW', 'RSIStrategy', 'BTC', 'BUY', 0.0, 0.1, 0, ?)
            """, (int(now_ms / 1000.0) + 1 + i,))
            
        await db.commit()
        
    with pytest.raises(ChampionCooldownBlockedError) as excinfo:
        await repo.approve_proposal_atomic(2, two_days_later_ms) # 시간은 넘었으나 'p1'의 정상 거래량은 0건이므로 미달
    assert "cooldown" in str(excinfo.value)
    
    # 5. 둘 다 충족한 경우 (성공)
    # 다시 p1에 정상적인 거래 5건 넣기 (시점은 now_ms / 1000.0 이후로 보장)
    async with aiosqlite.connect(DB_FILE) as db:
        for i in range(5):
            await db.execute("""
                INSERT INTO orders_history (portfolio_id, exchange_id, market, strategy_id, symbol, side, price, quantity, fee, timestamp)
                VALUES ('p1', 'upbit', 'KRW', 'RSIStrategy', 'BTC', 'BUY', 50000000, 0.1, 2500, ?)
            """, (int(now_ms / 1000.0) + 1 + i,))
        await db.commit()
        
    res2 = await repo.approve_proposal_atomic(2, two_days_later_ms)
    assert res2["new_version_id"] == 2


@pytest.mark.asyncio
async def test_champion_cooldown_safety_features_unblocked():
    # 수동 롤백, panic sell, live trading block 등이 쿨다운에 차단되지 않는지 검증
    repo = SqliteTradingRepository(
        db_path=DB_FILE,
        girs_shadow_mode_override=False,
        auto_strategy_promotion_enabled_override=True,
        champion_cooldown_days=7.0,
        champion_cooldown_trades=100
    )
    from src.database.schema import init_db
    await init_db(DB_FILE)
    
    now_ms = int(time.time()) * 1000
    
    # 1. 초기 챔피언 기용
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("""
            INSERT INTO strategy_proposals (id, strategy_id, portfolio_id, status, outcome, proposed_params, original_params, confidence_score)
            VALUES (1, 'RSIStrategy', 'p1', 'PENDING', 'NONE', '{"rsi_window": 14}', '{"rsi_window": 15}', 85)
        """)
        await db.commit()
    await repo.approve_proposal_atomic(1, now_ms)
    
    # 2. 수동 롤백 (이전 버전이 없어 예외가 날 수 있으므로 history 강제 추가 후 롤백 수행)
    # 롤백은 approve_proposal_atomic이 아닌 rollback_strategy_atomic을 호출함.
    # 쿨다운 중(1초 경과, 0거래)임에도 롤백이 차단되지 않고 정상 실행되어야 함.
    res_rollback = await repo.rollback_strategy_atomic('RSIStrategy', 1, now_ms + 1000)
    assert res_rollback["new_version_id"] == 2
    assert res_rollback["rollback_version_id"] == 1
    
    # 3. Panic Sell / Liquidate All 검증
    # PortfolioManager를 통해 전체 청산을 호출할 때 쿨다운에 의해 차단되지 않는지 확인
    pm = PortfolioManager(db_path=DB_FILE, repository=repo)
    
    # VirtualOrderExecutorAdapter가 0원 시세를 거부하므로 MockExecutor로 덮어씌움
    class MockExecutor(OrderExecutor):
        async def execute_order(self, exchange_id: str, symbol: str, side: str, quantity: float, **kwargs) -> dict:
            return {
                'exchange_id': exchange_id,
                'market': 'KRW',
                'symbol': symbol,
                'side': side,
                'price': 50000000.0,
                'quantity': quantity,
                'fee': 2500.0,
                'timestamp': int(time.time() * 1000)
            }
    pm.executors['simulation'] = MockExecutor()
    
    # 모의 포트폴리오 추가
    p = Portfolio(portfolio_id="p1", name="Test Simulation", portfolio_type="simulation")
    p.exchange_cash = {"upbit": 10000000.0}
    p.exchange_initial_cash = {"upbit": 10000000.0}
    p.update_position("upbit", "BTC", "BUY", 50000000.0, 0.1, 2500.0, strategy_id="RSIStrategy")
    pm.add_portfolio(p)
    
    # 청산 실행 -> 쿨다운 영향 없이 정상적으로 SELL 체결 목록이 반환되어야 함
    liquidated = await pm.liquidate_all("p1")
    assert len(liquidated) == 1
    assert liquidated[0]["side"] == "SELL"
    assert p.positions[("upbit", "BTC")].quantity == 0.0
    
    # 4. Live Trading Block 검증
    # live_trading_enabled = False 인 포트폴리오의 실거래 시 차단 동작이 정상 수행되는지 검증 (쿨다운 유무에 영향 없음)
    p_live = Portfolio(portfolio_id="p_live", name="Test Live", portfolio_type="live")
    p_live.exchange_cash = {"upbit": 10000000.0}
    p_live.exchange_initial_cash = {"upbit": 10000000.0}
    pm.add_portfolio(p_live)
    
    # live_trading_enabled = False 설정 반영
    pm.config_manager.config["system"]["live_trading_enabled"] = False
    
    class FakeSignal:
        exchange_id = "upbit"
        symbol = "BTC"
        action = "BUY"
        strategy_id = "RSIStrategy"
        reason = "Test"
        context = {}
        
    res_block = await pm.execute_pipeline_order("p_live", FakeSignal(), 0.1, 50000000.0)
    assert res_block is not None
    assert res_block["status"] == "BLOCKED"
    assert res_block["reason"] == "LIVE_TRADING_DISABLED"


@pytest.mark.asyncio
async def test_auto_scheduler_cooldown_logging():
    # 자동 승격 스케줄러에서 쿨다운 차단 시 PROMOTION_COOLDOWN_BLOCKED 시스템 이벤트 기록 검증
    scheduler = HybridAutoApplyScheduler(
        db_path=DB_FILE,
        debounce_seconds=0.1,
        girs_shadow_mode_override=False,
        auto_strategy_promotion_enabled_override=True
    )
    
    # 리포지토리의 쿨다운 설정을 2.0일, 5건으로 명시 주입
    scheduler.repository.champion_cooldown_days = 2.0
    scheduler.repository.champion_cooldown_trades = 5
    
    from src.database.schema import init_db
    await init_db(DB_FILE)
    
    now_ms = int(time.time()) * 1000
    
    # 1. 첫 번째 제안 승격 성공
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("""
            INSERT INTO strategy_proposals (id, strategy_id, portfolio_id, status, outcome, proposed_params, original_params, confidence_score)
            VALUES (1, 'RSIStrategy', 'p1', 'PENDING', 'NONE', '{"rsi_window": 14}', '{"rsi_window": 15}', 85)
        """)
        await db.commit()
        
    await scheduler.repository.approve_proposal_atomic(1, now_ms)
    
    # 2. 두 번째 제안 PENDING 생성 (점수 85)
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("""
            INSERT INTO strategy_proposals (id, strategy_id, portfolio_id, status, outcome, proposed_params, original_params, confidence_score)
            VALUES (2, 'RSIStrategy', 'p1', 'PENDING', 'NONE', '{"rsi_window": 12}', '{"rsi_window": 14}', 85)
        """)
        await db.commit()
        
    # 스케줄러에 제안 2 감지 통보 -> 디바운스 대기 후 배치 승인 돌면서 쿨다운 에러 유발
    await scheduler.notify_proposal_created(2)
    await asyncio.sleep(0.3) # 디바운스 0.1초 이상 대기
    
    # 3. 쿨다운 차단 이벤트가 데이터베이스에 적재되었는지 확인
    events = await scheduler.repository.get_system_events(limit=5)
    cooldown_events = [e for e in events if e["event_type"] == "PROMOTION_COOLDOWN_BLOCKED"]
    assert len(cooldown_events) >= 1
    assert "RSIStrategy" in cooldown_events[0]["target"]
    assert "Champion Cooldown 미경과" in cooldown_events[0]["message"]
    
    await scheduler.close()


@pytest.mark.asyncio
async def test_champion_cooldown_detailed_filtering():
    # 챔피언 쿨다운 정책 세부 검증 테스트
    repo = SqliteTradingRepository(
        db_path=DB_FILE,
        girs_shadow_mode_override=False,
        auto_strategy_promotion_enabled_override=True,
        champion_cooldown_days=3.0,
        champion_cooldown_trades=5
    )
    
    from src.database.schema import init_db
    await init_db(DB_FILE)
    
    # 정수 초 단위 밀리초 타임스탬프로 고정하여 오차 제거
    now_ms = int(time.time()) * 1000
    applied_at_sec = int(now_ms / 1000)
    
    # 1. 초기 챔피언 생성 (ACTIVE)
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("""
            INSERT INTO strategy_proposals (id, strategy_id, portfolio_id, status, outcome, proposed_params, original_params, confidence_score)
            VALUES (1, 'RSIStrategy', 'p1', 'PENDING', 'NONE', '{"rsi_window": 14}', '{"rsi_window": 15}', 85)
        """)
        await db.commit()
        
    res = await repo.approve_proposal_atomic(1, now_ms)
    assert res["new_version_id"] == 1
    
    # 2. 두 번째 제안 PENDING 등록 (승격 타겟)
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("""
            INSERT INTO strategy_proposals (id, strategy_id, portfolio_id, status, outcome, proposed_params, original_params, confidence_score)
            VALUES (2, 'RSIStrategy', 'p1', 'PENDING', 'NONE', '{"rsi_window": 12}', '{"rsi_window": 14}', 90)
        """)
        await db.commit()
        
    # 3. 무효 주문 데이터 적재
    async with aiosqlite.connect(DB_FILE) as db:
        # 3.1. 과거 주문 (applied_at_sec보다 과거 시점)
        for i in range(10):
            await db.execute("""
                INSERT INTO orders_history (portfolio_id, exchange_id, market, strategy_id, symbol, side, price, quantity, fee, timestamp)
                VALUES ('p1', 'upbit', 'KRW', 'RSIStrategy', 'BTC', 'BUY', 50000000, 0.1, 2500, ?)
            """, (applied_at_sec - 1 - i,))
            
        # 3.2. 타 전략 주문 ('other_strategy' - 기존 champion strategy_id와 불일치)
        for i in range(10):
            await db.execute("""
                INSERT INTO orders_history (portfolio_id, exchange_id, market, strategy_id, symbol, side, price, quantity, fee, timestamp)
                VALUES ('p1', 'upbit', 'KRW', 'other_strategy', 'BTC', 'BUY', 50000000, 0.1, 2500, ?)
            """, (applied_at_sec + 1 + i,))
            
        # 3.3. price <= 0 인 주문
        for i in range(10):
            await db.execute("""
                INSERT INTO orders_history (portfolio_id, exchange_id, market, strategy_id, symbol, side, price, quantity, fee, timestamp)
                VALUES ('p1', 'upbit', 'KRW', 'RSIStrategy', 'BTC', 'BUY', 0.0, 0.1, 0, ?)
            """, (applied_at_sec + 1 + i,))
            
        # 3.4. quantity <= 0 인 주문
        for i in range(10):
            await db.execute("""
                INSERT INTO orders_history (portfolio_id, exchange_id, market, strategy_id, symbol, side, price, quantity, fee, timestamp)
                VALUES ('p1', 'upbit', 'KRW', 'RSIStrategy', 'BTC', 'BUY', 50000000, 0.0, 0, ?)
            """, (applied_at_sec + 1 + i,))
            
        await db.commit()
        
    # 3일 경과 시각 설정 (시간 쿨다운은 이미 만족한 상태로 검증)
    three_days_later_ms = now_ms + 3 * 24 * 3600 * 1000 + 1000
    
    # 4. 무효 주문만 존재하는 상태에서 승격 시도 (차단되어야 함)
    with pytest.raises(ChampionCooldownBlockedError) as excinfo:
        await repo.approve_proposal_atomic(2, three_days_later_ms)
    assert "cooldown" in str(excinfo.value)
    
    # 5. 차단 실패 이후 DB 상태 원자성(Atomicity) 검증
    # - strategy_versions 테이블의 current_version_id가 1로 보존되었는지 검증
    ver_check = await repo.get_strategy_version('RSIStrategy')
    assert ver_check["current_version_id"] == 1
    
    # - strategy_proposals 테이블의 id=2 제안 status가 여전히 'PENDING'인지 검증
    prop_check = await repo.get_strategy_proposal(2)
    assert prop_check["status"] == "PENDING"
    assert prop_check["applied_at"] is None
    
    # 6. 경계값 검증용 주문 주입 (timestamp == applied_at_ms / 1000)
    # 5건 중 1건을 정확히 경계값 시점으로 적재하여 카운팅에 포함되는지 확인
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("""
            INSERT INTO orders_history (portfolio_id, exchange_id, market, strategy_id, symbol, side, price, quantity, fee, timestamp)
            VALUES ('p1', 'upbit', 'KRW', 'RSIStrategy', 'BTC', 'BUY', 50000000, 0.1, 2500, ?)
        """, (applied_at_sec,))
        
        # 나머지 유효 주문 4건 적재 (applied_at_sec 시점 이후)
        for i in range(4):
            await db.execute("""
                INSERT INTO orders_history (portfolio_id, exchange_id, market, strategy_id, symbol, side, price, quantity, fee, timestamp)
                VALUES ('p1', 'upbit', 'KRW', 'RSIStrategy', 'BTC', 'BUY', 50000000, 0.1, 2500, ?)
            """, (applied_at_sec + 1 + i,))
            
        await db.commit()
        
    # 7. 유효 주문 5건이 충족된 시점에서 승격 시도 (성공해야 함)
    res2 = await repo.approve_proposal_atomic(2, three_days_later_ms)
    assert res2["new_version_id"] == 2
    
    # 최종 DB 갱신 검증
    ver_final = await repo.get_strategy_version('RSIStrategy')
    assert ver_final["current_version_id"] == 2
    
    prop_final = await repo.get_strategy_proposal(2)
    assert prop_final["status"] == "APPLIED"
    assert prop_final["applied_at"] == three_days_later_ms
