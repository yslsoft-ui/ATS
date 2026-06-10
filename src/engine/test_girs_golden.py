# -*- coding: utf-8 -*-

import pytest
import math
from src.engine.girs_types import FeatureSnapshot, FeatureContractValidator
from src.engine.girs_scorer import MockONNXModel, GIRSScorer, verify_score_scales

def test_feature_contract_validation():
    # Feature ranges 설정
    feature_ranges = {
        "close": (0.0, 100000.0),
        "returns": (-0.3, 0.3),
        "volatility": (0.0, 1.0),
        "spread": (0.0, 0.1),
        "volume": (0.0, 10000000.0),
        "depth": (0.0, 10000000.0),
        "regime_index": (0.0, 10.0),
    }

    validator = FeatureContractValidator(
        expected_price_keys=["close", "returns", "volatility"],
        expected_liquidity_keys=["spread", "volume", "depth"],
        expected_regime_keys=["regime_index"],
        feature_ranges=feature_ranges,
        stale_threshold=3,  # 빠른 테스트를 위해 threshold를 3으로
        tick_threshold=5,
        volume_threshold=100.0
    )

    # 1. 정상 스냅샷 검증
    snap = FeatureSnapshot(
        price_features={"close": 50000.0, "returns": 0.01, "volatility": 0.15},
        liquidity_features={"spread": 0.002, "volume": 5000.0, "depth": 10000.0},
        regime_features={"regime_index": 2.0}
    )

    validated_snap, is_fallback, meta = validator.validate_and_clamp(
        snapshot=snap,
        market_session="regular_trading",
        expected_tick_count=10,
        recent_volume=2000.0
    )

    assert not is_fallback
    assert validated_snap.price_features["close"] == 50000.0
    assert validated_snap.price_features["returns"] == 0.01
    assert validator.metrics.total_checks == 1
    assert len(meta["raw_values_log"]) == 0

    # 2. NaN/inf 포함 스냅샷 검증 -> Fallback 발생해야 함
    snap_nan = FeatureSnapshot(
        price_features={"close": 50000.0, "returns": float('nan'), "volatility": 0.15},
        liquidity_features={"spread": 0.002, "volume": 5000.0, "depth": 10000.0},
        regime_features={"regime_index": 2.0}
    )

    _, is_fallback_nan, meta_nan = validator.validate_and_clamp(
        snapshot=snap_nan,
        market_session="regular_trading",
        expected_tick_count=10,
        recent_volume=2000.0
    )
    assert is_fallback_nan
    assert validator.metrics.feature_invalid_count == 1

    # 3. 범위 초과 피처 (returns = 0.5 > 0.3) -> Clamp 발생 및 원본 로깅 확인
    snap_overflow = FeatureSnapshot(
        price_features={"close": 50000.0, "returns": 0.5, "volatility": 0.15},
        liquidity_features={"spread": 0.002, "volume": 5000.0, "depth": 10000.0},
        regime_features={"regime_index": 2.0}
    )

    validated_snap_overflow, is_fallback_overflow, meta_overflow = validator.validate_and_clamp(
        snapshot=snap_overflow,
        market_session="regular_trading",
        expected_tick_count=10,
        recent_volume=2000.0
    )

    # 10% 미만 clamp 이므로 fallback은 발생하면 안 됨 (clamp 횟수 / checks가 작음)
    assert not is_fallback_overflow
    assert validated_snap_overflow.price_features["returns"] == 0.3  # clamped to max limit
    assert "returns" in meta_overflow["raw_values_log"]
    assert meta_overflow["raw_values_log"]["returns"] == 0.5
    assert meta_overflow["clamp_counts"]["returns"] == 1

    # 4. 장중 & 고유동성 조건부 stale count (zero-variance) 검증
    # 깨끗한 상태의 새 validator를 만들어 테스트
    validator_stale = FeatureContractValidator(
        expected_price_keys=["close", "returns", "volatility"],
        expected_liquidity_keys=["spread", "volume", "depth"],
        expected_regime_keys=["regime_index"],
        feature_ranges=feature_ranges,
        stale_threshold=3,
        tick_threshold=5,
        volume_threshold=100.0
    )

    snap_stale = FeatureSnapshot(
        price_features={"close": 50000.0, "returns": 0.01, "volatility": 0.15},
        liquidity_features={"spread": 0.002, "volume": 5000.0, "depth": 10000.0},
        regime_features={"regime_index": 2.0}
    )

    # 1번째 입력 (stale count = 0)
    _, is_fallback_stale_0, meta_stale_0 = validator_stale.validate_and_clamp(
        snapshot=snap_stale,
        market_session="regular_trading",
        expected_tick_count=10,
        recent_volume=2000.0
    )
    assert not is_fallback_stale_0
    assert meta_stale_0["stale_counts"].get("close", 0) == 0

    # 2번째 입력 (stale count = 1)
    _, is_fallback_stale_1, meta_stale_1 = validator_stale.validate_and_clamp(
        snapshot=snap_stale,
        market_session="regular_trading",
        expected_tick_count=10,
        recent_volume=2000.0
    )
    assert not is_fallback_stale_1
    assert meta_stale_1["stale_counts"]["close"] == 1

    # 3번째 입력 -> stale_count = 2 < stale_threshold = 3
    _, is_fallback_stale_2, meta_stale_2 = validator_stale.validate_and_clamp(
        snapshot=snap_stale,
        market_session="regular_trading",
        expected_tick_count=10,
        recent_volume=2000.0
    )
    assert not is_fallback_stale_2
    assert meta_stale_2["stale_counts"]["close"] == 2

    # 4번째 입력 -> stale_count = 3 >= stale_threshold -> Fallback 활성화
    _, is_fallback_stale_3, meta_stale_3 = validator_stale.validate_and_clamp(
        snapshot=snap_stale,
        market_session="regular_trading",
        expected_tick_count=10,
        recent_volume=2000.0
    )
    assert is_fallback_stale_3
    assert meta_stale_3["stale_counts"]["close"] == 3

    # 5. 비거래시간(regular_trading 아님) 또는 저유동성 시 stale count 누적이 안 됨을 확인
    validator_stale_guard = FeatureContractValidator(
        expected_price_keys=["close", "returns", "volatility"],
        expected_liquidity_keys=["spread", "volume", "depth"],
        expected_regime_keys=["regime_index"],
        feature_ranges=feature_ranges,
        stale_threshold=2,
        tick_threshold=5,
        volume_threshold=100.0
    )

    # 1번째 정상 체크
    validator_stale_guard.validate_and_clamp(
        snapshot=snap,
        market_session="regular_trading",
        expected_tick_count=10,
        recent_volume=2000.0
    )

    # 2번째 체크인데 비거래시간임 -> stale count 누적 안 됨
    _, is_fallback_guard_1, meta_guard_1 = validator_stale_guard.validate_and_clamp(
        snapshot=snap,
        market_session="out_of_market",  # regular_trading 아님
        expected_tick_count=10,
        recent_volume=2000.0
    )
    assert not is_fallback_guard_1
    assert not meta_guard_1["is_stale_active"]
    assert meta_guard_1["stale_counts"].get("close", 0) == 0

    # 3번째 체크인데 저유동성임 -> stale count 누적 안 됨
    _, is_fallback_guard_2, meta_guard_2 = validator_stale_guard.validate_and_clamp(
        snapshot=snap,
        market_session="regular_trading",
        expected_tick_count=1,  # 저유동성 (< 5)
        recent_volume=10.0      # 저유동성 (< 100.0)
    )
    assert not is_fallback_guard_2
    assert not meta_guard_2["is_stale_active"]
    assert meta_guard_2["stale_counts"].get("close", 0) == 0


def test_girs_scorer_and_stability():
    model = MockONNXModel("mock_model_v1")
    scorer = GIRSScorer(
        model=model,
        baseline_volatility=0.1,
        baseline_latency=0.05,
        ema_alpha=0.5
    )

    # 1. rank_stability 계산 검증
    # 첫 호출 (proposal_id_1) -> stability = 1.0 (초기값)
    stab_1 = scorer.calculate_rank_stability("p1", current_confirmed_rank=2, N=10)
    assert stab_1 == 1.0

    # 두 번째 호출: rank가 2에서 8로 변경됨. normalized_change = |8 - 2| / 10 = 0.6.
    # ema = 0.5 * 0.6 + 0.5 * 0.0 = 0.3.
    # stability = 1.0 / (1.0 + 0.3) = 0.7692...
    stab_2 = scorer.calculate_rank_stability("p1", current_confirmed_rank=8, N=10)
    assert math.isclose(stab_2, 1.0 / 1.3, rel_tol=1e-5)

    # 2. market_stability 계산 검증
    # 첫 호출
    mstab_1 = scorer.calculate_market_stability("p1", market_volatility=0.1)
    assert mstab_1 == 1.0

    # 20개 히스토리를 대충 넣어 std 계산 유도
    for v in [0.1, 0.15, 0.08, 0.12, 0.11]:
        mstab = scorer.calculate_market_stability("p1", market_volatility=v)
    assert 0.0 <= mstab <= 1.0

    # 3. system_stability 계산 검증
    # latency_jitter = 0.05 이고 baseline = 0.05 이면 jitter/baseline = 1.0.
    # stability = 1.0 / (1.0 + 1.0) = 0.5.
    sstab = scorer.calculate_system_stability(0.05)
    assert math.isclose(sstab, 0.5)

    # 4. stability_score 계산 검증
    # weak-link 결합식 검증
    score = scorer.calculate_stability_score(0.8, 0.7, 0.9)
    # min = 0.7, mean = 0.8
    # score = 0.6 * 0.7 + 0.4 * 0.8 = 0.42 + 0.32 = 0.74
    assert math.isclose(score, 0.74)


def test_regime_override():
    model = MockONNXModel("mock_model_v1")
    scorer = GIRSScorer(model=model)

    p_rule = [0.9, 0.1, 0.0, 0.0]
    p_ml = [0.2, 0.3, 0.4, 0.1]

    # rule_confidence > 0.8 -> absolute override
    final_vec_1 = scorer.resolve_regime(p_rule, p_ml, rule_confidence=0.85)
    assert final_vec_1 == p_rule

    # rule_confidence <= 0.8 -> 가중 평균
    # rule_confidence = 0.5
    # final = 0.5 * p_rule + 0.5 * p_ml = [0.55, 0.2, 0.2, 0.05]
    final_vec_2 = scorer.resolve_regime(p_rule, p_ml, rule_confidence=0.5)
    expected = [0.55, 0.2, 0.2, 0.05]
    for a, b in zip(final_vec_2, expected):
        assert math.isclose(a, b)


def test_fallback_risk_scorer_direction():
    model = MockONNXModel("mock_model_v1")
    scorer = GIRSScorer(model=model)

    limits = {
        "max_spread": 0.01,
        "max_volume": 10000.0,
        "max_depth": 10000.0,
        "max_volatility": 0.5,
        "max_drawdown": 0.3
    }

    # 기본(정상/안전) 조건
    risk_safe = scorer.calculate_fallback_risk(
        volatility=0.05, drawdown=0.02, regime_risk=0.1,
        spread=0.0005, volume=8000.0, depth=9000.0, limits=limits
    )

    # 위험 조건 1: spread가 넓어짐 (liquidity risk 상승 -> 전체 risk 상승)
    risk_wide_spread = scorer.calculate_fallback_risk(
        volatility=0.05, drawdown=0.02, regime_risk=0.1,
        spread=0.008, volume=8000.0, depth=9000.0, limits=limits
    )
    assert risk_wide_spread > risk_safe

    # 위험 조건 2: volume 및 depth가 하락 (liquidity risk 상승 -> 전체 risk 상승)
    risk_low_liquidity = scorer.calculate_fallback_risk(
        volatility=0.05, drawdown=0.02, regime_risk=0.1,
        spread=0.0005, volume=100.0, depth=150.0, limits=limits
    )
    assert risk_low_liquidity > risk_safe

    # 위험 조건 3: volatility 및 drawdown 상승 -> risk 상승
    risk_volatile = scorer.calculate_fallback_risk(
        volatility=0.4, drawdown=0.25, regime_risk=0.1,
        spread=0.0005, volume=8000.0, depth=9000.0, limits=limits
    )
    assert risk_volatile > risk_safe


def test_score_scales_golden_verification():
    model = MockONNXModel("mock_model_v1")
    scorer = GIRSScorer(model=model)

    # 1. 0.0 ~ 1.0 영역 검증
    # 정상 케이스
    model_risk = 0.3
    fallback_risk = 0.2
    stability = 0.6
    
    girs_p, fallback_p, final_p, _ = scorer.calculate_final_score(model_risk, fallback_risk, stability)
    assert verify_score_scales(model_risk, fallback_risk, girs_p, fallback_p, final_p)

    # 2. 비정상 케이스 1: model_risk가 범위를 벗어남 (> 1.0)
    assert not verify_score_scales(1.2, 0.2, -0.2, 0.8, 0.5)

    # 3. 비정상 케이스 2: promotion_score 단조성 불일치
    # girs_promotion_score가 1 - model_risk_score와 다름
    assert not verify_score_scales(0.3, 0.2, 0.5, 0.8, 0.6)
    # 4. 다양한 극단값에 대해 최종 스무딩 및 범위가 [0.0, 1.0]으로 안정적으로 들어오는지 확인
    for model_r in [0.0, 0.01, 0.5, 0.99, 1.0]:
        for fallback_r in [0.0, 0.05, 0.5, 0.95, 1.0]:
            for stab in [0.0, 0.1, 0.49, 0.5, 0.51, 0.9, 1.0]:
                gp, fp, final, _ = scorer.calculate_final_score(model_r, fallback_r, stab)
                assert verify_score_scales(model_r, fallback_r, gp, fp, final)
                assert 0.0 <= gp <= 1.0
                assert 0.0 <= fp <= 1.0
                assert 0.0 <= final <= 1.0

def test_stability_tracker_isolated():
    """StabilityTracker만을 단독 격리하여 안정성 점수를 검증합니다."""
    from src.engine.girs_scorer import StabilityTracker
    
    tracker = StabilityTracker(
        ema_alpha=0.5,
        rolling_window_size=5,
        baseline_volatility=0.1,
        baseline_latency=0.05
    )
    
    # 1. rank_stability 격리 검증
    s1 = tracker.calculate_rank_stability("p_test", current_confirmed_rank=1, N=10)
    assert s1 == 1.0  # 첫 호출은 항상 1.0
    
    # 2. market_stability 격리 검증
    # 1개 호출 시 1.0 반환 확인
    m1 = tracker.calculate_market_stability("p_test", market_volatility=0.1)
    assert m1 == 1.0
    
    # 3. system_stability 격리 검증
    sys_s = tracker.calculate_system_stability(0.0)
    assert sys_s == 1.0  # 지터가 0이면 최대 안정(1.0)

def test_fallback_risk_scorer_isolated():
    """FallbackRiskScorer만을 단독 격리하여 limits 설정 주입 및 단순화된 계산 인터페이스를 검증합니다."""
    from src.engine.girs_scorer import FallbackRiskScorer
    
    limits = {
        "max_spread": 0.02,
        "max_volume": 5000.0,
        "max_depth": 5000.0,
        "max_volatility": 0.4,
        "max_drawdown": 0.2
    }
    
    # 생성 시 limits 설정 주입
    scorer = FallbackRiskScorer(limits=limits, baseline_volatility=0.1)
    
    # limits 인자 없이 호출하여 내부 주입된 설정으로 계산 검증
    risk = scorer.calculate_fallback_risk(
        volatility=0.1,
        drawdown=0.05,
        regime_risk=0.2,
        spread=0.001,
        volume=4000.0,
        depth=4500.0
    )
    
    # 리스크 점수가 정상 범위 [0.0, 1.0] 내에 있는지 확인
    assert 0.0 <= risk <= 1.0
