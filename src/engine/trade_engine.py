import asyncio
import time
import hashlib
import json
import math
from typing import List, Dict, Optional, Any
from src.engine.utils.telemetry import get_logger

logger = get_logger(__name__)
from src.engine.candles import CandleGenerator, Candle
from src.engine.strategy import BaseStrategy, StrategyType, TradeSignal, StrategyResult
from src.engine.strategy_host import StrategyHost
from src.engine.market_data_context import MarketDataContext
from src.database.repository import BaseMarketDataRepository, SqliteMarketDataRepository
from src.engine.girs_types import FeatureSnapshot
from src.config.manager import ConfigManager

# 전략 레지스트리에 등록된 모든 전략을 불러오기 위해 임포트 (loader에 의해 로드됨)
from src.engine import strategies

class TradeEngine:
    def __init__(self, exchange: str, symbol: str, strategies: List[BaseStrategy], on_status_callback: Optional[Any] = None, market_data_repo: Optional[BaseMarketDataRepository] = None):
        self.exchange = exchange
        self.symbol = symbol
        self.strategies = strategies
        self.on_status_callback = on_status_callback
        self.market_data_repo = market_data_repo or SqliteMarketDataRepository()
        self.last_tick = None
        self.config_manager = ConfigManager("config/settings.yaml")
        
        # 전략들을 호스트로 래핑
        self.hosts = [StrategyHost(s, exchange, symbol, s.params.get('interval', 60)) for s in strategies]
        
        # 전략 분류 (호스트 기준)
        self.entry_hosts = [h for h in self.hosts if h.strategy.type in [StrategyType.ENTRY, StrategyType.BOTH]]
        self.exit_hosts = [h for h in self.hosts if h.strategy.type in [StrategyType.EXIT, StrategyType.BOTH]]
        
        # 전략들이 요구하는 모든 인터벌 추출 및 Context 모듈 초기화
        self.intervals = list(set(s.params.get('interval', 60) for s in strategies))
        if not self.intervals:
            self.intervals = [60] # 기본값
            
        self.contexts: Dict[int, MarketDataContext] = {
            interval: MarketDataContext(exchange, symbol, interval) for interval in self.intervals
        }
        self.candle_gen = CandleGenerator(intervals=self.intervals)

    async def warm_up(self, db_path: Optional[str] = None, lookback_ticks: int = 1000, lookback_candles: int = 100):
        """리포지토리를 사용해 과거 캔들 또는 틱 데이터를 읽어와 전략의 상태를 복구합니다."""
        try:
            # 1. 먼저 캔들 테이블에서 데이터 시도
            has_candle_data = False
            for interval in self.intervals:
                # get_candles 메서드를 사용해 이미 지표 연산까지 처리된 캔들 데이터를 획득
                candles_data = await self.market_data_repo.get_candles(
                    exchange=self.exchange,
                    symbol=self.symbol,
                    interval=interval,
                    limit=lookback_candles
                )
                if candles_data:
                    has_candle_data = True
                    context = self.contexts[interval]
                    # 시간순(과거 -> 최신) 주입
                    for j, row in enumerate(candles_data):
                        # DB에서 반환한 딕셔너리를 Candle 객체로 변환
                        candle = Candle(
                            exchange=self.exchange,
                            symbol=self.symbol,
                            interval=interval,
                            timestamp=row['timestamp'],
                            open=row['open'],
                            high=row['high'],
                            low=row['low'],
                            close=row['close'],
                            volume=row['volume'],
                            is_closed=True
                        )
                        context.add_candle(candle)
                        
                        # 20건마다 아주 짧게 쉬어줌 (UI 응답성 최우선)
                        if j % 20 == 0:
                            await asyncio.sleep(0.001)
                await asyncio.sleep(0.01) # 인터벌 사이 휴식
            
            # 2. 캔들 데이터가 없는 경우에만 틱 데이터로 워밍업 (Fallback)
            if not has_candle_data:
                trades_data = await self.market_data_repo.get_recent_trades(
                    exchange=self.exchange,
                    symbol=self.symbol,
                    limit=lookback_ticks
                )
                if trades_data:
                    # get_recent_trades는 최신 -> 과거 순이므로 시간순 처리를 위해 reversed 사용
                    for i, row in enumerate(reversed(trades_data)):
                        await self.process_tick({
                            'trade_price': row['trade_price'],
                            'trade_volume': row['trade_volume'],
                            'ask_bid': row['ask_bid'],
                            'trade_timestamp': row['trade_timestamp']
                        }, None, is_warmup=True)
                        
                        # 20건마다 제어권 양보
                        if i % 20 == 0:
                            await asyncio.sleep(0.001)
            
            logger.debug(f"{self.symbol} warmed up (Source: {'Candles' if has_candle_data else 'Ticks'}).")
        except Exception as e:
            logger.warning(f"{self.symbol} warmup failed: {e}")

    async def process_tick(self, tick: Dict, portfolio_manager: Any, is_warmup: bool = False) -> tuple[List[TradeSignal], List[Candle]]:
        """실시간 틱을 처리하고, 완성된 캔들이 있을 경우 전략을 실행하여 신호와 캔들을 반환합니다."""
        self.last_tick = tick
        closed_candles = self.candle_gen.process_tick(
            self.exchange,
            self.symbol, 
            tick['trade_price'], 
            tick['trade_volume'], 
            tick['ask_bid'], 
            tick['trade_timestamp']
        )
        
        signals = []
        for candle in closed_candles:
            # 완성된 캔들을 컨텍스트에 갱신하여 지표 계산 캐시 무효화 및 데이터 누적
            context = self.contexts[candle.interval]
            context.add_candle(candle)
            
            # 1. 매수 전략 체크
            for host in self.entry_hosts:
                if host.interval == candle.interval:
                    action_result = await host.execute(context, portfolio_manager)
                    if not is_warmup and action_result:
                        # 브로드캐스트 및 시그널 가공 처리
                        await self._handle_strategy_result(host, candle, action_result, signals)
            
            # 2. 매도 전략 체크
            for host in self.exit_hosts:
                if host.interval == candle.interval:
                    action_result = await host.execute(context, portfolio_manager)
                    if not is_warmup and action_result:
                        await self._handle_strategy_result(host, candle, action_result, signals)
                        
        return signals, closed_candles

    async def _handle_strategy_result(self, host: StrategyHost, candle: Candle, action_result: Any, signals: List[TradeSignal]):
        """전략 판단 결과물로부터 신호를 빌드하고 UI로 실시간 브로드캐스트합니다."""
        context_data = self.contexts[candle.interval]
        
        # --- 실시간 상태 브로드캐스트 (Audit Log) ---
        if self.on_status_callback:
            # 하위 호환성을 위해 사전 선언한 지표 딕셔너리를 포함
            indicators_data = {}
            required = getattr(host.strategy, 'required_indicators', [])
            for ind in required:
                window = host.params.get('rsi_window', host.params.get('sma_window', 20))
                indicators_data[ind] = context_data.get_indicator(ind, window=window)
                
            status_info = {
                "type": "strategy_status",
                "strategy_id": host.strategy.id,
                "exchange": self.exchange,
                "symbol": self.symbol,
                "indicators": indicators_data,
                "last_action": action_result.action if hasattr(action_result, 'action') else str(action_result),
                "timestamp": int(time.time() * 1000)
            }
            asyncio.create_task(self.on_status_callback(status_info))

        # --- 신호 가공 및 패킹 ---
        if isinstance(action_result, StrategyResult):
            if action_result.action in ['BUY', 'SELL']:
                signals.append(TradeSignal(
                    exchange=self.exchange,
                    symbol=self.symbol,
                    action=action_result.action,
                    price=action_result.price or candle.close,
                    reason=action_result.reason or f"Strategy {host.strategy.id} signal",
                    interval=candle.interval,
                    strategy_id=host.strategy.id,
                    context=action_result.context
                ))
        elif action_result in ['BUY', 'SELL']:
            signals.append(TradeSignal(
                exchange=self.exchange,
                symbol=self.symbol,
                action=action_result,
                price=candle.close,
                reason=f"Strategy {host.strategy.id} legacy signal",
                interval=candle.interval,
                strategy_id=host.strategy.id
            ))

    def update_strategy_params(self, strategy_id: str, params: Dict):
        """등록된 특정 전략의 파라미터를 업데이트합니다."""
        for strategy in self.strategies:
            if strategy.__class__.__name__.lower() == strategy_id.lower():
                strategy.update_params(params)

    async def capture_feature_snapshot(self, proposal_id: str, strategy_id: str, exchange: str, symbol: str, proposal_type: str) -> FeatureSnapshot:
        """
        현재 시점의 실시간 시세 데이터, 지표 데이터 및 거래소 시장 특성을 취합하여 FeatureSnapshot DTO를 생성합니다.
        """
        import numpy as np
        current_time_ms = int(time.time() * 1000)
        
        # 1. settings.yaml 설정 로드
        system_config = self.config_manager.get("system", {})
        freshness_ttl_config = system_config.get("freshness_ttl", {})
        
        # 2. 리포지토리에서 최근 틱 조회 (유동성 프록시 계산 목적)
        recent_ticks = []
        try:
            recent_ticks = await self.market_data_repo.get_recent_trades(
                exchange=exchange,
                symbol=symbol,
                limit=1000
            )
        except Exception as e:
            logger.error(f"[TradeEngine] 최근 틱 조회 중 오류: {e}")
            
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
            if self.last_tick:
                latest_tick_price = self.last_tick.get('trade_price', 0.0)
                latest_tick_time = self.last_tick.get('trade_timestamp', 0)
                
        # 4. 연령(Age) 계산
        trade_age_ms = 0
        if latest_tick_time > 0:
            trade_age_ms = current_time_ms - latest_tick_time
            
        target_interval = 60
        for host in self.hosts:
            if host.strategy.id.lower() == strategy_id.lower() or host.strategy.__class__.__name__.lower() == strategy_id.lower():
                target_interval = host.interval
                break
                
        context = self.contexts.get(target_interval)
        indicator_age_ms = 999999999
        
        close_price = latest_tick_price
        returns = 0.0
        volatility = 0.0
        rsi_val = 50.0
        macd_val = 0.0
        
        if context and context.candles:
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
            
        if not context or not context.candles:
            is_fresh = False
            stale_reasons.append("NO_CANDLES")
        elif indicator_age_ms > indicator_ttl * 1000:
            is_fresh = False
            stale_reasons.append("INDICATOR_STALE")
            
        stale_reason = ",".join(stale_reasons) if stale_reasons else ""
        
        # 6. 시장 메타데이터
        session_state = 'regular_trading'
        if market_type == 'stock':
            kis_config = self.config_manager.get("exchanges.kis", {})
            market_hours = kis_config.get("market_hours", {"start_time": "09:00", "end_time": "15:30"})
            start_str = market_hours.get("start_time", "09:00")
            end_str = market_hours.get("end_time", "15:30")
            from datetime import datetime
            now_time_str = datetime.now().strftime("%H:%M")
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
        
        # 8. 이중 해시 헬퍼 함수
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
            generated_at=time.time(),
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


