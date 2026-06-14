import json
import time
from typing import List, Dict, Any, Optional
from src.database.connection import get_db_conn
from src.engine.utils.telemetry import get_logger

logger = get_logger("strategy_analyzer")

class StrategyHypothesisAnalyzer:
    """
    실거래 이력과 시장 Regime 요약을 비교 분석하여 실패 원인 가설을 수립하고,
    개선된 파라미터 후보군(One Parameter Mutation)을 생성합니다.
    """
    def __init__(self, db_path: str):
        self.db_path = db_path

    async def analyze_failures(self, portfolio_id: str, strategy_id: str) -> List[Dict[str, Any]]:
        """
        포트폴리오의 실거래 이력을 분석하여 실패 케이스를 식별하고
        인접 시장 Regime 통계를 결합해 개선 파라미터 후보군을 생성해 반환합니다.
        """
        logger.info(f"[Analyzer] 실패 거래 분석 시작: portfolio_id={portfolio_id}, strategy_id={strategy_id}")
        
        # 1. 특정 전략의 거래 이력 획득
        from src.engine.portfolio import get_integer_portfolio_id
        pid = get_integer_portfolio_id(portfolio_id)
        trades = []
        async with get_db_conn(self.db_path) as db:
            async with db.execute(
                "SELECT * FROM orders_history WHERE portfolio_id = ? AND strategy_id = ? ORDER BY timestamp ASC",
                (pid, strategy_id)
            ) as cursor:
                rows = await cursor.fetchall()
                trades = [dict(r) for r in rows]
                
        if len(trades) < 5:
            logger.info("[Analyzer] 분석에 필요한 거래 이력이 부족합니다. (최소 5건 필요)")
            return []

        # 2. 손실 거래(실패 케이스) 필터링
        # BUY-SELL을 매칭하여 평단가 대비 손실이 발생한 SELL 거래 추적
        loss_trades = []
        temp_positions = {} # symbol -> (quantity, avg_price)
        
        for t in trades:
            sym = t["symbol"]
            side = t["side"]
            price = t["price"]
            qty = t["quantity"]
            fee = t["fee"]
            ts = t["timestamp"]
            
            if sym not in temp_positions:
                temp_positions[sym] = [0.0, 0.0]
                
            p_qty, p_avg = temp_positions[sym]
            
            if side == "BUY":
                total_cost = (p_avg * p_qty) + (price * qty)
                p_qty += qty
                if p_qty > 0:
                    p_avg = total_cost / p_qty
                temp_positions[sym] = [p_qty, p_avg]
            else:
                # SELL 거래에서 손실이 났는지 체크
                profit = (price - p_avg) * qty - fee
                if profit < 0:
                    loss_trades.append({
                        "symbol": sym,
                        "price": price,
                        "quantity": qty,
                        "avg_price": p_avg,
                        "loss": abs(profit),
                        "timestamp": ts
                    })
                p_qty -= qty
                if p_qty <= 0:
                    p_qty = 0.0
                    p_avg = 0.0
                temp_positions[sym] = [p_qty, p_avg]

        if not loss_trades:
            logger.info("[Analyzer] 손실 거래가 없어 가설 생성을 스킵합니다.")
            return []

        # 3. 손실 거래 시점의 시장 Regime 요약 결합
        regime_matches = []
        for lt in loss_trades:
            ts_ms = lt["timestamp"] * 1000 if lt["timestamp"] < 10000000000 else lt["timestamp"]
            # 인접 5분 범위 내의 market_regime_summary 조회
            async with get_db_conn(self.db_path) as db:
                async with db.execute(
                    "SELECT * FROM market_regime_summaries "
                    "WHERE timestamp BETWEEN ? AND ? "
                    "ORDER BY ABS(timestamp - ?) ASC LIMIT 1",
                    (ts_ms - 300000, ts_ms + 300000, ts_ms)
                ) as cursor:
                    row = await cursor.fetchone()
                    if row:
                        regime_matches.append({
                            "trade": lt,
                            "regime": dict(row)
                        })

        if not regime_matches:
            logger.info("[Analyzer] 손실 시점과 매칭되는 시장 Regime 데이터가 없어 분석을 스킵합니다.")
            return []

        # 4. 통계적 편향(가설) 도출 및 인사이트 발굴
        # 예: 고변동성 구간에서 손실의 50% 이상 발생 여부 체크
        high_vol_losses = [rm for rm in regime_matches if rm["regime"].get("volatility", 0.0) > 0.02]
        low_rsi_losses = [rm for rm in regime_matches if rm["regime"].get("rsi", 50.0) < 35.0]
        
        insights = []
        mutations = []
        
        # 현재 전략 파라미터 조회
        current_version_info = await self.get_current_params(strategy_id)
        if not current_version_info:
            logger.info(f"[Analyzer] 전략 {strategy_id}의 현재 파라미터 버전을 조회할 수 없어 종료합니다.")
            return []
            
        curr_params = current_version_info["current_params"]
        
        # 가설 A: 고변동성 구간에서의 손실이 전체의 40% 이상인 경우 -> 진입 필터 강화 또는 손절 타이밍 확대
        if len(high_vol_losses) / len(regime_matches) >= 0.4:
            fact = f"손실 거래의 {int(len(high_vol_losses)/len(regime_matches)*100)}%가 변동성이 높은 시장 regime에서 발생했습니다."
            insights.append({
                "category": "ENTRY_FILTER",
                "fact_summary": fact,
                "details": {"high_vol_losses_count": len(high_vol_losses), "total_losses": len(regime_matches)}
            })
            
            # 파라미터 변이 제안: rsi_window 나 buy_threshold 조정
            if "buy_threshold" in curr_params:
                # 변동성이 높을 때는 더 보수적으로 진입하도록 buy_threshold 하향 조정 (RSI 하향 시 진입 지연 효과)
                mutated = curr_params.copy()
                mutated["buy_threshold"] = max(5.0, curr_params["buy_threshold"] - 5.0)
                mutations.append({
                    "proposed_params": mutated,
                    "mutation_trace": {"buy_threshold": [curr_params["buy_threshold"], mutated["buy_threshold"]], "reason": "변동성 과열 시 보수적 진입 필터링"}
                })
                
        # 가설 B: 과매도 매수 진입 후 반등 실패로 인한 손실이 많은 경우 -> 손절선(stop_loss) 또는 RSIWindow 최적화
        if len(low_rsi_losses) / len(regime_matches) >= 0.4:
            fact = f"손실 거래의 {int(len(low_rsi_losses)/len(regime_matches)*100)}%가 과매도(RSI < 35) 상태에서 진입 후 발생했습니다."
            insights.append({
                "category": "STOP_LOSS",
                "fact_summary": fact,
                "details": {"low_rsi_losses_count": len(low_rsi_losses), "total_losses": len(regime_matches)}
            })
            
            if "stop_loss" in curr_params:
                # 손절폭을 늘려서 숨쉴 공간을 제공하거나, 반대로 좁혀서 빠른 탈출 도모
                mutated1 = curr_params.copy()
                mutated1["stop_loss"] = curr_params["stop_loss"] - 1.0  # -3.0% -> -4.0%
                mutations.append({
                    "proposed_params": mutated1,
                    "mutation_trace": {"stop_loss": [curr_params["stop_loss"], mutated1["stop_loss"]], "reason": "과매도 지연 반등 대비 손절폭 확대"}
                })

        # 매칭되는 조건이 없어도 기본 One Parameter Mutation 제안 풀백 제공
        if not mutations:
            # RSI Window를 미세 튜닝하는 기본 제안
            if "rsi_window" in curr_params:
                mutated = curr_params.copy()
                mutated["rsi_window"] = curr_params["rsi_window"] + 2
                mutations.append({
                    "proposed_params": mutated,
                    "mutation_trace": {"rsi_window": [curr_params["rsi_window"], mutated["rsi_window"]], "reason": "시장 지연 반응 대응을 위한 윈도우 스무딩"}
                })
                
        # 5. 발굴된 인사이트 DB 저장 및 제안 리스트 리턴
        from src.engine.portfolio import get_integer_portfolio_id
        pid = get_integer_portfolio_id(portfolio_id)
        for ins in insights:
            async with get_db_conn(self.db_path) as db:
                await db.execute('''
                    INSERT INTO strategy_insights (portfolio_id, strategy_id, category, fact_summary, details_json)
                    VALUES (?, ?, ?, ?, ?)
                ''', (pid, strategy_id, ins["category"], ins["fact_summary"], json.dumps(ins["details"])))
                await db.commit()
                
        # Shadow Backtest로 흘려보낼 최종 제안 변이 정보 취합
        results = []
        for mut in mutations:
            results.append({
                "strategy_id": strategy_id,
                "portfolio_id": portfolio_id,
                "original_params": curr_params,
                "proposed_params": mut["proposed_params"],
                "mutation_trace": mut["mutation_trace"]
            })
            
        logger.info(f"[Analyzer] 가설 도출 완료. 제안 후보 수: {len(results)}")
        return results

    async def get_current_params(self, strategy_id: str) -> Optional[Dict[str, Any]]:
        async with get_db_conn(self.db_path) as db:
            async with db.execute(
                "SELECT current_params FROM strategy_versions WHERE strategy_id = ?",
                (strategy_id,)
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    return {"current_params": json.loads(row[0])}
        return None
