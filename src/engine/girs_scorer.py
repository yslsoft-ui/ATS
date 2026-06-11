# -*- coding: utf-8 -*-

import math
from collections import deque
from typing import Dict, List, Optional, Tuple, Any
from src.engine.girs_types import FeatureSnapshot
from src.engine.utils.telemetry import get_logger

logger = get_logger("girs_scorer")

try:
    import onnxruntime as ort
    import numpy as np
    HAS_ORT = True
except ImportError:
    HAS_ORT = False

class MockONNXModel:
    """ONNX 모델의 동작을 흉내내는 Mock Scorer"""
    def __init__(self, model_version: str = "mock_v1"):
        self.model_version = model_version

    def predict(self, snapshot: FeatureSnapshot) -> float:
        returns = snapshot.price_features.get("returns", 0.0)
        volatility = snapshot.price_features.get("volatility", 0.1)
        spread = snapshot.liquidity_features.get("spread", 0.001)
        
        # 임의의 결정론적 계산.
        raw_val = abs(returns) * 2.0 + volatility * 0.5 + spread * 5.0
        # sigmoid mapping
        p = 1.0 / (1.0 + math.exp(-raw_val))
        return p

class StabilityTracker:
    """
    순위, 시장 및 시스템 안정성 지표와 이력을 관리하는 전담 상태 관리기.
    """
    def __init__(
        self,
        ema_alpha: float = 0.2,
        rolling_window_size: int = 20,
        baseline_volatility: float = 0.1,
        baseline_latency: float = 0.05,
        eps: float = 1e-9,
        market_std_weight: float = 1.0,
        market_mean_weight: float = 0.5,
        system_jitter_weight: float = 1.0,
        system_latency_weight: float = 0.5
    ):
        self.ema_alpha = ema_alpha
        self.rolling_window_size = rolling_window_size
        self.baseline_volatility = baseline_volatility
        self.baseline_latency = baseline_latency
        self.eps = eps

        # 가중치 속성 정의 및 음수 보정
        self.market_std_weight = max(0.0, market_std_weight)
        self.market_mean_weight = max(0.0, market_mean_weight)
        self.system_jitter_weight = max(0.0, system_jitter_weight)
        self.system_latency_weight = max(0.0, system_latency_weight)

        # 상태 관리 필드 전담 소유
        self.rank_states: Dict[str, Tuple[int, float]] = {}
        self.market_volatility_hist: Dict[str, deque] = {}

    def calculate_rank_stability(
        self,
        proposal_id: str,
        current_confirmed_rank: int,
        N: int
    ) -> float:
        if N <= 0:
            return 1.0

        if proposal_id not in self.rank_states:
            self.rank_states[proposal_id] = (current_confirmed_rank, 0.0)
            return 1.0

        last_rank, last_ema = self.rank_states[proposal_id]
        normalized_change = abs(current_confirmed_rank - last_rank) / max(1, N)
        
        # EMA 계산
        new_ema = self.ema_alpha * normalized_change + (1.0 - self.ema_alpha) * last_ema
        self.rank_states[proposal_id] = (current_confirmed_rank, new_ema)
        
        rank_stability = 1.0 / (1.0 + new_ema)
        return min(max(rank_stability, 0.0), 1.0)

    def calculate_market_stability(
        self,
        proposal_id: str,
        market_volatility: float
    ) -> float:
        if proposal_id not in self.market_volatility_hist:
            self.market_volatility_hist[proposal_id] = deque(maxlen=self.rolling_window_size)
        
        hist = self.market_volatility_hist[proposal_id]
        hist.append(market_volatility)
        
        if len(hist) < 2:
            return 1.0
            
        # rolling std 계산
        mean_val = sum(hist) / len(hist)
        variance = sum((x - mean_val) ** 2 for x in hist) / (len(hist) - 1)
        rolling_std = math.sqrt(variance)
        
        safe_baseline_volatility = max(self.baseline_volatility, self.eps)
        
        # std와 mean의 가중합 계산
        weighted_metric = (self.market_std_weight * rolling_std) + (self.market_mean_weight * mean_val)
        stability_market = 1.0 / (1.0 + weighted_metric / safe_baseline_volatility)
        return min(max(stability_market, 0.0), 1.0)

    def calculate_system_stability(
        self,
        system_latency_jitter: float,
        average_latency: float = 0.0
    ) -> float:
        safe_baseline_latency = max(self.baseline_latency, self.eps)
        
        # jitter와 average_latency의 가중합 계산
        weighted_metric = (self.system_jitter_weight * system_latency_jitter) + (self.system_latency_weight * average_latency)
        stability_system = 1.0 / (1.0 + weighted_metric / safe_baseline_latency)
        return min(max(stability_system, 0.0), 1.0)

    def calculate_stability_score(
        self,
        rank_stability: float,
        stability_market: float,
        stability_system: float
    ) -> float:
        stabilities = [rank_stability, stability_market, stability_system]
        min_stab = min(stabilities)
        mean_stab = sum(stabilities) / 3.0
        stability_score = 0.6 * min_stab + 0.4 * mean_stab
        return min(max(stability_score, 0.0), 1.0)

class FallbackRiskScorer:
    """
    설정을 주입받아 룰 기반 대체 리스크(Fallback Risk)를 산출하는 계산기.
    """
    def __init__(
        self,
        limits: Optional[Dict[str, float]] = None,
        baseline_volatility: float = 0.1,
        eps: float = 1e-9
    ):
        self.limits = limits or {
            "max_spread": 0.05,
            "max_volume": 1000000.0,
            "max_depth": 1000000.0,
            "max_volatility": 1.0,
            "max_drawdown": 0.5
        }
        self.baseline_volatility = baseline_volatility
        self.eps = eps

    def resolve_regime(
        self,
        p_rule: List[float],
        p_ml: List[float],
        rule_confidence: float
    ) -> List[float]:
        # rule_confidence > 0.8 이면 absolute override
        if rule_confidence > 0.8:
            return p_rule
            
        final_vector = []
        for r_val, m_val in zip(p_rule, p_ml):
            final_vector.append(rule_confidence * r_val + (1.0 - rule_confidence) * m_val)
        return final_vector

    def calculate_fallback_risk(
        self,
        volatility: float,
        drawdown: float,
        regime_risk: float,
        spread: float,
        volume: float,
        depth: float,
        limits: Optional[Dict[str, float]] = None
    ) -> float:
        # GIRSScorer facade 호환을 위해 limits를 옵션 인자로 허용하며, 전달된 것이 있으면 우선해 사용합니다.
        active_limits = limits if limits is not None else self.limits

        max_spread = active_limits.get("max_spread", 0.05)
        max_volume = active_limits.get("max_volume", 1000000.0)
        max_depth = active_limits.get("max_depth", 1000000.0)
        max_volatility = active_limits.get("max_volatility", 1.0)
        max_drawdown = active_limits.get("max_drawdown", 0.5)

        # 1. Liquidity risk 계산 (높을수록 위험)
        normalized_spread = min(max(spread / max_spread, 0.0), 1.0)
        normalized_volume = min(max(volume / max_volume, 0.0), 1.0)
        normalized_depth = min(max(depth / max_depth, 0.0), 1.0)

        spread_risk = normalized_spread
        volume_risk = 1.0 - normalized_volume
        depth_risk = 1.0 - normalized_depth
        liquidity_risk = (spread_risk + volume_risk + depth_risk) / 3.0

        # 2. Volatility & Drawdown risk
        volatility_risk = min(max(volatility / max_volatility, 0.0), 1.0)
        drawdown_risk = min(max(drawdown / max_drawdown, 0.0), 1.0)

        # 3. 가중합 계산
        fallback_risk = (
            0.3 * volatility_risk +
            0.3 * drawdown_risk +
            0.2 * regime_risk +
            0.2 * liquidity_risk
        )
        return min(max(fallback_risk, 0.0), 1.0)

class GIRSScorer:
    """
    GIRS Scorer Facade & Orchestrator.
    외부 인터페이스 및 테스트 호환성을 완전히 유지하면서 연산을 전문 클래스로 위임(Delegation)합니다.
    """
    def __init__(
        self,
        model: MockONNXModel,
        baseline_volatility: float = 0.1,
        baseline_latency: float = 0.05,
        ema_alpha: float = 0.2,
        rolling_window_size: int = 20,
        eps: float = 1e-9,
        onnx_model_path: Optional[str] = None,
        calibration_passed: bool = True,
        limits: Optional[Dict[str, float]] = None,
        market_std_weight: Optional[float] = None,
        market_mean_weight: Optional[float] = None,
        system_jitter_weight: Optional[float] = None,
        system_latency_weight: Optional[float] = None
    ):
        self.model = model
        self.baseline_volatility = baseline_volatility
        self.baseline_latency = baseline_latency
        self.ema_alpha = ema_alpha
        self.rolling_window_size = rolling_window_size
        self.eps = eps
        self.onnx_model_path = onnx_model_path
        self.calibration_passed = calibration_passed
        self.onnx_session = None

        # Composition 구성
        self.tracker = StabilityTracker(
            ema_alpha=ema_alpha,
            rolling_window_size=rolling_window_size,
            baseline_volatility=baseline_volatility,
            baseline_latency=baseline_latency,
            eps=eps,
            market_std_weight=market_std_weight if market_std_weight is not None else 1.0,
            market_mean_weight=market_mean_weight if market_mean_weight is not None else 0.5,
            system_jitter_weight=system_jitter_weight if system_jitter_weight is not None else 1.0,
            system_latency_weight=system_latency_weight if system_latency_weight is not None else 0.5
        )
        self.fallback_scorer = FallbackRiskScorer(
            limits=limits,
            baseline_volatility=baseline_volatility,
            eps=eps
        )

        self.market_std_weight = self.tracker.market_std_weight
        self.market_mean_weight = self.tracker.market_mean_weight
        self.system_jitter_weight = self.tracker.system_jitter_weight
        self.system_latency_weight = self.tracker.system_latency_weight

    # 기존 인메모리 상태 직접 접근 코드를 위한 호환 프로퍼티 데코레이터
    @property
    def rank_states(self) -> Dict[str, Tuple[int, float]]:
        return self.tracker.rank_states

    @property
    def market_volatility_hist(self) -> Dict[str, deque]:
        return self.tracker.market_volatility_hist

    # --- 위임 메서드 (Facade) ---

    def calculate_rank_stability(
        self,
        proposal_id: str,
        current_confirmed_rank: int,
        N: int
    ) -> float:
        return self.tracker.calculate_rank_stability(proposal_id, current_confirmed_rank, N)

    def calculate_market_stability(
        self,
        proposal_id: str,
        market_volatility: float
    ) -> float:
        return self.tracker.calculate_market_stability(proposal_id, market_volatility)

    def calculate_system_stability(
        self,
        system_latency_jitter: float,
        average_latency: float = 0.0
    ) -> float:
        return self.tracker.calculate_system_stability(system_latency_jitter, average_latency)

    def calculate_stability_score(
        self,
        rank_stability: float,
        stability_market: float,
        stability_system: float
    ) -> float:
        return self.tracker.calculate_stability_score(rank_stability, stability_market, stability_system)

    def resolve_regime(
        self,
        p_rule: List[float],
        p_ml: List[float],
        rule_confidence: float
    ) -> List[float]:
        return self.fallback_scorer.resolve_regime(p_rule, p_ml, rule_confidence)

    def calculate_fallback_risk(
        self,
        volatility: float,
        drawdown: float,
        regime_risk: float,
        spread: float,
        volume: float,
        depth: float,
        limits: Optional[Dict[str, float]] = None
    ) -> float:
        return self.fallback_scorer.calculate_fallback_risk(
            volatility=volatility,
            drawdown=drawdown,
            regime_risk=regime_risk,
            spread=spread,
            volume=volume,
            depth=depth,
            limits=limits
        )

    def calculate_final_score(
        self,
        model_risk_score: float,
        fallback_risk_score: float,
        stability_score: float,
        snapshot: Optional[FeatureSnapshot] = None,
        data_quality_blocked: bool = False
    ) -> Tuple[float, float, float, Dict[str, Any]]:
        # 1. 자동 승격 전면 금지 국면 (데이터 품질 검사 실패 또는 stability_score <= 0.2)
        if stability_score <= 0.2 or data_quality_blocked:
            blocked_reason = "DATA_QUALITY_BLOCKED" if data_quality_blocked else "LOW_STABILITY"
            logger.warning(f"[GIRSScorer] 자동 승격 금지 발동: {blocked_reason} (stability_score={stability_score:.4f})")
            
            meta = {
                "is_calibrated": self.calibration_passed,
                "shadow_risk_score": None,
                "score_type": "risk_index",
                "uncertainty_score": 1.0,
                "confidence_score": 0.0,
                "blocked_reason": blocked_reason
            }
            # 리스크 1.0에 대응하기 위해 모든 프로모션 점수를 0.0으로 반환하여 승격 원천 금지
            return 0.0, 0.0, 0.0, meta

        # 2. 그 외 정상 상황 -> GIRS / Fallback 계산
        girs_promotion_score = 1.0 - model_risk_score
        fallback_promotion_score = 1.0 - fallback_risk_score

        # Sigmoid 스무딩 결합 적용
        alpha = 1.0 / (1.0 + math.exp(-10.0 * (stability_score - 0.5)))
        final_promotion_score = alpha * girs_promotion_score + (1.0 - alpha) * fallback_promotion_score
        
        # model_risk_score를 기반으로 uncertainty_score 및 confidence_score 계산
        p_clip = min(max(model_risk_score, 1e-15), 1.0 - 1e-15)
        uncertainty_score = (-p_clip * math.log(p_clip) - (1.0 - p_clip) * math.log(1.0 - p_clip)) / math.log(2.0)
        confidence_score = 1.0 - uncertainty_score

        # Shadow mode ONNX GNN inference 비교 기록
        shadow_risk_score = None
        if snapshot is not None:
            shadow_risk_score = self.predict_onnx(snapshot)
            if shadow_risk_score is not None:
                logger.info(
                    f"[GIRSScorer] Shadow Comparison: Mock Risk={model_risk_score:.4f}, "
                    f"ONNX GNN Risk={shadow_risk_score:.4f}, "
                    f"Diff={abs(model_risk_score - shadow_risk_score):.4f}"
                )
                
        meta = {
            "is_calibrated": self.calibration_passed,
            "shadow_risk_score": shadow_risk_score,
            "score_type": "probability" if self.calibration_passed else "risk_index",
            "uncertainty_score": uncertainty_score,
            "confidence_score": confidence_score
        }
        
        return girs_promotion_score, fallback_promotion_score, final_promotion_score, meta

    def predict_onnx(self, snapshot: FeatureSnapshot) -> Optional[float]:
        if not HAS_ORT or not self.onnx_model_path:
            return None
            
        try:
            if self.onnx_session is None:
                self.onnx_session = ort.InferenceSession(self.onnx_model_path)
                
            flat_features = []
            for k in ["close", "returns", "volatility"]:
                flat_features.append(snapshot.price_features.get(k, 0.0))
            for k in ["spread", "volume", "depth"]:
                flat_features.append(snapshot.liquidity_features.get(k, 0.0))
            flat_features.append(snapshot.regime_features.get("regime_index", 0.0))
            
            input_data = np.array([flat_features], dtype=np.float32)
            input_name = self.onnx_session.get_inputs()[0].name
            onnx_out = self.onnx_session.run(None, {input_name: input_data})[0]
            
            return float(onnx_out[0][0])
        except Exception as e:
            logger.error(f"Failed ONNX inference in GIRSScorer: {e}")
            return None

def verify_score_scales(
    model_risk_score: float,
    fallback_risk_score: float,
    girs_promotion_score: float,
    fallback_promotion_score: float,
    final_promotion_score: float
) -> bool:
    """모든 점수가 [0.0, 1.0] 범위에 있는지 검증하고, promotion = 1 - risk의 단조성을 체크합니다."""
    scores = {
        "model_risk_score": model_risk_score,
        "fallback_risk_score": fallback_risk_score,
        "girs_promotion_score": girs_promotion_score,
        "fallback_promotion_score": fallback_promotion_score,
        "final_promotion_score": final_promotion_score
    }
    
    for name, val in scores.items():
        if val < -1e-7 or val > 1.0 + 1e-7:
            logger.error(f"ScoreScaleValidationError: '{name}' value {val} is outside [0.0, 1.0] range.")
            return False
            
    # 자동 승격 차단 시에는 실질 리스크가 1.0으로 강제되므로, 
    # 단조성 계산 시 이를 보정하여 예외 처리합니다.
    is_blocked = (girs_promotion_score == 0.0 and fallback_promotion_score == 0.0 and final_promotion_score == 0.0)
    effective_model_risk = 1.0 if is_blocked else model_risk_score
    effective_fallback_risk = 1.0 if is_blocked else fallback_risk_score

    if abs(girs_promotion_score - (1.0 - effective_model_risk)) > 1e-6:
        logger.error(f"ScoreScaleValidationError: girs_promotion_score ({girs_promotion_score}) does not match 1 - model_risk_score ({1.0 - effective_model_risk})")
        return False
        
    if abs(fallback_promotion_score - (1.0 - effective_fallback_risk)) > 1e-6:
        logger.error(f"ScoreScaleValidationError: fallback_promotion_score ({fallback_promotion_score}) does not match 1 - fallback_risk_score ({1.0 - effective_fallback_risk})")
        return False
        
    return True
