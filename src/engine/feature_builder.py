# -*- coding: utf-8 -*-

import time
import json
import math
import hashlib
from dataclasses import dataclass
from typing import List, Dict, Any, Optional, Tuple
from src.engine.girs_types import FeatureSnapshot
from src.engine.utils.telemetry import get_logger

logger = get_logger("feature_builder")


class Clock:
    """시간 흐름 제어 및 테스트 모킹을 위한 클록 클래스"""
    def __init__(self, start_time: Optional[float] = None):
        self._current_time = start_time if start_time is not None else time.time()

    def now(self) -> float:
        return self._current_time

    def set_time(self, t: float):
        self._current_time = t


@dataclass
class FeatureBuildRequest:
    """실시간 피처 조립 요청을 위한 파라미터 캡슐화 DTO"""
    hosts: List[Any]
    contexts: Dict[int, Any]
    last_tick: Optional[Dict[str, Any]] = None


class FeatureBuilder:
    """
    실시간 GIRS 및 GNN용 FeatureSnapshot을 조립하고 검증하는 전담 모듈.
    SQLite나 WebSocket 연결에 직접 의존하지 않고, 주입받은 Repository 인터페이스와 DTO를 활용합니다.
    """
    def __init__(
        self,
        market_data_repo: Any,
        config_manager: Any = None,
        clock: Optional[Clock] = None
    ):
        self.market_data_repo = market_data_repo
        self.config_manager = config_manager
        self.clock = clock if clock is not None else Clock()

    async def capture_feature_snapshot(
        self,
        proposal_id: str,
        strategy_id: str,
        exchange: str,
        symbol: str,
        proposal_type: str,
        request: FeatureBuildRequest
    ) -> FeatureSnapshot:
        """
        주어진 시장 정보 및 전략 호스트, 최근 거래 이력 등을 수집하여
        2-Stage 검증 데이터와 해시 식별자가 포함된 FeatureSnapshot DTO를 조립합니다.
        """
        import numpy as np
        current_time_ms = int(self.clock.now() * 1000)

        # 1. settings.yaml 설정 로드 (config_manager를 통해)
        freshness_ttl_config = {}
        system_config = {}
        if self.config_manager:
            try:
                system_config = self.config_manager.get("system", {})
                freshness_ttl_config = system_config.get("freshness_ttl", {})
            except Exception as e:
                logger.warning(f"Failed to load settings from config_manager: {e}")

        # 2. 리포지토리 인터페이스를 사용해 최근 틱 조회 (유동성 프록시 계산)
        recent_ticks = []
        try:
            recent_ticks = await self.market_data_repo.get_recent_trades(
                exchange=exchange,
                symbol=symbol,
                limit=1000
            )
        except Exception as e:
            logger.error(f"[FeatureBuilder] 최근 틱 조회 중 오류: {e}")

        # 3. 유동성 프록시 계산
        volume_20m = 0.0
        value_20m = 0.0
        tps_20m = 0.0
        idle_time_s = 0.0

        latest_tick_price = 0.0
        latest_tick_time = 0

        if recent_ticks:
            latest_tick = recent_ticks[0]
            latest_tick_price = latest_tick.get('trade_price', 0.0)
            latest_tick_time = latest_tick.get('trade_timestamp', 0)

            # 20분(1200초 = 1,200,000 ms) 이내의 틱 필터링
            cutoff_time = current_time_ms - 1200000
            ticks_20m = [t for t in recent_ticks if t.get('trade_timestamp', 0) >= cutoff_time]

            if ticks_20m:
                volume_20m = sum(t.get('trade_volume', 0.0) for t in ticks_20m)
                value_20m = sum(t.get('trade_volume', 0.0) * t.get('trade_price', 0.0) for t in ticks_20m)
                tps_20m = len(ticks_20m) / 1200.0

                if len(ticks_20m) >= 2:
                    intervals = []
                    for i in range(len(ticks_20m) - 1):
                        diff = (ticks_20m[i].get('trade_timestamp', 0) - ticks_20m[i+1].get('trade_timestamp', 0)) / 1000.0
                        intervals.append(diff)
                    idle_time_s = sum(intervals) / len(intervals)
                else:
                    idle_time_s = (current_time_ms - latest_tick_time) / 1000.0
            else:
                idle_time_s = 1200.0
        else:
            idle_time_s = 1200.0
            if request.last_tick:
                latest_tick_price = request.last_tick.get('trade_price', 0.0)
                latest_tick_time = request.last_tick.get('trade_timestamp', 0)

        # 4. 연령(Age) 계산
        trade_age_ms = 0
        if latest_tick_time > 0:
            trade_age_ms = current_time_ms - latest_tick_time

        # 대상 호스트의 인터벌 찾기
        target_interval = 60
        for host in request.hosts:
            host_strategy = getattr(host, 'strategy', None)
            if host_strategy:
                host_strategy_id = getattr(host_strategy, 'id', host_strategy.__class__.__name__)
                if host_strategy_id.lower() == strategy_id.lower() or host_strategy.__class__.__name__.lower() == strategy_id.lower():
                    target_interval = getattr(host, 'interval', 60)
                    break

        context = request.contexts.get(target_interval)
        indicator_age_ms = 999999999

        close_price = latest_tick_price
        returns = 0.0
        volatility = 0.0
        rsi_val = 50.0
        macd_val = 0.0

        if context and getattr(context, 'candles', None):
            latest_candle = context.candles[-1]
            indicator_age_ms = current_time_ms - (latest_candle.timestamp * 1000)
            if close_price == 0.0:
                close_price = latest_candle.close

            if latest_candle.open > 0:
                returns = (latest_candle.close - latest_candle.open) / latest_candle.open

            closes = [c.close for c in context.candles[-20:]]
            if len(closes) >= 2:
                mean_close = sum(closes) / len(closes)
                variance = sum((x - mean_close) ** 2 for x in closes) / len(closes)
                std_close = variance ** 0.5
                volatility = std_close / mean_close if mean_close > 0 else 0.0

            try:
                rsi_res = context.get_indicator('rsi', window=14)
                if rsi_res is not None:
                    if isinstance(rsi_res, (list, np.ndarray)) and len(rsi_res) > 0:
                        rsi_val = float(rsi_res[-1])
                    else:
                        rsi_val = float(rsi_res)
            except Exception:
                pass

            try:
                macd_res = context.get_indicator('macd')
                if macd_res is not None:
                    macd_line = macd_res.get('macd')
                    if macd_line is not None:
                        if isinstance(macd_line, (list, np.ndarray)) and len(macd_line) > 0:
                            macd_val = float(macd_line[-1])
                        else:
                            macd_val = float(macd_line)
            except Exception:
                pass

        # 5. Freshness 가드 판정
        market_type = 'stock' if exchange.lower() in ('kis', 'shinhan') else 'crypto'
        ttl_settings = freshness_ttl_config.get(market_type, {})
        trade_ttl = ttl_settings.get('trade', 10)
        indicator_ttl = ttl_settings.get('indicator', 60)

        is_fresh = True
        stale_reasons = []

        if latest_tick_time == 0:
            is_fresh = False
            stale_reasons.append("NO_TICK_RECEIVED")
        elif trade_age_ms > trade_ttl * 1000:
            is_fresh = False
            stale_reasons.append("TICK_TTL_EXCEEDED")

        if not context or not getattr(context, 'candles', None):
            is_fresh = False
            stale_reasons.append("NO_CANDLES")
        elif indicator_age_ms > indicator_ttl * 1000:
            is_fresh = False
            stale_reasons.append("INDICATOR_STALE")

        stale_reason = ",".join(stale_reasons) if stale_reasons else ""

        # 6. 시장 메타데이터 판정
        session_state = 'regular_trading'
        if market_type == 'stock':
            kis_config = {}
            if self.config_manager:
                try:
                    kis_config = self.config_manager.get("exchanges.kis", {})
                except Exception:
                    pass
            market_hours = kis_config.get("market_hours", {"start_time": "09:00", "end_time": "15:30"})
            start_str = market_hours.get("start_time", "09:00")
            end_str = market_hours.get("end_time", "15:30")
            
            # clock의 현재 날짜를 기반으로 시간 추출
            from datetime import datetime, timezone, timedelta
            # Seoul 시간대 적용 (+9h)
            utc_now = datetime.fromtimestamp(self.clock.now(), tz=timezone.utc)
            seoul_now = utc_now.astimezone(timezone(timedelta(hours=9)))
            now_time_str = seoul_now.strftime("%H:%M")

            if start_str <= now_time_str <= end_str:
                session_state = 'regular_trading'
            else:
                session_state = 'closed'
        else:
            session_state = '24h'

        vol_threshold = 0.01 if market_type == 'stock' else 0.02
        volatility_regime = 'high' if volatility > vol_threshold else 'low'

        liq_threshold = 100_000_000 if market_type == 'stock' else 50_000_000
        liquidity_regime = 'low' if value_20m < liq_threshold else 'high'

        tick_size = 0.1 if market_type == 'crypto' else 1.0
        price_limit = 0.0 if market_type == 'crypto' else 0.3
        fee_model = 'fixed'
        slippage_model = 'percentage'

        # 7. 피처 딕셔너리 구성
        price_features = {
            "close": float(close_price),
            "returns": float(returns),
            "volatility": float(volatility)
        }
        liquidity_features = {
            "spread": 0.0,
            "depth": 0.0,
            "imbalance": 0.0,
            "volume": float(volume_20m),
            "value": float(value_20m),
            "tps": float(tps_20m),
            "idle_time": float(idle_time_s)
        }
        regime_features = {
            "regime_index": float(rsi_val > 50.0),
            "rsi": float(rsi_val),
            "macd": float(macd_val)
        }

        # 8. 이중 해시 연산
        def clean_val(v):
            if isinstance(v, float):
                if math.isnan(v):
                    return "NaN"
                if math.isinf(v):
                    return "Infinity"
                return round(v, 6)
            elif isinstance(v, dict):
                return {k: clean_val(val) for k, val in v.items()}
            elif isinstance(v, list):
                return [clean_val(val) for val in v]
            return v

        vector_dict = {
            "price_features": price_features,
            "liquidity_features": liquidity_features,
            "regime_features": regime_features
        }
        feature_vector_hash = hashlib.sha256(json.dumps(clean_val(vector_dict), sort_keys=True).encode('utf-8')).hexdigest()

        snapshot_dict = {
            "price_features": price_features,
            "liquidity_features": liquidity_features,
            "regime_features": regime_features,
            "schema_version": "1.0",
            "exchange": exchange,
            "market_type": market_type,
            "session_state": session_state,
            "volatility_regime": volatility_regime,
            "liquidity_regime": liquidity_regime,
            "tick_size": tick_size,
            "price_limit": price_limit,
            "fee_model": fee_model,
            "slippage_model": slippage_model,
            "trade_age_ms": int(trade_age_ms),
            "orderbook_age_ms": 0,
            "indicator_age_ms": int(indicator_age_ms) if indicator_age_ms != 999999999 else -1,
            "is_fresh": is_fresh,
            "stale_reason": stale_reason,
            "snapshot_version": "1.0",
            "orderbook_available": False
        }
        snapshot_hash = hashlib.sha256(json.dumps(clean_val(snapshot_dict), sort_keys=True).encode('utf-8')).hexdigest()

        # 9. FeatureSnapshot DTO 생성 및 반환
        return FeatureSnapshot(
            price_features=price_features,
            liquidity_features=liquidity_features,
            regime_features=regime_features,
            schema_version="1.0",
            feature_hash=snapshot_hash,
            generated_at=self.clock.now(),
            exchange=exchange,
            market_type=market_type,
            session_state=session_state,
            volatility_regime=volatility_regime,
            liquidity_regime=liquidity_regime,
            tick_size=tick_size,
            price_limit=price_limit,
            fee_model=fee_model,
            slippage_model=slippage_model,
            trade_age_ms=int(trade_age_ms),
            orderbook_age_ms=0,
            indicator_age_ms=int(indicator_age_ms) if indicator_age_ms != 999999999 else -1,
            is_fresh=is_fresh,
            stale_reason=stale_reason,
            snapshot_version="1.0",
            snapshot_hash=snapshot_hash,
            feature_vector_hash=feature_vector_hash,
            orderbook_available=False
        )
