import time
import uuid
import json
from typing import List, Dict, Any, Optional
from src.database.connection import get_db_conn
from src.engine.backtest import BacktestEngine
from src.engine.utils.telemetry import get_logger
from src.database.repository import SqliteTradingRepository
from src.engine.diversity_analyzer import get_combined_lambda_boost
from src.engine.girs_types import CandidateProposal, FeatureSnapshot
from src.engine.promotion_queue import PromotionQueue

logger = get_logger("shadow_backtest")

class ShadowBacktestEngine:
    """
    제안된 파라미터 변이 후보군을 대상으로 최근 1일(단기) 및 7일(중기) 백테스트를 수행하고
    통계적 거래 빈도(30건 이상) 필터링 및 신뢰도 점수(Confidence Score)를 산출해 제안을 영구 적재합니다.
    """
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.backtest_engine = BacktestEngine(db_path=self.db_path)
        self.repository = SqliteTradingRepository(db_path=self.db_path)

    async def _get_lambda_boost(self, strategy_id: str) -> float:
        """
        최근 30일 완료된 Counterfactual 기록과 현재 PENDING/APPLIED 제안의
        파라미터 공간 Entropy를 함께 평가하여 λ 보정 계수를 반환합니다.

        - Entropy < 0.3 AND 오판율 > 30%  → 1.2 (HIGH)
        - 둘 중 하나만                    → 1.1 (MEDIUM)
        - 둘 다 정상                       → 1.0 (NONE)
        """
        try:
            thirty_days_ms = 30 * 24 * 3600 * 1000
            cutoff = int(time.time() * 1000) - thirty_days_ms
            async with get_db_conn(self.db_path) as db:
                async with db.execute(
                    """
                    SELECT id, status, confidence_score, proposed_params,
                           original_params, counterfactual_roi,
                           is_counterfactual_tracked, created_at, decision_path_hash
                    FROM strategy_proposals
                    WHERE strategy_id = ? AND created_at > ?
                    """,
                    (strategy_id, cutoff),
                ) as cursor:
                    rows = await cursor.fetchall()

            proposals = []
            for r in rows:
                raw = dict(r)
                for field in ("proposed_params", "original_params"):
                    if isinstance(raw.get(field), str):
                        try:
                            raw[field] = json.loads(raw[field])
                        except Exception:
                            raw[field] = {}
                proposals.append(raw)

            result = get_combined_lambda_boost(
                proposals,
                entropy_threshold=0.3,
                max_boost=1.2,
            )
            if result["alert_level"] != "NONE":
                logger.info(
                    f"[ShadowBacktest] λ 보정 트리거: {result['alert_level']} "
                    f"(entropy={result['entropy']}, outperform_rate={result['outperform_rate']}) "
                    f"→ boost={result['lambda_boost']}, threshold_delta={result['diversity_threshold_delta']}"
                )
            return result["lambda_boost"], result["diversity_threshold_delta"]
        except Exception as e:
            logger.warning(f"[ShadowBacktest] λ boost 계산 실패, 기본값 사용: {e}")
            return 1.0, 0.0

    async def run_shadow_backtest(self, candidate_proposals: List[Dict[str, Any]], capture_snapshot_fn: Optional[Any] = None) -> List[int]:
        """
        후보 파라미터 셋들을 받아 백테스트 검증을 실행하고, 
        적격 후보에 대한 제안(Proposal)을 DB에 저장한 후 삽입된 ID 리스트를 반환합니다.
        """
        logger.info(f"[ShadowBacktest] 백테스트 검증 시작. 후보 개수: {len(candidate_proposals)}")
        inserted_ids = []
        
        now_ms = int(time.time() * 1000)
        one_day_ms = 24 * 3600 * 1000
        seven_days_ms = 7 * 24 * 3600 * 1000
        
        for cand in candidate_proposals:
            strategy_id = cand["strategy_id"]
            portfolio_id = cand["portfolio_id"]
            original_params = cand["original_params"]
            proposed_params = cand["proposed_params"]
            mutation_trace = cand["mutation_trace"]
            
            # 1. 대상 종목 및 거래소 식별
            # 활성 포트폴리오를 조회해 거래소 획득
            exchange = "upbit"
            symbol = "BTC"  # 기본값 fallback
            
            async with get_db_conn(self.db_path) as db:
                async with db.execute(
                    "SELECT exchange_id FROM portfolios WHERE id = ?", (portfolio_id,)
                ) as cursor:
                    row = await cursor.fetchone()
                    if row:
                        exchange = row["exchange_id"]
                        if exchange == "all":
                            exchange = "upbit" # 기본 매칭
                            
            # DB trades 테이블에서 최근 거래가 발생한 종목 하나를 획득하여 테스트 대상으로 삼음
            async with get_db_conn(self.db_path) as db:
                async with db.execute(
                    "SELECT symbol FROM trades WHERE exchange = ? ORDER BY trade_timestamp DESC LIMIT 1",
                    (exchange,)
                ) as cursor:
                    row = await cursor.fetchone()
                    if row:
                        symbol = row["symbol"]

            # 2. 최근 7일 백테스트 수행
            start_7d = now_ms - seven_days_ms
            end_time = now_ms
            
            # candidate_proposals 형태로 strategy_configs 전달
            # BacktestEngine은 strategy_configs = {strategy_name: {"enabled": True, "params": proposed_params}} 형태 기대
            strategy_configs = {
                strategy_id: {
                    "enabled": True,
                    "params": proposed_params
                }
            }
            
            logger.info(f"[ShadowBacktest] {strategy_id} 전략 중기(7일) 백테스트 개시 ({symbol})")
            res_7d = await self.backtest_engine.run(
                exchange=exchange,
                symbol=symbol,
                start_date=start_7d,
                end_date=end_time,
                initial_cash=10000000.0,
                strategy_configs=strategy_configs,
                risk_limits_enabled=False
            )
            
            if res_7d.get("status") != "success":
                logger.warning(f"[ShadowBacktest] 중기 백테스트 실패: {res_7d.get('message')}")
                continue
                
            summary_7d = res_7d["summary"]
            trade_count_7d = summary_7d["trade_count"]
            roi_7d = summary_7d["roi"]
            
            # [안전장치] 최근 7일 최소 거래 기준 체크
            from src.config.manager import ConfigManager
            config_manager = ConfigManager("config/settings.yaml")
            min_trades = config_manager.get("system.shadow_min_trades_limit", 30)
            if trade_count_7d < min_trades:
                logger.info(f"[ShadowBacktest] 7일 거래 수 {trade_count_7d}건이 최저 기준({min_trades}건)에 미달하여 제안 대상에서 탈락합니다.")
                continue

            # 3. 최근 1일 백테스트 수행
            start_1d = now_ms - one_day_ms
            logger.info(f"[ShadowBacktest] {strategy_id} 전략 단기(1일) 백테스트 개시 ({symbol})")
            res_1d = await self.backtest_engine.run(
                exchange=exchange,
                symbol=symbol,
                start_date=start_1d,
                end_date=end_time,
                initial_cash=10000000.0,
                strategy_configs=strategy_configs,
                risk_limits_enabled=False
            )
            
            roi_1d = 0.0
            if res_1d.get("status") == "success":
                roi_1d = res_1d["summary"]["roi"]
            else:
                logger.warning(f"[ShadowBacktest] 단기 백테스트 실패: {res_1d.get('message')}. ROI 0.0으로 진행.")

            # 4. 시장 국면 데이터(ATR Ratio, ADX 대용) 조회
            atr_ratio = 1.0
            adx = 20.0
            async with get_db_conn(self.db_path) as db:
                async with db.execute(
                    "SELECT volatility, rsi FROM market_regime_summaries ORDER BY timestamp DESC LIMIT 1"
                ) as cursor:
                    row = await cursor.fetchone()
                    if row:
                        atr_ratio = row["volatility"] if row["volatility"] is not None else 1.0
                        rsi_val = row["rsi"]
                        if rsi_val is not None and (rsi_val < 35.0 or rsi_val > 65.0):
                            adx = 30.0

            # 5. 롤백 이력 조회 및 proposed_params 와의 Parameter-weighted Normalized Distance 비교
            rollback_penalty = 0
            now_ms = int(time.time() * 1000)
            two_weeks_ms = 14 * 24 * 3600 * 1000
            cutoff_ts = now_ms - two_weeks_ms
            
            async with get_db_conn(self.db_path) as db:
                async with db.execute(
                    "SELECT old_params FROM strategy_parameter_history WHERE strategy_id = ? AND change_reason = 'ROLLBACK' AND created_at > ?",
                    (strategy_id, cutoff_ts)
                ) as cursor:
                    rows = await cursor.fetchall()
                    for r in rows:
                        if r["old_params"]:
                            try:
                                old_p = json.loads(r["old_params"])
                                dist = calculate_parameter_distance(old_p, proposed_params)
                                if dist < 0.1:
                                    rollback_penalty = 15
                                    break
                            except Exception as ex:
                                logger.warning(f"Error parsing old_params for rollback distance calculation: {ex}")

            # 5.5. 다양성 평가 (Proposal Diversity Constraint)
            # 최근 14일 제안 파라미터 및 현재 가동 파라미터 셋들과의 유사도 산출
            active_params_list = []
            async with get_db_conn(self.db_path) as db:
                async with db.execute(
                    "SELECT current_params FROM strategy_versions WHERE strategy_id = ?", (strategy_id,)
                ) as cursor:
                    row = await cursor.fetchone()
                    if row and row["current_params"]:
                        try:
                            active_params_list.append(json.loads(row["current_params"]))
                        except:
                            pass
                
                async with db.execute(
                    "SELECT proposed_params FROM strategy_proposals WHERE strategy_id = ? AND status IN ('PENDING', 'APPLIED') AND created_at > ?",
                    (strategy_id, cutoff_ts)
                ) as cursor:
                    rows = await cursor.fetchall()
                    for r in rows:
                        if r["proposed_params"]:
                            try:
                                active_params_list.append(json.loads(r["proposed_params"]))
                            except:
                                pass

            min_distance = 999.0
            for act_p in active_params_list:
                d = calculate_parameter_distance(act_p, proposed_params)
                if d < min_distance:
                    min_distance = d

            # settings.yaml 에서 enable_auto_proposal 획득하여 동적 패널티 강도(λ) 및 임계치 조정
            import yaml
            enable_auto = False
            try:
                with open("config/settings.yaml", "r", encoding="utf-8") as f:
                    cfg = yaml.safe_load(f)
                    enable_auto = cfg.get("system", {}).get("enable_auto_proposal", False)
            except:
                pass

            # Counterfactual Feedback + Entropy Drift 복합 λ 보정
            cf_boost, threshold_delta = await self._get_lambda_boost(strategy_id)

            base_lambda = int((25 if enable_auto else 15) * cf_boost)
            if atr_ratio > 1.2:
                lambda_dynamic = base_lambda * 0.6
                effective_threshold = max(0.07, 0.10 - threshold_delta)  # 고변동성: 수렴 허용 (하한 0.07)
            else:
                lambda_dynamic = base_lambda * 1.2
                effective_threshold = min(0.25, 0.18 + threshold_delta)  # 횡보: 다양성 강제 (상한 0.25)

            if adx > 25.0:
                effective_threshold = max(0.07, 0.10 - threshold_delta)  # 강한 추세장도 수렴 허용

            diversity_penalty = 0.0
            if min_distance < effective_threshold:
                diversity_penalty = lambda_dynamic * (1.0 - (min_distance / effective_threshold))
            diversity_penalty = min(diversity_penalty, lambda_dynamic)

            # 6. Multi-factor Scoring 점수 산출
            win_rate = summary_7d.get("win_rate", 50.0)
            profit_factor = summary_7d.get("profit_factor", 1.2)
            mdd = summary_7d.get("mdd", 2.0)
            
            base_score = calculate_multifactor_score(
                roi_7d=roi_7d,
                roi_1d=roi_1d,
                win_rate=win_rate,
                profit_factor=profit_factor,
                mdd=mdd
            )
            
            # 국면별 가중치 연산
            regime_weight = get_regime_weighting(
                atr_ratio=atr_ratio,
                adx=adx,
                original_params=original_params,
                proposed_params=proposed_params
            )
            
            # 최종 신뢰도 점수 산출
            confidence_score = base_score + regime_weight - rollback_penalty - diversity_penalty
            confidence_score = int(min(max(confidence_score, 0), 100))

            # 의사결정 해시 생성 (재현성 및 Mutation Graph 연계)
            import hashlib
            sorted_proposed = sorted(proposed_params.items())
            proposed_str = ",".join([f"{k}:{v}" for k, v in sorted_proposed])
            raw_hash_src = f"{strategy_id}:{original_params}:{proposed_str}:{atr_ratio}:{adx}"
            decision_path_hash = hashlib.sha256(raw_hash_src.encode("utf-8")).hexdigest()[:16]

            # Audit Log 생성 (설명 가능성 확보)
            audit_log_json = {
                "base_score": base_score,
                "regime_weight": regime_weight,
                "rollback_penalty": rollback_penalty,
                "diversity_penalty": int(diversity_penalty),
                "min_distance_observed": round(min_distance, 4) if min_distance != 999.0 else None,
                "effective_threshold": effective_threshold,
                "lambda_applied": lambda_dynamic,
                "performance_limit_triggered": True if (profit_factor < 1.0 or win_rate < 40.0) else False,
                "atr_ratio": round(atr_ratio, 3),
                "adx": round(adx, 3),
                "decision_path_hash": decision_path_hash
            }

            metrics_data = {
                "roi_1d": roi_1d,
                "roi_7d": roi_7d,
                "trade_count_7d": trade_count_7d,
                "total_fee_7d": summary_7d["total_fee"],
                "win_rate": win_rate,
                "profit_factor": profit_factor,
                "mdd": mdd
            }
            
            proposal_group_id = f"pg_{int(time.time())}_{uuid.uuid4().hex[:6]}"
            
            proposal_data = {
                "insight_id": None,
                "proposal_group_id": proposal_group_id,
                "version": 1,
                "portfolio_id": portfolio_id,
                "strategy_id": strategy_id,
                "status": "PENDING",
                "outcome": "RUNNING",
                "original_params": original_params,
                "proposed_params": proposed_params,
                "metrics": metrics_data,
                "mutation_trace": mutation_trace,
                "confidence_score": confidence_score,
                "decision_path_hash": decision_path_hash,
                "audit_log_json": audit_log_json,
                "applied_at": None,
                "rolled_back_at": None
            }
            
            inserted_id = await self.repository.insert_strategy_proposal(proposal_data)
            inserted_ids.append(inserted_id)
            logger.info(f"[ShadowBacktest] 제안 등록 성공! ID={inserted_id}, Status={'PRUNED' if confidence_score < 60 else 'PENDING'}, Confidence={confidence_score}점")
            
            # 3. proposal_evaluations에 1:N Horizon PENDING 레코드 일괄 생성 및 실시간 스냅샷 캡처
            try:
                # 설정 파일에서 horizons 설정 로드
                import os
                config_path = os.getenv("ATS_CONFIG", "config/settings_production.yaml")
                with open(config_path, "r", encoding="utf-8") as f:
                    import yaml
                    full_cfg = yaml.safe_load(f) or {}
                system_cfg = full_cfg.get("system", {})
                horizons_cfg = system_cfg.get("horizons", {})
                
                market_type = "stock" if exchange.lower() in ("kis", "shinhan") else "crypto"
                horizons_list = horizons_cfg.get(market_type, [])
                
                from src.engine.evaluation_policy import calculate_due_at
                
                # 실시간 스냅샷 캡처 시도 (없으면 Fallback)
                snap = None
                if capture_snapshot_fn:
                    try:
                        snap = await capture_snapshot_fn(exchange, symbol, strategy_id)
                        if snap:
                            # proposal_id를 캡처된 스냅샷에 갱신해줌 (혹은 DB에 기록 시 연계용)
                            logger.info(f"[ShadowBacktest] 실시간 피처 캡처 성공! Hash={snap.feature_hash}")
                    except Exception as ex:
                        logger.error(f"[ShadowBacktest] 실시간 피처 캡처 중 오류, Fallback 적용: {ex}")
                
                if not snap:
                    snap = FeatureSnapshot(
                        price_features={"close": 50000.0, "returns": roi_1d / 100.0, "volatility": atr_ratio},
                        liquidity_features={"spread": 0.002, "volume": float(summary_7d.get("volume", 5000.0)), "depth": 10000.0},
                        regime_features={"regime_index": float(adx > 25.0)},
                        exchange=exchange,
                        symbol=symbol,
                        market_type=market_type
                    )
                
                async with get_db_conn(self.db_path) as db:
                    for hz in horizons_list:
                        due_at = calculate_due_at(market_type, hz, int(time.time()))
                        await db.execute(
                            """
                            INSERT INTO proposal_evaluations (
                                proposal_id, horizon_name, due_at, evaluation_status,
                                horizon_type, horizon_value, policy_version, scorer_version,
                                predicted_risk_score
                            )
                            VALUES (?, ?, ?, 'PENDING', ?, ?, 'v4', ?, ?)
                            """,
                            (
                                inserted_id,
                                hz.get("name"),
                                due_at,
                                hz.get("type"),
                                hz.get("value") if isinstance(hz.get("value"), int) else None,
                                "mock_v1",  # scorer_version
                                float(confidence_score) / 100.0 if confidence_score is not None else 0.5  # predicted_risk_score
                            )
                        )
                    await db.commit()
                logger.info(f"[ShadowBacktest] Proposal {inserted_id}에 대한 {len(horizons_list)}개 Horizon PENDING 평가 레코드 생성 완료")
                
                cand_proposal = CandidateProposal(
                    proposal_id=str(inserted_id),
                    source_strategy_id=strategy_id,
                    features=snap,
                    backtest_result=metrics_data,
                    model_version="mock_v1",
                    scaler_version="mock_v1"
                )
                
                queue = PromotionQueue(db_path=self.db_path)
                await queue.init_table()
                
                # 1. Queue Ingest
                ingest_evt = str(uuid.uuid4())
                await queue.ingest_proposal(cand_proposal, ingest_evt)
                
                # 2. Ranking Dry-run을 위한 Scored 전이
                score_evt = str(uuid.uuid4())
                final_score = float(confidence_score) / 100.0
                await queue.transition_state(
                    str(inserted_id), "SCORED", score_evt,
                    {"final_promotion_score": final_score}
                )
                
                # 3. 랭킹 리스트 로깅
                ranked = queue.get_ranked_proposals()
                logger.info(f"[ShadowBacktest] [GIRS Queue Dry-run] Ranked proposals after ingest: {ranked}")
            except Exception as e:
                logger.error(f"[ShadowBacktest] [GIRS Queue Dry-run] Failed to run GIRS queue ingest/dry-run: {e}")

        return inserted_ids


def calculate_multifactor_score(roi_7d: float, roi_1d: float, win_rate: float, profit_factor: float, mdd: float) -> int:
    if profit_factor < 1.0 or win_rate < 40.0:
        return 50
        
    roi_7d_score = min(max(roi_7d * 2.0, 0.0), 25.0)
    roi_1d_score = min(max(roi_1d * 3.0, 0.0), 15.0)
    win_rate_contribution = min(max((win_rate - 40.0) * 0.6, 0.0), 30.0)
    pf_contribution = min(max((profit_factor - 1.0) * 10.0, 0.0), 20.0)
    mdd_penalty = mdd * 2.0
    
    score = 50 + roi_7d_score + roi_1d_score + win_rate_contribution + pf_contribution - mdd_penalty
    return int(min(max(score, 50.0), 100.0))


def get_regime_weighting(atr_ratio: float, adx: float, original_params: dict, proposed_params: dict) -> int:
    weight = 0
    orig_buy = original_params.get("buy_threshold")
    prop_buy = proposed_params.get("buy_threshold")
    orig_sell = original_params.get("sell_threshold")
    prop_sell = proposed_params.get("sell_threshold")
    
    is_conservative = False
    if orig_buy is not None and prop_buy is not None and prop_buy < orig_buy:
        is_conservative = True
    if orig_sell is not None and prop_sell is not None and prop_sell > orig_sell:
        is_conservative = True
        
    if atr_ratio > 1.2 and is_conservative:
        weight += 5
        
    orig_rsi = original_params.get("rsi_window")
    prop_rsi = proposed_params.get("rsi_window")
    
    if adx > 25.0 and orig_rsi is not None and prop_rsi is not None and (orig_rsi - prop_rsi) >= 4:
        weight -= 10
        
    return weight


def calculate_parameter_distance(p1: dict, p2: dict) -> float:
    weights = {
        "rsi_window": 0.2,
        "buy_threshold": 0.8,
        "sell_threshold": 0.8
    }
    
    distance = 0.0
    for p, w in weights.items():
        v1 = p1.get(p)
        v2 = p2.get(p)
        if v1 is not None and v2 is not None:
            baseline = float(v1)
            if baseline != 0.0:
                distance += w * (abs(float(v2) - baseline) / baseline)
                
    for k in p1.keys():
        if k not in weights and k != "insight_id" and k != "proposal_group_id":
            v1 = p1[k]
            v2 = p2.get(k)
            if isinstance(v1, str) or isinstance(v2, str):
                if v1 != v2:
                    distance += 1.0
                    
    return distance
