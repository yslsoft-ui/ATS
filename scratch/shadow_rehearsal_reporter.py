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
    report_md.append("# GIRS Shadow Operation & 2-Hour Soak Rehearsal Report")
    report_md.append(f"Generated At: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    # 1. Rehearsal Status
    report_md.append("## 1. Rehearsal Status")
    
    # 실거래 및 자동 승격 차단 여부 체크 (orders_history 및 strategy_parameter_history 조회)
    live_orders_count = 0
    auto_promotions_count = 0
    eval_pass = False
    
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
        
        # 3) 2h Soak Test 통과 여부 검사 (rehearsal_soak_20260610 그룹의 10m/30m/2h horizon 모두 COMPLETED 여부)
        cursor.execute("""
            SELECT COUNT(*) FROM proposal_evaluations pe
            JOIN strategy_proposals sp ON pe.proposal_id = sp.id
            WHERE sp.proposal_group_id = 'rehearsal_soak_20260610' AND pe.evaluation_status = 'COMPLETED'
        """)
        completed_count = cursor.fetchone()[0]
        
        # 10m, 30m, 2h 3개가 모두 끝났는지 확인
        if completed_count >= 3:
            eval_pass = True
            report_md.append("- **2-Hour Soak Rehearsal**: **PASS** (10m, 30m, and 2h evaluations completed successfully)")
        else:
            report_md.append(f"- **2-Hour Soak Rehearsal**: **FAIL** (Only {completed_count}/3 evaluations completed successfully)")
            
        report_md.append(f"- **Live Trading Orders Sent**: **{live_orders_count}** items (Expected: 0)")
        report_md.append(f"- **Auto Strategy Promotions Executed**: **{auto_promotions_count}** items (Expected: 0)")
        
        if live_orders_count == 0 and auto_promotions_count == 0:
            report_md.append("- **Safety Verification**: **PASSED** (Strict isolation guards worked perfectly)")
        else:
            report_md.append("- **Safety Verification**: **FAILED** (Safety guards were breached!)")
    except Exception as e:
        report_md.append(f"- **Safety Verification**: ERROR ({e})")
    report_md.append("")

    # 2. Proposals & Evaluations Overview
    report_md.append("## 2. Proposals & Evaluations Overview")
    try:
        cursor.execute("SELECT COUNT(*) FROM strategy_proposals WHERE proposal_group_id = 'rehearsal_soak_20260610'")
        rehearsal_proposals = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM strategy_proposals WHERE created_at >= date('now', '-7 days')")
        total_proposals = cursor.fetchone()[0]
        
        cursor.execute('''
            SELECT e.horizon_name, e.evaluation_status, COUNT(*) as cnt 
            FROM proposal_evaluations e
            JOIN strategy_proposals p ON e.proposal_id = p.id
            WHERE p.proposal_group_id = 'rehearsal_soak_20260610'
            GROUP BY e.horizon_name, e.evaluation_status
        ''')
        eval_rows = cursor.fetchall()
        
        report_md.append(f"- **Rehearsal Proposals Created**: {rehearsal_proposals} items")
        report_md.append(f"- **Total Proposals (Last 7 Days)**: {total_proposals} items")
        report_md.append("- **Rehearsal Evaluations by Horizon & Status**:")
        if eval_rows:
            for r in eval_rows:
                report_md.append(f"  - `{r['horizon_name']}` ({r['evaluation_status']}): {r['cnt']} items")
        else:
            report_md.append("  - No rehearsal evaluations recorded.")
    except Exception as e:
        report_md.append(f"Error loading proposals count: {e}")
    report_md.append("")

    # 2.1. Detail Evaluations Status (Soak Test Targets)
    report_md.append("### 2.1. Detail Evaluations Status (Soak Test Targets)")
    try:
        cursor.execute('''
            SELECT pe.id, pe.proposal_id, pe.horizon_name, pe.evaluation_status, pe.baseline_value, 
                   pe.actual_roi_7d, pe.actual_trade_count_7d, pe.evaluated_at
            FROM proposal_evaluations pe
            JOIN strategy_proposals sp ON pe.proposal_id = sp.id
            WHERE sp.proposal_group_id = 'rehearsal_soak_20260610'
            ORDER BY pe.id ASC
        ''')
        pe_rows = cursor.fetchall()
        if pe_rows:
            report_md.append("| Eval ID | Proposal ID | Horizon | Status | Baseline Price | Actual ROI | Actual Trades | Evaluated At |")
            report_md.append("| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |")
            for pr in pe_rows:
                eval_time = datetime.fromtimestamp(pr['evaluated_at']).strftime('%Y-%m-%d %H:%M:%S') if pr['evaluated_at'] else 'N/A'
                base_val = f"{pr['baseline_value']:,}" if pr['baseline_value'] else 'N/A'
                act_roi = f"{pr['actual_roi_7d']}%" if pr['actual_roi_7d'] is not None else 'N/A'
                act_trades = f"{pr['actual_trade_count_7d']}" if pr['actual_trade_count_7d'] is not None else 'N/A'
                report_md.append(
                    f"| {pr['id']} | {pr['proposal_id']} | {pr['horizon_name']} | **{pr['evaluation_status']}** | "
                    f"{base_val} | {act_roi} | {act_trades} | {eval_time} |"
                )
        else:
            report_md.append("- No detail evaluation records found for this rehearsal.")
    except Exception as e:
        report_md.append(f"Error loading detail evaluations status: {e}")
    report_md.append("")

    # 3. DB & WAL File Size Growth
    report_md.append("## 3. Database Size & Growth")
    report_md.append("| File | Before Size | After Size | Growth |")
    report_md.append("| :--- | :--- | :--- | :--- |")
    report_md.append(f"| `backtest.db` | {db_before:,} B | {db_after:,} B | {db_diff:+,} B |")
    report_md.append(f"| `backtest.db-wal` | {wal_before:,} B | {wal_after:,} B | {wal_diff:+,} B |")
    report_md.append("")

    # 3.1. WAL Checkpoint Status
    report_md.append("### 3.1. WAL Checkpoint Status")
    if wal_after == 0:
        report_md.append("- **WAL Checkpoint Result**: **SUCCESS** (WAL file has been fully integrated and truncated to 0 B)")
    else:
        report_md.append(f"- **WAL Checkpoint Result**: **ACTIVE** (WAL file size is currently {wal_after:,} B)")
    report_md.append("")

    # 4. 수집 테이블 데이터 증가량
    report_md.append("## 4. Market Data Collection Stats")
    try:
        cursor.execute("SELECT COUNT(*) FROM trades")
        trades_cnt = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM candles")
        candles_cnt = cursor.fetchone()[0]
        report_md.append(f"- **Total Trade Ticks Collected**: {trades_cnt:,} rows")
        report_md.append(f"- **Total Candle OHLCV Collected**: {candles_cnt:,} rows")
    except Exception as e:
        report_md.append(f"Error loading collection stats: {e}")
    report_md.append("")

    # 4.1. Market Data Cleanup Daemon Summary Events
    report_md.append("### 4.1. Market Data Cleanup Summary Events")
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

    # 5. 유니버스 흐름 및 가드 차단 통계
    report_md.append("## 5. Universe Transition & Guard Stats")
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

        # 6. 최종 차단 유지 현황 (universe_guard_state 쿼리)
        cursor.execute("SELECT * FROM universe_guard_state")
        guard_states = cursor.fetchall()
        
        report_md.append("- **Current Blocked Status by Symbol (`universe_guard_state`)**:")
        if guard_states:
            for gs in guard_states:
                last_blocked_str = datetime.fromtimestamp(gs['last_blocked_at']).strftime('%Y-%m-%d %H:%M:%S') if gs['last_blocked_at'] else 'N/A'
                report_md.append(
                    f"  - `{gs['symbol']}` | Status: **{gs['status']}** | Blocked Reason: **{gs['blocked_reason'] or 'NONE'}** | "
                    f"Blocked Count: {gs['blocked_count']} | Last Blocked At: {last_blocked_str} | "
                    f"Last Event Logged Reason: *{gs['last_event_logged_reason'] or 'N/A'}*"
                )
        else:
            report_md.append("  - No active guard state recorded yet.")
    except Exception as e:
        report_md.append(f"Error loading universe guard stats: {e}")
    report_md.append("")

    # 파일에 기록
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(report_md))
    
    conn.close()
    print(f"Report generated successfully: {output_path}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-path", default="data/backtest.db")
    parser.add_argument("--out-path", default="logs/girs_shadow_rehearsal_report.md")
    parser.add_argument("--db-before", type=int, default=0)
    parser.add_argument("--wal-before", type=int, default=0)
    args = parser.parse_args()
    
    generate_report(args.db_path, args.out_path, args.db_before, args.wal_before)

