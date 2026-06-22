import asyncio
import aiosqlite
import yaml
import time
import os
from datetime import datetime

DB_PATH = 'data/backtest.db'
SETTINGS_PATH = 'config/settings.yaml'

def parse_created_at(val) -> int:
    """
    created_at 값을 안전하게 epoch ms 타임스탬프로 파싱합니다.
    """
    if not val:
        return int(time.time() * 1000)
    
    # 1. 숫자형태(int, float)인 경우 바로 반환
    if isinstance(val, (int, float)):
        # 만약 초 단위라면 ms 단위로 보정
        if val < 100000000000:  # 약 3000년 이전 타임스탬프
            return int(val * 1000)
        return int(val)
        
    val_str = str(val).strip()
    
    # 2. 날짜 문자열 포맷 시도 ('2026-06-08 23:47:36' 등)
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%dT%H:%M:%S.%f'):
        try:
            dt = datetime.strptime(val_str, fmt)
            return int(dt.timestamp() * 1000)
        except ValueError:
            continue
            
    # 3. 문자열 내 숫자로만 되어 있는 경우 파싱
    try:
        float_val = float(val_str)
        if float_val < 100000000000:
            return int(float_val * 1000)
        return int(float_val)
    except ValueError:
        pass
        
    print(f"[Warning] Failed to parse created_at value: '{val}'. Fallback to current time.")
    return int(time.time() * 1000)

async def main():
    print("=== Start Migration and Evaluations Backfill ===")
    
    # 1. settings.yaml 로드
    if not os.path.exists(SETTINGS_PATH):
        print(f"[Error] Settings file not found at {SETTINGS_PATH}")
        return
        
    with open(SETTINGS_PATH, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    
    horizons_cfg = config.get('system', {}).get('horizons', {})
    
    # 2. DB 연결
    print(f"Connecting to database: {DB_PATH}")
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        
        # 3. proposal_evaluations 테이블 재생성 (외래키 참조 수정)
        print("Re-creating proposal_evaluations table with correct FOREIGN KEY...")
        # 외래키 일시 비활성화 (테이블 Drop을 위해)
        await db.execute("PRAGMA foreign_keys = OFF;")
        
        # 기존 테이블 DROP
        await db.execute("DROP TABLE IF EXISTS proposal_evaluations;")
        
        # 올바른 외래키를 참조하는 신규 테이블 CREATE
        await db.execute("""
            CREATE TABLE proposal_evaluations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                proposal_id INTEGER NOT NULL,
                horizon_name TEXT NOT NULL,
                predicted_roi_7d REAL,
                actual_roi_7d REAL,
                roi_divergence REAL,
                predicted_trade_count_7d INTEGER,
                actual_trade_count_7d INTEGER,
                trade_count_divergence INTEGER,
                candidate_roi REAL,
                champion_roi REAL,
                roi_gap REAL,
                candidate_mdd REAL,
                champion_mdd REAL,
                virtual_rollback INTEGER DEFAULT 0,
                actual_label TEXT,
                actual_label_source TEXT,
                due_at INTEGER NOT NULL DEFAULT 0,
                evaluated_at INTEGER,
                locked_at INTEGER,
                retry_count INTEGER DEFAULT 0,
                last_error TEXT,
                evaluation_status TEXT NOT NULL DEFAULT 'PENDING',
                horizon_type TEXT,
                horizon_value INTEGER,
                policy_version TEXT,
                scorer_version TEXT,
                predicted_risk_score REAL,
                baseline_value REAL,
                baseline_timestamp INTEGER,
                baseline_volume INTEGER,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (proposal_id) REFERENCES strategy_proposals(id) ON UPDATE CASCADE ON DELETE CASCADE,
                UNIQUE (proposal_id, horizon_name)
            );
        """)
        
        # 인덱스 생성
        await db.execute("CREATE INDEX IF NOT EXISTS idx_prop_eval_status_due ON proposal_evaluations (evaluation_status, due_at);")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_prop_eval_id_horizon ON proposal_evaluations (proposal_id, horizon_name);")
        await db.commit()
        
        # 외래키 다시 활성화
        await db.execute("PRAGMA foreign_keys = ON;")
        print("proposal_evaluations table successfully re-created.")
        
        # 4. strategy_proposals 조회
        print("Fetching existing proposals...")
        async with db.execute("SELECT id, strategy_id, status, confidence_score, created_at FROM strategy_proposals;") as cursor:
            proposals = await cursor.fetchall()
            
        print(f"Found {len(proposals)} proposals in database.")
        
        backfilled_count = 0
        for p in proposals:
            p_id = p['id']
            strategy_id = p['strategy_id']
            confidence_score = p['confidence_score']
            created_at_val = p['created_at']
            
            created_at_ms = parse_created_at(created_at_val)
            
            # 전략명이나 속성을 기준으로 자산군 판별
            # (RSIStrategy 등은 crypto, KIS/Shinhan 관련은 stock)
            if 'rsi' in strategy_id.lower() or 'crypto' in strategy_id.lower():
                market_type = 'crypto'
            else:
                market_type = 'stock'
                
            horizons_list = horizons_cfg.get(market_type, [])
            
            # 각 horizon 별 PENDING 평가 레코드 생성
            for hz in horizons_list:
                hz_name = hz.get('name')
                hz_type = hz.get('type')
                hz_val = hz.get('value')
                
                # due_at = 제안생성시각(created_at_ms) + horizon경과초(hz_val * 1000)
                due_at = created_at_ms + (hz_val * 1000)
                
                await db.execute("""
                    INSERT INTO proposal_evaluations (
                        proposal_id, horizon_name, due_at, evaluation_status,
                        horizon_type, horizon_value, policy_version, scorer_version,
                        predicted_risk_score
                    )
                    VALUES (?, ?, ?, 'PENDING', ?, ?, 'v4', ?, ?)
                """, (
                    p_id,
                    hz_name,
                    due_at,
                    hz_type,
                    hz_val,
                    "mock_v1",
                    float(confidence_score) / 100.0 if confidence_score is not None else 0.5
                ))
                backfilled_count += 1
                
        await db.commit()
        print(f"Backfill complete! Generated {backfilled_count} evaluation records (PENDING) for {len(proposals)} proposals.")

if __name__ == '__main__':
    asyncio.run(main())
