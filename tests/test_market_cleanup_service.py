# -*- coding: utf-8 -*-

import os
import time
import pytest
import sqlite3
from typing import Any
from src.database.connection import get_db_conn
from src.database.schema import init_db
from src.services.market_cleanup_service import MarketDataCleanupService

class MockConfig:
    def __init__(self, db_path: str):
        self.db_path = db_path

    def get(self, key: str, default: Any = None) -> Any:
        if key == 'system.db_path':
            return self.db_path
        elif key == 'system.retention':
            return {'trades_hours': 72, 'candles_days': 30}
        elif key == 'system.cleanup_interval_seconds':
            return 3600
        return default

@pytest.mark.asyncio
async def test_market_cleanup_and_idempotency():
    db_path = "data/test_cleanup.db"
    
    # 0. 깨끗한 테스트 DB 초기화
    if os.path.exists(db_path):
        os.remove(db_path)
    if os.path.exists(db_path + "-wal"):
        os.remove(db_path + "-wal")
    if os.path.exists(db_path + "-shm"):
        os.remove(db_path + "-shm")
        
    await init_db(db_path)
    
    config = MockConfig(db_path)
    service = MarketDataCleanupService(config_manager=config, event_bus=None)
    service._is_running = True
    
    # 테스트용 시각 설정 (현재 시각 가정)
    now = int(time.time())
    now_ms = now * 1000
    
    # 오래된 시간 (35일 전, 정각 1시간 배수로 맞추어 정밀 비교)
    old_time = (now - (35 * 24 * 3600)) // 3600 * 3600
    old_time_ms = old_time * 1000
    hour_bucket_sec = old_time
    
    # 1. 테스트용 분봉 데이터 주입 (35일 전 데이터)
    test_candles = [
        # exchange_id, symbol, interval, timestamp, open, high, low, close, volume
        ('upbit', 'BTC', 60, hour_bucket_sec, 50000.0, 51000.0, 49000.0, 50500.0, 1.5),
        ('upbit', 'BTC', 60, hour_bucket_sec + 60, 50500.0, 52000.0, 50000.0, 51500.0, 2.0),
        # 보존해야 할 최근 캔들 (현재 시점)
        ('upbit', 'BTC', 60, now, 60000.0, 61000.0, 59000.0, 60500.0, 1.0)
    ]
    
    async with get_db_conn(db_path) as db:
        for c in test_candles:
            await db.execute("""
                INSERT INTO candles (exchange_id, symbol, interval, timestamp, open, high, low, close, volume)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, c)
        
        # 테스트용 틱 데이터 주입
        # 1) 4일 전 오래된 틱 (72시간 경과) -> 삭제 대상
        old_trade_time_ms = (now - (4 * 24 * 3600)) * 1000
        await db.execute("""
            INSERT INTO trades (exchange_id, market, symbol, trade_price, trade_volume, ask_bid, trade_timestamp)
            VALUES ('upbit', 'KRW', 'BTC', 49500.0, 0.5, 'ASK', ?)
        """, (old_trade_time_ms,))
        
        # 2) 최근 틱 -> 보존 대상
        await db.execute("""
            INSERT INTO trades (exchange_id, market, symbol, trade_price, trade_volume, ask_bid, trade_timestamp)
            VALUES ('upbit', 'KRW', 'BTC', 60500.0, 0.2, 'BID', ?)
        """, (now_ms,))
        
        await db.commit()

    # 2. 첫 번째 다운샘플링 및 청소 실행
    # 30일 경과 임계시각 = now - 30 days
    cutoff_ts = now - (30 * 24 * 3600)
    
    # 2.1. 다운샘플링 실행
    ds_count1 = await service._downsample_old_candles(cutoff_ts)
    assert ds_count1 == 1, f"Expected 1 downsampled hour candle, got {ds_count1}"
    
    # 다운샘플링된 1시간봉 검증
    async with get_db_conn(db_path) as db:
        async with db.execute(
            "SELECT * FROM candles WHERE interval = 3600 AND timestamp = ?", (hour_bucket_sec,)
        ) as cur:
            row = await cur.fetchone()
            assert row is not None, "다운샘플링된 1시간봉이 생성되어야 합니다."
            # open은 최초 캔들의 open인 50000.0 이어야 함
            assert row['open'] == 50000.0
            # close는 최후 캔들의 close인 51500.0 이어야 함
            assert row['close'] == 51500.0
            # high는 최대인 52000.0
            assert row['high'] == 52000.0
            # low는 최소인 49000.0
            assert row['low'] == 49000.0
            # volume은 합산인 3.5
            assert row['volume'] == 3.5
            
    # 3. 멱등성 검증 (동일 다운샘플링 2회차 실행)
    # 동일한 다운샘플링 쿼리를 2회차 실행해도 중복된 행이 생기거나 에러가 발생하지 않아야 함
    ds_count2 = await service._downsample_old_candles(cutoff_ts)
    # REPLACE 되므로 rowcount는 여전히 1 (또는 SQLite에 따라 1이나 업데이트 반영)
    assert ds_count2 == 1 or ds_count2 == 0, f"Idempotent run should not fail. Got: {ds_count2}"
    
    # 중복 저장 여부 검증
    async with get_db_conn(db_path) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM candles WHERE interval = 3600 AND timestamp = ?", (hour_bucket_sec,)
        ) as cur:
            cnt = (await cur.fetchone())[0]
            assert cnt == 1, "멱등성이 깨져 중복 생성되었습니다."

    # 4. 삭제(TTL Cleanup) 실행 검증
    # 4.1. trades 삭제
    trades_cutoff = now - (72 * 3600)
    del_trades = await service._chunked_delete_trades(trades_cutoff)
    assert del_trades == 1, "4일 전의 오래된 trades 1건이 삭제되어야 합니다."
    
    # 4.2. candles 삭제 (오래된 1분봉만 지우고 상위 3600인 캔들은 유지)
    del_candles = await service._chunked_delete_candles(cutoff_ts)
    assert del_candles == 2, "35일 전의 1분봉 2건이 삭제되어야 합니다."
    
    # 최종 생존 캔들/틱 확인
    async with get_db_conn(db_path) as db:
        # 최근 1분봉(현재 시각)과 다운샘플링된 1시간봉(3600) 총 2개 존재해야 함
        async with db.execute("SELECT COUNT(*) FROM candles") as cur:
            total_c = (await cur.fetchone())[0]
            assert total_c == 2, f"Expected 2 candles left (1 recent, 1 downsampled), got {total_c}"
            
        # 최근 틱 1건만 존재해야 함
        async with db.execute("SELECT COUNT(*) FROM trades") as cur:
            total_t = (await cur.fetchone())[0]
            assert total_t == 1, f"Expected 1 trade left, got {total_t}"

    # 5. Safety Guard 검증 (PENDING 평가 대상 보호)
    # 5.1. PENDING 평가 추가
    # 평가 대상 기간의 시작점: now - 35 days (old_time 근처)
    # 이 평가가 PENDING 이라면, 35일 전의 candles는 clean_old_candles 시 삭제 대상이어야 하지만 보호되어야 함.
    async with get_db_conn(db_path) as db:
        # 외래 키 제약을 위해 strategy_proposals 에 mock 데이터 선제 주입
        await db.execute("INSERT OR IGNORE INTO portfolios (id, name, type) VALUES (999, 'sim_port_rehearsal', 'simulation')")
        await db.execute("""
            INSERT INTO strategy_proposals (id, version, portfolio_id, strategy_id, status, outcome)
            VALUES (999, 1, 999, 'BTC', 'PENDING', 'RUNNING')
        """)
        # PENDING 평가를 하나 강제로 insert
        await db.execute("""
            INSERT INTO proposal_evaluations (
                proposal_id, horizon_name, due_at, evaluation_status, horizon_type, horizon_value
            ) VALUES (999, '2h', ?, 'PENDING', 'elapsed', 7200)
        """, (old_time + 7200,))
        # 다시 35일 전 분봉 복구 주입
        for c in test_candles[:2]:
            await db.execute("""
                INSERT OR REPLACE INTO candles (exchange_id, symbol, interval, timestamp, open, high, low, close, volume)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, c)
        await db.commit()

    # Safety Guard 활성화 하에 전체 cleanup 실행 시도
    # PENDING 평가의 최소 시작 시각 = (old_time + 7200) - 7200 = old_time
    # 따라서 cleanup_cutoff는 old_time으로 보정되어, 35일 전 분봉은 삭제되지 않고 보호되어야 함.
    await service._execute_cleanup()
    
    async with get_db_conn(db_path) as db:
        # 35일 전 분봉 2건 + 1시간봉 1건이 삭제되지 않고 그대로 존재해야 함!
        async with db.execute("SELECT COUNT(*) FROM candles WHERE timestamp BETWEEN ? AND ?", (hour_bucket_sec, hour_bucket_sec + 60)) as cur:
            cnt_protected = (await cur.fetchone())[0]
            # 1분봉 2개 + 1시간봉 1개 = 총 3개 존재해야 함
            assert cnt_protected == 3, f"Expected 3 candles protected, got {cnt_protected}"

    # 6. 임시 파일 정리
    if os.path.exists(db_path):
        os.remove(db_path)
    if os.path.exists(db_path + "-wal"):
        os.remove(db_path + "-wal")
    if os.path.exists(db_path + "-shm"):
        os.remove(db_path + "-shm")
        
    print("[PASS] 모든 Market Cleanup & Idempotency 테스트 통과!")
