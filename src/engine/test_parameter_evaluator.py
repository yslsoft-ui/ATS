import pytest
from src.engine.parameter_evaluator import (
    ParameterEvaluator,
    calculate_parameter_distance,
    calculate_multifactor_score,
    get_regime_weighting,
    calculate_diversity_penalty,
    calculate_confidence_score
)

@pytest.fixture
def evaluator():
    return ParameterEvaluator()

def test_calculate_parameter_distance():
    p1 = {"rsi_window": 10, "buy_threshold": 30, "sell_threshold": 70}
    p2 = {"rsi_window": 12, "buy_threshold": 33, "sell_threshold": 63}
    
    # rsi_window: weight 0.2, (12 - 10) / 10 = 0.2 -> 0.2 * 0.2 = 0.04
    # buy_threshold: weight 0.8, (33 - 30) / 30 = 0.1 -> 0.8 * 0.1 = 0.08
    # sell_threshold: weight 0.8, (63 - 70) / 70 = 0.1 -> 0.8 * 0.1 = 0.08
    # total = 0.04 + 0.08 + 0.08 = 0.20
    dist = calculate_parameter_distance(p1, p2)
    assert dist == pytest.approx(0.20)

def test_calculate_parameter_distance_with_extra_keys():
    p1 = {"rsi_window": 10, "buy_threshold": 30, "sell_threshold": 70, "extra_param": "A"}
    p2 = {"rsi_window": 10, "buy_threshold": 30, "sell_threshold": 70, "extra_param": "B"}
    
    # rsi_window, buy, sell의 거리는 0.0
    # extra_param: weight 이외의 키이며 문자열 다름 -> +1.0
    dist = calculate_parameter_distance(p1, p2)
    assert dist == 1.0

def test_get_regime_weighting_conservative():
    # buy_threshold가 낮아지면 매수에 보수적(is_conservative = True)
    orig = {"buy_threshold": 30, "sell_threshold": 70, "rsi_window": 14}
    prop = {"buy_threshold": 25, "sell_threshold": 70, "rsi_window": 14}
    
    # atr_ratio > 1.2 이고 보수적 성향인 경우 -> +5
    w = get_regime_weighting(atr_ratio=1.5, adx=20.0, original_params=orig, proposed_params=prop)
    assert w == 5

def test_get_regime_weighting_trend_rsi_shrink():
    orig = {"buy_threshold": 30, "sell_threshold": 70, "rsi_window": 14}
    prop = {"buy_threshold": 30, "sell_threshold": 70, "rsi_window": 10}
    
    # adx > 25.0 이고 rsi_window가 4 이상 좁혀진 경우 -> -10
    w = get_regime_weighting(atr_ratio=1.0, adx=30.0, original_params=orig, proposed_params=prop)
    assert w == -10

def test_calculate_multifactor_score_fail_criteria():
    # PF < 1.0 이거나 Win Rate < 40.0 이면 무조건 50점
    score = calculate_multifactor_score(roi_7d=20.0, roi_1d=5.0, win_rate=35.0, profit_factor=1.5, mdd=1.0)
    assert score == 50
    
    score_pf = calculate_multifactor_score(roi_7d=20.0, roi_1d=5.0, win_rate=50.0, profit_factor=0.9, mdd=1.0)
    assert score_pf == 50

def test_calculate_multifactor_score_normal():
    # 7일 ROI 2.0% (4점) + 1일 ROI 1.0% (3점) + 승률 50% (6점) + PF 1.2 (2점) - MDD 1.0 (2점)
    # 기본 50 + 4 + 3 + 6 + 2 - 2 = 63
    score = calculate_multifactor_score(roi_7d=2.0, roi_1d=1.0, win_rate=50.0, profit_factor=1.2, mdd=1.0)
    assert score == 63

def test_calculate_diversity_penalty():
    # 거리가 임계값 미만일 때: lambda_dynamic * (1.0 - dist / threshold)
    # 15 * (1.0 - 0.05 / 0.10) = 7.5
    penalty = calculate_diversity_penalty(min_distance=0.05, effective_threshold=0.10, lambda_dynamic=15.0)
    assert penalty == pytest.approx(7.5)
    
    # 거리가 임계값 이상일 때: 0.0
    penalty_zero = calculate_diversity_penalty(min_distance=0.15, effective_threshold=0.10, lambda_dynamic=15.0)
    assert penalty_zero == 0.0

def test_calculate_confidence_score_clamp():
    # 100 초과 점수 -> 100으로 제한
    score_high = calculate_confidence_score(base_score=95, regime_weight=10, rollback_penalty=0, diversity_penalty=0.0)
    assert score_high == 100
    
    # 0 미만 점수 -> 0으로 제한
    score_low = calculate_confidence_score(base_score=50, regime_weight=-10, rollback_penalty=30, diversity_penalty=20.0)
    assert score_low == 0
    
    # 정상 범위
    score = calculate_confidence_score(base_score=70, regime_weight=5, rollback_penalty=10, diversity_penalty=5.0)
    assert score == 60

def test_parameter_evaluator_class_wrapper(evaluator):
    p1 = {"rsi_window": 10, "buy_threshold": 30, "sell_threshold": 70}
    p2 = {"rsi_window": 12, "buy_threshold": 33, "sell_threshold": 63}
    
    # 클래스 메소드를 통한 연산 결과가 독립 함수 호출 결과와 동일한지 검증
    dist_class = evaluator.calculate_parameter_distance(p1, p2)
    dist_func = calculate_parameter_distance(p1, p2)
    assert dist_class == dist_func
    
    score_class = evaluator.calculate_multifactor_score(roi_7d=2.0, roi_1d=1.0, win_rate=50.0, profit_factor=1.2, mdd=1.0)
    score_func = calculate_multifactor_score(roi_7d=2.0, roi_1d=1.0, win_rate=50.0, profit_factor=1.2, mdd=1.0)
    assert score_class == score_func
