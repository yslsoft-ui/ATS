# -*- coding: utf-8 -*-
import os
import sqlite3
import json
import time
import math
from typing import Dict, List, Any

def generate_report(db_path: str, output_path: str):
    # 데이터베이스 연결
    if not os.path.exists(db_path):
        print(f"Database file not found: {db_path}")
        return
        
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # 1. 섀도 메트릭 데이터 조회
    try:
        cursor.execute("SELECT * FROM girs_shadow_metrics ORDER BY timestamp ASC")
        metrics = [dict(r) for r in cursor.fetchall()]
    except sqlite3.OperationalError:
        print("girs_shadow_metrics table not found. Creating empty report.")
        metrics = []
        
    # 2. 제안(Proposals) 및 평가 결과 조회
    try:
        cursor.execute("SELECT * FROM strategy_proposals")
        proposals = {r["id"]: dict(r) for r in cursor.fetchall()}
    except sqlite3.OperationalError:
        proposals = {}
        
    try:
        cursor.execute("SELECT * FROM proposal_evaluations")
        evaluations = {r["proposal_id"]: dict(r) for r in cursor.fetchall()}
    except sqlite3.OperationalError:
        evaluations = {}
        
    conn.close()
    
    # 지표 산정 변수
    total_evals = len(evaluations)
    false_positives = 0
    false_negatives = 0
    true_positives = 0  # GIRS가 위험하다고 차단했고, 실제로도 나빴을 경우
    true_negatives = 0  # GIRS가 안전하다고 승격했고, 실제로도 좋았을 경우
    
    blocked_count = 0
    promoted_count = 0
    
    expired_count = 0
    rejected_count = 0
    
    # proposals 상태 분류
    for pid, prop in proposals.items():
        status = prop.get("status")
        if status == "EXPIRED":
            expired_count += 1
        elif status == "REJECTED":
            rejected_count += 1
            
    # FP/FN 계산
    # positive = "롤백 위험 높음" -> GIRS가 위험하다고 차단하는 것 (final_promotion_score < 0.8)
    # negative = "안전함" -> GIRS가 승격 허용하는 것 (final_promotion_score >= 0.8)
    
    # 각 제안별 최종 결정값 수집
    proposal_girs_decisions = {}
    for m in metrics:
        pid = m.get("proposal_id")
        if not pid or pid == "SYSTEM":
            continue
        try:
            pid_int = int(pid)
        except ValueError:
            continue
            
        final_score = m.get("final_promotion_score")
        if final_score is not None:
            # 여러 번 평가된 경우 최신 점수 기준
            proposal_girs_decisions[pid_int] = final_score
            
    # 매칭되는 평가 데이터를 활용해 FP/FN 산정
    for pid, val in evaluations.items():
        # GIRS 판단
        girs_score = proposal_girs_decisions.get(pid, 1.0) # 없으면 기본 안전(1.0)으로 취급
        girs_predicted_danger = girs_score < 0.8  # Positive = 롤백위험높음 (차단)
        
        # 실제 성과 판정
        # 실제 ROI 괴리가 음수이거나 (predicted 대비 실측 ROI 하락), 실제 롤백이 일어난 경우 나쁜 성과로 판단
        # 롤백 여부 조회
        prop = proposals.get(pid, {})
        is_rolled_back = prop.get("rolled_back_at") is not None
        roi_div = val.get("roi_divergence", 0.0)
        
        actual_bad = is_rolled_back or roi_div < -2.0  # 임계값 -2.0% 괴리 시 나쁜 성과
        
        if girs_predicted_danger:  # GIRS 예측: Positive (위험함)
            blocked_count += 1
            if not actual_bad:  # 실제는 좋았음 -> False Positive
                false_positives += 1
            else:  # 실제도 나빴음 -> True Positive
                true_positives += 1
        else:  # GIRS 예측: Negative (안전함)
            promoted_count += 1
            if actual_bad:  # 실제로는 나빴음 -> False Negative
                false_negatives += 1
            else:  # 실제도 좋았음 -> True Negative
                true_negatives += 1
                
    # Replay Drift 분석
    drift_events = [m for m in metrics if m.get("decision_type") == "REPLAY"]
    drift_count = len(drift_events)
    avg_drift = sum(m.get("replay_drift", 0.0) for m in drift_events) / max(1, drift_count)
    
    # Calibration ECE (Expected Calibration Error) 간이 연산
    # 0~1 구간을 5개 bin으로 쪼개서 각 bin별 예측 신뢰도와 실제 양성율 차이의 가중합 연산
    bins = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]
    bin_data = {i: {"preds": [], "actuals": []} for i in range(len(bins)-1)}
    
    for pid, val in evaluations.items():
        girs_score = proposal_girs_decisions.get(pid)
        if girs_score is None:
            continue
        # GIRS 가 예측한 "안전할 확률"
        pred_prob = girs_score
        
        # 실제 안전했는지 여부 (1: 안전함(Good), 0: 위험함(Bad))
        prop = proposals.get(pid, {})
        is_rolled_back = prop.get("rolled_back_at") is not None
        roi_div = val.get("roi_divergence", 0.0)
        actual_good = 0 if (is_rolled_back or roi_div < -2.0) else 1
        
        for i in range(len(bins)-1):
            if bins[i] <= pred_prob <= bins[i+1]:
                bin_data[i]["preds"].append(pred_prob)
                bin_data[i]["actuals"].append(actual_good)
                break
                
    ece = 0.0
    total_samples = sum(len(b["preds"]) for b in bin_data.values())
    brier_score = 0.0
    
    brier_samples = 0
    for i, b in bin_data.items():
        bin_size = len(b["preds"])
        if bin_size > 0:
            avg_pred = sum(b["preds"]) / bin_size
            avg_act = sum(b["actuals"]) / bin_size
            ece += (bin_size / max(1, total_samples)) * abs(avg_pred - avg_act)
            
            for p, a in zip(b["preds"], b["actuals"]):
                brier_score += (p - a) ** 2
                brier_samples += 1
                
    brier_score = brier_score / max(1, brier_samples)
    
    # 마크다운 리포트 생성
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    
    accuracy = (true_positives + true_negatives) / max(1, total_evals) * 100
    fp_rate = false_positives / max(1, blocked_count) * 100
    fn_rate = false_negatives / max(1, promoted_count) * 100
    
    report_content = f"""# GIRS Shadow Operation 검증 리포트
발행 시간: {time.strftime('%Y-%m-%d %H:%M:%S')}
분석 대상 DB: {db_path}

## 1. 종합 요약
- **총 평가 제안 수**: {total_evals} 건
- **롤백 예측 정확도 (Accuracy)**: {accuracy:.2f}%
- **False Positive Rate (GIRS 과잉 차단)**: {fp_rate:.2f}% (위험하다고 막았으나 실제 좋았던 비율: {false_positives}/{max(1, blocked_count)})
- **False Negative Rate (GIRS 위험 노출)**: {fn_rate:.2f}% (안전하다고 승격했으나 실제 롤백/손실 발생: {false_negatives}/{max(1, promoted_count)})

## 2. 혼동 행렬 (Confusion Matrix)
| 구분 | 실제 롤백/손실 발생 (Actual Bad) | 실제 성과 우수 (Actual Good) |
|---|---|---|
| **GIRS 차단 예측 (Predicted Bad)** | True Positive: {true_positives} | False Positive: {false_positives} |
| **GIRS 승격 예측 (Predicted Good)** | False Negative: {false_negatives} | True Negative: {true_negatives} |

*※ Positive 정의: "롤백 위험 높음 (차단 대상)"*

## 3. 제안 상태 통계 (Queue Stats)
- **만료 (Expired)**: {expired_count} 건
- **기각 (Rejected)**: {rejected_count} 건
- **차단 (Blocked by GIRS)**: {blocked_count} 건
- **승격 (Promoted by GIRS)**: {promoted_count} 건

## 4. Replay Drift 분석
- **Replay Drift 측정 횟수**: {drift_count} 회
- **평균 Drift 수치**: {avg_drift:.4f}
- **Drift 임계치 초과 보정 활성화 상태**: {"활성화" if any(m.get("correction_active") for m in drift_events) else "비활성화"}

## 5. Calibration 상태
- **Expected Calibration Error (ECE)**: {ece:.4f}
- **Brier Score (예측 정밀도)**: {brier_score:.4f}
"""
    
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(report_content)
    print(f"Verification report created successfully: {output_path}")

if __name__ == "__main__":
    generate_report("data/backtest.db", "logs/girs_shadow_report.md")
