# -*- coding: utf-8 -*-
import os
import sqlite3
import json
import time
import math
from typing import Dict, List, Any

from src.config.manager import ConfigManager

def generate_report(db_path: str, output_path: str):
    if not os.path.exists(db_path):
        print(f"Database file not found: {db_path}")
        return
        
    # 1. 설정 로드
    config_manager = ConfigManager("config/settings.yaml")
    min_samples = config_manager.get("system.report_min_samples_per_slice", 30)
    cutoff_config = config_manager.get("system.girs_risk_cutoff", {})
    
    # 컷오프 파서 정의 (우선순위: market_horizons -> horizons -> market_type -> default -> fallback 0.5)
    def get_cutoff(market_type: str, horizon_name: str) -> float:
        if not cutoff_config:
            return 0.5
            
        # 1. market_horizons 복합 오버라이드 (예: crypto.10m)
        if market_type and horizon_name and "market_horizons" in cutoff_config:
            composite_key = f"{market_type}.{horizon_name}"
            if composite_key in cutoff_config["market_horizons"]:
                return float(cutoff_config["market_horizons"][composite_key])
                
        # 2. horizons 오버라이드 (예: 10m)
        if horizon_name and "horizons" in cutoff_config:
            if horizon_name in cutoff_config["horizons"]:
                return float(cutoff_config["horizons"][horizon_name])
                
        # 3. market_type 오버라이드 (예: crypto, stock)
        if market_type and market_type in cutoff_config:
            return float(cutoff_config[market_type])
            
        # 4. default 기본값
        if "default" in cutoff_config:
            return float(cutoff_config["default"])
            
        return 0.5
    
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # 2. 다차원 조인 데이터 조회
    query = """
        SELECT 
            pe.id as pe_id, pe.proposal_id, pe.horizon_name, pe.candidate_roi, pe.champion_roi, pe.roi_gap,
            pe.candidate_mdd, pe.champion_mdd, pe.virtual_rollback, pe.actual_label, pe.actual_label_source,
            pe.predicted_risk_score, pe.horizon_type, pe.horizon_value,
            sp.strategy_id, sp.portfolio_id, sp.status as prop_status, sp.outcome as prop_outcome,
            gsm.market_type, gsm.session_state, gsm.volatility_regime, gsm.liquidity_regime, gsm.exchange
        FROM proposal_evaluations pe
        JOIN strategy_proposals sp ON pe.proposal_id = sp.id
        LEFT JOIN girs_shadow_metrics gsm ON CAST(pe.proposal_id AS TEXT) = gsm.proposal_id
        WHERE pe.evaluation_status = 'COMPLETED'
          AND COALESCE(sp.strategy_id, '') != 'smoke_test'
          AND COALESCE(pe.actual_label_source, '') != 'SMOKE'
    """
    
    try:
        cursor.execute(query)
        rows = [dict(r) for r in cursor.fetchall()]
    except sqlite3.OperationalError as e:
        print(f"Operational error: {e}. Checking without joining shadow metrics.")
        # girs_shadow_metrics가 없는 경우 sp와만 조인하여 Fallback 조회
        fallback_query = """
            SELECT 
                pe.id as pe_id, pe.proposal_id, pe.horizon_name, pe.candidate_roi, pe.champion_roi, pe.roi_gap,
                pe.candidate_mdd, pe.champion_mdd, pe.virtual_rollback, pe.actual_label, pe.actual_label_source,
                pe.predicted_risk_score, pe.horizon_type, pe.horizon_value,
                sp.strategy_id, sp.portfolio_id, sp.status as prop_status, sp.outcome as prop_outcome,
                NULL as market_type, NULL as session_state, NULL as volatility_regime, NULL as liquidity_regime, NULL as exchange
            FROM proposal_evaluations pe
            JOIN strategy_proposals sp ON pe.proposal_id = sp.id
            WHERE pe.evaluation_status = 'COMPLETED'
              AND COALESCE(sp.strategy_id, '') != 'smoke_test'
              AND COALESCE(pe.actual_label_source, '') != 'SMOKE'
        """
        try:
            cursor.execute(fallback_query)
            rows = [dict(r) for r in cursor.fetchall()]
        except Exception as ex:
            print(f"Fallback query also failed: {ex}")
            rows = []
            
    conn.close()
    
    if not rows:
        print("No completed evaluations found to generate report.")
        return
        
    # 3. 메트릭 계산 헬퍼 함수
    def calculate_metrics_for_slice(slice_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
        # evaluation_status = 'COMPLETED'인 것 중, predicted_risk_score와 actual_label이 모두 존재하는 것 필터링
        valid_rows = [
            r for r in slice_rows
            if r.get("predicted_risk_score") is not None
            and r.get("actual_label") in ("GOOD", "BAD")
        ]
        
        total = len(valid_rows)
        if total == 0:
            return {
                "ece": 0.0, "brier": 0.0, "count": 0, "accuracy": 0.0,
                "tp": 0, "tn": 0, "fp": 0, "fn": 0,
                "avg_candidate_roi": 0.0, "avg_champion_roi": 0.0,
                "avg_candidate_mdd": 0.0, "avg_champion_mdd": 0.0,
                "rollback_count": 0, "ref_thresholds": {}
            }
            
        correct = 0
        bins = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]
        bin_data = {i: {"preds": [], "actuals": []} for i in range(len(bins)-1)}
        
        brier_score = 0.0
        
        # 공식 cutoff 기준 혼동 행렬
        tp = tn = fp = fn = 0
        
        for r in valid_rows:
            pred_prob = r["predicted_risk_score"]
            actual_label = r["actual_label"]
            actual_bad = 1 if actual_label == 'BAD' else 0
            
            # 레코드별 동적 컷오프 조회
            cutoff = get_cutoff(r.get("market_type"), r.get("horizon_name"))
            pred_bad = 1 if pred_prob >= cutoff else 0
            
            if pred_bad == 1 and actual_bad == 1:
                tp += 1
                correct += 1
            elif pred_bad == 0 and actual_bad == 0:
                tn += 1
                correct += 1
            elif pred_bad == 1 and actual_bad == 0:
                fp += 1
            elif pred_bad == 0 and actual_bad == 1:
                fn += 1
                
            brier_score += (pred_prob - actual_bad) ** 2
            
            # binning
            for i in range(len(bins)-1):
                if bins[i] <= pred_prob <= bins[i+1]:
                    bin_data[i]["preds"].append(pred_prob)
                    bin_data[i]["actuals"].append(actual_bad)
                    break
                    
        brier_score = brier_score / total
        
        ece = 0.0
        for i, b in bin_data.items():
            bin_size = len(b["preds"])
            if bin_size > 0:
                avg_pred = sum(b["preds"]) / bin_size
                avg_act = sum(b["actuals"]) / bin_size
                ece += (bin_size / total) * abs(avg_pred - avg_act)
                
        # 0.3, 0.5, 0.7 다중 threshold 참고 지표 계산
        ref_thresholds = {}
        for th in [0.3, 0.5, 0.7]:
            r_tp = r_tn = r_fp = r_fn = 0
            for r in valid_rows:
                p_prob = r["predicted_risk_score"]
                act_label = r["actual_label"]
                act_bad = 1 if act_label == 'BAD' else 0
                p_bad = 1 if p_prob >= th else 0
                
                if p_bad == 1 and act_bad == 1:
                    r_tp += 1
                elif p_bad == 0 and act_bad == 0:
                    r_tn += 1
                elif p_bad == 1 and act_bad == 0:
                    r_fp += 1
                elif p_bad == 0 and act_bad == 1:
                    r_fn += 1
            ref_thresholds[th] = {"tp": r_tp, "tn": r_tn, "fp": r_fp, "fn": r_fn}
            
        avg_candidate_roi = sum(r.get("candidate_roi", 0.0) for r in valid_rows) / total
        avg_champion_roi = sum(r.get("champion_roi", 0.0) for r in valid_rows) / total
        avg_candidate_mdd = sum(r.get("candidate_mdd", 0.0) for r in valid_rows) / total
        avg_champion_mdd = sum(r.get("champion_mdd", 0.0) for r in valid_rows) / total
        rollback_count = sum(1 for r in valid_rows if r.get("virtual_rollback", 0) == 1)
        
        return {
            "count": total,
            "accuracy": (correct / total) * 100.0,
            "brier": brier_score,
            "ece": ece,
            "tp": tp,
            "tn": tn,
            "fp": fp,
            "fn": fn,
            "avg_candidate_roi": avg_candidate_roi,
            "avg_champion_roi": avg_champion_roi,
            "avg_candidate_mdd": avg_candidate_mdd,
            "avg_champion_mdd": avg_champion_mdd,
            "rollback_count": rollback_count,
            "ref_thresholds": ref_thresholds
        }

    def format_slice_metrics(metrics: Dict[str, Any]) -> Dict[str, str]:
        count = metrics["count"]
        if count == 0:
            return {
                "header_warn": " [⚠️ 데이터 없음]",
                "ece": "N/A", "brier": "N/A", "accuracy": "N/A",
                "count": "0", "tp_tn_fp_fn": "N/A",
                "avg_candidate_roi": "N/A", "avg_champion_roi": "N/A",
                "avg_candidate_mdd": "N/A", "avg_champion_mdd": "N/A",
                "rollback_ratio": "N/A"
            }
            
        if count < min_samples:
            header_warn = f" [⚠️ N 부족 경고 (N={count})]"
            ece_str = f"<span style='color:gray'>({metrics['ece']:.4f})</span>"
            brier_str = f"<span style='color:gray'>({metrics['brier']:.4f})</span>"
            acc_str = f"<span style='color:gray'>({metrics['accuracy']:.2f}%)</span>"
        else:
            header_warn = ""
            ece_str = f"**{metrics['ece']:.4f}**"
            brier_str = f"**{metrics['brier']:.4f}**"
            acc_str = f"**{metrics['accuracy']:.2f}%**"
            
        tp, tn, fp, fn = metrics["tp"], metrics["tn"], metrics["fp"], metrics["fn"]
        tp_tn_fp_fn_str = f"TP:{tp} / TN:{tn} / FP:{fp} / FN:{fn}"
            
        return {
            "header_warn": header_warn,
            "ece": ece_str,
            "brier": brier_str,
            "accuracy": acc_str,
            "count": str(count),
            "tp_tn_fp_fn": tp_tn_fp_fn_str,
            "avg_candidate_roi": f"{metrics['avg_candidate_roi'] * 100.0:.2f}%",
            "avg_champion_roi": f"{metrics['avg_champion_roi'] * 100.0:.2f}%",
            "avg_candidate_mdd": f"{metrics['avg_candidate_mdd'] * 100.0:.2f}%",
            "avg_champion_mdd": f"{metrics['avg_champion_mdd'] * 100.0:.2f}%",
            "rollback_ratio": f"{(metrics['rollback_count'] / count) * 100.0:.1f}% ({metrics['rollback_count']}/{count})"
        }

    # 4. 데이터 그룹화 연산
    # (1) Horizon별 최우선 분류
    by_horizon = {}
    for r in rows:
        hz = r["horizon_name"]
        by_horizon.setdefault(hz, []).append(r)
        
    horizon_results = {}
    for hz, hz_rows in by_horizon.items():
        horizon_results[hz] = calculate_metrics_for_slice(hz_rows)

    # (2) Horizon + 다차원 피처 분할
    dimensions = ["market_type", "exchange", "session_state", "volatility_regime", "liquidity_regime"]
    multidim_results = {dim: {} for dim in dimensions}
    
    for r in rows:
        hz = r["horizon_name"]
        for dim in dimensions:
            dim_val = r.get(dim)
            if dim_val is not None:
                key = (hz, str(dim_val))
                multidim_results[dim].setdefault(key, []).append(r)
                
    multidim_metrics = {dim: {} for dim in dimensions}
    for dim, items in multidim_results.items():
        for key, dim_rows in items.items():
            multidim_metrics[dim][key] = calculate_metrics_for_slice(dim_rows)

    # 5. 리포트 본문 작성
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    
    report_md = []
    report_md.append("# GIRS Shadow Operation 다차원 평가 리포트")
    report_md.append(f"- **발행 시각**: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    report_md.append(f"- **분석 대상 DB**: `{db_path}`")
    report_md.append(f"- **소표본 임계값 (Min Samples)**: {min_samples} 건")
    report_md.append(f"- **설정된 GIRS 리스크 차단 임계값 (`girs_risk_cutoff`):**")
    report_md.append("```json\n" + json.dumps(cutoff_config, indent=2) + "\n```")
    report_md.append("\n> [!NOTE]\n> **단일 예측 위험 점수의 다중 Horizon 재사용 한계 해석 안내**\n> GIRS Shadow 모드는 제안 발생 시점에 단일 `predicted_risk_score`를 계산하며, 이를 모든 Horizon(10m, 30m, 2h 등) 평가에 동일하게 재사용합니다.\n> 특정 Horizon에서 ECE나 Brier Score가 낮게 나타나더라도, 그것은 해당 Horizon에 대한 고유한 예측 신뢰도가 아닐 수 있으며, 각 Horizon의 특성에 맞춰 위험 점수가 해석되어야 함을 인지해야 합니다.\n")
 
    # (1) Horizon별 성과 요약 테이블
    report_md.append("## 1. Horizon별 종합 평가 결과 (공식 Cutoff 적용)")
    report_md.append("| Horizon | 표본 수 | 예측 정확도 (Accuracy) | Brier Score | ECE (Expected Calibration Error) | 혼동 행렬 (TP/TN/FP/FN) | 후보 평균 ROI | 챔피언 평균 ROI | 후보 평균 MDD | 챔피언 평균 MDD | 가상 롤백 비율 |")
    report_md.append("|---|---|---|---|---|---|---|---|---|---|---|")
    
    for hz in sorted(horizon_results.keys()):
        met = horizon_results[hz]
        f = format_slice_metrics(met)
        warn = f["header_warn"]
        hz_display = f"{hz}{warn}"
        report_md.append(f"| {hz_display} | {f['count']} | {f['accuracy']} | {f['brier']} | {f['ece']} | {f['tp_tn_fp_fn']} | {f['avg_candidate_roi']} | {f['avg_champion_roi']} | {f['avg_candidate_mdd']} | {f['avg_champion_mdd']} | {f['rollback_ratio']} |")

    # (2) 다차원 교차 분석 결과
    report_md.append("\n## 2. 다차원 세부 세그먼트 분석 (공식 Cutoff 적용)")
    report_md.append("표본 수가 30개 미만인 세그먼트는 회색 괄호 `(수치)`로 마킹되며, 통계적 유의성이 부족하므로 참고용으로만 사용하십시오.\n")
    
    dim_titles = {
        "market_type": "시장 유형별 (Market Type)",
        "exchange": "거래소별 (Exchange)",
        "session_state": "장 운영 상태별 (Session State)",
        "volatility_regime": "시장 변동성 레짐별 (Volatility Regime)",
        "liquidity_regime": "시장 유동성 레짐별 (Liquidity Regime)"
    }
    
    for dim in dimensions:
        report_md.append(f"### 2.{dimensions.index(dim)+1}. {dim_titles[dim]}")
        report_md.append("| Horizon | 세그먼트 | 표본 수 | Accuracy | Brier Score | ECE | 혼동 행렬 (TP/TN/FP/FN) | 후보 평균 ROI | 챔피언 평균 ROI | 가상 롤백 비율 |")
        report_md.append("|---|---|---|---|---|---|---|---|---|---|")
        
        dim_mets = multidim_metrics[dim]
        for key in sorted(dim_mets.keys()):
            hz, val = key
            met = dim_mets[key]
            f = format_slice_metrics(met)
            report_md.append(f"| {hz} | {val} | {f['count']} | {f['accuracy']} | {f['brier']} | {f['ece']} | {f['tp_tn_fp_fn']} | {f['avg_candidate_roi']} | {f['avg_champion_roi']} | {f['rollback_ratio']} |")
        report_md.append("")

    # (3) 다중 임계값(Threshold) 참고 분석 표
    report_md.append("## 3. 임계값(Threshold)별 참고 분석 (지표 튜닝용)")
    report_md.append("각 Horizon별로 임계값을 0.3, 0.5, 0.7로 임의 조정했을 때의 혼동 행렬 분포입니다. 최적의 차단 임계치를 탐색하는 용도로 참고하십시오.\n")
    
    for hz in sorted(horizon_results.keys()):
        met = horizon_results[hz]
        if met["count"] == 0:
            continue
        report_md.append(f"### 3.{list(sorted(horizon_results.keys())).index(hz)+1}. Horizon: {hz} (N = {met['count']})")
        report_md.append("| Threshold (임계값) | TP (위험포착) | TN (안전통과) | FP (과차단) | FN (위험미포착) | 정밀도 (Precision) | 재현율 (Recall) |")
        report_md.append("|---|---|---|---|---|---|---|")
        
        ref_th = met.get("ref_thresholds", {})
        for th in sorted(ref_th.keys()):
            counts = ref_th[th]
            tp_c = counts["tp"]
            tn_c = counts["tn"]
            fp_c = counts["fp"]
            fn_c = counts["fn"]
            
            precision = (tp_c / (tp_c + fp_c)) * 100.0 if (tp_c + fp_c) > 0 else 0.0
            recall = (tp_c / (tp_c + fn_c)) * 100.0 if (tp_c + fn_c) > 0 else 0.0
            
            report_md.append(f"| {th:.1f} | {tp_c} | {tn_c} | {fp_c} | {fn_c} | {precision:.2f}% | {recall:.2f}% |")
        report_md.append("")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(report_md))
        
    print(f"Verification report created successfully: {output_path}")

if __name__ == "__main__":
    generate_report("data/backtest.db", "logs/girs_shadow_report.md")
