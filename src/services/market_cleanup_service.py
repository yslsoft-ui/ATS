# -*- coding: utf-8 -*-

import asyncio
import time
import json
from typing import Dict, Any, List
from src.engine.utils.telemetry import get_logger
from src.engine.daemon_supervisor import DaemonService
from src.database.connection import get_db_conn

logger = get_logger("market_cleanup_service")

class MarketDataCleanupService(DaemonService):
    """
    시장 데이터(ticks/candles)의 생명주기를 관리하고 불필요한 데이터를 정리하는 서비스입니다.
    데이터 삭제 시 SQLite 락 경합을 방지하기 위해 청크 단위로 나누어 지우고 sleep하며,
    아직 평가되지 않은 제안들의 평가 데이터(틱/캔들)를 보호하는 안전 가드를 제공합니다.
    """
    def __init__(self, config_manager: Any, event_bus: Any):
        self.config = config_manager
        self.event_bus = event_bus
        self.db_path = self.config.get('system.db_path', 'data/backtest.db')
        
        # 보존 기간 (TTL) 설정 로드
        retention = self.config.get('system.retention', {})
        self.trades_hours = retention.get('trades_hours', 72)     # 기본 3일
        self.candles_days = retention.get('candles_days', 30)     # 기본 30일
        
        self.cleanup_interval = self.config.get('system.cleanup_interval_seconds', 3600) # 기본 1시간
        self._tasks: List[asyncio.Task] = []
        self._is_running = False

    async def start(self):
        logger.info("[MarketDataCleanupService] 서비스 기동 중...")
        self._is_running = True
        self._tasks.append(asyncio.create_task(self._cleanup_loop()))
        logger.info("[MarketDataCleanupService] 서비스 기동 완료.")

    async def stop(self):
        logger.info("[MarketDataCleanupService] 서비스 중지 중...")
        self._is_running = False
        for task in self._tasks:
            if not task.done():
                task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
            self._tasks.clear()
        logger.info("[MarketDataCleanupService] 서비스 중지 완료.")

    async def _cleanup_loop(self):
        logger.info("[MarketDataCleanupService] 데이터 정리 스케줄러 루프 시작")
        # 기동 직후 첫 실행
        await asyncio.sleep(5)
        while self._is_running:
            try:
                await self._execute_cleanup()
            except Exception as e:
                logger.error(f"[MarketDataCleanupService] 데이터 정리 중 에러 발생: {e}")
                
            await asyncio.sleep(self.cleanup_interval)

    async def _execute_cleanup(self):
        logger.info("[MarketDataCleanupService] 데이터 생명주기 정리 실행")
        
        # 1. 안전 가드 임계치 획득: PENDING인 평가들 중 가장 과거의 평가 시작 시점
        # start_time = due_at - horizon_value
        min_pending_start = None
        try:
            async with get_db_conn(self.db_path) as db:
                async with db.execute(
                    "SELECT MIN(due_at - horizon_value) FROM proposal_evaluations WHERE evaluation_status = 'PENDING'"
                ) as cur:
                    row = await cur.fetchone()
                    if row and row[0] is not None:
                        min_pending_start = int(row[0])
        except Exception as e:
            logger.error(f"[MarketDataCleanupService] PENDING 평가 임계치 획득 실패: {e}")
            
        now = int(time.time())
        
        # 2. trades TTL 임계치 계산
        trades_cutoff = now - (self.trades_hours * 3600)
        if min_pending_start is not None:
            # 안전 가드: PENDING 평가를 위한 데이터는 지우지 않고 보호
            trades_cutoff = min(trades_cutoff, min_pending_start)
            logger.info(f"[MarketDataCleanupService] [Safety Guard] trades 삭제 임계시각 보정 (PENDING 평가 최소 시각 보호: {min_pending_start})")

        # 3. candles TTL 임계치 계산
        candles_cutoff = now - (self.candles_days * 24 * 3600)
        if min_pending_start is not None:
            candles_cutoff = min(candles_cutoff, min_pending_start)
            logger.info(f"[MarketDataCleanupService] [Safety Guard] candles 삭제 임계시각 보정")

        # 4. 오래된 캔들 다운샘플링 (1시간봉 interval=3600으로 취합 보존)
        downsample_count = 0
        try:
            downsample_count = await self._downsample_old_candles(candles_cutoff)
        except Exception as e:
            logger.error(f"[MarketDataCleanupService] 오래된 캔들 다운샘플링 중 예외 발생: {e}")

        # 5. trades 청소 (청크 단위 50,000행씩 삭제 및 양보)
        trades_deleted = 0
        try:
            trades_deleted = await self._chunked_delete_trades(trades_cutoff)
        except Exception as e:
            logger.error(f"[MarketDataCleanupService] trades 틱 데이터 정리 중 예외 발생: {e}")

        # 6. candles 청소 (세밀 분봉 정리, 상위 주기는 보존)
        candles_deleted = 0
        try:
            candles_deleted = await self._chunked_delete_candles(candles_cutoff)
        except Exception as e:
            logger.error(f"[MarketDataCleanupService] candles 분봉 데이터 정리 중 예외 발생: {e}")

        # 7. 정리 요약 이벤트 적재 (MARKET_DATA_CLEANUP_SUMMARY)
        summary_msg = json.dumps({
            "timestamp": now,
            "trades_deleted": trades_deleted,
            "candles_deleted": candles_deleted,
            "candles_downsampled": downsample_count,
            "trades_cutoff": trades_cutoff,
            "candles_cutoff": candles_cutoff
        })
        try:
            async with get_db_conn(self.db_path) as db:
                await db.execute(
                    "INSERT INTO system_events (event_type, target, message, timestamp) "
                    "VALUES ('MARKET_DATA_CLEANUP_SUMMARY', 'market_cleanup', ?, ?)",
                    (summary_msg, now * 1000)
                )
                await db.commit()
            logger.info(f"[MarketDataCleanupService] 정리 완료 및 요약 이벤트 적재 완료: {summary_msg}")
        except Exception as e:
            logger.error(f"[MarketDataCleanupService] 정리 감사 로그 적재 실패: {e}")

    async def _chunked_delete_trades(self, cutoff_ts: int) -> int:
        total_deleted = 0
        cutoff_ms = cutoff_ts * 1000
        logger.info(f"[MarketDataCleanupService] trades 정리 시작 (기준 시각: {cutoff_ts})")
        
        while self._is_running:
            async with get_db_conn(self.db_path) as db:
                # 50,000건씩 청크 단위 삭제
                cursor = await db.execute(
                    "DELETE FROM trades WHERE id IN (SELECT id FROM trades WHERE trade_timestamp < ? LIMIT 50000)",
                    (cutoff_ms,)
                )
                deleted = cursor.rowcount
                await db.commit()
                
            total_deleted += deleted
            if deleted < 50000:
                break
            
            # SQLite 락 양보 및 CPU 부담 감소를 위한 휴지기
            await asyncio.sleep(0.1)
            
        logger.info(f"[MarketDataCleanupService] trades 정리 완료: 총 {total_deleted:,} rows 삭제됨.")
        return total_deleted

    async def _chunked_delete_candles(self, cutoff_ts: int) -> int:
        total_deleted = 0
        cutoff_ms = cutoff_ts * 1000
        logger.info(f"[MarketDataCleanupService] candles 정리 시작 (기준 시각: {cutoff_ts})")
        
        while self._is_running:
            async with get_db_conn(self.db_path) as db:
                # interval이 3600(1시간) 미만인 세밀한 단기 분봉들만 삭제하고, 상위 주기는 보존
                cursor = await db.execute(
                    "DELETE FROM candles WHERE rowid IN ("
                    "  SELECT rowid FROM candles "
                    "  WHERE timestamp < ? AND interval < 3600 LIMIT 50000"
                    ")",
                    (cutoff_ms,)
                )
                deleted = cursor.rowcount
                await db.commit()
                
            total_deleted += deleted
            if deleted < 50000:
                break
            
            await asyncio.sleep(0.1)
            
        logger.info(f"[MarketDataCleanupService] candles 정리 완료: 총 {total_deleted:,} rows 삭제됨.")
        return total_deleted

    async def _downsample_old_candles(self, cutoff_ts: int) -> int:
        cutoff_ms = cutoff_ts * 1000
        logger.info(f"[MarketDataCleanupService] 1시간봉 단위 다운샘플링 취합 시작 (대상: timestamp < {cutoff_ts})")
        
        async with get_db_conn(self.db_path) as db:
            # interval이 3600 미만인 분봉 데이터들을 대상으로 1시간봉 취합하여 INSERT OR REPLACE
            # 각 hour_bucket 내에서 최초의 open 가격과 최후의 close 가격을 정확히 쿼리하기 위해 서브쿼리 사용
            cursor = await db.execute("""
                INSERT OR REPLACE INTO candles (exchange, symbol, interval, timestamp, open, high, low, close, volume)
                SELECT 
                    t.exchange,
                    t.symbol,
                    3600 as interval,
                    t.hour_bucket,
                    (SELECT c.open FROM candles c 
                     WHERE c.exchange = t.exchange AND c.symbol = t.symbol AND c.interval < 3600 AND c.timestamp = t.min_ts LIMIT 1) as open,
                    t.high,
                    t.low,
                    (SELECT c.close FROM candles c 
                     WHERE c.exchange = t.exchange AND c.symbol = t.symbol AND c.interval < 3600 AND c.timestamp = t.max_ts LIMIT 1) as close,
                    t.volume
                FROM (
                    SELECT 
                        exchange,
                        symbol,
                        (timestamp / 3600000) * 3600000 as hour_bucket,
                        MIN(timestamp) as min_ts,
                        MAX(timestamp) as max_ts,
                        MAX(high) as high,
                        MIN(low) as low,
                        SUM(volume) as volume
                    FROM candles
                    WHERE timestamp < ? AND interval < 3600
                    GROUP BY exchange, symbol, hour_bucket
                ) t
            """, (cutoff_ms,))
            
            downsampled = cursor.rowcount
            await db.commit()
            
        logger.info(f"[MarketDataCleanupService] 다운샘플링 완료: {downsampled} 건의 1시간봉 캔들이 생성/갱신되었습니다.")
        return downsampled

    async def handle_config_change(self, new_config: dict):
        logger.info("[MarketDataCleanupService] 설정 변경 감지")
        system_cfg = new_config.get('system', {})
        retention = system_cfg.get('retention', {})
        self.trades_hours = retention.get('trades_hours', self.trades_hours)
        self.candles_days = retention.get('candles_days', self.candles_days)
        self.cleanup_interval = system_cfg.get('cleanup_interval_seconds', self.cleanup_interval)

    async def handle_control_message(self, topic: str, data: dict) -> bool:
        return False

    def get_status_payloads(self) -> List[tuple[str, dict]]:
        return [
            ("signal_data", {
                "type": "market_cleanup_status",
                "is_running": self._is_running,
                "cleanup_interval": self.cleanup_interval,
                "trades_hours": self.trades_hours,
                "candles_days": self.candles_days
            })
        ]


