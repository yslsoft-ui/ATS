import time
import asyncio
from typing import Dict, Any, List
from src.database.repository import SqliteTradingRepository
from src.engine.backtest import BacktestEngine
from src.engine.utils.telemetry import get_logger
from src.database.connection import get_db_conn

logger = get_logger("counterfactual_tracker")

class CounterfactualSamplingTracker:
    """
    Counterfactual Sampling Engine (반사실적 제안 샘플링 추적기)
    - PRUNED 및 DEFERRED 상태 중 중요도 샘플링을 통과한 제안(is_counterfactual_tracked = 1)의 가상 성과를 트래킹합니다.
    - 5분 주기로 동작하며, 제안 시점(created_at)부터 현재 시점까지의 백테스트를 재구동하여 실시간 가상 ROI/MDD를 갱신합니다.
    - 7일 관찰 윈도우가 종료된 제안은 트래킹 완료(is_counterfactual_tracked = 2) 상태로 전환합니다.
    """
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.repository = SqliteTradingRepository(db_path=self.db_path)
        self.backtest_engine = BacktestEngine(db_path=self.db_path)
        self._running = False
        self._task = None

    async def start(self):
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("[CounterfactualTracker] 엔진 기동 완료.")

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("[CounterfactualTracker] 엔진이 안전하게 종료되었습니다.")

    async def _loop(self):
        while self._running:
            try:
                await self.run_step()
            except Exception as e:
                logger.error(f"[CounterfactualTracker] 루프 에러 발생: {e}")
            await asyncio.sleep(300) # 5분 대기

    async def run_step(self):
        # 1. 트래킹 대상 제안 조회 (최대 20개 샘플링)
        targets = await self.repository.get_counterfactual_targets(limit=20)
        if not targets:
            return

        logger.info(f"[CounterfactualTracker] {len(targets)}개 제안에 대해 반사실적 가상 성과 갱신 중...")
        now_ms = int(time.time() * 1000)
        seven_days_ms = 7 * 24 * 3600 * 1000

        for prop in targets:
            proposal_id = prop["id"]
            strategy_id = prop["strategy_id"]
            portfolio_id = prop["portfolio_id"]
            created_at = prop["created_at"] # Epoch ms
            proposed_params = prop["proposed_params"]

            # 7일 윈도우 초과 여부 확인
            if now_ms - created_at >= seven_days_ms:
                # 트래킹 종료 처리 (최종 ROI/MDD 고정 및 완료 전환)
                roi, mdd = await self._run_counterfactual_backtest(
                    strategy_id, portfolio_id, created_at, created_at + seven_days_ms, proposed_params
                )
                await self.repository.update_counterfactual_metrics(proposal_id, roi, mdd, track_status=2)
                logger.info(f"[CounterfactualTracker] 제안 #{proposal_id} 7일 윈도우 관찰 완료. 최종 ROI: {roi}%, MDD: {mdd}%")
                continue

            # 실시간 갱신 (created_at부터 현재까지 백테스트 구동)
            roi, mdd = await self._run_counterfactual_backtest(
                strategy_id, portfolio_id, created_at, now_ms, proposed_params
            )
            await self.repository.update_counterfactual_metrics(proposal_id, roi, mdd, track_status=1)
            logger.info(f"[CounterfactualTracker] 제안 #{proposal_id} 가상 성과 업데이트. ROI: {roi}%, MDD: {mdd}%")

    async def _run_counterfactual_backtest(self, strategy_id: str, portfolio_id: str, start_ms: int, end_ms: int, params: Dict[str, Any]) -> (float, float):
        try:
            # 1. 대상 거래소 및 심볼 획득 (다중 거래소 대응을 위한 JOIN/IN 쿼리 사용)
            exchange = "upbit"
            symbol = "BTC"
            
            async with get_db_conn(self.db_path) as db:
                async with db.execute(
                    """
                    SELECT t.exchange_id, t.symbol
                    FROM trades t
                    JOIN portfolio_exchanges pe ON t.exchange_id = pe.exchange_id
                    WHERE pe.portfolio_id = ?
                    ORDER BY t.trade_timestamp DESC
                    LIMIT 1
                    """,
                    (portfolio_id,)
                ) as cursor:
                    row = await cursor.fetchone()
                    if row:
                        exchange = row["exchange_id"]
                        symbol = row["symbol"]
                    else:
                        # 거래 이력이 전혀 없는 경우의 Fallback
                        async with db.execute(
                            "SELECT exchange_id FROM portfolio_exchanges WHERE portfolio_id = ?",
                            (portfolio_id,)
                        ) as pe_cursor:
                            pe_rows = await pe_cursor.fetchall()
                            if pe_rows:
                                exchange = pe_rows[0]["exchange_id"]

            # 2. 백테스트 구동 (인자 명칭을 exchange_id로 수정)
            strategy_configs = {
                strategy_id: {
                    "enabled": True,
                    "params": params
                }
            }
            res = await self.backtest_engine.run(
                exchange_id=exchange,
                symbol=symbol,
                start_date=start_ms,
                end_date=end_ms,
                initial_cash=10000000.0,
                strategy_configs=strategy_configs,
                risk_limits_enabled=False
            )
            if res.get("status") == "success":
                summary = res["summary"]
                return summary["roi"], summary["mdd"]
        except Exception as e:
            logger.error(f"[CounterfactualTracker] 가상 백테스트 구동 에러: {e}")
        return 0.0, 0.0
