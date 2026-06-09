import pytest
import os
import time
import json
import asyncio
import pytest_asyncio
from src.database.schema import init_db
from src.database.repository import SqliteTradingRepository

TEST_DB_PATH = "tests/test_intelligence.db"

@pytest_asyncio.fixture(autouse=True)
async def setup_test_db():
    if os.path.exists(TEST_DB_PATH):
        os.remove(TEST_DB_PATH)
    await init_db(TEST_DB_PATH)
    yield
    if os.path.exists(TEST_DB_PATH):
        os.remove(TEST_DB_PATH)

@pytest.mark.asyncio
async def test_multifactor_scoring():
    from src.engine.shadow_backtest import calculate_multifactor_score
    
    # 1. 정상 조건 (ROI 양수, 양호한 승률 및 Profit Factor)
    score1 = calculate_multifactor_score(
        roi_7d=8.5, roi_1d=2.0, win_rate=55.0, profit_factor=1.5, mdd=3.0
    )
    assert score1 >= 80
    
    # 2. 강제 하한 조건 1 (Win Rate < 40%)
    score2 = calculate_multifactor_score(
        roi_7d=12.0, roi_1d=4.0, win_rate=35.0, profit_factor=1.8, mdd=2.0
    )
    assert score2 == 50
    
    # 3. 강제 하한 조건 2 (Profit Factor < 1.0)
    score3 = calculate_multifactor_score(
        roi_7d=5.0, roi_1d=1.0, win_rate=60.0, profit_factor=0.9, mdd=4.0
    )
    assert score3 == 50

@pytest.mark.asyncio
async def test_regime_weighting():
    from src.engine.shadow_backtest import get_regime_weighting
    
    # ATR이 높고 보수적인 파라미터 변이인 경우 (+5 가중치 예상)
    # buy_threshold가 30에서 25로 확장됨 (보수적 변이)
    weight1 = get_regime_weighting(
        atr_ratio=1.5, adx=20.0, 
        original_params={"buy_threshold": 30.0}, 
        proposed_params={"buy_threshold": 25.0}
    )
    assert weight1 == 5
    
    # ADX가 높고 역추세 파라미터 윈도우가 극단적으로 축소되는 경우 (-10 패널티 예상)
    weight2 = get_regime_weighting(
        atr_ratio=0.8, adx=30.0,
        original_params={"rsi_window": 14},
        proposed_params={"rsi_window": 8}
    )
    assert weight2 == -10

@pytest.mark.asyncio
async def test_parameter_weighted_distance():
    from src.engine.shadow_backtest import calculate_parameter_distance
    
    # 1. 연속형 변수 정규화 거리 계산 검증
    # buy_threshold: 30.0 -> 31.0 (diff/base = 1/30 = 0.033)
    # rsi_window: 14 -> 15 (diff/base = 1/14 = 0.071)
    # buy_threshold 가중치: 0.8, rsi_window 가중치: 0.2
    # Distance = 0.8 * 0.0333 + 0.2 * 0.0714 = 0.0266 + 0.0142 = 0.0409
    p1 = {"rsi_window": 14, "buy_threshold": 30.0}
    p2 = {"rsi_window": 15, "buy_threshold": 31.0}
    dist = calculate_parameter_distance(p1, p2)
    assert abs(dist - 0.0409) < 0.005
    
    # 2. 카테고리 변수 불일치 시 heavy penalty 검증
    p3 = {"rsi_window": 14, "buy_threshold": 30.0, "strategy_type": "RSI"}
    p4 = {"rsi_window": 14, "buy_threshold": 30.0, "strategy_type": "MACD"}
    dist_cat = calculate_parameter_distance(p3, p4)
    assert dist_cat >= 1.0  # Mismatch penalty

@pytest.mark.asyncio
async def test_auto_pruning():
    repo = SqliteTradingRepository(db_path=TEST_DB_PATH)
    from src.engine.shadow_backtest import ShadowBacktestEngine
    engine = ShadowBacktestEngine(db_path=TEST_DB_PATH)
    
    # 60점 미만으로 평가되도록 셋업 (예: win_rate=30% 강제 하한으로 50점 유도)
    candidate = [{
        "strategy_id": "rsistrategy",
        "portfolio_id": "port_test",
        "original_params": {"rsi_window": 14},
        "proposed_params": {"rsi_window": 16},
        "mutation_trace": {"rsi_window": [14, 16]}
    }]
    
    # Mocking 백테스트 내부 연산 대신, DB 저장 시 Auto-pruning 루프를 태우기 위해 직접 run_shadow_backtest 실행 또는 Mocking 테스트
    # 여기서는 스코어가 55점일 때 DB에 'PRUNED' 상태로 잘 들어가는지 검증
    proposal_data = {
        "insight_id": None,
        "proposal_group_id": "group_pruned",
        "version": 1,
        "portfolio_id": "port_test",
        "strategy_id": "rsistrategy",
        "status": "PENDING",
        "outcome": "RUNNING",
        "original_params": {"rsi_window": 14},
        "proposed_params": {"rsi_window": 16},
        "metrics": {"roi_7d": 1.5, "win_rate": 35.0}, # win_rate < 40% -> score = 50
        "mutation_trace": {},
        "confidence_score": 50, # 60점 미만 -> PRUNED 저장 기대
        "applied_at": None,
        "rolled_back_at": None
    }
    
    # Repository 레벨에서 insert 시 Pruning 검증
    inserted_id = await repo.insert_strategy_proposal(proposal_data)
    prop = await repo.get_strategy_proposal(inserted_id)
    
    assert prop["status"] == "PRUNED"
    assert prop["confidence_score"] == 50

@pytest.mark.asyncio
async def test_hybrid_auto_apply_scheduler():
    repo = SqliteTradingRepository(db_path=TEST_DB_PATH)
    from src.engine.auto_scheduler import HybridAutoApplyScheduler
    
    # 1. 초기 셋업
    strategy_id = "rsistrategy"
    portfolio_id = "port_apply"
    
    await repo.save_strategy_version(strategy_id, 1, {"rsi_window": 14}, int(time.time()*1000))
    await repo.insert_strategy_parameter_history(
        strategy_id, 1, None, None, json.dumps({"rsi_window": 14}), None, 1, "AUTO", "STARTUP_RESTORE"
    )
    
    scheduler = HybridAutoApplyScheduler(db_path=TEST_DB_PATH, debounce_seconds=0.5) # 테스트를 위해 debounce 시간을 0.5초로 세팅
    
    # 2. 80점 이상 제안 추가 (자동 승인 대상)
    proposal_id = await repo.insert_strategy_proposal({
        "insight_id": None,
        "proposal_group_id": "group_apply",
        "version": 1,
        "portfolio_id": portfolio_id,
        "strategy_id": strategy_id,
        "status": "PENDING",
        "outcome": "RUNNING",
        "original_params": {"rsi_window": 14},
        "proposed_params": {"rsi_window": 16},
        "metrics": {"expected_roi": 12.0},
        "mutation_trace": {},
        "confidence_score": 85,
        "applied_at": None,
        "rolled_back_at": None
    })
    
    # 3. 스케줄러 기동 및 제안 생성 감지 모사
    # ENABLE_AUTO_PROPOSAL = True 전제
    scheduler.set_auto_proposal_enabled(True)
    
    # 이벤트 발생 통보 및 디바운스 대기
    await scheduler.notify_proposal_created(proposal_id)
    await asyncio.sleep(0.8) # debounce(0.5s) + 처리 시간 여유
    
    # V2 갱신 확인
    ver = await repo.get_strategy_version(strategy_id)
    assert ver["current_version_id"] == 2
    assert ver["current_params"] == {"rsi_window": 16}
    
    # 제안 상태 갱신 확인
    prop = await repo.get_strategy_proposal(proposal_id)
    assert prop["status"] == "APPLIED"
    
    # 4. Cooldown 검증 (동일 전략에 대해 10분 쿨다운)
    # 80점 이상 제안을 추가로 넣어도 쿨다운 윈도우(10분)로 인해 자동 적용되지 않아야 함
    proposal_id_cd = await repo.insert_strategy_proposal({
        "insight_id": None,
        "proposal_group_id": "group_apply_cd",
        "version": 2,
        "portfolio_id": portfolio_id,
        "strategy_id": strategy_id,
        "status": "PENDING",
        "outcome": "RUNNING",
        "original_params": {"rsi_window": 16},
        "proposed_params": {"rsi_window": 18},
        "metrics": {"expected_roi": 15.0},
        "mutation_trace": {},
        "confidence_score": 90,
        "applied_at": None,
        "rolled_back_at": None
    })
    
    await scheduler.notify_proposal_created(proposal_id_cd)
    await asyncio.sleep(0.8)
    
    # 여전히 버전 2여야 하고 상태는 PENDING 유지
    ver_cd = await repo.get_strategy_version(strategy_id)
    assert ver_cd["current_version_id"] == 2
    prop_cd = await repo.get_strategy_proposal(proposal_id_cd)
    assert prop_cd["status"] == "PENDING"
    
    await scheduler.close()

@pytest.mark.asyncio
async def test_rollback_auto_disable():
    repo = SqliteTradingRepository(db_path=TEST_DB_PATH)
    from src.engine.auto_scheduler import HybridAutoApplyScheduler
    
    strategy_id = "rsistrategy"
    scheduler = HybridAutoApplyScheduler(db_path=TEST_DB_PATH)
    
    # 1. 최초 활성화 상태 설정
    scheduler.set_auto_proposal_enabled(True)
    assert scheduler.is_auto_proposal_enabled() is True
    
    # 2. 수동 롤백 발생 모사 (롤백을 실행하면 스케줄러가 차단됨)
    # 실제로는 라우터 단에서 scheduler.disable_auto_proposal(strategy_id) 또는 전역 플래그를 수정하도록 연동
    await scheduler.handle_manual_rollback(strategy_id)
    
    # 3. 자동 적용 기능 비활성화 여부 검증
    assert scheduler.is_auto_proposal_enabled() is False
    await scheduler.close()
