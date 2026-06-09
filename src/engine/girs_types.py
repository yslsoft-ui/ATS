# -*- coding: utf-8 -*-

import math
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any
from src.engine.utils.telemetry import get_logger

logger = get_logger("girs_types")

@dataclass
class FeatureSnapshot:
    price_features: Dict[str, float]
    liquidity_features: Dict[str, float]
    regime_features: Dict[str, Any]  # e.g., regime_index: float, regime_vector: List[float]
    schema_version: str = "1.0"
    feature_hash: str = ""
    generated_at: float = field(default_factory=time.time)
    
    # 확장 시장 메타데이터
    exchange: str = ""
    symbol: str = ""
    market_type: str = ""
    session_state: str = ""
    volatility_regime: str = ""
    liquidity_regime: str = ""
    tick_size: float = 0.0
    price_limit: float = 0.0
    fee_model: str = ""
    slippage_model: str = ""

    # 데이터 보증 및 Freshness
    trade_age_ms: int = 0
    orderbook_age_ms: int = 0
    indicator_age_ms: int = 0
    is_fresh: bool = True
    stale_reason: str = ""
    snapshot_version: str = "1.0"
    snapshot_hash: str = ""
    feature_vector_hash: str = ""
    orderbook_available: bool = False


@dataclass
class CandidateProposal:
    proposal_id: str
    source_strategy_id: str
    features: FeatureSnapshot
    backtest_result: Dict[str, Any]
    gnn_score: Optional[float] = None
    graph_embedding: Optional[List[float]] = None
    model_version: Optional[str] = None
    scaler_version: Optional[str] = None
    status: str = "CANDIDATE"
    outcome: str = "RUNNING"
    sequence_no: int = 0

@dataclass
class FeatureValidationMetrics:
    total_checks: int = 0
    feature_invalid_count: int = 0
    clamp_counts: Dict[str, int] = field(default_factory=dict)
    
    def record_clamp(self, feature_name: str):
        self.clamp_counts[feature_name] = self.clamp_counts.get(feature_name, 0) + 1

    def get_clamp_ratio(self, feature_name: str) -> float:
        if self.total_checks == 0:
            return 0.0
        return self.clamp_counts.get(feature_name, 0) / self.total_checks

class FeatureContractValidator:
    def __init__(
        self,
        expected_price_keys: List[str],
        expected_liquidity_keys: List[str],
        expected_regime_keys: List[str],
        feature_ranges: Dict[str, Tuple[float, float]],
        stale_threshold: int = 5,
        tick_threshold: int = 10,
        volume_threshold: float = 1000.0
    ):
        self.expected_price_keys = expected_price_keys
        self.expected_liquidity_keys = expected_liquidity_keys
        self.expected_regime_keys = expected_regime_keys
        self.feature_ranges = feature_ranges
        self.stale_threshold = stale_threshold
        self.tick_threshold = tick_threshold
        self.volume_threshold = volume_threshold
        
        self.metrics = FeatureValidationMetrics()
        
        # State tracking for stale detection
        self.last_values: Dict[str, Any] = {}
        self.stale_counts: Dict[str, int] = {}

    def validate_and_clamp(
        self,
        snapshot: FeatureSnapshot,
        market_session: str,
        expected_tick_count: int,
        recent_volume: float
    ) -> Tuple[FeatureSnapshot, bool, Dict[str, Any]]:
        self.metrics.total_checks += 1
        is_fallback_required = False
        raw_values_log = {}
        clamped_features = {
            "price_features": {},
            "liquidity_features": {},
            "regime_features": {}
        }
        
        # 1. Type & Container validation
        if not isinstance(snapshot.price_features, dict) or \
           not isinstance(snapshot.liquidity_features, dict) or \
           not isinstance(snapshot.regime_features, dict):
            logger.error("FeatureContract validation error: feature containers must be dicts.")
            self.metrics.feature_invalid_count += 1
            return snapshot, True, {"error": "container_type_mismatch"}

        # Validate price features presence
        for k in self.expected_price_keys:
            if k not in snapshot.price_features:
                logger.error(f"Missing price feature: {k}")
                self.metrics.feature_invalid_count += 1
                return snapshot, True, {"error": f"missing_price_feature_{k}"}
                
        # Validate liquidity features presence
        for k in self.expected_liquidity_keys:
            if k not in snapshot.liquidity_features:
                logger.error(f"Missing liquidity feature: {k}")
                self.metrics.feature_invalid_count += 1
                return snapshot, True, {"error": f"missing_liquidity_feature_{k}"}

        # Validate regime features presence
        for k in self.expected_regime_keys:
            if k not in snapshot.regime_features:
                logger.error(f"Missing regime feature: {k}")
                self.metrics.feature_invalid_count += 1
                return snapshot, True, {"error": f"missing_regime_feature_{k}"}

        # 2. NaN/inf, dtype and Range (clamp) check
        def process_feature_value(name: str, val: Any, category: str) -> Tuple[Any, bool]:
            nonlocal is_fallback_required
            if isinstance(val, (int, float)):
                f_val = float(val)
                if math.isnan(f_val) or math.isinf(f_val):
                    logger.error(f"Feature {name} has invalid float value: {f_val}")
                    self.metrics.feature_invalid_count += 1
                    is_fallback_required = True
                    return val, True
                
                # Range check
                if name in self.feature_ranges:
                    min_val, max_val = self.feature_ranges[name]
                    if f_val < min_val or f_val > max_val:
                        raw_values_log[name] = f_val
                        clamped_val = max(min_val, min(max_val, f_val))
                        self.metrics.record_clamp(name)
                        
                        ratio = self.metrics.get_clamp_ratio(name)
                        if self.metrics.total_checks >= 50 and ratio > 0.1:
                            logger.warning(f"Frequent range clamps on feature '{name}': ratio={ratio:.2f}")
                            if ratio > 0.3:
                                logger.error(f"Clamp ratio for '{name}' exceeded critical limit: {ratio:.2f}. Forcing fallback.")
                                is_fallback_required = True
                        return clamped_val, False
                return f_val, False
            
            elif isinstance(val, list):
                processed_list = []
                for idx, item in enumerate(val):
                    if not isinstance(item, (int, float)):
                        logger.error(f"Feature {name}[{idx}] is not a float: {item}")
                        self.metrics.feature_invalid_count += 1
                        is_fallback_required = True
                        return val, True
                    f_item = float(item)
                    if math.isnan(f_item) or math.isinf(f_item):
                        logger.error(f"Feature {name}[{idx}] has invalid float: {f_item}")
                        self.metrics.feature_invalid_count += 1
                        is_fallback_required = True
                        return val, True
                    
                    sub_name = f"{name}_{idx}"
                    if sub_name in self.feature_ranges:
                        min_val, max_val = self.feature_ranges[sub_name]
                    else:
                        min_val, max_val = 0.0, 1.0
                    
                    if f_item < min_val or f_item > max_val:
                        raw_values_log[sub_name] = f_item
                        clamped_item = max(min_val, min(max_val, f_item))
                        self.metrics.record_clamp(sub_name)
                        processed_list.append(clamped_item)
                    else:
                        processed_list.append(f_item)
                return processed_list, False
            
            else:
                logger.error(f"Feature {name} has unsupported type: {type(val)}")
                self.metrics.feature_invalid_count += 1
                is_fallback_required = True
                return val, True

        # Process price features
        for k, v in snapshot.price_features.items():
            processed_v, err = process_feature_value(k, v, "price")
            if err:
                return snapshot, True, {"error": f"invalid_value_{k}"}
            clamped_features["price_features"][k] = processed_v

        # Process liquidity features
        for k, v in snapshot.liquidity_features.items():
            processed_v, err = process_feature_value(k, v, "liquidity")
            if err:
                return snapshot, True, {"error": f"invalid_value_{k}"}
            clamped_features["liquidity_features"][k] = processed_v

        # Process regime features
        for k, v in snapshot.regime_features.items():
            processed_v, err = process_feature_value(k, v, "regime")
            if err:
                return snapshot, True, {"error": f"invalid_value_{k}"}
            clamped_features["regime_features"][k] = processed_v

        validated_snapshot = FeatureSnapshot(
            price_features=clamped_features["price_features"],
            liquidity_features=clamped_features["liquidity_features"],
            regime_features=clamped_features["regime_features"],
            schema_version=snapshot.schema_version,
            feature_hash=snapshot.feature_hash,
            generated_at=snapshot.generated_at
        )

        # 3. Stale count (zero-variance) detection
        is_stale_active = (market_session == "regular_trading") and (
            expected_tick_count >= self.tick_threshold or recent_volume >= self.volume_threshold
        )
        
        stale_detected = False
        all_features_to_check = {}
        all_features_to_check.update(validated_snapshot.price_features)
        all_features_to_check.update(validated_snapshot.liquidity_features)
        for k, v in validated_snapshot.regime_features.items():
            if isinstance(v, list):
                all_features_to_check[k] = tuple(v)
            else:
                all_features_to_check[k] = v

        if is_stale_active:
            for k, val in all_features_to_check.items():
                if k in self.last_values:
                    if self.last_values[k] == val:
                        self.stale_counts[k] = self.stale_counts.get(k, 0) + 1
                        if self.stale_counts[k] >= self.stale_threshold:
                            logger.error(f"Zero-variance stale state detected for feature '{k}' (stale count={self.stale_counts[k]})")
                            stale_detected = True
                    else:
                        self.stale_counts[k] = 0
                else:
                    self.stale_counts[k] = 0
                self.last_values[k] = val
        else:
            for k, val in all_features_to_check.items():
                self.last_values[k] = val

        if stale_detected:
            is_fallback_required = True

        validation_metadata = {
            "raw_values_log": raw_values_log,
            "clamp_counts": dict(self.metrics.clamp_counts),
            "clamp_ratios": {k: self.metrics.get_clamp_ratio(k) for k in self.metrics.clamp_counts},
            "feature_invalid_count": self.metrics.feature_invalid_count,
            "stale_counts": dict(self.stale_counts),
            "is_stale_active": is_stale_active
        }

        return validated_snapshot, is_fallback_required, validation_metadata
