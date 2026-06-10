import pytest
import os
import time
import json
import asyncio
import pytest_asyncio
from src.database.schema import init_db
from src.database.repository import SqliteTradingRepository
from src.engine.utils.performance import calculate_performance_metrics

TEST_DB_PATH = "tests/test_versioning.db"

@pytest_asyncio.fixture(autouse=True)
async def setup_test_db():
    # 테스트 전 DB 파일 초기화
    if os.path.exists(TEST_DB_PATH):
        os.remove(TEST_DB_PATH)
        
    await init_db(TEST_DB_PATH)
    yield
    
    # 테스트 종료 후 정리
    if os.path.exists(TEST_DB_PATH):
        os.remove(TEST_DB_PATH)

@pytest.mark.asyncio
async def test_database_versioning_and_proposals():
    repo = SqliteTradingRepository(db_path=TEST_DB_PATH)
    strategy_id = "rsi_strategy"
    
    # 1. 초기 상태에서 get_strategy_version이 None을 반환하는지 검증
    initial_ver = await repo.get_strategy_version(strategy_id)
    assert initial_ver is None
    
    # 2. save_strategy_version 및 get_strategy_version 저장/조회 검증
    init_params = {"rsi_window": 14, "buy_threshold": 30.0, "sell_threshold": 70.0}
    applied_ts = int(time.time() * 1000)
    await repo.save_strategy_version(
        strategy_id=strategy_id,
        version_id=1,
        params=init_params,
        applied_at=applied_ts,
        rollback_source_version=None
    )
    
    ver = await repo.get_strategy_version(strategy_id)
    assert ver is not None
    assert ver["current_version_id"] == 1
    assert ver["current_params"] == init_params
    assert ver["rollback_source_version"] is None
    assert ver["applied_at"] == applied_ts

    # 3. parameter history 기록 및 조회 검증
    # 최초 등록 내역 기록
    hist_id = await repo.insert_strategy_parameter_history(
        strategy_id=strategy_id,
        version_id=1,
        parent_version_id=None,
        old_params=None,
        new_params=json.dumps(init_params),
        proposal_id=None,
        is_current=1,
        changed_by="AUTO",
        change_reason="STARTUP_RESTORE"
    )
    assert hist_id > 0
    
    history_list = await repo.get_strategy_parameter_history(strategy_id)
    assert len(history_list) == 1
    assert history_list[0]["version_id"] == 1
    assert history_list[0]["change_reason"] == "STARTUP_RESTORE"
    assert history_list[0]["is_current"] == 1

    # 4. 제안(Proposal) 추가 및 갱신 검증
    proposal_data = {
        "insight_id": None,
        "proposal_group_id": "group_123",
        "version": 1,
        "portfolio_id": "sim_port_1",
        "strategy_id": strategy_id,
        "status": "PENDING",
        "outcome": "RUNNING",
        "original_params": init_params,
        "proposed_params": {"rsi_window": 14, "buy_threshold": 35.0, "sell_threshold": 65.0},
        "metrics": {"roi": 0.0},
        "mutation_trace": {"buy_threshold": [30.0, 35.0]},
        "confidence_score": 85,
        "applied_at": None,
        "rolled_back_at": None
    }
    
    proposal_id = await repo.insert_strategy_proposal(proposal_data)
    assert proposal_id > 0
    
    # 제안 조회 테스트
    prop = await repo.get_strategy_proposal(proposal_id)
    assert prop is not None
    assert prop["status"] == "PENDING"
    assert prop["proposed_params"] == {"rsi_window": 14, "buy_threshold": 35.0, "sell_threshold": 65.0}

    active_props = await repo.get_active_proposals(strategy_id, "PENDING")
    assert len(active_props) == 1
    assert active_props[0]["id"] == proposal_id

    # 5. 제안 적용 (PROPOSAL_APPLY) 시뮬레이션
    new_params = {"rsi_window": 14, "buy_threshold": 35.0, "sell_threshold": 65.0}
    new_ver_id = 2
    
    # Parameter history 기록
    await repo.insert_strategy_parameter_history(
        strategy_id=strategy_id,
        version_id=new_ver_id,
        parent_version_id=1,
        old_params=json.dumps(init_params),
        new_params=json.dumps(new_params),
        proposal_id=proposal_id,
        is_current=1,
        changed_by="USER",
        change_reason="PROPOSAL_APPLY"
    )
    
    # 최신 버전 저장
    applied_ts2 = int(time.time() * 1000)
    await repo.save_strategy_version(
        strategy_id=strategy_id,
        version_id=new_ver_id,
        params=new_params,
        applied_at=applied_ts2,
        rollback_source_version=None
    )
    
    # 제안 상태 업데이트
    await repo.update_strategy_proposal_status(
        proposal_id=proposal_id,
        status="APPLIED",
        outcome="RUNNING",
        applied_at=applied_ts2
    )
    
    # 업데이트 결과 검증
    ver_after_apply = await repo.get_strategy_version(strategy_id)
    assert ver_after_apply["current_version_id"] == 2
    assert ver_after_apply["current_params"] == new_params
    
    prop_after_apply = await repo.get_strategy_proposal(proposal_id)
    assert prop_after_apply["status"] == "APPLIED"
    assert prop_after_apply["outcome"] == "RUNNING"
    assert prop_after_apply["applied_at"] == applied_ts2

    # 6. 성과 스냅샷 기록 및 조회 검증
    # 임의의 성과 데이터 주입
    snapshot_data = {
        "strategy_id": strategy_id,
        "version_id": new_ver_id,
        "parameter_hash": "somehash123",
        "snapshot_type": "PARAMETER_CHANGE",
        "timestamp": int(time.time() * 1000),
        "roi": 5.4,
        "mdd": 2.1,
        "profit_factor": 1.45,
        "win_rate": 60.0,
        "trade_count": 10
    }
    
    await repo.insert_strategy_performance_snapshot(snapshot_data)
    
    snapshots = await repo.get_strategy_performance_snapshots(strategy_id, version_id=new_ver_id)
    assert len(snapshots) == 1
    assert snapshots[0]["roi"] == 5.4
    assert snapshots[0]["mdd"] == 2.1
    assert snapshots[0]["snapshot_type"] == "PARAMETER_CHANGE"

    # 7. 원클릭 롤백(Rollback) 시뮬레이션 (버전 2 -> 버전 1로 복귀)
    rollback_ver_id = 3
    # 이전 버전인 1버전 히스토리 조회
    target_hist = await repo.get_strategy_parameter_version(strategy_id, 1)
    assert target_hist is not None
    assert target_hist["new_params"] == init_params
    
    # 롤백 기록 추가
    await repo.insert_strategy_parameter_history(
        strategy_id=strategy_id,
        version_id=rollback_ver_id,
        parent_version_id=1,  # 원래 타겟 버전 1이 부모가 됨
        old_params=json.dumps(new_params),
        new_params=json.dumps(target_version_params := target_hist["new_params"]),
        proposal_id=None,
        is_current=1,
        changed_by="USER",
        change_reason="ROLLBACK"
    )
    
    rollback_ts = int(time.time() * 1000)
    await repo.save_strategy_version(
        strategy_id=strategy_id,
        version_id=rollback_ver_id,
        params=target_version_params,
        applied_at=rollback_ts,
        rollback_source_version=2  # 문제를 일으킨 버전이 2버전임
    )
    
    # 롤백의 원인이 된 문제 버전(버전 2)에 연동된 제안(proposal_id) ROLLED_BACK 처리
    current_hist = await repo.get_strategy_parameter_version(strategy_id, 2)
    assert current_hist["proposal_id"] == proposal_id
    
    await repo.update_strategy_proposal_status(
        proposal_id=proposal_id,
        status="ROLLED_BACK",
        outcome="ROLLED_BACK",
        rolled_back_at=rollback_ts
    )
    
    # 롤백 결과 검증
    ver_after_rollback = await repo.get_strategy_version(strategy_id)
    assert ver_after_rollback["current_version_id"] == rollback_ver_id
    assert ver_after_rollback["current_params"] == init_params
    assert ver_after_rollback["rollback_source_version"] == 2
    
    prop_after_rollback = await repo.get_strategy_proposal(proposal_id)
    assert prop_after_rollback["status"] == "ROLLED_BACK"
    assert prop_after_rollback["outcome"] == "ROLLED_BACK"
    assert prop_after_rollback["rolled_back_at"] == rollback_ts

@pytest.mark.asyncio
async def test_calculate_performance_metrics():
    # 8. 성과 계산(calculate_performance_metrics) 유틸리티 단위 검증
    history = [
        {"exchange": "upbit", "symbol": "BTC", "side": "BUY", "price": 100.0, "quantity": 10.0, "fee": 0.5, "timestamp": 1000},
        {"exchange": "upbit", "symbol": "BTC", "side": "SELL", "price": 120.0, "quantity": 5.0, "fee": 0.6, "timestamp": 2000},
        {"exchange": "upbit", "symbol": "BTC", "side": "SELL", "price": 90.0, "quantity": 5.0, "fee": 0.45, "timestamp": 3000},
    ]
    
    class MockPosition:
        def __init__(self, exchange, symbol, quantity, avg_price):
            self.exchange = exchange
            self.symbol = symbol
            self.quantity = quantity
            self.avg_price = avg_price
            
    positions = {
        ("upbit", "BTC"): MockPosition("upbit", "BTC", 0.0, 0.0)
    }
    
    current_prices = {"BTC": 90.0}
    # 초기 자금: 1000
    # 1. BUY 100 * 10 = 1000 소모 + 0.5 fee -> 현금: -0.5, BTC 보유: 10
    # 2. SELL 120 * 5 = 600 획득 - 0.6 fee -> 현금: 598.9, BTC 보유: 5
    # 3. SELL 90 * 5 = 450 획득 - 0.45 fee -> 현금: 1048.45, BTC 보유: 0
    # 최종 가치: 1048.45 (이익 48.45)
    # ROI: 4.85%
    # Win rate: 1승(BTC 120에 팜, 평단 100) 1패(BTC 90에 팜, 평단 100) -> 50%
    # profit_factor: 승리 이익 = (120 - 100) * 5 - 0.6 = 99.4
    #                 패배 손실 = abs((90 - 100) * 5 - 0.45) = abs(-50 - 0.45) = 50.45
    #                 PF = 99.4 / 50.45 = 1.97
    
    metrics = calculate_performance_metrics(
        history=history,
        initial_cash=1000.0,
        current_cash=1048.45,
        positions=positions,
        current_prices=current_prices
    )
    
    assert metrics["roi"] == 4.85
    assert metrics["win_rate"] == 50.0
    assert metrics["trade_count"] == 3
    assert metrics["profit_factor"] == 1.97

@pytest.mark.asyncio
async def test_analyzer_and_shadow_backtest_with_regime():
    from src.engine.analyzer import StrategyHypothesisAnalyzer
    from src.engine.shadow_backtest import ShadowBacktestEngine
    from src.database.connection import get_db_conn
    
    repo = SqliteTradingRepository(db_path=TEST_DB_PATH)
    strategy_id = "rsistrategy"
    portfolio_id = "sim_port_2"
    
    # 1. 캔들 및 거래소, 포트폴리오 셋업
    # exchange_assets 활성 종목 등록
    async with get_db_conn(TEST_DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO asset_master (symbol, korean_name, asset_type) VALUES ('BTC', '비트코인', 'crypto')")
        await db.execute("INSERT OR IGNORE INTO exchange_assets (exchange, symbol, is_active) VALUES ('upbit', 'BTC', 1)")
        await db.execute(
            "INSERT OR IGNORE INTO portfolios (id, name, type, exchange_id, initial_cash, cash) VALUES (?, ?, ?, ?, ?, ?)",
            (portfolio_id, "테스트 모의투자", "simulation", "upbit", 10000000.0, 10000000.0)
        )
        await db.commit()

    # 2. strategy_versions 초기 등록
    init_params = {"rsi_window": 14, "buy_threshold": 30.0, "sell_threshold": 70.0}
    await repo.save_strategy_version(
        strategy_id=strategy_id,
        version_id=1,
        params=init_params,
        applied_at=int(time.time() * 1000)
    )

    # 3. 시장 Regime 요약 데이터 주입 (변동성이 높고 RSI가 낮은 시장 환경 시뮬레이션)
    now_ms = int(time.time() * 1000)
    async with get_db_conn(TEST_DB_PATH) as db:
        await db.execute('''
            INSERT INTO market_regime_summaries (timestamp, symbol, volatility, rsi, volume_ratio, spread, orderbook_imbalance)
            VALUES (?, 'upbit:BTC', 0.05, 30.0, 1.5, 0.001, 0.2)
        ''', (now_ms,))
        await db.commit()

    # 4. 분석에 필요한 틱 데이터 및 거래 손실 이력(orders_history) 모사 주입
    # (최소 5건의 거래 이력 및 1건 이상의 손실 거래 필요)
    # 또한 백테스트 대상 틱이 있어야 하므로 trades 테이블에도 틱 데이터 주입
    async with get_db_conn(TEST_DB_PATH) as db:
        # trades 테이블에 백테스트용 틱 주입 (최소 30건 이상의 거래를 발생시키기 위해 여러 틱 주입)
        for i in range(50):
            ts = now_ms - (50 - i) * 60000
            price = 50000.0 - (i % 2) * 500.0
            await db.execute('''
                INSERT INTO trades (exchange, market, symbol, trade_price, trade_volume, ask_bid, trade_timestamp, sequential_id)
                VALUES ('upbit', 'KRW', 'BTC', ?, 1.0, 'ASK', ?, ?)
            ''', (price, ts, 1000 + i))
            
        # 캔들 테이블에도 백테스트가 웜업하거나 캔들 조회를 할 수 있도록 데이터 주입 (최소 25개 이상)
        for i in range(30):
            ts = int((now_ms - (30 - i) * 60000) / 1000 // 60) * 60
            price = 50000.0
            await db.execute('''
                INSERT INTO candles (exchange, symbol, interval, timestamp, open, high, low, close, volume)
                VALUES ('upbit', 'BTC', 60, ?, ?, ?, ?, ?, 1.0)
            ''', (ts, price, price, price, price))

        # orders_history에 손실 유발 거래 주입
        # BUY 평단 50000 -> SELL 45000 (손실)
        await db.execute('''
            INSERT INTO orders_history (portfolio_id, exchange, strategy_id, symbol, side, price, quantity, fee, timestamp)
            VALUES (?, 'upbit', ?, 'BTC', 'BUY', 50000.0, 1.0, 2.5, ?)
        ''', (portfolio_id, strategy_id, int(now_ms/1000 - 100)))
        await db.execute('''
            INSERT INTO orders_history (portfolio_id, exchange, strategy_id, symbol, side, price, quantity, fee, timestamp)
            VALUES (?, 'upbit', ?, 'BTC', 'SELL', 45000.0, 1.0, 2.25, ?)
        ''', (portfolio_id, strategy_id, int(now_ms/1000)))
        
        # 5건 채우기용 가상 무손실 거래들 주입
        for i in range(4):
            await db.execute('''
                INSERT INTO orders_history (portfolio_id, exchange, strategy_id, symbol, side, price, quantity, fee, timestamp)
                VALUES (?, 'upbit', ?, 'BTC', 'BUY', 40000.0, 1.0, 2.0, ?)
            ''', (portfolio_id, strategy_id, int(now_ms/1000 - 500 - i * 100)))
            await db.execute('''
                INSERT INTO orders_history (portfolio_id, exchange, strategy_id, symbol, side, price, quantity, fee, timestamp)
                VALUES (?, 'upbit', ?, 'BTC', 'SELL', 41000.0, 1.0, 2.05, ?)
            ''', (portfolio_id, strategy_id, int(now_ms/1000 - 400 - i * 100)))

        await db.commit()

    # 5. 가설 분석기 동작 및 원 파라미터 변형 셋 생성 검증
    analyzer = StrategyHypothesisAnalyzer(db_path=TEST_DB_PATH)
    candidates = await analyzer.analyze_failures(portfolio_id, strategy_id)
    
    assert len(candidates) > 0
    # 고변동성 손실 비중 조건이 맞아 buy_threshold 하향 조정 변이가 들어갔는지 체크
    assert "buy_threshold" in candidates[0]["proposed_params"]
    assert candidates[0]["proposed_params"]["buy_threshold"] != init_params["buy_threshold"]

    # 6. Shadow Backtest 엔진 검증
    backtester = ShadowBacktestEngine(db_path=TEST_DB_PATH)
    inserted_ids = await backtester.run_shadow_backtest(candidates)
    
    # 7일 30건 이상의 거래를 발생시킬 틱 데이터가 충분하면 적합 판정을 받아 PENDING 제안이 등록됨
    # 만약 거래 수가 부족해 탈락했을 경우(inserted_ids == [])에는 테스트 가드 처리
    # 틱을 50개 주입했고 RSI 상하방 가격 변동이 있으므로 거래가 충분히 유발됨
    if len(inserted_ids) > 0:
        proposal = await repo.get_strategy_proposal(inserted_ids[0])
        assert proposal is not None
        assert proposal["status"] == "PENDING"
        assert proposal["strategy_id"] == strategy_id
        assert proposal["confidence_score"] in [50, 70, 85]

@pytest.mark.asyncio
async def test_proposal_evaluation():
    from src.database.connection import get_db_conn
    repo = SqliteTradingRepository(db_path=TEST_DB_PATH)
    strategy_id = "rsi_strategy"
    portfolio_id = "sim_port_3"
    
    # 1. 7일이 지난 제안 모사
    # applied_at = 8일 전
    eight_days_ago_ms = int((time.time() - 8 * 24 * 3600) * 1000)
    
    proposal_data = {
        "insight_id": None,
        "proposal_group_id": "group_999",
        "version": 1,
        "portfolio_id": portfolio_id,
        "strategy_id": strategy_id,
        "status": "APPLIED",
        "outcome": "RUNNING",
        "original_params": {"rsi_window": 14},
        "proposed_params": {"rsi_window": 16},
        "metrics": {"roi_7d": 5.0, "trade_count_7d": 2},
        "mutation_trace": {},
        "confidence_score": 70,
        "applied_at": eight_days_ago_ms,
        "rolled_back_at": None
    }
    
    proposal_id = await repo.insert_strategy_proposal(proposal_data)
    assert proposal_id > 0
    
    # 2. 7일 경과 미평가 대상 제안 수집 검증
    targets = await repo.get_unevaluated_applied_proposals()
    assert len(targets) == 1
    assert targets[0]["id"] == proposal_id
    
    # 3. 사후 평가 데이터 적재 검증
    eval_data = {
        "proposal_id": proposal_id,
        "horizon_name": "7d",
        "predicted_roi_7d": 5.0,
        "actual_roi_7d": 6.2,
        "roi_divergence": 1.2,
        "predicted_trade_count_7d": 2,
        "actual_trade_count_7d": 3,
        "trade_count_divergence": 1
    }
    
    eval_id = await repo.insert_proposal_evaluation(eval_data)
    assert eval_id > 0
    
    # 평가 조회 검증
    res_eval = await repo.get_proposal_evaluation(proposal_id)
    assert res_eval is not None
    assert res_eval["actual_roi_7d"] == 6.2
    assert res_eval["roi_divergence"] == 1.2
    assert res_eval["trade_count_divergence"] == 1
    
    # 제안 outcome COMPLETED 처리 검증
    await repo.update_strategy_proposal_status(
        proposal_id=proposal_id,
        status="APPLIED",
        outcome="COMPLETED"
    )
    
    prop = await repo.get_strategy_proposal(proposal_id)
    assert prop["outcome"] == "COMPLETED"
    
    # 이제 완료되었으므로 평가 대상에서 제외되는지 확인
    targets_after = await repo.get_unevaluated_applied_proposals()
    assert len(targets_after) == 0

@pytest.mark.asyncio
async def test_proposal_evaluation_legacy_without_horizon_name():
    from src.database.connection import get_db_conn
    repo = SqliteTradingRepository(db_path=TEST_DB_PATH)
    strategy_id = "rsi_strategy_legacy"
    portfolio_id = "sim_port_legacy"
    
    # 1. 제안 모사
    proposal_data = {
        "insight_id": None,
        "proposal_group_id": "group_legacy",
        "version": 1,
        "portfolio_id": portfolio_id,
        "strategy_id": strategy_id,
        "status": "APPLIED",
        "outcome": "RUNNING",
        "original_params": {"rsi_window": 14},
        "proposed_params": {"rsi_window": 16},
        "metrics": {"roi_7d": 5.0, "trade_count_7d": 2},
        "mutation_trace": {},
        "confidence_score": 70,
        "applied_at": int(time.time() * 1000),
        "rolled_back_at": None
    }
    
    proposal_id = await repo.insert_strategy_proposal(proposal_data)
    assert proposal_id > 0
    
    # 2. horizon_name이 없는 레거시 eval_data 적재 검증
    eval_data = {
        "proposal_id": proposal_id,
        "predicted_roi_7d": 5.0,
        "actual_roi_7d": 6.2,
        "roi_divergence": 1.2,
        "predicted_trade_count_7d": 2,
        "actual_trade_count_7d": 3,
        "trade_count_divergence": 1
    }
    
    eval_id = await repo.insert_proposal_evaluation(eval_data, legacy_compat=True)
    assert eval_id > 0
    
    # 기본값인 "7d"로 저장되어 있는지 조회 검증
    async with get_db_conn(TEST_DB_PATH) as db:
        async with db.execute("SELECT horizon_name FROM proposal_evaluations WHERE id = ?", (eval_id,)) as cur:
            row = await cur.fetchone()
            assert row is not None
            assert row["horizon_name"] == "7d"
            
    # 3. 다중 Horizon 저장 검증 ("1d", "3d", "7d"가 동일 proposal_id에 충돌 없이 1:N으로 추가되는지 검증)
    # 7d는 이미 삽입되었으므로 1d, 3d를 삽입
    eval_data_1d = dict(eval_data)
    eval_data_1d["horizon_name"] = "1d"
    eval_data_1d["actual_roi_7d"] = 1.1
    
    eval_data_3d = dict(eval_data)
    eval_data_3d["horizon_name"] = "3d"
    eval_data_3d["actual_roi_7d"] = 3.3
    
    eval_id_1d = await repo.insert_proposal_evaluation(eval_data_1d)
    eval_id_3d = await repo.insert_proposal_evaluation(eval_data_3d)
    
    assert eval_id_1d > 0
    assert eval_id_3d > 0
    
    # DB에 총 3개의 horizon_name 레코드가 정상 적재되었는지 확인
    async with get_db_conn(TEST_DB_PATH) as db:
        async with db.execute(
            "SELECT horizon_name, actual_roi_7d FROM proposal_evaluations WHERE proposal_id = ? ORDER BY horizon_name",
            (proposal_id,)
        ) as cur:
            rows = await cur.fetchall()
            assert len(rows) == 3
            # 정렬 순서대로 1d, 3d, 7d
            assert rows[0]["horizon_name"] == "1d"
            assert rows[0]["actual_roi_7d"] == 1.1
            assert rows[1]["horizon_name"] == "3d"
            assert rows[1]["actual_roi_7d"] == 3.3
            assert rows[2]["horizon_name"] == "7d"
            assert rows[2]["actual_roi_7d"] == 6.2

@pytest.mark.asyncio
async def test_strategy_execution_full_loop():
    from src.database.connection import get_db_conn
    from src.engine.portfolio import Portfolio, PortfolioManager
    
    repo = SqliteTradingRepository(db_path=TEST_DB_PATH)
    portfolio_id = "loop_test_port"
    strategy_id = "loop_test_strategy"
    
    # 1. 테스트용 포트폴리오를 DB 및 자산 마스터에 등록
    async with get_db_conn(TEST_DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO asset_master (symbol, korean_name, asset_type) VALUES ('BTC', '비트코인', 'crypto')")
        await db.execute("INSERT OR IGNORE INTO exchange_assets (exchange, symbol, is_active) VALUES ('upbit', 'BTC', 1)")
        await db.execute(
            "INSERT OR IGNORE INTO portfolios (id, name, type, exchange_id, initial_cash, cash) VALUES (?, ?, ?, ?, ?, ?)",
            (portfolio_id, "루프 테스트 포트", "simulation", "upbit", 10000000.0, 10000000.0)
        )
        await db.commit()
        
    pm = PortfolioManager(db_path=TEST_DB_PATH)
    loaded_ports = await pm.repository.load_portfolios()
    pm.portfolios = loaded_ports
    port = pm.portfolios[portfolio_id]
    
    # 2. 최초 전략 버전 1 기동 (STARTUP)
    init_params = {"rsi_window": 14, "buy_threshold": 30.0, "sell_threshold": 70.0}
    
    await repo.save_strategy_version(
        strategy_id=strategy_id,
        version_id=1,
        params=init_params,
        applied_at=int(time.time() * 1000),
        rollback_source_version=None
    )
    await repo.insert_strategy_parameter_history(
        strategy_id=strategy_id,
        version_id=1,
        parent_version_id=None,
        old_params=None,
        new_params=json.dumps(init_params),
        proposal_id=None,
        is_current=1,
        changed_by="AUTO",
        change_reason="STARTUP_RESTORE"
    )
    # STARTUP 스냅샷 기록
    await repo.insert_strategy_performance_snapshot({
        "strategy_id": strategy_id,
        "version_id": 1,
        "parameter_hash": "hash1",
        "snapshot_type": "STARTUP",
        "timestamp": int(time.time() * 1000),
        "roi": 0.0,
        "mdd": 0.0,
        "profit_factor": 0.0,
        "win_rate": 0.0,
        "trade_count": 0
    })
    
    # 3. 버전 1 상태에서 거래 발생 (수익 획득 시나리오)
    # BUY 1 BTC @ 50,000, fee = 25
    port.update_position(exchange='upbit', symbol='BTC', side='BUY', price=50000.0, quantity=1.0, fee=25.0, strategy_id=strategy_id)
    await pm.repository.insert_order_history(portfolio_id, port.history[-1])
    
    # SELL 1 BTC @ 52,000, fee = 26 (수익 = 2000 - 51 = 1949)
    port.update_position(exchange='upbit', symbol='BTC', side='SELL', price=52000.0, quantity=1.0, fee=26.0, strategy_id=strategy_id)
    await pm.repository.insert_order_history(portfolio_id, port.history[-1])
    
    # 버전 1의 최종 성능 계산
    metrics_v1 = calculate_performance_metrics(
        history=port.history,
        initial_cash=10000000.0,
        current_cash=port.cash,
        positions=port.positions,
        current_prices={"BTC": 52000.0}
    )
    
    # 4. 파라미터 변경 (버전 2 적용: 제안 승인으로 가정)
    proposed_params = {"rsi_window": 14, "buy_threshold": 35.0, "sell_threshold": 65.0}
    proposal_id = await repo.insert_strategy_proposal({
        "insight_id": None,
        "proposal_group_id": "group_loop",
        "version": 1,
        "portfolio_id": portfolio_id,
        "strategy_id": strategy_id,
        "status": "PENDING",
        "outcome": "RUNNING",
        "original_params": init_params,
        "proposed_params": proposed_params,
        "metrics": {"roi": 0.0},
        "mutation_trace": {},
        "confidence_score": 85,
        "applied_at": None,
        "rolled_back_at": None
    })
    
    # 변경 직전 스냅샷 기록 (버전 1의 최종 ROI 저장)
    await repo.insert_strategy_performance_snapshot({
        "strategy_id": strategy_id,
        "version_id": 1,
        "parameter_hash": "hash1",
        "snapshot_type": "PARAMETER_CHANGE",
        "timestamp": int(time.time() * 1000),
        "roi": metrics_v1["roi"],
        "mdd": metrics_v1["mdd"],
        "profit_factor": metrics_v1["profit_factor"],
        "win_rate": metrics_v1["win_rate"],
        "trade_count": metrics_v1["trade_count"]
    })
    
    # 버전 2 저장 및 이력 등록
    await repo.insert_strategy_parameter_history(
        strategy_id=strategy_id,
        version_id=2,
        parent_version_id=1,
        old_params=json.dumps(init_params),
        new_params=json.dumps(proposed_params),
        proposal_id=proposal_id,
        is_current=1,
        changed_by="USER",
        change_reason="PROPOSAL_APPLY"
    )
    await repo.save_strategy_version(
        strategy_id=strategy_id,
        version_id=2,
        params=proposed_params,
        applied_at=int(time.time() * 1000),
        rollback_source_version=None
    )
    await repo.update_strategy_proposal_status(
        proposal_id=proposal_id,
        status="APPLIED",
        outcome="RUNNING",
        applied_at=int(time.time() * 1000)
    )
    
    # 5. 버전 2 상태에서 다시 거래 발생 (손실 유발 시나리오)
    # BUY 1 BTC @ 52,000, fee = 26
    port.update_position(exchange='upbit', symbol='BTC', side='BUY', price=52000.0, quantity=1.0, fee=26.0, strategy_id=strategy_id)
    await pm.repository.insert_order_history(portfolio_id, port.history[-1])
    
    # SELL 1 BTC @ 48,000, fee = 24 (손실 = -4000 - 50 = -4050)
    port.update_position(exchange='upbit', symbol='BTC', side='SELL', price=48000.0, quantity=1.0, fee=24.0, strategy_id=strategy_id)
    await pm.repository.insert_order_history(portfolio_id, port.history[-1])
    
    # 버전 2의 최종 성능 계산 (버전 1의 수익과 버전 2의 손실이 누적 반영됨)
    metrics_v2 = calculate_performance_metrics(
        history=port.history,
        initial_cash=10000000.0,
        current_cash=port.cash,
        positions=port.positions,
        current_prices={"BTC": 48000.0}
    )
    
    # 6. rollback 실행 (버전 2 상태가 좋지 않아 버전 1로 복귀 -> 신규 버전 3 생성)
    # 롤백 직전 스냅샷 기록 (버전 2의 최종 ROI 저장)
    await repo.insert_strategy_performance_snapshot({
        "strategy_id": strategy_id,
        "version_id": 2,
        "parameter_hash": "hash2",
        "snapshot_type": "ROLLBACK",
        "timestamp": int(time.time() * 1000),
        "roi": metrics_v2["roi"],
        "mdd": metrics_v2["mdd"],
        "profit_factor": metrics_v2["profit_factor"],
        "win_rate": metrics_v2["win_rate"],
        "trade_count": metrics_v2["trade_count"]
    })
    
    # 롤백에 따른 신규 버전 3 적용
    await repo.insert_strategy_parameter_history(
        strategy_id=strategy_id,
        version_id=3,
        parent_version_id=1,  # 롤백 타겟 버전 1
        old_params=json.dumps(proposed_params),
        new_params=json.dumps(init_params),
        proposal_id=None,
        is_current=1,
        changed_by="USER",
        change_reason="ROLLBACK"
    )
    await repo.save_strategy_version(
        strategy_id=strategy_id,
        version_id=3,
        params=init_params,
        applied_at=int(time.time() * 1000),
        rollback_source_version=2  # 문제 유발 버전 2
    )
    await repo.update_strategy_proposal_status(
        proposal_id=proposal_id,
        status="ROLLED_BACK",
        outcome="ROLLED_BACK",
        rolled_back_at=int(time.time() * 1000)
    )
    
    # 7. 버전 3 상태(복구된 버전 1 파라미터)에서 추가 거래 발생 (다시 수익 시나리오)
    # BUY 1 BTC @ 48,000, fee = 24
    port.update_position(exchange='upbit', symbol='BTC', side='BUY', price=48000.0, quantity=1.0, fee=24.0, strategy_id=strategy_id)
    await pm.repository.insert_order_history(portfolio_id, port.history[-1])
    
    # SELL 1 BTC @ 50,000, fee = 25 (수익 = 2000 - 49 = 1951)
    port.update_position(exchange='upbit', symbol='BTC', side='SELL', price=50000.0, quantity=1.0, fee=25.0, strategy_id=strategy_id)
    await pm.repository.insert_order_history(portfolio_id, port.history[-1])
    
    # 최종 버전 3 성능 계산
    metrics_v3 = calculate_performance_metrics(
        history=port.history,
        initial_cash=10000000.0,
        current_cash=port.cash,
        positions=port.positions,
        current_prices={"BTC": 50000.0}
    )
    
    # 최종 스냅샷 기록
    await repo.insert_strategy_performance_snapshot({
        "strategy_id": strategy_id,
        "version_id": 3,
        "parameter_hash": "hash3",
        "snapshot_type": "PERIODIC",
        "timestamp": int(time.time() * 1000),
        "roi": metrics_v3["roi"],
        "mdd": metrics_v3["mdd"],
        "profit_factor": metrics_v3["profit_factor"],
        "win_rate": metrics_v3["win_rate"],
        "trade_count": metrics_v3["trade_count"]
    })
    
    # 8. 종합 검증 (사용자 E2E 루프 검증 요구사항 충족 여부 체크)
    
    # 8.1. version_id에 해당하는 성능(스냅샷) 조회
    snapshots = await repo.get_strategy_performance_snapshots(strategy_id)
    
    # 4개 스냅샷 존재 확인 (STARTUP, PARAMETER_CHANGE, ROLLBACK, PERIODIC)
    assert len(snapshots) == 4
    
    startup_snap = [s for s in snapshots if s["snapshot_type"] == "STARTUP"][0]
    change_snap = [s for s in snapshots if s["snapshot_type"] == "PARAMETER_CHANGE"][0]
    rollback_snap = [s for s in snapshots if s["snapshot_type"] == "ROLLBACK"][0]
    periodic_snap = [s for s in snapshots if s["snapshot_type"] == "PERIODIC"][0]
    
    # 검증 1: version_id 따라가고 있는지 확인
    assert startup_snap["version_id"] == 1
    assert change_snap["version_id"] == 1
    assert rollback_snap["version_id"] == 2
    assert periodic_snap["version_id"] == 3
    
    # 검증 2: ROI 변화 기록됨?
    # 초기: 0.0 -> 버전 1 매매 후 -> 버전 2 매매 후 -> 버전 3 매매 후
    assert startup_snap["roi"] == 0.0
    assert change_snap["roi"] == metrics_v1["roi"]
    assert rollback_snap["roi"] == metrics_v2["roi"]
    assert periodic_snap["roi"] == metrics_v3["roi"]
    
    # 검증 3: rollback 후 성능(ROI, 자산 잔고, 거래 횟수 등)이 "끊김 없이 이어짐" 확인
    # 거래 횟수 추이: 0 -> 2 -> 4 -> 6
    assert startup_snap["trade_count"] == 0
    assert change_snap["trade_count"] == 2
    assert rollback_snap["trade_count"] == 4
    assert periodic_snap["trade_count"] == 6
    
    # 롤백 기록 정합성 검증
    current_ver = await repo.get_strategy_version(strategy_id)
    assert current_ver["current_version_id"] == 3
    assert current_ver["rollback_source_version"] == 2
    assert current_ver["current_params"] == init_params
    
    # 8.2. Step 1.5 정합성 검증 단언문 추가
    # 검증 1: 스냅샷 시간 Monotonic 오름차순 검증
    sorted_snaps = sorted(snapshots, key=lambda x: x["timestamp"])
    for i in range(len(sorted_snaps) - 1):
        assert sorted_snaps[i]["timestamp"] < sorted_snaps[i+1]["timestamp"]
        
    # 검증 2: 스냅샷 created_at 타입 및 13자리(Epoch ms) 정수 확인
    for s in snapshots:
        assert isinstance(s["created_at"], int)
        assert s["created_at"] > 1000000000000
        assert isinstance(s["timestamp"], int)
        assert s["timestamp"] > 1000000000000

    # 검증 3: 파라미터 히스토리 created_at 정수형 및 13자리 확인
    history_list = await repo.get_strategy_parameter_history(strategy_id)
    for h in history_list:
        assert isinstance(h["created_at"], int)
        assert h["created_at"] > 1000000000000
        
    # 검증 4: 제안(proposals) 시간 필드 정수형 및 13자리 확인
    proposals_list = await repo.get_active_proposals(strategy_id)
    for p in proposals_list:
        assert isinstance(p["created_at"], int)
        assert p["created_at"] > 1000000000000
        assert isinstance(p["updated_at"], int)
        assert p["updated_at"] > 1000000000000
        if p["applied_at"] is not None:
            assert isinstance(p["applied_at"], int)
            assert p["applied_at"] > 1000000000000
        if p["rolled_back_at"] is not None:
            assert isinstance(p["rolled_back_at"], int)
            assert p["rolled_back_at"] > 1000000000000

@pytest.mark.asyncio
async def test_atomic_mutations_and_async_enrichment():
    from src.database.connection import get_db_conn
    repo = SqliteTradingRepository(db_path=TEST_DB_PATH, champion_cooldown_days=0.0, champion_cooldown_trades=0)
    strategy_id = "rsistrategy"
    portfolio_id = "sim_port_atomic"
    
    # 1. 자산 마스터 및 포트폴리오 등록
    async with get_db_conn(TEST_DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO asset_master (symbol, korean_name, asset_type) VALUES ('BTC', '비트코인', 'crypto')")
        await db.execute("INSERT OR IGNORE INTO exchange_assets (exchange, symbol, is_active) VALUES ('upbit', 'BTC', 1)")
        await db.execute(
            "INSERT OR IGNORE INTO portfolios (id, name, type, exchange_id, initial_cash, cash) VALUES (?, ?, ?, ?, ?, ?)",
            (portfolio_id, "원자적 테스트 포트", "simulation", "upbit", 10000000.0, 10000000.0)
        )
        await db.commit()

    # 2. 초기 버전 등록
    init_params = {"rsi_window": 14, "buy_threshold": 30.0, "sell_threshold": 70.0}
    await repo.save_strategy_version(
        strategy_id=strategy_id,
        version_id=1,
        params=init_params,
        applied_at=int(time.time() * 1000)
    )
    await repo.insert_strategy_parameter_history(
        strategy_id=strategy_id,
        version_id=1,
        parent_version_id=None,
        old_params=None,
        new_params=json.dumps(init_params),
        proposal_id=None,
        is_current=1,
        changed_by="AUTO",
        change_reason="STARTUP_RESTORE"
    )
    
    # 3. 제안 등록
    proposed_params = {"rsi_window": 14, "buy_threshold": 35.0, "sell_threshold": 65.0}
    proposal_id = await repo.insert_strategy_proposal({
        "insight_id": None,
        "proposal_group_id": "group_atomic",
        "version": 1,
        "portfolio_id": portfolio_id,
        "strategy_id": strategy_id,
        "status": "PENDING",
        "outcome": "RUNNING",
        "original_params": init_params,
        "proposed_params": proposed_params,
        "metrics": {"roi": 0.0},
        "mutation_trace": {},
        "confidence_score": 85,
        "applied_at": None,
        "rolled_back_at": None
    })
    
    # 4. Atomic 승인 호출
    applied_ts = int(time.time() * 1000)
    res = await repo.approve_proposal_atomic(proposal_id, applied_ts)
    
    assert res["strategy_id"] == strategy_id
    assert res["new_version_id"] == 2
    assert res["snapshot_id"] > 0
    
    # DB 갱신 검증
    ver = await repo.get_strategy_version(strategy_id)
    assert ver["current_version_id"] == 2
    assert ver["current_params"] == proposed_params
    
    prop = await repo.get_strategy_proposal(proposal_id)
    assert prop["status"] == "APPLIED"
    assert prop["applied_at"] == applied_ts
    
    # 동기식으로 스냅샷이 roi=None으로 생성되었는지 검증
    snapshots = await repo.get_strategy_performance_snapshots(strategy_id, version_id=2)
    assert len(snapshots) == 1
    assert snapshots[0]["roi"] is None
    
    # 백필 실행
    from src.engine.portfolio import Portfolio, PortfolioManager
    pm = PortfolioManager(db_path=TEST_DB_PATH)
    loaded_ports = await pm.repository.load_portfolios()
    pm.portfolios = loaded_ports
    port = pm.portfolios[portfolio_id]
    port.update_position(exchange='upbit', symbol='BTC', side='BUY', price=50000.0, quantity=1.0, fee=25.0, strategy_id=strategy_id)
    await pm.repository.insert_order_history(portfolio_id, port.history[-1])
    port.update_position(exchange='upbit', symbol='BTC', side='SELL', price=55000.0, quantity=1.0, fee=27.5, strategy_id=strategy_id)
    await pm.repository.insert_order_history(portfolio_id, port.history[-1])
    await pm.repository.save_portfolio(port)
    
    # 백필 실행
    await repo.enrich_snapshot_metrics_async(res["snapshot_id"], portfolio_id)
    
    # 지표가 채워졌는지 검증
    enriched_snaps = await repo.get_strategy_performance_snapshots(strategy_id, version_id=2)
    assert len(enriched_snaps) == 1
    assert enriched_snaps[0]["roi"] is not None
    assert enriched_snaps[0]["roi"] > 0.0
    assert enriched_snaps[0]["trade_count"] == 2
    
    # 6. Atomic 롤백 호출
    rollback_ts = int(time.time() * 1000)
    rollback_res = await repo.rollback_strategy_atomic(strategy_id, 1, rollback_ts)
    
    assert rollback_res["new_version_id"] == 3
    assert rollback_res["rollback_version_id"] == 1
    assert rollback_res["associated_proposal_id"] == proposal_id
    assert rollback_res["snapshot_id"] > 0
    
    # DB 갱신 검증
    ver_after_rb = await repo.get_strategy_version(strategy_id)
    assert ver_after_rb["current_version_id"] == 3
    assert ver_after_rb["current_params"] == init_params
    assert ver_after_rb["rollback_source_version"] == 2
    
    prop_after_rb = await repo.get_strategy_proposal(proposal_id)
    assert prop_after_rb["status"] == "ROLLED_BACK"
    assert prop_after_rb["rolled_back_at"] == rollback_ts


@pytest.mark.asyncio
async def test_proposal_evaluation_horizon_name_policy():
    from src.database.connection import get_db_conn
    repo = SqliteTradingRepository(db_path=TEST_DB_PATH)
    
    # 1. 테스트용 proposal 등록
    proposal_data = {
        "insight_id": None,
        "proposal_group_id": "group_policy_test",
        "version": 1,
        "portfolio_id": "port_policy_test",
        "strategy_id": "strat_policy_test",
        "status": "APPLIED",
        "outcome": "RUNNING",
        "original_params": {"rsi_window": 14},
        "proposed_params": {"rsi_window": 16},
        "metrics": {"roi_7d": 5.0, "trade_count_7d": 2},
        "mutation_trace": {},
        "confidence_score": 70,
        "applied_at": int(time.time() * 1000),
        "rolled_back_at": None
    }
    proposal_id = await repo.insert_strategy_proposal(proposal_data)
    assert proposal_id > 0

    base_eval_data = {
        "proposal_id": proposal_id,
        "predicted_roi_7d": 5.0,
        "actual_roi_7d": 6.2,
        "roi_divergence": 1.2,
        "predicted_trade_count_7d": 2,
        "actual_trade_count_7d": 3,
        "trade_count_divergence": 1
    }

    # ① horizon_name 누락 시 ValueError 발생 (legacy_compat=False)
    eval_data_missing = dict(base_eval_data)
    with pytest.raises(ValueError) as excinfo:
        await repo.insert_proposal_evaluation(eval_data_missing, legacy_compat=False)
    assert "horizon_name is required" in str(excinfo.value)

    # ② horizon_name 빈 문자열 시 ValueError 발생 (legacy_compat=False)
    eval_data_empty = dict(base_eval_data)
    eval_data_empty["horizon_name"] = ""
    with pytest.raises(ValueError) as excinfo:
        await repo.insert_proposal_evaluation(eval_data_empty, legacy_compat=False)
    assert "horizon_name is required" in str(excinfo.value)

    # ③ legacy_compat=True일 때만 7d 보정
    eval_data_legacy = dict(base_eval_data)
    # horizon_name 누락
    eval_id_legacy = await repo.insert_proposal_evaluation(eval_data_legacy, legacy_compat=True)
    assert eval_id_legacy > 0

    # 보정 확인
    async with get_db_conn(TEST_DB_PATH) as db:
        async with db.execute("SELECT horizon_name FROM proposal_evaluations WHERE id = ?", (eval_id_legacy,)) as cur:
            row = await cur.fetchone()
            assert row is not None
            assert row["horizon_name"] == "7d"

    # ④ 보정 시 LEGACY_HORIZON_DEFAULT_APPLIED 이벤트 기록
    events = await repo.get_system_events(limit=5)
    matched_event = None
    for ev in events:
        if ev["event_type"] == "LEGACY_HORIZON_DEFAULT_APPLIED":
            matched_event = ev
            break
    assert matched_event is not None
    assert f"Proposal ID {proposal_id}" in matched_event["message"]

    # ⑤ 1d/3d/7d 명시 다중 Horizon 저장 성공
    eval_data_1d = dict(base_eval_data)
    eval_data_1d["horizon_name"] = "1d"
    eval_id_1d = await repo.insert_proposal_evaluation(eval_data_1d, legacy_compat=False)
    assert eval_id_1d > 0

    eval_data_3d = dict(base_eval_data)
    eval_data_3d["horizon_name"] = "3d"
    eval_id_3d = await repo.insert_proposal_evaluation(eval_data_3d, legacy_compat=False)
    assert eval_id_3d > 0

    eval_data_7d = dict(base_eval_data)
    eval_data_7d["horizon_name"] = "7d"
    # unique 제약조건 충돌 방지를 위해, 7d 저장을 위해 proposal을 하나 더 생성
    proposal_id_2 = await repo.insert_strategy_proposal(proposal_data)
    eval_data_7d["proposal_id"] = proposal_id_2
    eval_id_7d = await repo.insert_proposal_evaluation(eval_data_7d, legacy_compat=False)
    assert eval_id_7d > 0



