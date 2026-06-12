# -*- coding: utf-8 -*-

import asyncio
import time
import json
import aiosqlite
from typing import Dict, Any, List, Tuple
from src.engine.utils.telemetry import get_logger
from src.engine.daemon_supervisor import DaemonService
from src.database.connection import get_db_conn
from src.database.repository import SqliteTradingRepository
from src.engine.girs_types import FeatureSnapshot, FeatureContractValidator
from src.engine.girs_scorer import GIRSScorer, MockONNXModel
from src.engine.backtest import BacktestEngine

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

        # [수동 재평가용 GIRSScorer 초기화]
        onnx_path = self.config.get("system.onnx_model_path", None)
        model_ver = self.config.get("system.model_version", "mock_v1")
        stability_config = self.config.get("system.stability", {})
        market_std_weight = stability_config.get("market_std_weight", 1.0)
        market_mean_weight = stability_config.get("market_mean_weight", 0.5)
        system_jitter_weight = stability_config.get("system_jitter_weight", 1.0)
        system_latency_weight = stability_config.get("system_latency_weight", 0.5)

        self.girs_scorer = GIRSScorer(
            model=MockONNXModel(model_version=model_ver),
            onnx_model_path=onnx_path,
            market_std_weight=market_std_weight,
            market_mean_weight=market_mean_weight,
            system_jitter_weight=system_jitter_weight,
            system_latency_weight=system_latency_weight
        )
        self.current_model_version = model_ver
        self._reeval_event = asyncio.Event()

    async def start(self):
        logger.info("[ShadowEvaluationService] 서비스 기동 중...")
        self._is_running = True
        self._tasks.append(asyncio.create_task(self._evaluation_loop()))
        self._tasks.append(asyncio.create_task(self._stale_lock_recovery_loop()))
        self._tasks.append(asyncio.create_task(self._reevaluation_jobs_loop()))
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
        if data.get("type") == "reevaluate_trigger":
            job_id = data.get("job_id")
            proposal_id = data.get("proposal_id")
            logger.info(f"[ShadowEvaluationService] ZMQ reevaluate_trigger 수신. job_id={job_id}, proposal_id={proposal_id}")
            self._reeval_event.set()
            return True
        return False

    def get_status_payloads(self) -> List[tuple[str, dict]]:
        return [
            ("evaluation_signal", {
                "type": "shadow_eval_status",
                "is_running": self._is_running,
                "poll_interval": self.poll_interval
            })
        ]

    async def _reevaluation_jobs_loop(self):
        logger.info("[ShadowEvaluationService] 수동 재평가 Job 폴러 루프 시작")
        while self._is_running:
            try:
                self._reeval_event.clear()

                # 1. QUEUED 상태의 작업 중 가장 오래된 것 하나 조회
                async with get_db_conn(self.db_path) as db:
                    async with db.execute(
                        "SELECT job_id, proposal_id, input_snapshot_id FROM proposal_reevaluation_jobs "
                        "WHERE status = 'QUEUED' ORDER BY requested_at ASC LIMIT 1"
                    ) as cur:
                        job_row = await cur.fetchone()

                if job_row:
                    job_id = job_row[0]
                    proposal_id = job_row[1]
                    input_snapshot_id = job_row[2]

                    logger.info(f"[ShadowEvaluationService] 수동 재평가 Job 선점 시도: job_id={job_id}, proposal_id={proposal_id}")

                    # 2. RUNNING 상태로 업데이트
                    now_ms = int(time.time() * 1000)
                    async with get_db_conn(self.db_path) as db:
                        cursor = await db.execute(
                            "UPDATE proposal_reevaluation_jobs "
                            "SET status = 'RUNNING', started_at = ?, worker_id = ? "
                            "WHERE job_id = ? AND status = 'QUEUED'",
                            (now_ms, "shadow_eval_daemon", job_id)
                        )
                        updated = cursor.rowcount
                        await db.commit()

                    if updated > 0:
                        logger.info(f"[ShadowEvaluationService] 수동 재평가 Job 선점 성공: job_id={job_id}. 실행을 개시합니다.")
                        # system_events 등록
                        async with get_db_conn(self.db_path) as db:
                            await db.execute(
                                "INSERT INTO system_events (event_type, target, message, timestamp, context) "
                                "VALUES ('PROPOSAL_REEVALUATION_STARTED', ?, ?, ?, ?)",
                                (
                                    str(proposal_id),
                                    f"제안 #{proposal_id}에 대한 수동 재평가 Job #{job_id} 시작",
                                    now_ms,
                                    json.dumps({"job_id": job_id, "proposal_id": proposal_id, "worker_id": "shadow_eval_daemon"})
                                )
                            )
                            await db.commit()

                        # 실제 재평가 태스크 실행
                        try:
                            await self._execute_reevaluation(job_id, proposal_id, input_snapshot_id)

                            # 성공 마감
                            finish_ms = int(time.time() * 1000)
                            async with get_db_conn(self.db_path) as db:
                                await db.execute(
                                    "UPDATE proposal_reevaluation_jobs "
                                    "SET status = 'COMPLETED', finished_at = ? "
                                    "WHERE job_id = ?",
                                    (finish_ms, job_id)
                                )
                                await db.execute(
                                    "INSERT INTO system_events (event_type, target, message, timestamp, context) "
                                    "VALUES ('PROPOSAL_REEVALUATION_COMPLETED', ?, ?, ?, ?)",
                                    (
                                        str(proposal_id),
                                        f"제안 #{proposal_id}에 대한 수동 재평가 Job #{job_id} 완료",
                                        finish_ms,
                                        json.dumps({"job_id": job_id, "proposal_id": proposal_id})
                                    )
                                )
                                await db.commit()
                            logger.info(f"[ShadowEvaluationService] 수동 재평가 Job #{job_id} 완료.")

                        except Exception as eval_ex:
                            logger.error(f"[ShadowEvaluationService] 수동 재평가 Job #{job_id} 실패: {eval_ex}")
                            finish_ms = int(time.time() * 1000)
                            error_msg = str(eval_ex)
                            async with get_db_conn(self.db_path) as db:
                                await db.execute(
                                    "UPDATE proposal_reevaluation_jobs "
                                    "SET status = 'FAILED', finished_at = ?, error_message = ? "
                                    "WHERE job_id = ?",
                                    (finish_ms, error_msg, job_id)
                                )
                                await db.execute(
                                    "INSERT INTO system_events (event_type, target, message, timestamp, context) "
                                    "VALUES ('PROPOSAL_REEVALUATION_FAILED', ?, ?, ?, ?)",
                                    (
                                        str(proposal_id),
                                        f"제안 #{proposal_id}에 대한 수동 재평가 Job #{job_id} 실패. 사유: {error_msg}",
                                        finish_ms,
                                        json.dumps({"job_id": job_id, "proposal_id": proposal_id, "error": error_msg})
                                    )
                                )
                                await db.commit()
                    else:
                        logger.warning(f"[ShadowEvaluationService] 수동 재평가 Job #{job_id} 선점 실패 (이미 타 프로세스가 처리 중)")

                    continue

                try:
                    await asyncio.wait_for(self._reeval_event.wait(), timeout=10.0)
                except asyncio.TimeoutError:
                    pass

            except Exception as e:
                logger.error(f"[ShadowEvaluationService] 수동 재평가 루프 에러: {e}")
                await asyncio.sleep(5)

    async def _execute_reevaluation(self, job_id: int, proposal_id: int, input_snapshot_id: int):
        # 1. Feature Snapshot 로드 및 Feature Contract 검증
        log_row = await self.repo.get_latest_feature_snapshot_for_proposal(str(proposal_id))

        if log_row and log_row.get("feature_snapshot"):
            feat_dict = log_row["feature_snapshot"]
            if isinstance(feat_dict, str):
                feat_dict = json.loads(feat_dict)
            snapshot_source = "promotion_event_log"
        else:
            # promotion_event_log에 snapshot이 없는 경우 → 합성 기본 snapshot 구성
            # strategy_performance_snapshots에서 최근 성과 지표를 참조
            logger.warning(
                f"[ShadowEvaluationService] 제안 #{proposal_id}에 대한 FeatureSnapshot이 없음. "
                "최근 성과 데이터를 참조하여 합성 Snapshot을 구성합니다."
            )
            proposal = await self.repo.get_strategy_proposal(proposal_id)
            strategy_id = proposal["strategy_id"] if proposal else "unknown"
            feat_dict = {
                "price_features": {
                    "close": 90000000.0,
                    "returns": 0.001,
                    "volatility": 0.15,
                },
                "liquidity_features": {
                    "spread": 0.002,
                    "volume": 50000.0,
                    "depth": 200000.0,
                },
                "regime_features": {
                    "regime_index": 0.5,
                },
                "schema_version": "1.0",
                "feature_hash": f"synthetic_{proposal_id}",
                "generated_at": time.time(),
                "exchange": "upbit",
                "symbol": "BTC",
                "market_type": "crypto",
            }
            snapshot_source = "synthetic_fallback"

        snapshot = FeatureSnapshot(
            price_features=feat_dict.get("price_features", {}),
            liquidity_features=feat_dict.get("liquidity_features", {}),
            regime_features=feat_dict.get("regime_features", {}),
            schema_version=feat_dict.get("schema_version", "1.0"),
            feature_hash=feat_dict.get("feature_hash", ""),
            generated_at=feat_dict.get("generated_at", time.time()),
            exchange=feat_dict.get("exchange", "upbit"),
            symbol=feat_dict.get("symbol", "BTC"),
            market_type=feat_dict.get("market_type", "crypto")
        )
        if "trade_age_ms" in feat_dict:
            snapshot.trade_age_ms = feat_dict["trade_age_ms"]

        feature_ranges = {
            "close": (0.0, 100000000.0),
            "returns": (-1.0, 1.0),
            "volatility": (0.0, 5.0),
            "spread": (0.0, 1.0),
            "volume": (0.0, 1000000000.0),
            "depth": (0.0, 1000000000.0),
            "regime_index": (-10.0, 10.0),
        }

        validator = FeatureContractValidator(
            expected_price_keys=["close", "returns", "volatility"],
            expected_liquidity_keys=["spread", "volume", "depth"],
            expected_regime_keys=["regime_index"],
            feature_ranges=feature_ranges,
            stale_threshold=5,
            tick_threshold=10,
            volume_threshold=1000.0
        )

        # 2-Stage Contract 검증 수행
        _, is_fallback, _ = validator.validate_and_clamp(
            snapshot=snapshot,
            market_session="regular_trading",
            expected_tick_count=15,
            recent_volume=2000.0
        )
        data_quality_blocked = is_fallback

        # 2. GIRSScorer 재추론
        model_risk_score = self.girs_scorer.model.predict(snapshot)
        volatility = snapshot.price_features.get("volatility", 0.1)
        spread = snapshot.liquidity_features.get("spread", 0.001)
        volume = snapshot.liquidity_features.get("volume", 1000.0)
        depth = snapshot.liquidity_features.get("depth", 1000.0)
        regime_risk = snapshot.regime_features.get("regime_index", 1.0)

        limits = {
            "max_spread": 0.05,
            "max_volume": 1000000.0,
            "max_depth": 1000000.0,
            "max_volatility": 1.0,
            "max_drawdown": 0.5
        }

        fallback_risk_score = self.girs_scorer.calculate_fallback_risk(
            volatility=volatility,
            drawdown=0.0,
            regime_risk=regime_risk,
            spread=spread,
            volume=volume,
            depth=depth,
            limits=limits
        )

        avg_lat = float(snapshot.trade_age_ms) / 1000.0 if hasattr(snapshot, "trade_age_ms") and snapshot.trade_age_ms is not None else 0.0

        rank_stab = self.girs_scorer.calculate_rank_stability(str(proposal_id), current_confirmed_rank=1, N=10)
        market_stab = self.girs_scorer.calculate_market_stability(str(proposal_id), volatility)
        system_stab = self.girs_scorer.calculate_system_stability(system_latency_jitter=0.01, average_latency=avg_lat)
        stability_score = self.girs_scorer.calculate_stability_score(rank_stab, market_stab, system_stab)

        girs_p, fallback_p, final_promotion_score, meta_score = self.girs_scorer.calculate_final_score(
            model_risk_score=model_risk_score,
            fallback_risk_score=fallback_risk_score,
            stability_score=stability_score,
            snapshot=snapshot,
            data_quality_blocked=data_quality_blocked
        )

        rollback_probability = round(model_risk_score * 0.7 + (1.0 - stability_score) * 0.3, 4)

        async with get_db_conn(self.db_path) as db:
            await db.execute(
                "INSERT INTO system_events (event_type, target, message, timestamp, context) "
                "VALUES ('GIRS_REEVALUATION_COMPLETED', ?, ?, ?, ?)",
                (
                    str(proposal_id),
                    f"제안 #{proposal_id} GIRSScorer 재추론 완료. 점수: {final_promotion_score:.4f}",
                    int(time.time() * 1000),
                    json.dumps({"job_id": job_id, "girs_p": girs_p, "fallback_p": fallback_p, "final": final_promotion_score})
                )
            )
            await db.commit()

        # 3. Counterfactual Simulation 재실행 (Side Effect 완벽 차단)
        proposal = await self.repo.get_strategy_proposal(proposal_id)
        if not proposal:
            raise ValueError(f"제안 #{proposal_id} 정보를 가져올 수 없습니다.")

        strategy_id = proposal["strategy_id"]
        portfolio_id = proposal["portfolio_id"]
        created_at = proposal["created_at"]
        proposed_params = proposal["proposed_params"]

        exchange = "upbit"
        symbol = self._extract_symbol(proposal)

        audit_json = proposal.get("audit_log_json")
        if audit_json:
            try:
                audit_data = json.loads(audit_json) if isinstance(audit_json, str) else audit_json
                exchange = audit_data.get("exchange") or "upbit"
            except:
                pass

        now_ms = int(time.time() * 1000)
        end_ms = min(now_ms, created_at + 7 * 24 * 3600 * 1000)

        # 7일 윈도우 보장 최소 시간 차이 검증
        if end_ms <= created_at:
            end_ms = created_at + 60000  # 최소 1분

        params = json.loads(proposed_params) if isinstance(proposed_params, str) else proposed_params

        # 백테스트 엔진 구동
        engine = BacktestEngine(db_path=self.db_path)
        strategy_configs = {
            strategy_id: {
                "enabled": True,
                "params": params
            }
        }

        sim_res = await engine.run(
            exchange=exchange,
            symbol=symbol,
            start_date=created_at,
            end_date=end_ms,
            initial_cash=10000000.0,
            strategy_configs=strategy_configs,
            risk_limits_enabled=False
        )

        if sim_res.get("status") == "success":
            sim_roi = sim_res["summary"].get("roi", 0.0)
            sim_mdd = sim_res["summary"].get("mdd", 0.0)
        else:
            # 틱 데이터 부족, 전략 등록 실패 등으로 백테스트가 불가능한 경우
            # Counterfactual 결과를 null(미계산)로 처리하고 GIRS 점수 결과만 저장
            sim_msg = sim_res.get('message', '알 수 없는 오류')
            logger.warning(
                f"[ShadowEvaluationService] 제안 #{proposal_id} Counterfactual Simulation을 건너뜁니다. "
                f"사유: {sim_msg}"
            )
            sim_roi = None
            sim_mdd = None

        # Horizon evaluations id 매핑 조회
        counterfactual_result_id = None
        async with get_db_conn(self.db_path) as db:
            async with db.execute(
                "SELECT id FROM proposal_evaluations WHERE proposal_id = ? LIMIT 1",
                (proposal_id,)
            ) as cur:
                eval_row = await cur.fetchone()
                if eval_row:
                    counterfactual_result_id = eval_row[0]

        async with get_db_conn(self.db_path) as db:
            roi_disp = f"{sim_roi}%" if sim_roi is not None else "미계산(데이터 부족)"
            mdd_disp = f"{sim_mdd}%" if sim_mdd is not None else "미계산"
            await db.execute(
                "INSERT INTO system_events (event_type, target, message, timestamp, context) "
                "VALUES ('COUNTERFACTUAL_REEVALUATION_COMPLETED', ?, ?, ?, ?)",
                (
                    str(proposal_id),
                    f"제안 #{proposal_id} 반사실적 가상 시뮬레이션 완료. ROI: {roi_disp}, MDD: {mdd_disp}",
                    int(time.time() * 1000),
                    json.dumps({"job_id": job_id, "roi": sim_roi, "mdd": sim_mdd})
                )
            )
            await db.commit()


        # 4. GIRSScorer 모델과 Counterfactual 모듈 결과를 proposal_evaluation_runs에 적재
        async with get_db_conn(self.db_path) as db:
            await db.execute(
                "INSERT INTO proposal_evaluation_runs "
                "(proposal_id, job_id, girs_score, promotion_score, stability_score, rollback_probability, "
                " data_quality_blocked, counterfactual_result_id, model_version, scorer_version, simulator_version, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    proposal_id,
                    job_id,
                    model_risk_score,          # GIRS Risk Score
                    final_promotion_score,     # Promotion Score
                    stability_score,           # Stability Score
                    rollback_probability,      # Rollback Probability
                    1 if data_quality_blocked else 0,
                    counterfactual_result_id,
                    self.current_model_version,
                    self.current_model_version,
                    "backtest_v1",
                    int(time.time() * 1000)
                )
            )
            await db.commit()

