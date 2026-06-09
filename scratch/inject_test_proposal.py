# -*- coding: utf-8 -*-
import sys
import os
import asyncio
import time

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.database.repository import SqliteTradingRepository
from src.database.connection import get_db_conn

async def main():
    db_path = "data/backtest.db"
    repo = SqliteTradingRepository(db_path=db_path)
    
    # 1. 포트폴리오 ID 조회
    portfolio_id = "sim_port_rehearsal"
    async with get_db_conn(db_path) as db:
        async with db.execute("SELECT id FROM portfolios LIMIT 1") as cursor:
            row = await cursor.fetchone()
            if row:
                portfolio_id = row[0]
    
    print(f"Using Portfolio ID: {portfolio_id}")

    # 2. strategy_proposals 주입
    proposal_data = {
        "insight_id": None,
        "proposal_group_id": "rehearsal_soak_20260610",
        "version": 1,
        "portfolio_id": portfolio_id,
        "strategy_id": "rsistrategy",
        "status": "PENDING",
        "outcome": "RUNNING",
        "original_params": '{"rsi_window": 14, "buy_threshold": 30.0, "sell_threshold": 70.0}',
        "proposed_params": '{"rsi_window": 14, "buy_threshold": 32.0, "sell_threshold": 68.0}',
        "metrics": '{"roi_7d": 5.0, "trade_count_7d": 35}',
        "mutation_trace": '{}',
        "confidence_score": 85,
        "applied_at": None,
        "rolled_back_at": None,
        "decision_path_hash": "hash_rehearsal_soak",
        "audit_log_json": "{}"
    }
    
    proposal_id = await repo.insert_strategy_proposal(proposal_data)
    print(f"Inserted strategy_proposal with ID: {proposal_id}")
    
    # 3. proposal_evaluations 주입 (raw SQL 사용)
    now = int(time.time())
    horizons = [
        {"name": "10m", "val": 600, "due": now + 600},
        {"name": "30m", "val": 1800, "due": now + 1800},
        {"name": "2h", "val": 7200, "due": now + 7200}
    ]
    
    async with get_db_conn(db_path) as db:
        for hz in horizons:
            await db.execute("""
                INSERT INTO proposal_evaluations (
                    proposal_id, horizon_name, due_at, evaluation_status,
                    horizon_type, horizon_value, policy_version, scorer_version,
                    predicted_risk_score, predicted_roi_7d, actual_roi_7d, roi_divergence,
                    predicted_trade_count_7d, actual_trade_count_7d, trade_count_divergence
                ) VALUES (?, ?, ?, 'PENDING', 'elapsed', ?, 'rehearsal_v1', 'mock_v1', 0.15, 5.0, 0.0, 0.0, 35, 0, 0)
            """, (proposal_id, hz["name"], hz["due"], hz["val"]))
        await db.commit()
    print("Inserted 3 proposal_evaluations records.")
    
    # 4. Sanity Check 수행 (정합성 검증)
    async with get_db_conn(db_path) as db:
        async with db.execute(
            "SELECT horizon_name, evaluation_status, due_at FROM proposal_evaluations WHERE proposal_id = ?",
            (proposal_id,)
        ) as cursor:
            rows = [dict(r) for r in await cursor.fetchall()]
            
    assert len(rows) == 3, f"Expected 3 evaluations, got {len(rows)}"
    for r in rows:
        name = r["horizon_name"]
        status = r["evaluation_status"]
        due = r["due_at"]
        expected_hz = next(h for h in horizons if h["name"] == name)
        
        # 정합성 검증 단언
        assert status == "PENDING", f"Expected PENDING for {name}, got {status}"
        assert due == expected_hz["due"], f"Expected due_at {expected_hz['due']} for {name}, got {due}"
        print(f"[Sanity Check PASS] Horizon={name}, Status={status}, DueAt={due}")

if __name__ == "__main__":
    asyncio.run(main())
