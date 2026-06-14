# -*- coding: utf-8 -*-

import os
import time
import pytest
import yaml
from typing import Any
from src.config.manager import ConfigManager
from src.database.connection import get_db_conn
from src.database.schema import init_db
from src.database.repository import SqliteTradingRepository
from src.services.shadow_eval_service import ShadowEvaluationService
from src.engine.girs_types import FeatureSnapshot

TEST_DB_PATH = "tests/test_baseline_snapshot.db"
SAFETY_TEST_YAML = "config/settings_safety_gate_test.yaml"

@pytest.fixture(scope="module", autouse=True)
def setup_teardown():
    # 실행 전 정리
    for p in [TEST_DB_PATH, TEST_DB_PATH + "-wal", TEST_DB_PATH + "-shm", SAFETY_TEST_YAML]:
        if os.path.exists(p):
            os.remove(p)
            
    yield
    
    # 실행 후 정리
    for p in [TEST_DB_PATH, TEST_DB_PATH + "-wal", TEST_DB_PATH + "-shm", SAFETY_TEST_YAML]:
        if os.path.exists(p):
            os.remove(p)

class MockConfig:
    def __init__(self, db_path: str):
        self.db_path = db_path

    def get(self, key: str, default: Any = None) -> Any:
        if key == 'system.db_path':
            return self.db_path
        elif key == 'system.evaluation_lock_timeout_seconds':
            return 300
        elif key == 'system.evaluation_max_retry_count':
            return 3
        return default

@pytest.mark.asyncio
async def test_safety_gate_fail_fast():
    """
    live_trading_enabled=true 와 auto_strategy_promotion_enabled=true 가 동시에 켜진 경우
    ATS_EXPLICIT_REAL_TRADING_OVERRIDE="true" 가 없으면 ValueError 가 나는지 검증합니다.
    """
    # 임시 설정 파일 작성
    test_config = {
        "system": {
            "db_path": TEST_DB_PATH,
            "live_trading_enabled": True,
            "auto_strategy_promotion_enabled": True,
            "operation_mode": "shadow",
            "girs_shadow_mode": True
        }
    }
    
    with open(SAFETY_TEST_YAML, "w", encoding="utf-8") as f:
        yaml.dump(test_config, f)
        
    # 1) override 환경 변수 없는 경우 -> ValueError 기대
    if "ATS_EXPLICIT_REAL_TRADING_OVERRIDE" in os.environ:
        del os.environ["ATS_EXPLICIT_REAL_TRADING_OVERRIDE"]
        
    with pytest.raises(ValueError) as excinfo:
        ConfigManager(SAFETY_TEST_YAML)
    assert "CRITICAL SAFETY GATE VIOLATION" in str(excinfo.value)
    
    # 2) override 환경 변수가 "true" 인 경우 -> 정상 로드 기대
    os.environ["ATS_EXPLICIT_REAL_TRADING_OVERRIDE"] = "true"
    try:
        cfg = ConfigManager(SAFETY_TEST_YAML)
        assert cfg.get("system.live_trading_enabled") is True
    finally:
        if "ATS_EXPLICIT_REAL_TRADING_OVERRIDE" in os.environ:
            del os.environ["ATS_EXPLICIT_REAL_TRADING_OVERRIDE"]
            
    # 테스트 종료 후 파일 제거
    if os.path.exists(SAFETY_TEST_YAML):
        os.remove(SAFETY_TEST_YAML)

@pytest.mark.asyncio
async def test_baseline_snapshot_정합성_및_TTL_삭제_대응():
    """
    1. 시작/종료 가격의 exchange, symbol, interval, market_type 기준 일치 여부 검증
    2. 원본 candles 데이터가 TTL cleanup으로 완전 삭제된 이후에도,
       Baseline Snapshot에 저장된 baseline_value 가 있다면 사후 ROI 평가가 정상 수행되는지 검증
    3. 미래 데이터(due_at 초과 시각의 candles) 참조 차단 검증
    """
    if os.path.exists(TEST_DB_PATH):
        os.remove(TEST_DB_PATH)
        
    await init_db(TEST_DB_PATH)
    repo = SqliteTradingRepository(db_path=TEST_DB_PATH)
    config = MockConfig(TEST_DB_PATH)
    
    # 1. Mock Proposal 생성
    proposal_data = {
        "insight_id": None,
        "proposal_group_id": "group_baseline_test",
        "version": 1,
        "portfolio_id": "port_baseline_test",
        "strategy_id": "strat_baseline_test",
        "status": "APPLIED",
        "outcome": "RUNNING",
        "original_params": {"rsi_window": 14},
        "proposed_params": {"rsi_window": 16},
        "metrics": {"roi_7d": 5.0, "trade_count_7d": 2},
        "mutation_trace": {},
        "confidence_score": 75,
        "applied_at": int(time.time() * 1000),
        "rolled_back_at": None
    }
    proposal_id = await repo.insert_strategy_proposal(proposal_data)
    
    # 2. 10m PENDING 평가 등록 (시작 캔들 가격: 50000.0)
    now = int(time.time())
    due_at = now + 600
    pe_id = await repo.insert_proposal_evaluation({
        "proposal_id": proposal_id,
        "horizon_name": "10m",
        "due_at": due_at,
        "predicted_roi_7d": 5.0,
        "actual_roi_7d": 0.0,
        "roi_divergence": 0.0,
        "predicted_trade_count_7d": 10,
        "actual_trade_count_7d": 0,
        "trade_count_divergence": 0,
        "evaluation_status": "PENDING"
    }, legacy_compat=False)
    
    # 3. 시작 시점 캔들 (timestamp = now) 및 만기 캔들 (timestamp = due_at) 주입
    # 10m 호라이즌이므로 시작 시점의 가격 기준 (timestamp = now)
    # 1분봉으로 간주 (interval = 60)
    async with get_db_conn(TEST_DB_PATH) as db:
        # 1) 시작 캔들 (가격: 50000.0)
        await db.execute("""
            INSERT INTO candles (exchange_id, symbol, interval, timestamp, open, high, low, close, volume)
            VALUES ('upbit', 'BTC', 60, ?, 50000.0, 50000.0, 50000.0, 50000.0, 1.0)
        """, (now * 1000,))
        # 2) 만기 시점 캔들 (가격: 52500.0, ROI 5.0% 상승)
        await db.execute("""
            INSERT INTO candles (exchange_id, symbol, interval, timestamp, open, high, low, close, volume)
            VALUES ('upbit', 'BTC', 60, ?, 52500.0, 52500.0, 52500.0, 52500.0, 1.0)
        """, (due_at * 1000,))
        # 3) 미래 캔들 (timestamp = due_at + 120, 가격: 60000.0) -> 만기 이후 데이터라 평가에 참조되면 안 됨!
        await db.execute("""
            INSERT INTO candles (exchange_id, symbol, interval, timestamp, open, high, low, close, volume)
            VALUES ('upbit', 'BTC', 60, ?, 60000.0, 60000.0, 60000.0, 60000.0, 1.0)
        """, ((due_at + 120) * 1000,))
        await db.commit()

    # 4. Baseline 스냅샷 캡처 시뮬레이션
    # due_at - 600 시각의 캔들을 읽어 baseline_value 로 캡처 및 update
    snap = FeatureSnapshot(
        price_features={"close": 50000.0},
        liquidity_features={"spread": 0.001, "volume": 1000.0, "depth": 5000.0},
        regime_features={"regime_index": 0.0},
        exchange_id="upbit",
        symbol="BTC",
        market_type="crypto"
    )
    # DB에 baseline_value 갱신
    async with get_db_conn(TEST_DB_PATH) as db:
        await db.execute(
            "UPDATE proposal_evaluations SET baseline_value = ? WHERE id = ?", (50000.0, pe_id)
        )
        await db.commit()

    # 5. TTL Cleanup으로 시작 시점의 캔들을 완전히 삭제 (50000.0 가격의 캔들)
    async with get_db_conn(TEST_DB_PATH) as db:
        await db.execute("DELETE FROM candles WHERE timestamp = ?", (now * 1000,))
        await db.commit()
        
    # 시작 시각 캔들이 삭제되었는지 확인
    async with get_db_conn(TEST_DB_PATH) as db:
        async with db.execute("SELECT * FROM candles WHERE timestamp = ?", (now * 1000,)) as cur:
            assert await cur.fetchone() is None, "시작 캔들이 DB 상에서 지워져야 합니다."

    # 6. Shadow Evaluation Service 기동 및 평가 수행
    service = ShadowEvaluationService(config_manager=config, event_bus=None)
    # mock repository 바인딩
    service.repository = repo
    
    # claim 선점
    claim_ok = await repo.claim_evaluation(pe_id, int(time.time()))
    assert claim_ok is True
    
    # 평가 실행
    await service._evaluate_record(pe_id, due_at, "10m", 600, proposal_id, "upbit", "BTC", "crypto")
    
    # 7. 평가 결과 검증
    evals = await repo.get_proposal_evaluations(proposal_id)
    assert len(evals) == 1
    ev = evals[0]
    assert ev["evaluation_status"] == "COMPLETED"
    assert ev["actual_roi_7d"] == 5.0  # (52500 - 50000) / 50000 * 100
    assert ev["roi_divergence"] == 0.0  # 5.0 (predicted) - 5.0 (actual) = 0.0
