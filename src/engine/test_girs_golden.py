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

def test_girs_uncertainty_and_confidence():
    """GIRSScorer의 uncertainty_score 및 confidence_score 계산의 정합성과 예외 안전성을 검증합니다."""
    model = MockONNXModel("mock_model_v1")
    scorer = GIRSScorer(model=model)

    # 1. model_risk_score = 0.5 일 때 uncertainty_score 거의 1.0, confidence_score 거의 0.0 검증
    gp, fp, final, meta = scorer.calculate_final_score(
        model_risk_score=0.5,
        fallback_risk_score=0.2,
        stability_score=0.8
    )
    assert "uncertainty_score" in meta
    assert "confidence_score" in meta
    assert math.isclose(meta["uncertainty_score"], 1.0, abs_tol=1e-5)
    assert math.isclose(meta["confidence_score"], 0.0, abs_tol=1e-5)

    # 2. model_risk_score가 0 또는 1에 가까울 때 uncertainty_score 거의 0.0, confidence_score 거의 1.0 검증
    # 0에 가까운 값 (1e-5)
    _, _, _, meta_near_zero = scorer.calculate_final_score(
        model_risk_score=1e-5,
        fallback_risk_score=0.2,
        stability_score=0.8
    )
    assert meta_near_zero["uncertainty_score"] < 0.01
    assert meta_near_zero["confidence_score"] > 0.99

    # 1에 가까운 값 (1 - 1e-5)
    _, _, _, meta_near_one = scorer.calculate_final_score(
        model_risk_score=1.0 - 1e-5,
        fallback_risk_score=0.2,
        stability_score=0.8
    )
    assert meta_near_one["uncertainty_score"] < 0.01
    assert meta_near_one["confidence_score"] > 0.99

    # 3. model_risk_score가 극단적인 0 또는 1일 때 log(0) 예외 발생 없이 정상 계산되는지 검증
    # 0일 때
    _, _, _, meta_zero = scorer.calculate_final_score(
        model_risk_score=0.0,
        fallback_risk_score=0.2,
        stability_score=0.8
    )
    assert "uncertainty_score" in meta_zero
    assert "confidence_score" in meta_zero
    # 1e-15 클램핑으로 인해 예외 없이 연산되며 거의 0에 수렴해야 함
    assert math.isclose(meta_zero["uncertainty_score"], 0.0, abs_tol=1e-9)
    assert math.isclose(meta_zero["confidence_score"], 1.0, abs_tol=1e-9)

    # 1일 때
    _, _, _, meta_one = scorer.calculate_final_score(
        model_risk_score=1.0,
        fallback_risk_score=0.2,
        stability_score=0.8
    )
    assert "uncertainty_score" in meta_one
    assert "confidence_score" in meta_one
    assert math.isclose(meta_one["uncertainty_score"], 0.0, abs_tol=1e-9)
    assert math.isclose(meta_one["confidence_score"], 1.0, abs_tol=1e-9)


def test_girs_scorer_zero_division_guard():
    # baseline_volatility=0.0 또는 음수 주입 시 Division by Zero 오류 방어 검증
    model = MockONNXModel("mock_model_v1")
    
    # 1. 0.0 주입 시
    scorer_zero = GIRSScorer(
        model=model,
        baseline_volatility=0.0,
        baseline_latency=0.0,
        eps=1e-9
    )
    
    scorer_zero.calculate_market_stability("p_zero", market_volatility=0.1)
    mstab = scorer_zero.calculate_market_stability("p_zero", market_volatility=0.2)
    assert math.isfinite(mstab)
    assert 0.0 <= mstab <= 1.0
    
    sstab = scorer_zero.calculate_system_stability(system_latency_jitter=0.05)
    assert math.isfinite(sstab)
    assert 0.0 <= sstab <= 1.0
    
    # 2. 음수 주입 시
    scorer_neg = GIRSScorer(
        model=model,
        baseline_volatility=-0.05,
        baseline_latency=-0.01,
        eps=1e-9
    )
    scorer_neg.calculate_market_stability("p_neg", market_volatility=0.1)
    mstab_neg = scorer_neg.calculate_market_stability("p_neg", market_volatility=0.2)
    assert math.isfinite(mstab_neg)
    assert 0.0 <= mstab_neg <= 1.0
    
    sstab_neg = scorer_neg.calculate_system_stability(system_latency_jitter=0.05)
    assert math.isfinite(sstab_neg)
    assert 0.0 <= sstab_neg <= 1.0


def test_verify_score_scales_precision_boundaries():
    # verify_score_scales의 미세 부동소수점 경계값 판별 검증
    # 1. 허용 범위 내부 경계값
    assert verify_score_scales(
        model_risk_score=-0.999e-7,
        fallback_risk_score=0.5,
        girs_promotion_score=1.0 - (-0.999e-7),
        fallback_promotion_score=0.5,
        final_promotion_score=0.5
    )
    assert verify_score_scales(
        model_risk_score=1.0 + 0.999e-7,
        fallback_risk_score=0.5,
        girs_promotion_score=1.0 - (1.0 + 0.999e-7),
        fallback_promotion_score=0.5,
        final_promotion_score=0.5
    )
    
    # 2. 허용 범위 외부 경계값
    assert not verify_score_scales(
        model_risk_score=-1.001e-7,
        fallback_risk_score=0.5,
        girs_promotion_score=1.0 - (-1.001e-7),
        fallback_promotion_score=0.5,
        final_promotion_score=0.5
    )
    assert not verify_score_scales(
        model_risk_score=1.0 + 1.001e-7,
        fallback_risk_score=0.5,
        girs_promotion_score=1.0 - (1.0 + 1.001e-7),
        fallback_promotion_score=0.5,
        final_promotion_score=0.5
    )


def test_stability_tracker_eviction_and_short_history():
    from src.engine.girs_scorer import StabilityTracker
    
    # 윈도우 크기 = 3, baseline_volatility = 0.1, std와 mean 가중치를 1.0, 0.0으로 설정하여 std 전용 계산 검증
    tracker = StabilityTracker(
        rolling_window_size=3,
        baseline_volatility=0.1,
        market_std_weight=1.0,
        market_mean_weight=0.0
    )
    
    # 1. 히스토리가 2개 미만일 때 예외 없이 1.0 리턴 확인
    assert tracker.calculate_market_stability("p_evict", market_volatility=0.1) == 1.0
    
    # 2. 데이터가 누적되었을 때 sample stdev(표본표준편차, N-1 자유도) 공식 수치 검증
    # 값 2개: [0.1, 0.2] -> mean = 0.15
    # variance = ((0.1-0.15)^2 + (0.2-0.15)^2) / (2-1) = 0.005
    # sample std = math.sqrt(0.005) = 0.070710678...
    mstab_2 = tracker.calculate_market_stability("p_evict", market_volatility=0.2)
    sample_std_2 = math.sqrt(0.005)
    expected_mstab_2 = 1.0 / (1.0 + sample_std_2 / 0.1)
    assert math.isclose(mstab_2, expected_mstab_2, rel_tol=1e-9)
    
    # 3. 윈도우 크기(3)를 초과하여 데이터 유입 시 오래된 값 방출(Eviction) 및 sample std 검증
    # 값 3개째 주입: [0.1, 0.2, 0.3] -> mean = 0.2
    # variance = ((0.1-0.2)^2 + (0.0)^2 + (0.3-0.2)^2) / 2 = 0.01
    # sample std = math.sqrt(0.01) = 0.1
    # expected_mstab_3 = 1.0 / (1.0 + 0.1 / 0.1) = 0.5
    mstab_3 = tracker.calculate_market_stability("p_evict", market_volatility=0.3)
    assert math.isclose(mstab_3, 0.5, rel_tol=1e-9)
    
    # 값 4개째 주입: [0.1, 0.2, 0.3, 0.4] -> 첫 번째 0.1이 방출되어 [0.2, 0.3, 0.4]가 윈도우에 남음
    # mean = 0.3, variance = 0.01, sample std = 0.1, expected_mstab = 0.5
    # (만약 0.1이 방출되지 않고 모조리 남아있거나 pop되지 않았다면 std가 다르게 나옴)
    mstab_4 = tracker.calculate_market_stability("p_evict", market_volatility=0.4)
    assert math.isclose(mstab_4, 0.5, rel_tol=1e-9)


def test_onnx_inference_isolated_safety(monkeypatch):
    # 1. invalid path 주입 시 predict_onnx() 호출이 크래시 없이 None을 리턴하는지 검증
    model = MockONNXModel("mock_model_v1")
    scorer = GIRSScorer(model=model, onnx_model_path="invalid_path_to_model.onnx")
    
    snap = FeatureSnapshot(
        price_features={"close": 50000.0, "returns": 0.01, "volatility": 0.15},
        liquidity_features={"spread": 0.002, "volume": 5000.0, "depth": 10000.0},
        regime_features={"regime_index": 2.0}
    )
    
    res = scorer.predict_onnx(snap)
    assert res is None
    
    # 2. monkeypatch를 이용해 onnxruntime 세션 생성 실패 시 예외 격리 검증
    import sys
    if "onnxruntime" in sys.modules:
        import onnxruntime as ort
        def mock_init(*args, **kwargs):
            raise RuntimeError("Mocked ONNX Runtime Initialization Failure")
        monkeypatch.setattr(ort, "InferenceSession", mock_init)
        
        scorer_mp = GIRSScorer(model=model, onnx_model_path="any_model_path.onnx")
        res_mp = scorer_mp.predict_onnx(snap)
        assert res_mp is None


def test_fallback_conservatism_invariant():
    """안정성 붕괴(stability_score <= 0.2) 또는 데이터 품질 에러(data_quality_blocked=True) 상황에서
    리스크 계산 및 자동 승격이 전면 차단되는지 불변조건(Invariant)을 검증합니다."""
    model = MockONNXModel("mock_model_v1")
    scorer = GIRSScorer(model=model)

    # 극단적으로 낙관적인 리스크 점수 (리스크 0.0 -> 프로모션 점수 1.0)
    girs_risk = 0.0
    fallback_risk = 0.0

    # 1. stability_score <= 0.2 시나리오 검증
    # 원래 식대로라면 girs_p=1.0, fallback_p=1.0이므로 final_p도 1.0이 나와야 하지만,
    # 자동 승격 차단이 작동하여 girs_p=0.0, fallback_p=0.0, final_p=0.0, confidence=0.0 이 반환되어야 함.
    gp_st, fp_st, final_st, meta_st = scorer.calculate_final_score(
        model_risk_score=girs_risk,
        fallback_risk_score=fallback_risk,
        stability_score=0.15,  # <= 0.2
        data_quality_blocked=False
    )
    assert gp_st == 0.0
    assert fp_st == 0.0
    assert final_st == 0.0
    assert meta_st["confidence_score"] == 0.0
    assert meta_st["blocked_reason"] == "LOW_STABILITY"
    # verify_score_scales가 이 차단 점수 조합에 대해서도 True를 반환하는지 보장
    assert verify_score_scales(1.0, 1.0, gp_st, fp_st, final_st)

    # 2. data_quality_blocked = True 시나리오 검증 (정상 stability_score 상황)
    gp_dq, fp_dq, final_dq, meta_dq = scorer.calculate_final_score(
        model_risk_score=girs_risk,
        fallback_risk_score=fallback_risk,
        stability_score=0.8,  # > 0.2
        data_quality_blocked=True
    )
    assert gp_dq == 0.0
    assert fp_dq == 0.0
    assert final_dq == 0.0
    assert meta_dq["confidence_score"] == 0.0
    assert meta_dq["blocked_reason"] == "DATA_QUALITY_BLOCKED"
    assert verify_score_scales(1.0, 1.0, gp_dq, fp_dq, final_dq)

    # 3. 정상 상황 검증 (기존 계산이 그대로 유지되는지)
    # stability_score = 0.8, data_quality_blocked = False
    # model_risk = 0.3 (girs_p = 0.7), fallback_risk = 0.2 (fallback_p = 0.8)
    gp_ok, fp_ok, final_ok, meta_ok = scorer.calculate_final_score(
        model_risk_score=0.3,
        fallback_risk_score=0.2,
        stability_score=0.8,
        data_quality_blocked=False
    )
    # 차단되지 않았으므로 0.0이 아니어야 함
    assert gp_ok == 0.7
    assert fp_ok == 0.8
    # alpha = 1 / (1 + exp(-10 * (0.8 - 0.5))) = 1 / (1 + exp(-3)) = 1 / (1 + 0.049787) = 0.95257
    # final = 0.95257 * 0.7 + (1 - 0.95257) * 0.8 = 0.6668 + 0.0379 = 0.7047
    assert math.isclose(final_ok, 0.7047, abs_tol=1e-3)
    assert meta_ok.get("blocked_reason") is None
    assert verify_score_scales(0.3, 0.2, gp_ok, fp_ok, final_ok)

    # 4. 음수 가중치 오설정 시 0.0 자동 보정 검증
    neg_scorer = GIRSScorer(
        model=model,
        market_std_weight=-1.5,
        market_mean_weight=-0.1,
        system_jitter_weight=-10.0,
        system_latency_weight=-0.0
    )
    assert neg_scorer.market_std_weight == 0.0
    assert neg_scorer.market_mean_weight == 0.0
    assert neg_scorer.system_jitter_weight == 0.0
    assert neg_scorer.system_latency_weight == 0.0


def test_market_system_stability_improvement():
    """market_stability 및 system_stability에 절대적인 크기(mean, average_latency)가 반영되는 공식 개선 사항을 검증합니다.
    테스트의 명확성을 위해 baseline_volatility, baseline_latency, weight 값들을 명시적으로 고정합니다."""
    model = MockONNXModel("mock_model_v1")
    
    # baseline 및 가중치 명시적 고정
    baseline_vol = 0.1
    baseline_lat = 0.05
    market_std_w = 1.0
    market_mean_w = 0.5
    system_jitter_w = 1.0
    system_latency_w = 0.5
    
    scorer = GIRSScorer(
        model=model,
        baseline_volatility=baseline_vol,
        baseline_latency=baseline_lat,
        market_std_weight=market_std_w,
        market_mean_weight=market_mean_w,
        system_jitter_weight=system_jitter_w,
        system_latency_weight=system_latency_w
    )
    
    # 시나리오 1: 변동성 0.8이 계속 유지되는 경우 (std = 0.0, mean = 0.8)
    # std가 0이더라도 mean이 0.8이므로 weighted_metric = 1.0 * 0.0 + 0.5 * 0.8 = 0.4
    # stability_market = 1.0 / (1.0 + 0.4 / 0.1) = 1.0 / 5.0 = 0.2
    for _ in range(10):
        m_stab = scorer.calculate_market_stability("p_high_vol", market_volatility=0.8)
    assert math.isclose(m_stab, 0.2, rel_tol=1e-5)
    
    # 시나리오 2: 변동성 0.05가 계속 유지되는 경우 (std = 0.0, mean = 0.05)
    # std = 0.0, mean = 0.05 -> weighted_metric = 1.0 * 0.0 + 0.5 * 0.05 = 0.025
    # stability_market = 1.0 / (1.0 + 0.025 / 0.1) = 1.0 / 1.25 = 0.8
    # 즉, 변동성이 매우 작고 안정되므로 0.8 이상 높게 유지되어야 함.
    for _ in range(10):
        m_stab_low = scorer.calculate_market_stability("p_low_vol", market_volatility=0.05)
    assert math.isclose(m_stab_low, 0.8, rel_tol=1e-5)
    assert m_stab_low >= 0.8
    
    # 시나리오 3: 지연시간 5초가 계속 유지되는 경우 (jitter = 0.0, latency = 5.0)
    # jitter = 0.0, latency = 5.0 -> weighted_metric = 1.0 * 0.0 + 0.5 * 5.0 = 2.5
    # stability_system = 1.0 / (1.0 + 2.5 / 0.05) = 1.0 / 51.0 = 0.0196
    s_stab_high_lat = scorer.calculate_system_stability(system_latency_jitter=0.0, average_latency=5.0)
    assert s_stab_high_lat < 0.05
    
    # 시나리오 4: 지연시간 jitter만 큰 경우 (latency_jitter = 1.0, average_latency = 0.01)
    # jitter = 1.0, latency = 0.01 -> weighted_metric = 1.0 * 1.0 + 0.5 * 0.01 = 1.005
    # stability_system = 1.0 / (1.0 + 1.005 / 0.05) = 1.0 / 21.1 = 0.047
    s_stab_high_jit = scorer.calculate_system_stability(system_latency_jitter=1.0, average_latency=0.01)
    assert s_stab_high_jit < 0.1
