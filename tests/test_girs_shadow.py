# -*- coding: utf-8 -*-
import pytest
import os
import time
import asyncio
from src.config.manager import ConfigManager
from src.engine.portfolio import PortfolioManager, Portfolio
from src.database.schema import init_db
from src.database.connection import get_db_conn
from src.database.repository import SqliteTradingRepository, InMemoryTradingRepository
from src.engine.auto_scheduler import HybridAutoApplyScheduler
from scratch.generate_shadow_report import generate_report

class DummySignal:
    def __init__(self, symbol="BTC", action="BUY", exchange="upbit", strategy_id="strat_1"):
        self.symbol = symbol
        self.action = action
        self.exchange = exchange
        self.strategy_id = strategy_id
        self.market = "KRW"
        self.reason = "Test Signal"
        self.context = {}

@pytest.fixture
def temp_db_path(tmp_path):
    return os.path.join(tmp_path, "test_trading.db")

@pytest.mark.asyncio
async def test_live_trading_blocked_and_events(temp_db_path):
    # 1. 설정 강제 세팅 (live_trading_enabled=False)
    config_manager = ConfigManager("config/settings.yaml")
    config_manager.update("system.live_trading_enabled", False)
    
    # 2. Schema 초기화 및 PortfolioManager 준비
    await init_db(temp_db_path)
    repo = SqliteTradingRepository(db_path=temp_db_path, girs_shadow_mode_override=False, auto_strategy_promotion_enabled_override=True)
    pm = PortfolioManager(db_path=temp_db_path, repository=repo)
    
    # live_trading_enabled를 설정 파일로부터 읽어올 수 있도록 pm.config_manager도 업데이트
    pm.config_manager.update("system.live_trading_enabled", False)
    
    # 3. live 포트폴리오 추가 및 저장
    live_portfolio = Portfolio(
        portfolio_id="live_port_1",
        name="Live Portfolio",
        initial_cash=1000000.0,
        exchange_id="upbit",
        portfolio_type="live"
    )
    pm.add_portfolio(live_portfolio)
    await repo.save_portfolio(live_portfolio)
    
    # 4. 실계좌 주문 시도 -> 차단 확인 및 BLOCKED_LIVE_ORDER 이벤트 확인
    signal = DummySignal()
    res = await pm.execute_pipeline_order(
        portfolio_id="live_port_1",
        signal=signal,
        quantity=0.1,
        execution_price=50000.0
    )
    
    assert res is not None
    assert res["status"] == "BLOCKED"
    assert res["reason"] == "LIVE_TRADING_DISABLED"
    
    # DB에 BLOCKED_LIVE_ORDER 이벤트가 기록되었는지 확인
    events = await repo.get_system_events(limit=10)
    blocked_events = [e for e in events if e["event_type"] == "BLOCKED_LIVE_ORDER"]
    assert len(blocked_events) > 0
    assert blocked_events[0]["target"] == "live_port_1"

@pytest.mark.asyncio
async def test_simulation_trading_allowed(temp_db_path):
    config_manager = ConfigManager("config/settings.yaml")
    config_manager.update("system.simulation_trading_enabled", True)
    
    await init_db(temp_db_path)
    repo = SqliteTradingRepository(db_path=temp_db_path, girs_shadow_mode_override=False, auto_strategy_promotion_enabled_override=True)
    pm = PortfolioManager(db_path=temp_db_path, repository=repo)
    
    # 3. simulation 포트폴리오 추가 및 저장
    sim_portfolio = Portfolio(
        portfolio_id="sim_port_1",
        name="Sim Portfolio",
        initial_cash=1000000.0,
        exchange_id="upbit",
        portfolio_type="simulation"
    )
    pm.add_portfolio(sim_portfolio)
    await repo.save_portfolio(sim_portfolio)
    
    signal = DummySignal()
    res = await pm.execute_pipeline_order(
        portfolio_id="sim_port_1",
        signal=signal,
        quantity=0.1,
        execution_price=50000.0
    )
    
    assert res is not None
    assert res.get("status") != "BLOCKED"
    assert res["side"] == "BUY"
    assert res["quantity"] == 0.1

@pytest.mark.asyncio
async def test_approve_proposal_blocked_in_shadow_mode(temp_db_path):
    await init_db(temp_db_path)
    repo = SqliteTradingRepository(db_path=temp_db_path, girs_shadow_mode_override=True, auto_strategy_promotion_enabled_override=False)
    
    # PENDING 제안 임의 등록
    async with get_db_conn(temp_db_path) as db:
        await db.execute("""
            INSERT INTO strategy_proposals 
            (id, strategy_id, portfolio_id, status, outcome, original_params, proposed_params, metrics, confidence_score)
            VALUES (1, 'strat_1', 'sim_port_1', 'PENDING', 'PENDING', '{}', '{}', '{}', 85)
        """)
        await db.commit()
        
    with pytest.raises(ValueError, match="Promotion blocked: Shadow operation mode active"):
        await repo.approve_proposal_atomic(1, int(time.time() * 1000))

@pytest.mark.asyncio
async def test_scheduler_promotion_skips_and_events(temp_db_path):
    await init_db(temp_db_path)
    # 1. 섀도 모드 overrides 명시적 주입
    repo = SqliteTradingRepository(db_path=temp_db_path, girs_shadow_mode_override=True, auto_strategy_promotion_enabled_override=False)
    
    # PENDING 제안 임의 등록
    async with get_db_conn(temp_db_path) as db:
        await db.execute("""
            INSERT INTO strategy_proposals 
            (id, strategy_id, portfolio_id, status, outcome, original_params, proposed_params, metrics, confidence_score)
            VALUES (2, 'strat_1', 'sim_port_1', 'PENDING', 'PENDING', '{}', '{}', '{}', 90)
        """)
        await db.commit()
        
    scheduler = HybridAutoApplyScheduler(
        db_path=temp_db_path, 
        debounce_seconds=0.1,
        girs_shadow_mode_override=True,
        auto_strategy_promotion_enabled_override=False
    )
    
    # 스케줄러 일괄 평가 강제 실행
    await scheduler._process_batch([2])
    
    # 실제 승격은 스킵되었으므로 제안의 상태가 여전히 PENDING인지 검증
    prop = await repo.get_strategy_proposal(2)
    assert prop["status"] == "PENDING"
    
    # SHADOW_PROMOTION_DETECTED 이벤트 기록 확인
    events = await repo.get_system_events(limit=10)
    promo_events = [e for e in events if e["event_type"] == "SHADOW_PROMOTION_DETECTED"]
    assert len(promo_events) > 0
    assert "proposal #2" in promo_events[0]["message"]

def test_report_generation_definitions(temp_db_path):
    import sqlite3
    conn = sqlite3.connect(temp_db_path)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS strategy_proposals (
            id INTEGER PRIMARY KEY, status TEXT, rolled_back_at INTEGER
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS proposal_evaluations (
            proposal_id INTEGER PRIMARY KEY, roi_divergence REAL
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS girs_shadow_metrics (
            timestamp REAL, proposal_id TEXT, final_promotion_score REAL, decision_type TEXT
        )
    """)
    
    # 1. False Positive: GIRS 차단(score < 0.8), 실제 성과 좋음 (roi_div >= -2.0, no rollback)
    c.execute("INSERT INTO strategy_proposals VALUES (1, 'PENDING', NULL)")
    c.execute("INSERT INTO proposal_evaluations VALUES (1, 1.5)")
    c.execute("INSERT INTO girs_shadow_metrics VALUES (1000.0, '1', 0.6, 'SHADOW')")
    
    # 2. False Negative: GIRS 승격(score >= 0.8), 실제 롤백됨
    c.execute("INSERT INTO strategy_proposals VALUES (2, 'APPLIED', 123456789)")
    c.execute("INSERT INTO proposal_evaluations VALUES (2, 0.5)")
    c.execute("INSERT INTO girs_shadow_metrics VALUES (1000.0, '2', 0.9, 'SHADOW')")
    
    conn.commit()
    conn.close()
    
    report_file = os.path.join(os.path.dirname(temp_db_path), "report.md")
    generate_report(temp_db_path, report_file)
    
    assert os.path.exists(report_file)
    with open(report_file, "r", encoding="utf-8") as f:
        content = f.read()
        
    assert "False Positive Rate (GIRS 과잉 차단)" in content
    assert "False Negative Rate (GIRS 위험 노출)" in content
    assert "False Positive: 1" in content
    assert "False Negative: 1" in content
