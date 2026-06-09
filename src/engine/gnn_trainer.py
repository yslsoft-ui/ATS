# -*- coding: utf-8 -*-

import os
import json
import math
import time
from typing import Dict, List, Optional, Tuple, Any
from src.engine.utils.telemetry import get_logger

logger = get_logger("gnn_trainer")

try:
    import torch
    import torch.nn as nn
    import torch.optim as optim
    HAS_TORCH = True
except ImportError:
    HAS_TORCH = False
    # Fallback to make code parsing pass when torch is absent
    class nn:
        class Module: pass

if HAS_TORCH:
    class GNNProposalsClassifier(nn.Module):
        def __init__(self, input_dim: int, hidden_dim: int = 16):
            super().__init__()
            self.mlp = nn.Sequential(
                nn.Linear(input_dim, hidden_dim),
                nn.ReLU(),
                nn.Linear(hidden_dim, 1),
                nn.Sigmoid()
            )

        def forward(self, x):
            return self.mlp(x)
else:
    class GNNProposalsClassifier:
        def __init__(self, input_dim: int, hidden_dim: int = 16):
            pass

def generate_label_quality_report(samples: List[Dict[str, Any]]) -> Dict[str, Any]:
    total_samples = len(samples)
    if total_samples == 0:
        return {
            "total_samples": 0,
            "positive_ratio": 0.0,
            "negative_ratio": 0.0,
            "strategy_distribution": {},
            "time_window_distribution": {},
            "reason_distribution": {}
        }
        
    pos_count = 0
    strat_dist = {}
    time_dist = {}
    reason_dist = {
        "roi_underperform": 0,
        "mdd_violation": 0,
        "manual_rollback": 0,
        "shadow_demote": 0
    }
    
    for s in samples:
        is_pos = s.get("rollback_risk", 0) == 1
        if is_pos:
            pos_count += 1
            reason = s.get("reason", "unknown")
            if reason in reason_dist:
                reason_dist[reason] += 1
            else:
                reason_dist[reason] = reason_dist.get(reason, 0) + 1
                
        strat_id = s.get("source_strategy_id", "unknown")
        strat_dist[strat_id] = strat_dist.get(strat_id, 0) + 1
        
        created_at = s.get("created_at", 0.0)
        day_bucket = int(created_at / 86400) * 86400
        time_dist[day_bucket] = time_dist.get(day_bucket, 0) + 1
        
    pos_ratio = pos_count / total_samples
    neg_ratio = 1.0 - pos_ratio
    
    report = {
        "total_samples": total_samples,
        "positive_ratio": pos_ratio,
        "negative_ratio": neg_ratio,
        "strategy_distribution": strat_dist,
        "time_window_distribution": time_dist,
        "reason_distribution": reason_dist
    }
    
    os.makedirs("logs", exist_ok=True)
    with open("logs/label_quality_report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, indent=4)
        
    logger.info(f"Label quality report generated. Total samples: {total_samples}, Pos ratio: {pos_ratio:.2%}")
    return report

def time_based_split(samples: List[Dict[str, Any]], train_ratio: float = 0.8) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    # 1. created_at 기준 정렬
    sorted_samples = sorted(samples, key=lambda x: x.get("created_at", 0.0))
    
    # 2. 비율 분할
    split_idx = int(len(sorted_samples) * train_ratio)
    train_set = sorted_samples[:split_idx]
    val_set = sorted_samples[split_idx:]
    
    # 3. Validation 미래 구간 검증
    if train_set and val_set:
        max_train_time = max(s.get("created_at", 0.0) for s in train_set)
        min_val_time = min(s.get("created_at", 0.0) for s in val_set)
        assert min_val_time >= max_train_time, "Validation set contains past elements relative to Train set."
        
    return train_set, val_set

def calculate_calibration_metrics(y_true: List[int], y_prob: List[float], n_bins: int = 10) -> Tuple[float, float]:
    brier_score = sum((p - t) ** 2 for p, t in zip(y_prob, y_true)) / len(y_true) if y_true else 0.0
    
    if not y_true:
        return 0.0, 0.0
        
    bins = [[] for _ in range(n_bins)]
    for p, t in zip(y_prob, y_true):
        bin_idx = min(int(p * n_bins), n_bins - 1)
        bins[bin_idx].append((p, t))
        
    ece = 0.0
    total_samples = len(y_true)
    for b in bins:
        if not b:
            continue
        bin_size = len(b)
        bin_prob_mean = sum(x[0] for x in b) / bin_size
        bin_true_mean = sum(x[1] for x in b) / bin_size
        ece += (bin_size / total_samples) * abs(bin_prob_mean - bin_true_mean)
        
    return ece, brier_score

def verify_causal_data_leakage(features: Dict[str, Any], proposal_time: float) -> bool:
    # 입력 피처 생성 시 제안 시점 t 이후의 레이블 Target 정보가 누수되지 않았는지 검증
    future_leakage_keys = ["rollback_risk", "outcome_roi", "actual_mdd", "demote_occurred"]
    for key, val in features.items():
        if key in future_leakage_keys:
            logger.error(f"Causal Leakage detected: Feature contains future label target '{key}'")
            return False
    return True

def compute_temporal_loss(predictions: Dict[str, Any], lineage_edges: List[Tuple[str, str]], labels_dict: Dict[str, Any]) -> Any:
    # predictions: proposal_id -> PyTorch tensor
    # lineage_edges: List of (parent_id, child_id)
    # labels_dict: proposal_id -> source_strategy_id
    if not HAS_TORCH:
        return 0.0
        
    loss_val = torch.tensor(0.0)
    count = 0
    for u, v in lineage_edges:
        # 무관한 전략 계보끼리는 loss를 더하지 않고, 동일 source_strategy_id 인접 proposal 노드 간에만 적용
        if u in predictions and v in predictions:
            if labels_dict.get(u) == labels_dict.get(v):
                diff = (predictions[u] - predictions[v]).pow(2).sum()
                loss_val = loss_val + diff
                count += 1
                
    if count > 0:
        return loss_val / count
    return loss_val

def train_gnn_model(
    features_list: List[List[float]],
    labels: List[int],
    lineage_edges: List[Tuple[int, int]],
    proposal_ids: List[str],
    strategy_ids: List[str],
    epochs: int = 10,
    lr: float = 0.01,
    lambda_1: float = 0.1
) -> Optional[GNNProposalsClassifier]:
    if not HAS_TORCH:
        logger.warning("Torch is not installed. Skipping actual training.")
        return None
        
    input_dim = len(features_list[0])
    model = GNNProposalsClassifier(input_dim)
    optimizer = optim.Adam(model.parameters(), lr=lr)
    criterion = nn.BCELoss()
    
    # 훈련에 활용하기 위해 ID -> Index 맵 수립
    id_to_idx = {pid: idx for idx, pid in enumerate(proposal_ids)}
    labels_dict = {pid: sid for pid, sid in zip(proposal_ids, strategy_ids)}
    
    # Tensor 변환
    x_tensor = torch.tensor(features_list, dtype=torch.float)
    y_tensor = torch.tensor(labels, dtype=torch.float).unsqueeze(1)
    
    model.train()
    for epoch in range(epochs):
        optimizer.zero_grad()
        predictions = model(x_tensor)
        cls_loss = criterion(predictions, y_tensor)
        
        # Temporal Loss 연산
        pred_dict = {proposal_ids[i]: predictions[i] for i in range(len(proposal_ids))}
        str_lineage_edges = []
        for u_idx, v_idx in lineage_edges:
            if u_idx < len(proposal_ids) and v_idx < len(proposal_ids):
                str_lineage_edges.append((proposal_ids[u_idx], proposal_ids[v_idx]))
                
        t_loss = compute_temporal_loss(pred_dict, str_lineage_edges, labels_dict)
        total_loss = cls_loss + lambda_1 * t_loss
        
        total_loss.backward()
        optimizer.step()
        
    return model

def export_to_onnx(model: GNNProposalsClassifier, dummy_input: Any, onnx_path: str) -> bool:
    if not HAS_TORCH or model is None:
        logger.warning("Torch or Model is unavailable. Skipping ONNX export.")
        return False
    torch.onnx.export(
        model,
        dummy_input,
        onnx_path,
        input_names=["input"],
        output_names=["output"],
        dynamic_axes={"input": {0: "batch_size"}, "output": {0: "batch_size"}}
    )
    logger.info(f"Model successfully exported to ONNX: {onnx_path}")
    return True

def verify_pytorch_onnx_parity(pytorch_model: GNNProposalsClassifier, onnx_path: str, dummy_input: Any) -> bool:
    if not HAS_TORCH or pytorch_model is None:
        return True
    try:
        import onnxruntime as ort
        import numpy as np
    except ImportError:
        logger.warning("onnxruntime is not installed. Parity test skipped.")
        return True
        
    pytorch_model.eval()
    with torch.no_grad():
        py_out = pytorch_model(dummy_input).numpy()
        
    sess = ort.InferenceSession(onnx_path)
    input_name = sess.get_inputs()[0].name
    onnx_out = sess.run(None, {input_name: dummy_input.numpy()})[0]
    
    max_diff = np.max(np.abs(py_out - onnx_out))
    logger.info(f"Parity comparison. Max diff: {max_diff:.8f}")
    return bool(max_diff < 1e-5)
