# -*- coding: utf-8 -*-

import os
import math
import pytest
from src.engine.gnn_trainer import (
    generate_label_quality_report,
    time_based_split,
    calculate_calibration_metrics,
    verify_causal_data_leakage,
    compute_temporal_loss,
    train_gnn_model,
    export_to_onnx,
    verify_pytorch_onnx_parity,
    HAS_TORCH
)
from src.engine.girs_types import FeatureSnapshot
from src.engine.girs_scorer import MockONNXModel, GIRSScorer

def test_label_generation_and_report():
    samples = [
        {"source_strategy_id": "strat_A", "created_at": 1700000000.0, "rollback_risk": 1, "reason": "roi_underperform"},
        {"source_strategy_id": "strat_A", "created_at": 1700086400.0, "rollback_risk": 0},
        {"source_strategy_id": "strat_B", "created_at": 1700172800.0, "rollback_risk": 1, "reason": "mdd_violation"},
        {"source_strategy_id": "strat_B", "created_at": 1700259200.0, "rollback_risk": 0},
    ]
    
    report = generate_label_quality_report(samples)
    assert report["total_samples"] == 4
    assert report["positive_ratio"] == 0.5
    assert report["strategy_distribution"]["strat_A"] == 2
    assert report["reason_distribution"]["roi_underperform"] == 1
    assert report["reason_distribution"]["mdd_violation"] == 1


def test_time_based_split_and_sequentiality():
    samples = [
        {"id": "s1", "created_at": 1000.0},
        {"id": "s2", "created_at": 3000.0},
        {"id": "s3", "created_at": 2000.0},
        {"id": "s4", "created_at": 4000.0},
    ]
    
    train, val = time_based_split(samples, train_ratio=0.75)
    assert len(train) == 3
    assert len(val) == 1
    assert train[0]["id"] == "s1"
    assert train[1]["id"] == "s3"
    assert train[2]["id"] == "s2"
    assert val[0]["id"] == "s4"
    assert val[0]["created_at"] >= train[-1]["created_at"]


def test_causal_data_leakage_guard():
    safe_features = {
        "close": 50000.0,
        "returns": -0.05,
        "volatility": 0.2
    }
    assert verify_causal_data_leakage(safe_features, proposal_time=1000.0)

    leaked_features = {
        "close": 50000.0,
        "returns": -0.05,
        "rollback_risk": 1
    }
    assert not verify_causal_data_leakage(leaked_features, proposal_time=1000.0)


def test_calibration_metrics_calculation():
    y_true = [1, 0, 1, 0, 0]
    y_prob = [0.9, 0.1, 0.8, 0.2, 0.3]
    
    ece, brier = calculate_calibration_metrics(y_true, y_prob, n_bins=5)
    assert math.isclose(brier, 0.038, rel_tol=1e-5)
    assert 0.0 <= ece <= 1.0


def test_temporal_loss_scope():
    if not HAS_TORCH:
        pytest.skip("Torch is not installed. Skipping temporal loss test.")
        
    import torch
    
    predictions = {
        "prop_1": torch.tensor([0.8]),
        "prop_2": torch.tensor([0.2]),
        "prop_3": torch.tensor([0.5])
    }
    lineage_edges = [
        ("prop_1", "prop_2"),
        ("prop_2", "prop_3")
    ]
    labels_dict = {
        "prop_1": "strat_A",
        "prop_2": "strat_A",
        "prop_3": "strat_B"
    }
    
    loss_val = compute_temporal_loss(predictions, lineage_edges, labels_dict)
    assert math.isclose(loss_val.item(), 0.36, rel_tol=1e-5)


def test_scorer_fallback_and_shadow_comparison():
    model = MockONNXModel("mock_model_v1")
    
    # 1. Calibration Passed = False (ranking-only risk index)
    scorer_uncalibrated = GIRSScorer(
        model=model,
        calibration_passed=False
    )
    
    snap = FeatureSnapshot(
        price_features={"close": 50000.0, "returns": 0.02, "volatility": 0.1},
        liquidity_features={"spread": 0.001, "volume": 1000.0, "depth": 2000.0},
        regime_features={"regime_index": 1.0}
    )
    
    girs_p, fallback_p, final_p, meta = scorer_uncalibrated.calculate_final_score(
        model_risk_score=0.4,
        fallback_risk_score=0.3,
        stability_score=0.7,
        snapshot=snap
    )
    
    assert not meta["is_calibrated"]
    assert meta["score_type"] == "risk_index"

    # 2. Calibration Passed = True (probability)
    scorer_calibrated = GIRSScorer(
        model=model,
        calibration_passed=True
    )
    
    girs_p2, fallback_p2, final_p2, meta2 = scorer_calibrated.calculate_final_score(
        model_risk_score=0.4,
        fallback_risk_score=0.3,
        stability_score=0.7,
        snapshot=snap
    )
    assert meta2["is_calibrated"]
    assert meta2["score_type"] == "probability"


def test_onnx_export_and_parity_dry():
    if not HAS_TORCH:
        pytest.skip("Torch is not installed. Skipping ONNX parity dry test.")
        
    import torch
    model = train_gnn_model(
        features_list=[[0.5, 0.2, 0.1, 0.002, 1000.0, 2000.0, 1.0]],
        labels=[0],
        lineage_edges=[],
        proposal_ids=["p1"],
        strategy_ids=["s1"],
        epochs=2
    )
    
    onnx_path = "logs/test_gnn_model.onnx"
    dummy_input = torch.randn(1, 7)
    
    exported = export_to_onnx(model, dummy_input, onnx_path)
    assert exported
    assert os.path.exists(onnx_path)
    
    parity_ok = verify_pytorch_onnx_parity(model, onnx_path, dummy_input)
    assert parity_ok

    if os.path.exists(onnx_path):
        try:
            os.remove(onnx_path)
        except OSError:
            pass
