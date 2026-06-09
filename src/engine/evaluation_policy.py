# -*- coding: utf-8 -*-

import time
from datetime import datetime, timedelta
from typing import Dict, List, Any, Tuple, Optional
from src.engine.girs_types import FeatureSnapshot
from src.config.manager import ConfigManager
from src.engine.utils.telemetry import get_logger

logger = get_logger("evaluation_policy")

def calculate_due_at(market_type: str, horizon: Dict[str, Any], current_time_s: Optional[int] = None) -> int:
    """
    거래소 시장 유형(crypto/stock) 및 Horizon 설정에 따른 평가 만기 시점(due_at)을 계산합니다.
    """
    if current_time_s is None:
        current_time_s = int(time.time())
        
    horizon_type = horizon.get("type", "elapsed")
    val = horizon.get("value")
    
    if market_type == "crypto" or horizon_type == "elapsed":
        # 코인 또는 단순 경과 시간의 경우 초 단위 단순 덧셈
        return current_time_s + int(val)
        
    # 주식 세션 기준 due_at 계산 (토/일 제외, 09:00~15:30 정규장 한정)
    dt = datetime.fromtimestamp(current_time_s)
    
    if horizon_type == "elapsed_in_session":
        # 세션 시간 적산 계산 (예: 1h = 3600초)
        seconds_to_add = int(val)
        current_dt = dt
        
        while seconds_to_add > 0:
            # 주말 건너뛰기
            if current_dt.weekday() >= 5:
                days_to_add = 7 - current_dt.weekday()
                current_dt = current_dt.replace(hour=9, minute=0, second=0, microsecond=0) + timedelta(days=days_to_add)
                continue
                
            start_time = current_dt.replace(hour=9, minute=0, second=0, microsecond=0)
            end_time = current_dt.replace(hour=15, minute=30, second=0, microsecond=0)
            
            if current_dt < start_time:
                current_dt = start_time
                
            if current_dt >= end_time:
                # 다음 영업일 장시작으로 이동
                current_dt = (current_dt + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
                continue
                
            seconds_left_today = (end_time - current_dt).total_seconds()
            
            if seconds_to_add <= seconds_left_today:
                current_dt += timedelta(seconds=seconds_to_add)
                seconds_to_add = 0
            else:
                seconds_to_add -= seconds_left_today
                current_dt = (current_dt + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
                
        return int(current_dt.timestamp())
        
    elif horizon_type == "calendar_session":
        current_dt = dt
        
        def next_trading_day(curr: datetime) -> datetime:
            nxt = curr + timedelta(days=1)
            while nxt.weekday() >= 5:
                nxt += timedelta(days=1)
            return nxt

        if val == "close":
            today_close = current_dt.replace(hour=15, minute=30, second=0, microsecond=0)
            if current_dt < today_close:
                return int(today_close.timestamp())
            else:
                nxt_day = next_trading_day(current_dt)
                return int(nxt_day.replace(hour=15, minute=30, second=0, microsecond=0).timestamp())
                
        elif val == "next_open":
            nxt_day = next_trading_day(current_dt)
            return int(nxt_day.replace(hour=9, minute=0, second=0, microsecond=0).timestamp())
            
        elif val == "3_days":
            day = current_dt
            for _ in range(3):
                day = next_trading_day(day)
            return int(day.replace(hour=15, minute=30, second=0, microsecond=0).timestamp())
            
        elif val == "7_days":
            day = current_dt
            for _ in range(7):
                day = next_trading_day(day)
            return int(day.replace(hour=15, minute=30, second=0, microsecond=0).timestamp())
            
    return current_time_s + 3600  # Fallback 1시간


class EvaluationPolicyRouter:
    """
    거래소, 시장 유형, 변동성/유동성 레짐, 세션 상태 및 freshness를 고려해
    가상 롤백 임계값을 유연하게 동적 매핑하고 판정하는 정책 분기 라우터입니다.
    """
    def __init__(self, config_manager: ConfigManager):
        self.config_manager = config_manager
        
    def get_rollback_thresholds(self, snapshot: FeatureSnapshot) -> Dict[str, float]:
        """
        FeatureSnapshot의 시장 메타데이터와 유동성 프록시를 반영하여 동적 임계치를 스케일링합니다.
        """
        # 기본값 (MDD와 ROI_GAP은 ratio(소수점) 형태 유지)
        mdd_limit = 0.05
        roi_gap_limit = -0.02
        
        market_type = snapshot.market_type or ("stock" if snapshot.exchange.lower() in ("kis", "shinhan") else "crypto")
        volatility_regime = snapshot.volatility_regime
        liquidity_regime = snapshot.liquidity_regime
        session_state = snapshot.session_state
        is_fresh = snapshot.is_fresh
        stale_reason = snapshot.stale_reason
        
        # 1. 시장 타입별 기본 임계치 적용
        if market_type == 'stock':
            mdd_limit = 0.03
            roi_gap_limit = -0.015
        else:
            mdd_limit = 0.07
            roi_gap_limit = -0.03
            
        # 2. 고변동성 레짐('high') 시 노이즈 필터링용 1.5배 완화
        if volatility_regime == 'high':
            mdd_limit *= 1.5
            roi_gap_limit *= 1.5
            
        # 3. 위험 징후(저유동성, TPS 급감, 체결 지연, Stale 등) 발생 시 대폭 강화(스케일 다운)
        tps = snapshot.liquidity_features.get("tps", 1.0)
        idle_time = snapshot.liquidity_features.get("idle_time", 0.0)
        
        system_config = self.config_manager.get("system", {})
        enable_orderbook = system_config.get("enable_orderbook_features", False)
        
        danger_triggers = []
        if liquidity_regime == 'low':
            danger_triggers.append("low_liquidity")
        if not is_fresh or stale_reason:
            danger_triggers.append("stale_data")
        if session_state == 'closed':
            danger_triggers.append("session_closed")
        if tps < 0.1:
            danger_triggers.append("very_low_tps")
        if idle_time > 60.0:
            danger_triggers.append("long_idle_time")
            
        if enable_orderbook:
            spread = snapshot.liquidity_features.get("spread", 0.0)
            if spread > 0.005:
                danger_triggers.append("wide_spread")
            
        if danger_triggers:
            # 트리거 누적당 0.2씩 축소, 최소 0.3배 한도 가드
            scale_factor = max(0.3, 1.0 - 0.2 * len(danger_triggers))
            mdd_limit *= scale_factor
            roi_gap_limit *= scale_factor
            
        return {
            "mdd_limit": mdd_limit,
            "roi_gap_limit": roi_gap_limit
        }
        
    def evaluate_virtual_rollback(self, snapshot: FeatureSnapshot, candidate_roi: float, champion_roi: float, candidate_mdd: float, champion_mdd: float) -> Tuple[bool, str]:
        """
        가상 롤백 여부를 최종 판정합니다. (모든 ROI/MDD 매개변수는 ratio 단위)
        """
        limits = self.get_rollback_thresholds(snapshot)
        roi_gap = candidate_roi - champion_roi
        
        if candidate_mdd > limits["mdd_limit"]:
            return True, f"MDD_EXCEEDED (candidate_mdd={candidate_mdd:.4f} > limit={limits['mdd_limit']:.4f})"
            
        if roi_gap < limits["roi_gap_limit"]:
            return True, f"ROI_GAP_EXCEEDED (roi_gap={roi_gap:.4f} < limit={limits['roi_gap_limit']:.4f})"
            
        return False, "NORMAL"
