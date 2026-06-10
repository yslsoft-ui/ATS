# -*- coding: utf-8 -*-

import sys
import os
import sqlite3
import json
import argparse
from datetime import datetime

def generate_report(db_path: str, output_path: str, db_before: int, wal_before: int):
    # 파일 크기 측정
    db_after = os.path.getsize(db_path) if os.path.exists(db_path) else 0
    wal_path = db_path + "-wal"
    wal_after = os.path.getsize(wal_path) if os.path.exists(wal_path) else 0
    
    db_diff = db_after - db_before
    wal_diff = wal_after - wal_before

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    report_md = []
    report_md.append("# GIRS Shadow Operation 24-Hour Soak Test Report")
    report_md.append(f"Generated At: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    # 1. Soak Test Status & PASS/FAIL Verdict
    report_md.append("## 1. Soak Test Status & Verdict")
    
    live_orders_count = 0
    auto_promotions_count = 0
    failed_evaluations_count = 0
    stale_evaluations_count = 0
    pass_verdict = True
    
    now_ts = int(datetime.now().timestamp())
    grace_period = 600 # 10분
    
    try:
        # 1) 실거래 주문 체크 (portfolios.type = 'live'인 포트폴리오에 대한 주문 수)
        cursor.execute("""
            SELECT COUNT(*) FROM orders_history o
            JOIN portfolios p ON o.portfolio_id = p.id
            WHERE p.type = 'live'
        """)
        live_orders_count = cursor.fetchone()[0]
        
        # 2) 자동 전략 승격 파라미터 변경 체크 (changed_by = 'AUTO' AND change_reason = 'PROPOSAL_APPLY')
        cursor.execute("""
            SELECT COUNT(*) FROM strategy_parameter_history
            WHERE changed_by = 'AUTO' AND change_reason = 'PROPOSAL_APPLY'
        """)
        auto_promotions_count = cursor.fetchone()[0]
        
        # 3) 만기 지난 평가 중 COMPLETED 가 아닌 것(FAILED, 혹은 만기+grace period가 지났는데 PENDING인 것)
        cursor.execute("""
            SELECT COUNT(*) FROM proposal_evaluations
            WHERE evaluation_status = 'FAILED'
        """)
        failed_evaluations_count = cursor.fetchone()[0]
        
        cursor.execute("""
            SELECT COUNT(*) FROM proposal_evaluations
            WHERE evaluation_status IN ('PENDING', 'EVALUATING') AND (due_at + ?) <= ?
        """, (grace_period, now_ts))
        stale_evaluations_count = cursor.fetchone()[0]
        
        # 미래의 PENDING은 정상 상태로 분류
        cursor.execute("""
            SELECT COUNT(*) FROM proposal_evaluations
            WHERE evaluation_status = 'PENDING' AND (due_at + ?) > ?
        """, (grace_period, now_ts))
        future_pending_count = cursor.fetchone()[0]
        
        # PASS 판정
        if live_orders_count > 0 or auto_promotions_count > 0 or failed_evaluations_count > 0 or stale_evaluations_count > 0:
            pass_verdict = False
            
        verdict_str = "**PASS**" if pass_verdict else "**FAIL**"
        report_md.append(f"- **Final Verdict**: {verdict_str}")
        report_md.append(f"- **Live Trading Orders Sent**: **{live_orders_count}** (Expected: 0)")
        report_md.append(f"- **Auto Strategy Promotions**: **{auto_promotions_count}** (Expected: 0)")
        report_md.append(f"- **Failed Evaluations**: **{failed_evaluations_count}** (Expected: 0)")
        report_md.append(f"- **Stale/Overdue Evaluations (Pending/Evaluating past grace period)**: **{stale_evaluations_count}** (Expected: 0)")
        report_md.append(f"- **Future/Valid Pending Evaluations**: **{future_pending_count}** (Normal status during Soak Test)")
        
        if pass_verdict:
            report_md.append("- **Safety Verification**: **PASSED** (Strict isolation guards and dynamic shadow evaluations completed normally)")
        else:
            report_md.append("- **Safety Verification**: **FAILED** (Anomaly detected in safety gates, scheduler or evaluations)")
            
    except Exception as e:
        report_md.append(f"- **Safety Verification**: ERROR ({e})")
        pass_verdict = False
    report_md.append("")

    # 2. Proposals & Evaluations Details
    report_md.append("## 2. Proposals & Evaluations Metrics")
    try:
        # proposal 생성 수
        cursor.execute("SELECT COUNT(*) FROM strategy_proposals")
        total_proposals = cursor.fetchone()[0]
        
        # 롤백 후보 여부
        cursor.execute("SELECT COUNT(*) FROM strategy_proposals WHERE status = 'ROLLED_BACK' OR rolled_back_at IS NOT NULL")
        rolled_back_proposals = cursor.fetchone()[0]
        
        report_md.append(f"- **Total Strategy Proposals Created**: {total_proposals} items")
        report_md.append(f"- **Rolled Back Proposals (Rollback Candidates)**: {rolled_back_proposals} items")
        
        # horizon별 PENDING / COMPLETED / FAILED 수
        cursor.execute("""
            SELECT horizon_name, evaluation_status, COUNT(*) as cnt 
            FROM proposal_evaluations 
            GROUP BY horizon_name, evaluation_status
        """)
        eval_summary = cursor.fetchall()
        report_md.append("- **Evaluations by Horizon & Status**:")
        if eval_summary:
            for r in eval_summary:
                report_md.append(f"  - `{r['horizon_name']}` ({r['evaluation_status']}): {r['cnt']} items")
        else:
            report_md.append("  - No evaluation records found.")
            
    except Exception as e:
        report_md.append(f"Error loading proposal & evaluation metrics: {e}")
    report_md.append("")

    # 3. FP / FN / TP / TN Signal Quality Metrics
    report_md.append("## 3. Signal Quality Metrics (FP/FN/TP/TN)")
    try:
        cursor.execute("""
            SELECT 
                SUM(CASE WHEN predicted_roi_7d > 0 AND actual_roi_7d > 0 THEN 1 ELSE 0 END) as tp,
                SUM(CASE WHEN predicted_roi_7d > 0 AND actual_roi_7d <= 0 THEN 1 ELSE 0 END) as fp,
                SUM(CASE WHEN predicted_roi_7d <= 0 AND actual_roi_7d > 0 THEN 1 ELSE 0 END) as fn,
                SUM(CASE WHEN predicted_roi_7d <= 0 AND actual_roi_7d <= 0 THEN 1 ELSE 0 END) as tn,
                COUNT(*) as total
            FROM proposal_evaluations
            WHERE evaluation_status = 'COMPLETED'
        """)
        sq = cursor.fetchone()
        tp = sq['tp'] or 0
        fp = sq['fp'] or 0
        fn = sq['fn'] or 0
        tn = sq['tn'] or 0
        total_completed = sq['total'] or 0
        
        precision = round(tp / (tp + fp) * 100, 2) if (tp + fp) > 0 else 0.0
        recall = round(tp / (tp + fn) * 100, 2) if (tp + fn) > 0 else 0.0
        accuracy = round((tp + tn) / total_completed * 100, 2) if total_completed > 0 else 0.0
        
        report_md.append(f"- **Total Completed Evaluations**: {total_completed} items")
        report_md.append(f"- **True Positive (TP)**: {tp} items (Predicted Up, Actual Up)")
        report_md.append(f"- **False Positive (FP)**: {fp} items (Predicted Up, Actual Down/Flat)")
        report_md.append(f"- **False Negative (FN)**: {fn} items (Predicted Down/Flat, Actual Up)")
        report_md.append(f"- **True Negative (TN)**: {tn} items (Predicted Down/Flat, Actual Down/Flat)")
        report_md.append(f"- **Metrics Summary**:")
        report_md.append(f"  - **Precision**: {precision}%")
        report_md.append(f"  - **Recall**: {recall}%")
        report_md.append(f"  - **Accuracy**: {accuracy}%")
    except Exception as e:
        report_md.append(f"Error loading Signal Quality metrics: {e}")
    report_md.append("")

    # 4. DB & WAL File Size Growth
    report_md.append("## 4. Database Size & Growth")
    report_md.append("| File | Before Size | After Size | Growth |")
    report_md.append("| :--- | :--- | :--- | :--- |")
    report_md.append(f"| `backtest.db` | {db_before:,} B | {db_after:,} B | {db_diff:+,} B |")
    report_md.append(f"| `backtest.db-wal` | {wal_before:,} B | {wal_after:,} B | {wal_diff:+,} B |")
    report_md.append("")

    # 4.1. WAL Checkpoint Status
    report_md.append("### 4.1. WAL Checkpoint Status")
    if wal_after == 0:
        report_md.append("- **WAL Checkpoint Result**: **SUCCESS** (WAL file has been fully integrated and truncated to 0 B)")
    else:
        report_md.append(f"- **WAL Checkpoint Result**: **ACTIVE** (WAL file size is currently {wal_after:,} B)")
    report_md.append("")

    # 5. 수집 테이블 데이터 증가량
    report_md.append("## 5. Market Data Collection Stats")
    try:
        cursor.execute("SELECT COUNT(*) FROM trades")
        trades_cnt = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM candles")
        candles_cnt = cursor.fetchone()[0]
        report_md.append(f"- **Total Trade Ticks in DB**: {trades_cnt:,} rows")
        report_md.append(f"- **Total Candle OHLCV in DB**: {candles_cnt:,} rows")
    except Exception as e:
        report_md.append(f"Error loading collection stats: {e}")
    report_md.append("")

    # 5.1. Market Data Cleanup Daemon Summary Events
    report_md.append("### 5.1. Market Data Cleanup Summary Events")
    try:
        cursor.execute("SELECT * FROM system_events WHERE event_type = 'MARKET_DATA_CLEANUP_SUMMARY' ORDER BY timestamp DESC")
        cleanup_events = cursor.fetchall()
        if cleanup_events:
            report_md.append("| Event Time | Trades Deleted | Candles Deleted | Candles Downsampled | Trades Cutoff | Candles Cutoff |")
            report_md.append("| :--- | :--- | :--- | :--- | :--- | :--- |")
            for ev in cleanup_events:
                dt_str = datetime.fromtimestamp(ev['timestamp'] / 1000).strftime('%Y-%m-%d %H:%M:%S')
                try:
                    msg_data = json.loads(ev['message'])
                    tr_del = msg_data.get('trades_deleted', 0)
                    cd_del = msg_data.get('candles_deleted', 0)
                    cd_ds = msg_data.get('candles_downsampled', 0)
                    tr_cut = datetime.fromtimestamp(msg_data.get('trades_cutoff', 0)).strftime('%Y-%m-%d %H:%M:%S')
                    cd_cut = datetime.fromtimestamp(msg_data.get('candles_cutoff', 0)).strftime('%Y-%m-%d %H:%M:%S')
                    report_md.append(f"| {dt_str} | {tr_del:,} | {cd_del:,} | {cd_ds:,} | {tr_cut} | {cd_cut} |")
                except Exception as ex:
                    report_md.append(f"| {dt_str} | Error parsing message: {ex} | | | | |")
        else:
            report_md.append("- No cleanup summary events recorded. (Cleanup daemon might not have executed a full run yet)")
    except Exception as e:
        report_md.append(f"Error loading cleanup summary events: {e}")
    report_md.append("")

    # 6. 유니버스 흐름 및 가드 차단 통계
    report_md.append("## 6. Universe Transition & Guard Stats")
    try:
        cursor.execute('''
            SELECT event_type, COUNT(*) as cnt 
            FROM system_events 
            WHERE event_type IN ('UNIVERSE_PROMOTION', 'UNIVERSE_DEMOTION')
            GROUP BY event_type
        ''')
        transitions = cursor.fetchall()
        report_md.append("- **Universe Transition Events**:")
        for t in transitions:
            report_md.append(f"  - `{t['event_type']}`: {t['cnt']} times")

        cursor.execute("SELECT message FROM system_events WHERE event_type = 'UNIVERSE_GUARD_SUMMARY' ORDER BY timestamp DESC")
        summary_rows = cursor.fetchall()
        
        total_cooldown = 0
        total_quota = 0
        total_limit = 0
        
        for sr in summary_rows:
            try:
                data = json.loads(sr['message'])
                total_cooldown += data.get('cooldown_blocked_count', 0)
                total_quota += data.get('quota_blocked_count', 0)
                total_limit += data.get('limit_blocked_count', 0)
            except:
                pass
                
        report_md.append("- **Universe Guard Block Count (Aggregated)**:")
        report_md.append(f"  - **Cooldown Blocked**: {total_cooldown} times")
        report_md.append(f"  - **Quota Blocked**: {total_quota} times")
        report_md.append(f"  - **Limit Blocked**: {total_limit} times")

        # 6.1. 최종 차단 유지 현황 (universe_guard_state 쿼리)
        cursor.execute("SELECT status, COUNT(*) as cnt FROM universe_guard_state GROUP BY status")
        guard_dist = cursor.fetchall()
        report_md.append("- **Universe Guard State Distribution**:")
        if guard_dist:
            for gd in guard_dist:
                report_md.append(f"  - **{gd['status']}**: {gd['cnt']} items")
        else:
            report_md.append("  - No active guard state recorded yet.")
            
        cursor.execute("SELECT * FROM universe_guard_state")
        guard_states = cursor.fetchall()
        report_md.append("- **Detail universe_guard_state by Symbol**:")
        if guard_states:
            for gs in guard_states:
                last_blocked_str = datetime.fromtimestamp(gs['last_blocked_at']).strftime('%Y-%m-%d %H:%M:%S') if gs['last_blocked_at'] else 'N/A'
                report_md.append(
                    f"  - `[{gs['exchange']}] {gs['market_type']} / {gs['symbol']}` | Status: **{gs['status']}** | Blocked Reason: **{gs['blocked_reason'] or 'NONE'}** | "
                    f"Blocked Count: {gs['blocked_count']} | Last Blocked At: {last_blocked_str}"
                )
        else:
            report_md.append("  - No active guard state recorded yet.")
    except Exception as e:
        report_md.append(f"Error loading universe guard stats: {e}")
    report_md.append("")

    # 7. System Warnings / Errors / CONFIG_LOADED events
    report_md.append("## 7. Daemon CONFIG_LOADED & Issues Log")
    try:
        # CONFIG_LOADED 이벤트를 조회하여 payload(context)와 함께 로깅
        cursor.execute("SELECT * FROM system_events WHERE event_type = 'CONFIG_LOADED' ORDER BY timestamp ASC")
        config_events = cursor.fetchall()
        report_md.append("- **CONFIG_LOADED Events**:")
        if config_events:
            for ce in config_events:
                dt_str = datetime.fromtimestamp(ce['timestamp'] / 1000).strftime('%Y-%m-%d %H:%M:%S')
                report_md.append(f"  - **[{dt_str}]** `{ce['target']}`: {ce['message']}")
                if ce['context']:
                    try:
                        ctx_parsed = json.loads(ce['context'])
                        report_md.append(f"    - Payload: `{json.dumps(ctx_parsed, indent=2)}`")
                    except:
                        report_md.append(f"    - Raw Context: `{ce['context']}`")
        else:
            report_md.append("  - No CONFIG_LOADED events found.")
            
        # DAEMON_CRASHED, ERROR, WARNING 등 이슈 목록
        cursor.execute("SELECT * FROM system_events WHERE event_type IN ('DAEMON_CRASHED', 'ERROR', 'WARNING') ORDER BY timestamp DESC")
        issue_events = cursor.fetchall()
        report_md.append("- **Recent Critical Alerts & Issues**:")
        if issue_events:
            for ie in issue_events:
                dt_str = datetime.fromtimestamp(ie['timestamp'] / 1000).strftime('%Y-%m-%d %H:%M:%S')
                report_md.append(f"  - **[{dt_str}]** `[{ie['event_type']}]` `{ie['target']}`: {ie['message']}")
        else:
            report_md.append("  - No critical alerts or daemon crashes reported in system_events.")
    except Exception as e:
        report_md.append(f"Error loading issues log: {e}")
    report_md.append("")

    # 파일에 기록
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(report_md))
    
    # JSON 요약 파일 동시 생성
    summary_json_path = output_path.replace(".md", ".json")
    summary_data = {
        "pass_verdict": pass_verdict,
        "live_orders_count": live_orders_count,
        "auto_promotions_count": auto_promotions_count,
        "failed_evaluations_count": failed_evaluations_count,
        "stale_evaluations_count": stale_evaluations_count,
        "future_pending_count": future_pending_count,
        "total_proposals": total_proposals,
        "rolled_back_proposals": rolled_back_proposals,
        "database_growth_bytes": db_diff,
        "wal_growth_bytes": wal_diff,
        "total_trades_cnt": trades_cnt if 'trades_cnt' in locals() else 0,
        "total_candles_cnt": candles_cnt if 'candles_cnt' in locals() else 0,
        "signal_quality": {
            "tp": tp if 'tp' in locals() else 0,
            "fp": fp if 'fp' in locals() else 0,
            "fn": fn if 'fn' in locals() else 0,
            "tn": tn if 'tn' in locals() else 0,
            "precision": precision if 'precision' in locals() else 0.0,
            "recall": recall if 'recall' in locals() else 0.0,
            "accuracy": accuracy if 'accuracy' in locals() else 0.0
        }
    }
    
    with open(summary_json_path, "w", encoding="utf-8") as jf:
        json.dump(summary_data, jf, indent=2, ensure_ascii=False)
        
    conn.close()
    print(f"Report generated successfully: {output_path}")
    print(f"Summary JSON generated successfully: {summary_json_path}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-path", default="data/backtest.db")
    parser.add_argument("--out-path", default="logs/girs_shadow_soak_report.md")
    parser.add_argument("--db-before", type=int, default=0)
    parser.add_argument("--wal-before", type=int, default=0)
    args = parser.parse_args()
    
    generate_report(args.db_path, args.out_path, args.db_before, args.wal_before)
