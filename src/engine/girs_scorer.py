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

class GIRSScorer:
    def __init__(
        self,
        model: MockONNXModel,
        baseline_volatility: float = 0.1,
        baseline_latency: float = 0.05,
        ema_alpha: float = 0.2,
        rolling_window_size: int = 20,
        eps: float = 1e-9,
        onnx_model_path: Optional[str] = None,
        calibration_passed: bool = True
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

        # 상태 관리
        # rank_stability 상태: proposal_id -> (last_rank, ema_normalized_change)
        self.rank_states: Dict[str, Tuple[int, float]] = {}
        # market_volatility 히스토리: proposal_id -> deque
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
        stability_market = 1.0 / (1.0 + rolling_std / safe_baseline_volatility)
        return min(max(stability_market, 0.0), 1.0)

    def calculate_system_stability(
        self,
        system_latency_jitter: float
    ) -> float:
        safe_baseline_latency = max(self.baseline_latency, self.eps)
        stability_system = 1.0 / (1.0 + system_latency_jitter / safe_baseline_latency)
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

    def calculate_uncertainty(self, model_risk_score: float) -> Tuple[float, float]:
        p = min(max(model_risk_score, 1e-6), 1.0 - 1e-6)
        uncertainty = (-p * math.log(p) - (1.0 - p) * math.log(1.0 - p)) / math.log(2.0)
        confidence = 1.0 - uncertainty
        return uncertainty, confidence

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
        limits: Dict[str, float]
    ) -> float:
        max_spread = limits.get("max_spread", 0.05)
        max_volume = limits.get("max_volume", 1000000.0)
        max_depth = limits.get("max_depth", 1000000.0)
        max_volatility = limits.get("max_volatility", 1.0)
        max_drawdown = limits.get("max_drawdown", 0.5)

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

    def calculate_final_score(
        self,
        model_risk_score: float,
        fallback_risk_score: float,
        stability_score: float,
        snapshot: Optional[FeatureSnapshot] = None
    ) -> Tuple[float, float, float, Dict[str, Any]]:
        girs_promotion_score = 1.0 - model_risk_score
        fallback_promotion_score = 1.0 - fallback_risk_score

        # Sigmoid 스무딩 결합 적용
        alpha = 1.0 / (1.0 + math.exp(-10.0 * (stability_score - 0.5)))
        final_promotion_score = alpha * girs_promotion_score + (1.0 - alpha) * fallback_promotion_score
        
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
            "score_type": "probability" if self.calibration_passed else "risk_index"
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
            
    if abs(girs_promotion_score - (1.0 - model_risk_score)) > 1e-6:
        logger.error(f"ScoreScaleValidationError: girs_promotion_score ({girs_promotion_score}) does not match 1 - model_risk_score ({1.0 - model_risk_score})")
        return False
        
    if abs(fallback_promotion_score - (1.0 - fallback_risk_score)) > 1e-6:
        logger.error(f"ScoreScaleValidationError: fallback_promotion_score ({fallback_promotion_score}) does not match 1 - fallback_risk_score ({1.0 - fallback_risk_score})")
        return False
        
    return True
