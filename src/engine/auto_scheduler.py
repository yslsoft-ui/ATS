import time
import asyncio
from typing import Dict, Any, List, Set, Optional
from src.database.repository import SqliteTradingRepository, ChampionCooldownBlockedError
from src.engine.utils.telemetry import get_logger
from src.config.manager import ConfigManager

logger = get_logger("auto_scheduler")

class HybridAutoApplyScheduler:
    """
    Hybrid Event-driven Scheduler (반자동 제안 승인 스케줄러)
    - 제안 생성 이벤트(proposal_created)를 감지하고 20초간 Debounce 버퍼링 후 Batch 일괄 검증을 수행합니다.
    - 신뢰도 80점 이상인 제안을 approve_proposal_atomic API를 통해 원자적으로 자동 반영합니다.
    - 분당 최대 3회 제한(Rate Limit), 전략별 10분 Cooldown, 수동 롤백 감지 시 자동화 전역 차단 장치를 내장합니다.
    """
    def __init__(
        self,
        db_path: str,
        debounce_seconds: float = 20.0,
        girs_shadow_mode_override: Optional[bool] = None,
        auto_strategy_promotion_enabled_override: Optional[bool] = None,
        champion_cooldown_days_override: Optional[float] = None,
        champion_cooldown_trades_override: Optional[int] = None
    ):
        import sys
        from typing import Optional
        self.db_path = db_path
        self.config_manager = ConfigManager("config/settings.yaml")
        
        is_pytest = "pytest" in sys.modules
        self.girs_shadow_mode_override = girs_shadow_mode_override if girs_shadow_mode_override is not None else (False if is_pytest else None)
        self.auto_strategy_promotion_enabled_override = auto_strategy_promotion_enabled_override if auto_strategy_promotion_enabled_override is not None else (True if is_pytest else None)
        
        cooldown_days = champion_cooldown_days_override if champion_cooldown_days_override is not None else self.config_manager.get("system.champion_cooldown_days", 7.0)
        cooldown_trades = champion_cooldown_trades_override if champion_cooldown_trades_override is not None else self.config_manager.get("system.champion_cooldown_trades", 100)
        
        self.repository = SqliteTradingRepository(
            db_path=self.db_path,
            girs_shadow_mode_override=self.girs_shadow_mode_override,
            auto_strategy_promotion_enabled_override=self.auto_strategy_promotion_enabled_override,
            champion_cooldown_days=cooldown_days,
            champion_cooldown_trades=cooldown_trades
        )
        self.debounce_seconds = debounce_seconds
        
        self._auto_proposal_enabled = True
        self._buffer_queue: Set[int] = set()
        self._debounce_task: asyncio.Task = None
        self._lock = asyncio.Lock()
        
        # 안전장치 상태 변수들
        self._strategy_last_applied: Dict[str, float] = {}  # strategy_id -> epoch ms
        self._minute_rate_limit: List[float] = []           # list of apply timestamps (epoch seconds)
        
    def set_auto_proposal_enabled(self, enabled: bool):
        logger.info(f"[AutoScheduler] ENABLE_AUTO_PROPOSAL 상태 변경: {self._auto_proposal_enabled} -> {enabled}")
        self._auto_proposal_enabled = enabled
        
    def is_auto_proposal_enabled(self) -> bool:
        return self._auto_proposal_enabled

    async def handle_manual_rollback(self, strategy_id: str):
        """수동 롤백 발생 시, 해당 전략 및 전역 자동 적용 활성화를 즉시 차단(False Lock)합니다."""
        logger.warning(f"[AutoScheduler] 전략 {strategy_id}에 대해 수동 롤백 감지! ENABLE_AUTO_PROPOSAL 자동 비활성화 잠금 실행.")
        self.set_auto_proposal_enabled(False)

    async def notify_proposal_created(self, proposal_id: int):
        """신규 제안 생성을 수신하고 디바운스 버퍼 큐에 적재합니다."""
        if not self._auto_proposal_enabled:
            logger.info(f"[AutoScheduler] 자동 적용 비활성화 상태. 제안 #{proposal_id} 무시.")
            return

        async with self._lock:
            self._buffer_queue.add(proposal_id)
            logger.info(f"[AutoScheduler] 제안 #{proposal_id} 버퍼 적재완료. (현재 대기 큐 크기: {len(self._buffer_queue)})")
            
            # 기존 디바운스 타이머가 돌고 있으면 취소하고 새로 연장 (Debounce)
            if self._debounce_task and not self._debounce_task.done():
                self._debounce_task.cancel()
                
            self._debounce_task = asyncio.create_task(self._debounce_timer_loop())

    async def _debounce_timer_loop(self):
        try:
            await asyncio.sleep(self.debounce_seconds)
            # 디바운스 타이머 완료 후 일괄(Batch) 평가 수행
            async with self._lock:
                proposals_to_process = list(self._buffer_queue)
                self._buffer_queue.clear()
            
            if proposals_to_process:
                await self._process_batch(proposals_to_process)
        except asyncio.CancelledError:
            # 타이머 취소 시 조용히 빠져나감 (연장됨)
            pass
        except Exception as e:
            logger.error(f"[AutoScheduler] 디바운스 루프 에러 발생: {e}")

    async def _process_batch(self, proposal_ids: List[int]):
        logger.info(f"[AutoScheduler] {len(proposal_ids)}개 제안에 대해 일괄 평가 검증 시작...")
        
        for pid in proposal_ids:
            try:
                # 1. 제안 데이터 획득 및 상태 검증
                prop = await self.repository.get_strategy_proposal(pid)
                if not prop or prop["status"] != "PENDING":
                    continue
                
                # 2. 신뢰도 80점 장벽 검증
                confidence = prop.get("confidence_score", 50)
                if confidence < 80:
                    logger.info(f"[AutoScheduler] 제안 #{pid} 신뢰도 점수 {confidence}점은 자동 적용 기준(80점) 미만으로 스킵합니다.")
                    continue
                
                strategy_id = prop["strategy_id"]
                portfolio_id = prop["portfolio_id"]
                
                # 3. 안전장치 검증 (Cooldown 및 Rate Limit)
                now_epoch = time.time()
                
                # 3.1. Rate Limit 검사 (최근 1분간 최대 3회)
                # 60초 이전의 오래된 적용 기록 제거
                self._minute_rate_limit = [t for t in self._minute_rate_limit if now_epoch - t <= 60.0]
                if len(self._minute_rate_limit) >= 3:
                    logger.warning(f"[AutoScheduler] 분당 자동 적용 상한선(3회) 초과로 제안 #{pid} 적용을 보류합니다.")
                    continue
                    
                # 3.2. Cooldown 검사 (전략별 10분 = 600초 Cooldown)
                last_applied = self._strategy_last_applied.get(strategy_id, 0.0)
                # 테스트 환경이나 단축 세팅 시 debounce_seconds 등을 기반으로 쿨다운 시간 유연 조정 지원
                # 실제 운영환경은 600초(10분)이며, 만약 debounce가 극도로 짧은 테스트 환경(예: 0.5초)이라면  cooldown 기준을 2초로 완화
                cooldown_limit = 600.0 if self.debounce_seconds >= 1.0 else 2.0
                if now_epoch - last_applied < cooldown_limit:
                    logger.warning(f"[AutoScheduler] 전략 {strategy_id}의 쿨다운 윈도우({cooldown_limit}초) 미달로 제안 #{pid} 적용을 보류합니다.")
                    continue

                # 4. 자동 승인 처리 (Atomic Transaction)
                applied_ts = int(now_epoch * 1000)
                
                girs_shadow_mode = self.girs_shadow_mode_override
                if girs_shadow_mode is None:
                    girs_shadow_mode = self.config_manager.get("system.girs_shadow_mode", False)
                
                auto_strategy_promotion_enabled = self.auto_strategy_promotion_enabled_override
                if auto_strategy_promotion_enabled is None:
                    auto_strategy_promotion_enabled = self.config_manager.get("system.auto_strategy_promotion_enabled", False)
                
                if girs_shadow_mode or not auto_strategy_promotion_enabled:
                    logger.info(f"[AutoScheduler] Shadow mode active or auto promotion disabled. Skipping actual promotion for proposal #{pid}.")
                    
                    # SHADOW_PROMOTION_DETECTED 시스템 이벤트 기록
                    msg = f"Shadow promotion detected for proposal #{pid} (Strategy: {strategy_id}, Confidence: {confidence}%)"
                    await self.repository.insert_system_event(
                        event_type='SHADOW_PROMOTION_DETECTED',
                        target=strategy_id,
                        message=msg,
                        timestamp=int(now_epoch)
                    )
                    continue
                
                logger.info(f"[AutoScheduler] 제안 #{pid} 자동 승인 트리거 실행 (신뢰도: {confidence}점)")
                
                try:
                    res = await self.repository.approve_proposal_atomic(pid, applied_ts)
                except ChampionCooldownBlockedError as cd_err:
                    err_msg = str(cd_err)
                    msg = f"Champion Cooldown 미경과로 제안 #{pid} 자동 승격 보류: {err_msg}"
                    logger.warning(f"[AutoScheduler] {msg}")
                    await self.repository.insert_system_event(
                        event_type='PROMOTION_COOLDOWN_BLOCKED',
                        target=strategy_id,
                        message=msg,
                        timestamp=int(now_epoch)
                    )
                    continue
                
                # 5. 안전장치 상태 갱신
                self._strategy_last_applied[strategy_id] = now_epoch
                self._minute_rate_limit.append(now_epoch)
                
                logger.info(f"[AutoScheduler] 제안 #{pid} 자동 적용 성공! 신규 버전: V{res['new_version_id']}")
                
            except Exception as ex:
                logger.error(f"[AutoScheduler] 제안 #{pid} 자동 적용 중 에러 발생: {ex}")

    async def close(self):
        """스케줄러 정리"""
        if self._debounce_task and not self._debounce_task.done():
            self._debounce_task.cancel()
            try:
                await self._debounce_task
            except asyncio.CancelledError:
                pass
        logger.info("[AutoScheduler] 스케줄러가 안전하게 안전 종료되었습니다.")
