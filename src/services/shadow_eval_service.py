# -*- coding: utf-8 -*-

import asyncio
import time
import json
from typing import Dict, Any, List
from src.engine.utils.telemetry import get_logger
from src.engine.daemon_supervisor import DaemonService
from src.database.connection import get_db_conn
from src.database.repository import SqliteTradingRepository

logger = get_logger("shadow_eval_service")

class ShadowEvaluationService(DaemonService):
    """
    실시간 GIRS Shadow 다중 Horizon 평가 도메인 서비스입니다.
    PENDING 상태의 평가 레코드가 만기(due_at)에 도달하면, 실제 시세(candles)와 틱(trades) 데이터를 바탕으로
    수익률(ROI) 및 거래량 실측 지표를 동적으로 계산하여 COMPLETED로 마감 처리합니다.
    """
    def __init__(self, config_manager: Any, event_bus: Any):
        self.config = config_manager
        self.event_bus = event_bus
        self.db_path = self.config.get('system.db_path', 'data/backtest.db')
        self.repo = SqliteTradingRepository(db_path=self.db_path)
        self.poll_interval = self.config.get('system.evaluation_poll_interval_seconds', 10)
        self.lock_timeout = self.config.get('system.evaluation_lock_timeout_seconds', 300)
        self.max_retries = self.config.get('system.evaluation_max_retry_count', 3)
        self._tasks: List[asyncio.Task] = []
        self._is_running = False

    async def start(self):
        logger.info("[ShadowEvaluationService] 서비스 기동 중...")
        self._is_running = True
        self._tasks.append(asyncio.create_task(self._evaluation_loop()))
        self._tasks.append(asyncio.create_task(self._stale_lock_recovery_loop()))
        logger.info("[ShadowEvaluationService] 서비스 기동 완료.")

    async def stop(self):
        logger.info("[ShadowEvaluationService] 서비스 중지 중...")
        self._is_running = False
        for task in self._tasks:
            if not task.done():
                task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
            self._tasks.clear()
        logger.info("[ShadowEvaluationService] 서비스 중지 완료.")

    def _extract_symbol(self, proposal: Dict[str, Any]) -> str:
        symbol = None
        # 1. proposed_params JSON 파싱
        params_str = proposal.get("proposed_params")
        if params_str:
            try:
                params = json.loads(params_str)
                symbol = params.get("symbol") or params.get("market")
            except:
                pass
        
        # 2. audit_log_json 파싱
        if not symbol:
            audit_json = proposal.get("audit_log_json")
            if audit_json:
                try:
                    audit_data = json.loads(audit_json)
                    symbol = audit_data.get("symbol") or audit_data.get("market")
                except:
                    pass
        
        # 3. strategy_id 분석 (예: SMACrossover_BTC-KRW 이면 BTC 또는 BTC-KRW 추출)
        if not symbol:
            strat_id = proposal.get("strategy_id")
            if strat_id and "_" in strat_id:
                parts = strat_id.split("_")
                symbol = parts[-1]
                # KRW-BTC 처럼 되어 있으면 BTC로 정규화 시도
                if "-" in symbol:
                    # ex: KRW-BTC -> BTC
                    sub_parts = symbol.split("-")
                    if sub_parts[0] == "KRW" and len(sub_parts) > 1:
                        symbol = sub_parts[1]
                    else:
                        symbol = sub_parts[0]

        # Default fallback
        return symbol or "BTC"

    async def _capture_baselines(self, now: int):
        try:
            pending_targets = await self.repo.get_pending_evaluations_without_baseline(now)
            for ev in pending_targets:
                pe_id = ev["id"]
                prop_id = ev["proposal_id"]
                hz_val = ev.get("horizon_value") or 600
                start_ts = ev["due_at"] - hz_val
                
                proposal = await self.repo.get_strategy_proposal(prop_id)
                if not proposal:
                    continue
                
                symbol = self._extract_symbol(proposal)
                
                # start_ts 근처의 캔들 종가 구하기
                async with get_db_conn(self.db_path) as db:
                    async with db.execute(
                        "SELECT close FROM candles WHERE symbol = ? AND timestamp >= ? ORDER BY timestamp ASC LIMIT 1",
                        (symbol, start_ts * 1000)
                    ) as cur:
                        row = await cur.fetchone()
                
                if row and row[0] is not None:
                    baseline_price = row[0]
                    await self.repo.update_baseline_snapshot(pe_id, baseline_price, start_ts, 0)
                    logger.info(f"[ShadowEvaluationService] baseline snapshot 캡처 성공: ID={pe_id}, Symbol={symbol}, BaselinePrice={baseline_price}")
        except Exception as e:
            logger.error(f"[ShadowEvaluationService] baseline 캡처 루틴 중 에러: {e}")

    async def _evaluate_record(self, pe_id: int, due_at: int, horizon_name: str, horizon_value: int, proposal_id: int, exchange: str, symbol: str, market_type: str):
        now = int(time.time())
        try:
            # 계산 기간: [due_at - horizon_value, due_at]
            start_ts = due_at - horizon_value
            end_ts = due_at
            
            # baseline_value, predicted 값들 조회
            async with get_db_conn(self.db_path) as db:
                async with db.execute(
                    "SELECT baseline_value, predicted_roi_7d, predicted_trade_count_7d, retry_count FROM proposal_evaluations WHERE id = ?",
                    (pe_id,)
                ) as cur:
                    ev_row = await cur.fetchone()
            
            if not ev_row:
                raise ValueError(f"평가 ID #{pe_id}를 찾을 수 없습니다.")
            
            baseline_value, predicted_roi, predicted_trades, retry_count = ev_row
            
            # 3.2. 실제 수익률(ROI) 계산
            close_start = baseline_value
            
            # baseline_value가 없다면 DB에서 시작가 조회 시도
            if close_start is None or close_start <= 0:
                async with get_db_conn(self.db_path) as db:
                    async with db.execute(
                        "SELECT close FROM candles WHERE symbol = ? AND timestamp >= ? ORDER BY timestamp ASC LIMIT 1",
                        (symbol, start_ts * 1000)
                    ) as cur:
                        start_row = await cur.fetchone()
                if start_row and start_row[0] > 0:
                    close_start = start_row[0]
            
            # 종료 시점 근처 종가
            async with get_db_conn(self.db_path) as db:
                async with db.execute(
                    "SELECT close FROM candles WHERE symbol = ? AND timestamp <= ? ORDER BY timestamp DESC LIMIT 1",
                    (symbol, end_ts * 1000)
                ) as cur:
                    end_row = await cur.fetchone()
            
            if close_start and end_row and close_start > 0:
                close_end = end_row[0]
                actual_roi = round(((close_end - close_start) / close_start) * 100.0, 4)
            else:
                # 데이터 정리(TTL)로 캔들이 소실되었고 baseline도 없으면 0.0 fallback 적용
                actual_roi = 0.0
                logger.warning(f"[ShadowEvaluationService] 만기 평가 #{pe_id} 계산에 필요한 캔들 데이터 없음. (symbol={symbol}, start={start_ts}, end={end_ts})")
            
            # 3.3. 실제 틱 거래량(Trades) 계산: trades count 기반
            actual_trades = 0
            async with get_db_conn(self.db_path) as db:
                async with db.execute(
                    "SELECT COUNT(*) FROM trades WHERE symbol = ? AND trade_timestamp BETWEEN ? AND ?",
                    (symbol, start_ts * 1000, end_ts * 1000)
                ) as cur:
                    actual_trades = (await cur.fetchone())[0]
            
            # 3.4. 예측값 대비 편차 산출
            predicted_roi_val = predicted_roi or 0.0
            predicted_trades_val = predicted_trades or 0
            
            roi_div = round(actual_roi - predicted_roi_val, 4)
            trade_div = actual_trades - predicted_trades_val
            
            # 3.5. 평가 마감 (COMPLETED)
            await self.repo.complete_evaluation(
                pe_id=pe_id,
                actual_roi=actual_roi,
                roi_div=roi_div,
                actual_trades=actual_trades,
                trade_div=trade_div,
                evaluated_at=now
            )
            logger.info(f"[ShadowEvaluationService] 평가 마감 완료: ID={pe_id}, ROI={actual_roi}%, Diff={roi_div}%")
            
        except Exception as calc_ex:
            logger.error(f"[ShadowEvaluationService] 평가 #{pe_id} 연산 실패: {calc_ex}")
            r_count = retry_count if 'retry_count' in locals() else 0
            await self.repo.fail_evaluation(
                pe_id=pe_id,
                error_msg=str(calc_ex),
                retry_count=r_count,
                max_retries=self.max_retries
            )

    async def _evaluation_loop(self):
        logger.info("[ShadowEvaluationService] 다중 Horizon 평가 폴러 루프 시작")
        while self._is_running:
            try:
                now = int(time.time())
                
                # 0. 아직 baseline이 없는 pending 대상에 대해 시작 시점이 도달한 경우 baseline 캡처
                await self._capture_baselines(now)
                
                # 1. 만기된 PENDING 평가 리스트 쿼리
                expired_evals = await self.repo.get_expired_pending_evaluations(now)
                
                for ev in expired_evals:
                    pe_id = ev["id"]
                    prop_id = ev["proposal_id"]
                    hz_name = ev["horizon_name"]
                    
                    # 2. 원자적 claim 획득 시도 (중복 평가 방지)
                    claimed = await self.repo.claim_evaluation(pe_id, now)
                    if not claimed:
                        continue
                    
                    logger.info(f"[ShadowEvaluationService] 평가 선점 성공: ID={pe_id}, Horizon={hz_name}, Proposal={prop_id}")
                    
                    try:
                        proposal = await self.repo.get_strategy_proposal(prop_id)
                        if not proposal:
                            raise ValueError(f"제안 ID #{prop_id}를 찾을 수 없습니다.")
                        
                        symbol = self._extract_symbol(proposal)
                        hz_val = ev.get("horizon_value") or 600
                        
                        audit_json = proposal.get("audit_log_json")
                        exchange = "upbit"
                        market_type = "crypto"
                        if audit_json:
                            try:
                                audit_data = json.loads(audit_json)
                                exchange = audit_data.get("exchange") or "upbit"
                                market_type = audit_data.get("market_type") or "crypto"
                            except:
                                pass
                        
                        await self._evaluate_record(
                            pe_id=pe_id,
                            due_at=ev["due_at"],
                            horizon_name=hz_name,
                            horizon_value=hz_val,
                            proposal_id=prop_id,
                            exchange=exchange,
                            symbol=symbol,
                            market_type=market_type
                        )
                    except Exception as e:
                        logger.error(f"[ShadowEvaluationService] 평가 레코드 처리 준비 중 오류: ID={pe_id}, Error={e}")
                        retry_count = ev.get("retry_count", 0)
                        await self.repo.fail_evaluation(
                            pe_id=pe_id,
                            error_msg=str(e),
                            retry_count=retry_count,
                            max_retries=self.max_retries
                        )
            except Exception as e:
                logger.error(f"[ShadowEvaluationService] 평가 루프 중 오류: {e}")
            
            await asyncio.sleep(self.poll_interval)

    async def _stale_lock_recovery_loop(self):
        logger.info("[ShadowEvaluationService] stale lock 복구 스캐너 루프 시작")
        while self._is_running:
            try:
                now = int(time.time())
                cutoff = now - self.lock_timeout
                
                # EVALUATING 상태로 락 타임아웃 경과된 stale 레코드 조회
                stale_evals = await self.repo.get_stale_evaluating_evaluations(cutoff)
                
                for ev in stale_evals:
                    pe_id = ev["id"]
                    r_count = ev.get("retry_count", 0)
                    
                    logger.warning(f"[ShadowEvaluationService] Stale Lock 감지! 복구 대상: ID={pe_id}, retry={r_count}/{self.max_retries}")
                    
                    # PENDING으로 롤백하거나 에러 처리
                    await self.repo.recover_stale_evaluation(
                        pe_id=pe_id,
                        retry_count=r_count,
                        max_retries=self.max_retries,
                        error_msg="LOCK_TIMEOUT"
                    )
            except Exception as e:
                logger.error(f"[ShadowEvaluationService] Stale Lock 복구 중 오류: {e}")
                
            await asyncio.sleep(60)

    async def handle_config_change(self, new_config: dict):
        logger.info("[ShadowEvaluationService] 설정 변경 감지")
        system_cfg = new_config.get('system', {})
        self.poll_interval = system_cfg.get('evaluation_poll_interval_seconds', self.poll_interval)
        self.lock_timeout = system_cfg.get('evaluation_lock_timeout_seconds', self.lock_timeout)
        self.max_retries = system_cfg.get('evaluation_max_retry_count', self.max_retries)

    async def handle_control_message(self, topic: str, data: dict) -> bool:
        return False

    def get_status_payloads(self) -> List[tuple[str, dict]]:
        return [
            ("evaluation_signal", {
                "type": "shadow_eval_status",
                "is_running": self._is_running,
                "poll_interval": self.poll_interval
            })
        ]

