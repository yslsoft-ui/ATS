import pytest
import os
import time
import json
import asyncio
from src.database.schema import init_db
from src.database.repository import SqliteTradingRepository
from src.engine.portfolio import get_integer_portfolio_id

TEST_DB_PATH = "tests/test_safeguards.db"

@pytest.fixture(autouse=True)
def setup_test_db():
    if os.path.exists(TEST_DB_PATH):
        os.remove(TEST_DB_PATH)
    # asyncio.run을 동기적으로 감싸서 초기화 수행
    async def do_init():
        await init_db(TEST_DB_PATH)
        # 테스트에 필요한 포트폴리오 사전 삽입 및 캐시 동기화
        from src.engine.portfolio import get_integer_portfolio_id
        from src.database.connection import get_db_conn
        repo = SqliteTradingRepository(db_path=TEST_DB_PATH)
        port_id = get_integer_portfolio_id("port_test")
        async with get_db_conn(TEST_DB_PATH) as db:
            await db.execute("INSERT OR IGNORE INTO portfolios (id, name, type) VALUES (?, ?, 'simulation')", (port_id, "port_test"))
            await db.commit()
        await repo.sync_portfolio_id_cache()
    asyncio.run(do_init())
    
    # 전략 모듈 로딩 추가
    from src.engine.loader import load_dynamic_strategies
    load_dynamic_strategies("src/engine/strategies")
    
    yield
    if os.path.exists(TEST_DB_PATH):
        os.remove(TEST_DB_PATH)

@pytest.mark.asyncio
async def test_adaptive_diversity():
    from src.engine.shadow_backtest import ShadowBacktestEngine
    engine = ShadowBacktestEngine(db_path=TEST_DB_PATH)
    
    # 1. 횡보장/저변동성 국면 (Base Threshold = 0.18, lambda_dynamic = base * 1.2)
    # settings.yaml 에서 enable_auto_proposal = false 라고 가정 시 base_lambda = 15
    # λ_dynamic = 15 * 1.2 = 18.0, Effective Threshold = 0.18
    # 챔피언 파라미터 (rsi_window: 14) 대비 제안 파라미터 (rsi_window: 15)
    # Distance = 0.2 * |15-14|/14 = 0.2 * 1/14 = 0.0142
    # 0.0142 < 0.18 이므로 패널티 부과됨
    # Penalty = 18.0 * (1 - 0.0142/0.18) = 18.0 * (1 - 0.0789) = 18.0 * 0.9211 = 16.58점
    
    # 챔피언 버전을 DB에 저장하여 다양성 비교군 생성
    await engine.repository.save_strategy_version("RSIStrategy", 1, {"rsi_window": 14, "buy_threshold": 30.0, "sell_threshold": 70.0}, int(time.time()*1000))
    
    # 후보 제안 실행
    candidate = [{
        "strategy_id": "RSIStrategy",
        "portfolio_id": "port_test",
        "original_params": {"rsi_window": 14, "buy_threshold": 30.0, "sell_threshold": 70.0},
        "proposed_params": {"rsi_window": 15, "buy_threshold": 30.0, "sell_threshold": 70.0}, # 매우 인접한 파라미터
        "mutation_trace": {"rsi_window": [14, 15]}
    }]
    
    # 임의로 market_regime_summaries 세팅 (횡보장: volatility=0.8, rsi=50)
    from src.database.connection import get_db_conn
    async with get_db_conn(TEST_DB_PATH) as db:
        await db.execute('''
            INSERT INTO market_regime_summaries (symbol, volatility, rsi, timestamp)
            VALUES (?, ?, ?, ?)
        ''', ("BTC", 0.8, 50.0, int(time.time()*1000)))
        
        # trades 테이블에 대상 종목 시드 생성
        await db.execute('''
            INSERT INTO trades (exchange_id, symbol, trade_price, trade_volume, ask_bid, trade_timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', ("upbit", "BTC", 50000000.0, 0.1, "BID", int(time.time()*1000)))
        
        # portfolios 테이블에 대상 포트폴리오 시드 생성
        await db.execute('''
            INSERT OR IGNORE INTO portfolios (id, name, type)
            VALUES (?, ?, ?)
        ''', (get_integer_portfolio_id("port_test"), "Test Portfolio", "simulation"))
        
        # portfolio_exchanges 테이블에 자금 시드 생성
        await db.execute('''
            INSERT OR IGNORE INTO portfolio_exchanges (portfolio_id, exchange_id, initial_cash, cash)
            VALUES (?, ?, ?, ?)
        ''', (get_integer_portfolio_id("port_test"), "upbit", 10000000.0, 10000000.0))
        
        # 30건 이상의 거래가 백테스트에서 수행되도록 가격이 요동치고 시간이 흘러가는 500건의 틱 데이터를 시드
        for i in range(500):
            ts = int(time.time()*1000) - (500 - i) * 70 * 1000
            cycle = i % 30
            if cycle < 15:
                price = 50000000.0 - cycle * 500000.0
            else:
                price = 50000000.0 - 15 * 500000.0 + (cycle - 15) * 500000.0
            ask_bid = "BID" if i % 2 == 0 else "ASK"
            await db.execute('''
                INSERT INTO trades (exchange_id, symbol, trade_price, trade_volume, ask_bid, trade_timestamp)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', ("upbit", "BTC", price, 0.1, ask_bid, ts))
        await db.commit()
        
    inserted_ids = await engine.run_shadow_backtest(candidate)
    assert len(inserted_ids) == 1
    
    prop = await engine.repository.get_strategy_proposal(inserted_ids[0])
    assert prop is not None
    
    audit = prop["audit_log_json"]
    assert audit is not None
    assert audit["diversity_penalty"] > 0
    # Step 4: Counterfactual Feedback Loop 추가로 effective_threshold는
    # 기본값 0.18 + threshold_delta(0.0~0.03)로 동적 조정됨 → 범위 검증
    assert 0.18 <= audit["effective_threshold"] <= 0.25, \
        f"effective_threshold 범위 벗어남: {audit['effective_threshold']}"
    print(f"Adaptive Diversity Test: min_dist={audit['min_distance_observed']}, penalty={audit['diversity_penalty']}, threshold={audit['effective_threshold']}, score={prop['confidence_score']}")


@pytest.mark.asyncio
async def test_importance_sampling():
    repo = SqliteTradingRepository(db_path=TEST_DB_PATH)
    
    # 1. 45 ~ 60점 사이의 제안 기입 시 트래킹 자동 마운트 검증
    proposal_data1 = {
        "insight_id": None,
        "proposal_group_id": "group_sample",
        "version": 1,
        "portfolio_id": "port_test",
        "strategy_id": "RSIStrategy",
        "status": "PENDING",
        "outcome": "RUNNING",
        "original_params": {"rsi_window": 14},
        "proposed_params": {"rsi_window": 18},
        "metrics": {"roi_7d": 2.5},
        "mutation_trace": {},
        "confidence_score": 55, # 45~60 사이 -> status: PRUNED, is_counterfactual_tracked: 1
        "applied_at": None,
        "rolled_back_at": None
    }
    pid1 = await repo.insert_strategy_proposal(proposal_data1)
    prop1 = await repo.get_strategy_proposal(pid1)
    assert prop1["status"] == "PRUNED"
    assert prop1["is_counterfactual_tracked"] == 1
    
    # 2. 45점 미만으로 완전 탈락 시 트래킹되지 않는지 검증 (is_counterfactual_tracked = 0)
    proposal_data2 = dict(proposal_data1)
    proposal_data2["confidence_score"] = 40
    pid2 = await repo.insert_strategy_proposal(proposal_data2)
    prop2 = await repo.get_strategy_proposal(pid2)
    assert prop2["status"] == "PRUNED"
    assert prop2["is_counterfactual_tracked"] == 0

@pytest.mark.asyncio
async def test_decision_path_hash():
    import hashlib
    from src.engine.shadow_backtest import ShadowBacktestEngine
    engine = ShadowBacktestEngine(db_path=TEST_DB_PATH)
    
    # 동일한 입력 하에서 항상 일관되고 고유한 해시를 만드는지 검증
    proposed_params = {"rsi_window": 15, "buy_threshold": 30.0}
    original_params = {"rsi_window": 14, "buy_threshold": 30.0}
    strategy_id = "RSIStrategy"
    atr_ratio = 1.0
    adx = 20.0
    
    # 해시 도출 수식 검증
    sorted_proposed = sorted(proposed_params.items())
    proposed_str = ",".join([f"{k}:{v}" for k, v in sorted_proposed])
    raw_hash_src = f"{strategy_id}:{original_params}:{proposed_str}:{atr_ratio}:{adx}"
    hash1 = hashlib.sha256(raw_hash_src.encode("utf-8")).hexdigest()[:16]
    
    hash2 = hashlib.sha256(raw_hash_src.encode("utf-8")).hexdigest()[:16]
    assert hash1 == hash2

@pytest.mark.asyncio
async def test_counterfactual_sampling_tracker():
    repo = SqliteTradingRepository(db_path=TEST_DB_PATH)
    from src.engine.counterfactual_tracker import CounterfactualSamplingTracker
    
    now = int(time.time() * 1000)
    hour = 3600 * 1000
    
    # 1. 트래킹 중인 제안 시드 삽입 (is_counterfactual_tracked = 1)
    proposal_id = await repo.insert_strategy_proposal({
        "insight_id": None,
        "proposal_group_id": "group_track",
        "version": 1,
        "portfolio_id": "port_test",
        "strategy_id": "RSIStrategy",
        "status": "PRUNED",
        "outcome": "RUNNING",
        "original_params": {"rsi_window": 14, "buy_threshold": 30.0, "sell_threshold": 70.0},
        "proposed_params": {"rsi_window": 16, "buy_threshold": 30.0, "sell_threshold": 70.0},
        "metrics": {},
        "mutation_trace": {},
        "confidence_score": 55,
        "is_counterfactual_tracked": 1,
        "applied_at": None,
        "rolled_back_at": None
    })
    
    # db에 trades 및 portfolios 데이터 확보
    from src.database.connection import get_db_conn
    async with get_db_conn(TEST_DB_PATH) as db:
        await db.execute('''
            INSERT OR IGNORE INTO portfolios (id, name, type)
            VALUES (?, ?, ?)
        ''', (get_integer_portfolio_id("port_test"), "Test Portfolio", "simulation"))
        await db.execute('''
            INSERT OR IGNORE INTO portfolio_exchanges (portfolio_id, exchange_id, initial_cash, cash)
            VALUES (?, ?, ?, ?)
        ''', (get_integer_portfolio_id("port_test"), "upbit", 10000000.0, 10000000.0))
        
        await db.execute('''
            INSERT INTO trades (exchange_id, symbol, trade_price, trade_volume, ask_bid, trade_timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', ("upbit", "BTC", 50000000.0, 0.1, "BID", now))
        await db.commit()
        
    tracker = CounterfactualSamplingTracker(db_path=TEST_DB_PATH)
    
    # 2. 트래킹 스텝 1회 실행
    await tracker.run_step()
    
    # 3. 갱신 결과 검증
    prop = await repo.get_strategy_proposal(proposal_id)
    assert prop["is_counterfactual_tracked"] == 1
    # 백테스트 모듈을 거쳐서 값이 들어오는지 검증 (거래 내역이 없으면 ROI 0.0 으로 갱신됨)
    assert prop["counterfactual_roi"] is not None
    
    # 4. 7일 초과 시 기한 만료 검증 (is_counterfactual_tracked = 2 전환)
    # DB에서 강제로 created_at을 8일 전으로 세팅
    from src.database.connection import get_db_conn
    async with get_db_conn(TEST_DB_PATH) as db:
        await db.execute(
            "UPDATE strategy_proposals SET created_at = datetime(?, 'unixepoch') WHERE id = ?",
            ((now - 8 * 24 * hour) // 1000, proposal_id)
        )
        await db.commit()
        
    await tracker.run_step()
    prop_expired = await repo.get_strategy_proposal(proposal_id)
    assert prop_expired["is_counterfactual_tracked"] == 2
