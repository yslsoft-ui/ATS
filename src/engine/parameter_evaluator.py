from typing import Dict, Any

def calculate_parameter_distance(p1: dict, p2: dict) -> float:
    """
    서로 다른 단위와 범주를 지닌 파라미터 간의 물리적 유사도를 가중치와 baseline 대비 비율로 산출합니다.
    """
    weights = {
        "rsi_window": 0.2,
        "buy_threshold": 0.8,
        "sell_threshold": 0.8
    }
    
    distance = 0.0
    for p, w in weights.items():
        v1 = p1.get(p)
        v2 = p2.get(p)
        if v1 is not None and v2 is not None:
            baseline = float(v1)
            if baseline != 0.0:
                distance += w * (abs(float(v2) - baseline) / baseline)
                
    for k in p1.keys():
        if k not in weights and k != "insight_id" and k != "proposal_group_id":
            v1 = p1[k]
            v2 = p2.get(k)
            if isinstance(v1, str) or isinstance(v2, str):
                if v1 != v2:
                    distance += 1.0
                    
    return distance

def get_regime_weighting(atr_ratio: float, adx: float, original_params: dict, proposed_params: dict) -> int:
    """
    시장 국면과 전략 파라미터(보수성 여부 등)를 매핑하여 국면 가중치를 산출합니다.
    """
    weight = 0
    orig_buy = original_params.get("buy_threshold")
    prop_buy = proposed_params.get("buy_threshold")
    orig_sell = original_params.get("sell_threshold")
    prop_sell = proposed_params.get("sell_threshold")
    
    is_conservative = False
    if orig_buy is not None and prop_buy is not None and prop_buy < orig_buy:
        is_conservative = True
    if orig_sell is not None and prop_sell is not None and prop_sell > orig_sell:
        is_conservative = True
        
    if atr_ratio > 1.2 and is_conservative:
        weight += 5
        
    orig_rsi = original_params.get("rsi_window")
    prop_rsi = proposed_params.get("rsi_window")
    
    if adx > 25.0 and orig_rsi is not None and prop_rsi is not None and (orig_rsi - prop_rsi) >= 4:
        weight -= 10
        
    return weight

def calculate_multifactor_score(roi_7d: float, roi_1d: float, win_rate: float, profit_factor: float, mdd: float) -> int:
    """
    수익률, 승률, Profit Factor, MDD 등의 지표를 종합하여 기본 다요소 점수를 계산합니다.
    """
    if profit_factor < 1.0 or win_rate < 40.0:
        return 50
        
    roi_7d_score = min(max(roi_7d * 2.0, 0.0), 25.0)
    roi_1d_score = min(max(roi_1d * 3.0, 0.0), 15.0)
    win_rate_contribution = min(max((win_rate - 40.0) * 0.6, 0.0), 30.0)
    pf_contribution = min(max((profit_factor - 1.0) * 10.0, 0.0), 20.0)
    mdd_penalty = mdd * 2.0
    
    score = 50 + roi_7d_score + roi_1d_score + win_rate_contribution + pf_contribution - mdd_penalty
    return int(min(max(score, 50.0), 100.0))

def calculate_diversity_penalty(min_distance: float, effective_threshold: float, lambda_dynamic: float) -> float:
    """
    기존/대기중 파라미터와의 최소 거리가 임계값 미만일 때 적용되는 패널티를 계산합니다.
    """
    if min_distance < effective_threshold:
        diversity_penalty = lambda_dynamic * (1.0 - (min_distance / effective_threshold))
        return min(diversity_penalty, lambda_dynamic)
    return 0.0

def calculate_confidence_score(base_score: int, regime_weight: int, rollback_penalty: int, diversity_penalty: float) -> int:
    """
    기본 다요소 점수, 국면 가중치, 롤백 패널티, 다양성 패널티를 종합하여 최종 신뢰도 점수를 산출합니다.
    최종 결과는 반드시 0~100 범위로 클램프(clamp)됩니다.
    """
    confidence_score = base_score + regime_weight - rollback_penalty - diversity_penalty
    return int(min(max(confidence_score, 0.0), 100.0))


class ParameterEvaluator:
    """
    ShadowBacktestEngine 내부에서 Composition 형태로 사용하는 파라미터 평가용 얇은 클래스 래퍼입니다.
    기본적으로 무상태(Stateless) 순수 함수 연산들을 래핑하여 제공합니다.
    """
    def calculate_parameter_distance(self, p1: dict, p2: dict) -> float:
        return calculate_parameter_distance(p1, p2)
        
    def get_regime_weighting(self, atr_ratio: float, adx: float, original_params: dict, proposed_params: dict) -> int:
        return get_regime_weighting(atr_ratio, adx, original_params, proposed_params)
        
    def calculate_multifactor_score(self, roi_7d: float, roi_1d: float, win_rate: float, profit_factor: float, mdd: float) -> int:
        return calculate_multifactor_score(roi_7d, roi_1d, win_rate, profit_factor, mdd)
        
    def calculate_diversity_penalty(self, min_distance: float, effective_threshold: float, lambda_dynamic: float) -> float:
        return calculate_diversity_penalty(min_distance, effective_threshold, lambda_dynamic)
        
    def calculate_confidence_score(self, base_score: int, regime_weight: int, rollback_penalty: int, diversity_penalty: float) -> int:
        return calculate_confidence_score(base_score, regime_weight, rollback_penalty, diversity_penalty)
