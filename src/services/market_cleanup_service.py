# -*- coding: utf-8 -*-

import asyncio
import time
import json
import os
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

        # 상태 제어 및 동시성 락 변수 추가
        self.cleanup_state = "ACTIVE"
        self._scheduler_wake_event = asyncio.Event()
        self._cleanup_lock = asyncio.Lock()

        # 텔레메트리 전송을 위한 메모리 캐시 변수
        self.last_cleanup_time = 0
        self.next_cleanup_time = 0
        self.last_cleanup_duration_ms = 0
        self.last_cleanup_summary = {
            "trades_deleted": 0,
            "candles_deleted": 0,
            "candles_downsampled": 0
        }
        self.last_error = None
        # 초기 기본 컷오프값 예상 설정 (Safety Guard 적용 전 기본 설정 기준)
        now = int(time.time())
        self.last_trades_cutoff = now - (self.trades_hours * 3600)
        self.last_candles_cutoff = now - (self.candles_days * 24 * 3600)
        
        # 다음 자동 삭제 대상 예상 수량 캐시 변수
        self.next_cleanup_target_trades = 0
        self.next_cleanup_target_candles = 0
        self.next_cleanup_target_downsample = 0
        self.next_cleanup_target_trades_cutoff = 0
        self.next_cleanup_target_candles_cutoff = 0

        # 데몬 기동 메타데이터 변수
        self.pid = os.getpid()
        self.start_time = int(time.time())

    async def start(self):
        logger.info("[MarketDataCleanupService] 서비스 기동 중...")
        self._is_running = True
        self._tasks.append(asyncio.create_task(self._cleanup_loop()))
        
        async def init_targets():
            async with self._cleanup_lock:
                await self._update_cleanup_targets()
        asyncio.create_task(init_targets())
        
        logger.info("[MarketDataCleanupService] 서비스 기동 완료.")

    async def stop(self):
        logger.info("[MarketDataCleanupService] 서비스 중지 중...")
        self._is_running = False
        self._scheduler_wake_event.set() # 대기 루프 탈출 유도
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
                if self.cleanup_state == "PAUSED":
                    logger.info("[MarketDataCleanupService] 자동 정리 일시 정지 상태(PAUSED). 활성화 대기 중...")
                    await self._scheduler_wake_event.wait()
                    continue

                # 락을 획득하고 정리를 실행 (ACTIVE 상태)
                async with self._cleanup_lock:
                    self.next_cleanup_time = int(time.time()) + self.cleanup_interval
                    await self._execute_cleanup()

            except Exception as e:
                logger.error(f"[MarketDataCleanupService] 데이터 정리 중 에러 발생: {e}")
                self.cleanup_state = "ERROR"
                self.last_error = str(e)
                
            self._scheduler_wake_event.clear()
            try:
                await asyncio.wait_for(self._scheduler_wake_event.wait(), timeout=self.cleanup_interval)
            except asyncio.TimeoutError:
                pass

    async def _execute_cleanup(self):
        logger.info("[MarketDataCleanupService] 데이터 생명주기 정리 실행")
        start_time = time.time()
        
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

        # 7. 메모리 요약 캐시 업데이트
        end_time = time.time()
        self.last_cleanup_time = now
        self.last_cleanup_duration_ms = int((end_time - start_time) * 1000)
        self.last_cleanup_summary = {
            "trades_deleted": trades_deleted,
            "candles_deleted": candles_deleted,
            "candles_downsampled": downsample_count
        }
        self.last_error = None
        self.last_trades_cutoff = trades_cutoff
        self.last_candles_cutoff = candles_cutoff

        # 8. 정리 요약 이벤트 적재 (MARKET_DATA_CLEANUP_SUMMARY)
        summary_msg = json.dumps({
            "timestamp": now,
            "trades_deleted": trades_deleted,
            "candles_deleted": candles_deleted,
            "candles_downsampled": downsample_count,
            "trades_cutoff": trades_cutoff,
            "candles_cutoff": candles_cutoff,
            "type": "auto"
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

        # 9. 다음 정리 대상 예측 수량 및 기준시각 업데이트
        await self._update_cleanup_targets()

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
        logger.info(f"[MarketDataCleanupService] candles 정리 시작 (기준 시각: {cutoff_ts})")
        
        while self._is_running:
            async with get_db_conn(self.db_path) as db:
                # interval이 3600(1시간) 미만인 세밀한 단기 분봉들만 삭제하고, 상위 주기는 보존
                cursor = await db.execute(
                    "DELETE FROM candles WHERE rowid IN ("
                    "  SELECT rowid FROM candles "
                    "  WHERE timestamp < ? AND interval < 3600 LIMIT 50000"
                    ")",
                    (cutoff_ts,)
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
                        (timestamp / 3600) * 3600 as hour_bucket,
                        MIN(timestamp) as min_ts,
                        MAX(timestamp) as max_ts,
                        MAX(high) as high,
                        MIN(low) as low,
                        SUM(volume) as volume
                    FROM candles
                    WHERE timestamp < ? AND interval < 3600
                    GROUP BY exchange, symbol, hour_bucket
                ) t
            """, (cutoff_ts,))
            
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
        msg_type = data.get("type")
        command_id = data.get("command_id")

        if msg_type == "cleanup_run_once":
            # 중복 실행 방지 뮤텍스 락 확인
            if self._cleanup_lock.locked():
                logger.warning(f"[MarketDataCleanupService] 클린업 작업 중복 요청 기각 (command_id: {command_id})")
                if self.event_bus and command_id:
                    await self.event_bus.publish("cleanup_signal", {
                        "type": "cleanup_command_result",
                        "command_id": command_id,
                        "success": False,
                        "error": "다른 클린업 작업이 진행 중입니다."
                    })
                return True

            date = data.get("date")
            limit = data.get("limit", 20000)
            asyncio.create_task(self._execute_cleanup_run_once(command_id, date, limit))
            return True

        elif msg_type == "cleanup_preview":
            # 중복 실행 방지 뮤텍스 락 확인
            if self._cleanup_lock.locked():
                logger.warning(f"[MarketDataCleanupService] 클린업 작업 중복 요청 기각 (command_id: {command_id})")
                if self.event_bus and command_id:
                    await self.event_bus.publish("cleanup_signal", {
                        "type": "cleanup_command_result",
                        "command_id": command_id,
                        "success": False,
                        "error": "다른 클린업 작업이 진행 중입니다."
                    })
                return True

            date = data.get("date")
            asyncio.create_task(self._execute_cleanup_preview(command_id, date))
            return True

        return False

    async def _execute_cleanup_preview(self, command_id: str, date_str: str):
        # 락 획득 후 preview 연산 수행 (틱 데이터 한정)
        async with self._cleanup_lock:
            try:
                import datetime
                dt = datetime.datetime.fromisoformat(date_str)
                ts = int(dt.timestamp() * 1000)
                
                async with get_db_conn(self.db_path) as db:
                    async with db.execute("SELECT COUNT(*) FROM trades WHERE trade_timestamp < ?", (ts,)) as cursor:
                        trades_count = (await cursor.fetchone())[0]
                        
                if self.event_bus:
                    await self.event_bus.publish("cleanup_signal", {
                        "type": "cleanup_command_result",
                        "command_id": command_id,
                        "success": True,
                        "data": {
                            "trades_count": trades_count,
                            "date": date_str
                        }
                    })
            except Exception as e:
                logger.error(f"[MarketDataCleanupService] Preview 실행 실패: {e}")
                if self.event_bus:
                    await self.event_bus.publish("cleanup_signal", {
                        "type": "cleanup_command_result",
                        "command_id": command_id,
                        "success": False,
                        "error": f"조회 실패: {str(e)}"
                    })

    async def _execute_cleanup_run_once(self, command_id: str, date_str: str, limit: int):
        # 락 획득 후 RUNNING_ONCE 상태로 삭제 작업 진행
        prev_state = self.cleanup_state
        self.cleanup_state = "RUNNING_ONCE"
        self.last_error = None
        
        async with self._cleanup_lock:
            try:
                import datetime
                dt = datetime.datetime.fromisoformat(date_str)
                ts = int(dt.timestamp() * 1000)
                ts_sec = ts // 1000
                
                start_time = time.time()
                
                trades_deleted = 0
                candles_deleted = 0
                
                async with get_db_conn(self.db_path) as db:
                    # 1. trades 테이블 분할 삭제
                    cursor_trades = await db.execute("""
                        DELETE FROM trades 
                        WHERE rowid IN (
                            SELECT rowid FROM trades 
                            WHERE trade_timestamp < ? 
                            LIMIT ?
                        )
                    """, (ts, limit))
                    trades_deleted = cursor_trades.rowcount
                    await db.commit()
                
                end_time = time.time()
                self.last_cleanup_time = int(time.time())
                self.last_cleanup_duration_ms = int((end_time - start_time) * 1000)
                self.last_cleanup_summary = {
                    "trades_deleted": trades_deleted,
                    "candles_deleted": 0,
                    "candles_downsampled": 0
                }
                self.last_trades_cutoff = ts // 1000
                
                # 감사 로그 적재
                async with get_db_conn(self.db_path) as db:
                    summary_msg = json.dumps({
                        "timestamp": self.last_cleanup_time,
                        "trades_deleted": trades_deleted,
                        "candles_deleted": 0,
                        "candles_downsampled": 0,
                        "command_id": command_id,
                        "type": "manual"
                    })
                    await db.execute(
                        "INSERT INTO system_events (event_type, target, message, timestamp) "
                        "VALUES ('MARKET_DATA_CLEANUP_SUMMARY', 'market_cleanup', ?, ?)",
                        (summary_msg, self.last_cleanup_time * 1000)
                    )
                    await db.commit()
                
                # 수동 삭제 완료 후 다음 자동 삭제 대상 수량 및 기준시각 업데이트
                await self._update_cleanup_targets()
                
                if self.event_bus:
                    await self.event_bus.publish("cleanup_signal", {
                        "type": "cleanup_command_result",
                        "command_id": command_id,
                        "success": True,
                        "message": f"성공적으로 정리되었습니다. (체결: {trades_deleted}건)",
                        "data": {
                            "trades_deleted": trades_deleted,
                            "candles_deleted": 0,
                            "total_deleted": trades_deleted
                        }
                    })
            except Exception as e:
                logger.error(f"[MarketDataCleanupService] 수동 클린업 실행 실패: {e}")
                self.cleanup_state = "ERROR"
                self.last_error = str(e)
                if self.event_bus:
                    await self.event_bus.publish("cleanup_signal", {
                        "type": "cleanup_command_result",
                        "command_id": command_id,
                        "success": False,
                        "error": f"정리 실패: {str(e)}"
                    })
            finally:
                if self.cleanup_state == "RUNNING_ONCE":
                    self.cleanup_state = prev_state

    def get_status_payloads(self) -> List[tuple[str, dict]]:
        return [
            ("cleanup_signal", {
                "type": "market_cleanup_status",
                "timestamp": int(time.time()),
                "cleanup_state": self.cleanup_state,
                "is_running": self._is_running,
                "cleanup_interval": self.cleanup_interval,
                "trades_hours": self.trades_hours,
                "candles_days": self.candles_days,
                "last_cleanup_time": self.last_cleanup_time,
                "next_cleanup_time": self.next_cleanup_time,
                "last_cleanup_duration_ms": self.last_cleanup_duration_ms,
                "last_cleanup_summary": self.last_cleanup_summary,
                "last_error": self.last_error,
                "last_trades_cutoff": self.last_trades_cutoff,
                "last_candles_cutoff": self.last_candles_cutoff,
                "next_cleanup_target_trades": self.next_cleanup_target_trades,
                "next_cleanup_target_candles": self.next_cleanup_target_candles,
                "next_cleanup_target_downsample": self.next_cleanup_target_downsample,
                "next_cleanup_target_trades_cutoff": self.next_cleanup_target_trades_cutoff,
                "next_cleanup_target_candles_cutoff": self.next_cleanup_target_candles_cutoff,
                "pid": self.pid,
                "start_time": self.start_time
            })
        ]

    async def _update_cleanup_targets(self):
        """다음 자동 삭제 대상 예상 수량 및 기준시각을 갱신합니다.
        락 획득 상태에서 호출되어야 하며, 데이터베이스를 조회해 최신 예측값을 메모리에 캐싱합니다.
        """
        try:
            # 다음 삭제 예정 시각 기준(보통 현재 시각 + 1시간 뒤)으로 예상 컷오프 계산
            base_time = self.next_cleanup_time if (self.next_cleanup_time and self.next_cleanup_time > 0) else int(time.time()) + self.cleanup_interval
            trades_cutoff = base_time - (self.trades_hours * 3600)
            candles_cutoff = base_time - (self.candles_days * 24 * 3600)
            
            min_pending_start = None
            async with get_db_conn(self.db_path) as db:
                async with db.execute(
                    "SELECT MIN(due_at - horizon_value) FROM proposal_evaluations WHERE evaluation_status = 'PENDING'"
                ) as cur:
                    row = await cur.fetchone()
                    if row and row[0] is not None:
                        min_pending_start = int(row[0])
                        
            if min_pending_start is not None:
                trades_cutoff = min(trades_cutoff, min_pending_start)
                candles_cutoff = min(candles_cutoff, min_pending_start)
                
            trades_cutoff_ms = trades_cutoff * 1000
            
            async with get_db_conn(self.db_path) as db:
                # 1. 삭제 예정 Trades (틱) 수 조회
                async with db.execute("SELECT COUNT(*) FROM trades WHERE trade_timestamp < ?", (trades_cutoff_ms,)) as cursor:
                    self.next_cleanup_target_trades = (await cursor.fetchone())[0]
                    
                # 2. 삭제 예정 Candles (분봉) 수 조회
                async with db.execute("SELECT COUNT(*) FROM candles WHERE timestamp < ? AND interval < 3600", (candles_cutoff,)) as cursor:
                    self.next_cleanup_target_candles = (await cursor.fetchone())[0]
                    
                # 3. 다운샘플링 예정 시간봉 캔들 수 조회
                async with db.execute("""
                    SELECT COUNT(*) FROM (
                        SELECT 1 FROM candles 
                        WHERE timestamp < ? AND interval < 3600
                        GROUP BY exchange, symbol, (timestamp / 3600) * 3600
                    )
                """, (candles_cutoff,)) as cursor:
                    self.next_cleanup_target_downsample = (await cursor.fetchone())[0]
            
            # 컷오프 시각 기록
            self.next_cleanup_target_trades_cutoff = trades_cutoff
            self.next_cleanup_target_candles_cutoff = candles_cutoff
            
            logger.info(f"[MarketDataCleanupService] 다음 삭제 대상 예상 수량 갱신 성공: trades={self.next_cleanup_target_trades}, candles={self.next_cleanup_target_candles}")
        except Exception as e:
            logger.error(f"[MarketDataCleanupService] 삭제 대상 예상 수량 조회 실패: {e}")


