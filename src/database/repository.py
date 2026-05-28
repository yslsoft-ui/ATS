import abc
import time
from typing import List, Dict, Optional, Any
from src.database.connection import get_db_conn
from src.engine.candles import CandleGenerator
from src.engine.indicators import IndicatorCalculator
from src.engine.utils.telemetry import get_logger

logger = get_logger(__name__)

class BaseMarketDataRepository(abc.ABC):
    """
    시장 데이터(Candle, Trade)를 조회하기 위한 추상 저장소 인터페이스(Seam)입니다.
    """
    @abc.abstractmethod
    async def get_candles(
        self,
        exchange: str,
        symbol: str,
        interval: int = 60,
        limit: int = 500,
        start_ts: Optional[int] = None,
        end_ts: Optional[int] = None,
        system_app_state_system: Optional[Any] = None
    ) -> List[Dict[str, Any]]:
        """
        요청된 조건에 부합하고 보조지표가 완벽하게 계산된 캔들 데이터 리스트를 반환합니다.
        """
        pass

    @abc.abstractmethod
    async def get_recent_trades(
        self,
        exchange: str,
        symbol: str,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        최근 체결(Trade) 데이터 리스트를 반환합니다.
        """
        pass

    @abc.abstractmethod
    async def get_restored_candles(
        self,
        exchange: Optional[str] = None,
        symbol: Optional[str] = None,
        limit_minutes: int = 1440
    ) -> List[Dict[str, Any]]:
        """
        DB에 누락되었으나 trades 틱 테이블을 통해 복원 가능한 1분봉 캔들 리스트를 반환합니다.
        """
        pass

    @abc.abstractmethod
    async def warm_up_kis_cache(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        0으로 마비된 KIS 실시간 캐시 복구를 위해 DB에서 최근 가격 및 변동 지표 데이터를 획득합니다.
        """
        pass




class SqliteMarketDataRepository(BaseMarketDataRepository):
    """
    실제 SQLite 데이터베이스 및 실시간 수집기의 메모리 상태를 연동하는 실거래용 어댑터입니다.
    """
    async def get_candles(
        self,
        exchange: str,
        symbol: str,
        interval: int = 60,
        limit: int = 500,
        start_ts: Optional[int] = None,
        end_ts: Optional[int] = None,
        system_app_state_system: Optional[Any] = None
    ) -> List[Dict[str, Any]]:
        async with get_db_conn() as db:
            all_candles = []
            
            # 1. 고분봉(60초 이상) 처리 최적화: 1분봉 기반 집계
            if interval >= 60 and interval % 60 == 0:
                if interval == 60:
                    # 1분봉 요청 시에는 조립 루프 없이 그대로 다이렉트 반환 (데이터 왜곡 방지 및 성능 극대화)
                    query = "SELECT * FROM candles WHERE exchange = ? AND symbol = ? AND interval = 60"
                    params = [exchange, symbol]
                    if start_ts and end_ts:
                        query += " AND timestamp BETWEEN ? AND ?"
                        params.extend([start_ts, end_ts])
                    query += " ORDER BY timestamp DESC LIMIT ?"
                    params.append(limit)
                    
                    async with db.execute(query, params) as cursor:
                        rows = await cursor.fetchall()
                        if rows:
                            raw_data = sorted([dict(r) for r in rows], key=lambda x: x['timestamp'])
                            for r in raw_data:
                                all_candles.append({
                                    'timestamp': r['timestamp'],
                                    'open': r['open'],
                                    'high': r['high'],
                                    'low': r['low'],
                                    'close': r['close'],
                                    'volume': r['volume']
                                })
                            
                            # 🚨 1분봉 진행 중인 메모리 속 미완성 캔들(currentCandle)을 실시간으로 병합 결합!
                            if system_app_state_system:
                                target_collector = None
                                for col in getattr(system_app_state_system, 'collectors', []):
                                    if getattr(col, 'exchange', '') == exchange:
                                        target_collector = col
                                        break
                                
                                if target_collector and hasattr(target_collector, 'candle_generator'):
                                    current = target_collector.candle_generator.get_current_candle(symbol, 60)
                                    if current and (not all_candles or all_candles[-1]['timestamp'] < current.timestamp):
                                        all_candles.append({
                                            'timestamp': current.timestamp,
                                            'open': current.open,
                                            'high': current.high,
                                            'low': current.low,
                                            'close': current.close,
                                            'volume': current.volume
                                        })
                            
                            # 💡 DB의 candles 테이블에 저장된 캔들 개수가 부족하거나 듬성듬성한 경우, trades 테이블의 최근 틱 데이터를 조회해 촘촘한 캔들로 보강합니다.
                            if len(all_candles) < limit:
                                start_time_ms = int((time.time() - (limit * interval * 1.5)) * 1000)
                                
                                tick_query = "SELECT * FROM trades WHERE exchange = ? AND symbol = ? AND trade_timestamp >= ?"
                                tick_params = [exchange, symbol, start_time_ms]
                                
                                tick_query += " ORDER BY trade_timestamp DESC LIMIT ?"
                                tick_params.append(30000) # 최대 30,000틱까지 안전 수용
                                
                                async with db.execute(tick_query, tick_params) as tick_cursor:
                                    tick_rows = await tick_cursor.fetchall()
                                    if tick_rows:
                                        ticks = sorted([dict(tr) for tr in tick_rows], key=lambda x: x['trade_timestamp'])
                                        temp_generator = CandleGenerator(intervals=[interval])
                                        restored_candles = []
                                        for row in ticks:
                                            closed = temp_generator.process_tick(exchange, symbol, row['trade_price'], row['trade_volume'], row['ask_bid'], row['trade_timestamp'])
                                            for c in closed:
                                                restored_candles.append({
                                                    'timestamp': c.timestamp,
                                                    'open': c.open,
                                                    'high': c.high,
                                                    'low': c.low,
                                                    'close': c.close,
                                                    'volume': c.volume
                                                })
                                        current = temp_generator.get_current_candle(symbol, interval)
                                        if current and (not restored_candles or restored_candles[-1]['timestamp'] < current.timestamp):
                                            restored_candles.append({
                                                'timestamp': current.timestamp,
                                                'open': current.open,
                                                'high': current.high,
                                                'low': current.low,
                                                'close': current.close,
                                                'volume': current.volume
                                            })
                                        if restored_candles:
                                            all_candles = restored_candles + all_candles
                else:
                    # 3분봉, 5분봉 등 상위 고분봉에 대해서만 1분봉을 징검다리로 삼아 정밀 병합 조립 (OHLCV 보존)
                    query = "SELECT * FROM candles WHERE exchange = ? AND symbol = ? AND interval = 60"
                    params = [exchange, symbol]
                    if start_ts and end_ts:
                        query += " AND timestamp BETWEEN ? AND ?"
                        params.extend([start_ts, end_ts])
                    query += " ORDER BY timestamp DESC LIMIT ?"
                    params.append(limit * (interval // 60) + 100)
                    
                    async with db.execute(query, params) as cursor:
                        rows = await cursor.fetchall()
                        if rows:
                            raw_data = sorted([dict(r) for r in rows], key=lambda x: x['timestamp'])
                            aggregated = {}
                            for r in raw_data:
                                ts = r['timestamp']
                                bucket = (ts // interval) * interval
                                
                                r_open = r['open']
                                r_high = r['high']
                                r_low = r['low']
                                r_close = r['close']
                                r_vol = r['volume']
                                
                                if bucket not in aggregated:
                                    aggregated[bucket] = {
                                        'timestamp': bucket,
                                        'open': r_open,
                                        'high': r_high,
                                        'low': r_low,
                                        'close': r_close,
                                        'volume': r_vol
                                    }
                                else:
                                    candle = aggregated[bucket]
                                    candle['high'] = max(candle['high'], r_high)
                                    candle['low'] = min(candle['low'], r_low)
                                    candle['close'] = r_close
                                    candle['volume'] += r_vol
                            
                            # 🚨 실시간 메모리 상에 기동 중인 미완성 1분봉까지 마지막 버킷에 정확하게 결합
                            if system_app_state_system:
                                target_collector = None
                                for col in getattr(system_app_state_system, 'collectors', []):
                                    if getattr(col, 'exchange', '') == exchange:
                                        target_collector = col
                                        break
                                
                                if target_collector and hasattr(target_collector, 'candle_generator'):
                                    current = target_collector.candle_generator.get_current_candle(symbol, 60)
                                    if current:
                                        ts = current.timestamp
                                        bucket = (ts // interval) * interval
                                        
                                        if bucket not in aggregated:
                                            aggregated[bucket] = {
                                                'timestamp': bucket,
                                                'open': current.open,
                                                'high': current.high,
                                                'low': current.low,
                                                'close': current.close,
                                                'volume': current.volume
                                            }
                                        else:
                                            candle = aggregated[bucket]
                                            candle['high'] = max(candle['high'], current.high)
                                            candle['low'] = min(candle['low'], current.low)
                                            candle['close'] = current.close
                                            candle['volume'] += current.volume
                                            
                            all_candles = list(aggregated.values())
            
            # 2. 저분봉(60초 미만) 또는 데이터 부족 시 틱 데이터 활용
            if not all_candles:
                needed_ticks = limit * 2 if interval < 10 else limit * 10
                query = "SELECT * FROM trades WHERE exchange = ? AND symbol = ? "
                params = [exchange, symbol]
                if start_ts and end_ts:
                    query += " AND trade_timestamp BETWEEN ? AND ?"
                    params.extend([start_ts * 1000, end_ts * 1000])
                else:
                    if exchange == 'kis':
                        query += " AND trade_timestamp > ?"
                        params.append(0)
                    else:
                        query += " AND trade_timestamp > ?"
                        params.append(int((time.time() - (limit * interval * 1.5)) * 1000))
                
                query += " ORDER BY trade_timestamp DESC LIMIT ?"
                params.append(min(needed_ticks, 30000))
                
                async with db.execute(query, params) as cursor:
                    rows = await cursor.fetchall()
                    if rows:
                        ticks = sorted([dict(r) for r in rows], key=lambda x: x['trade_timestamp'])
                        generator = CandleGenerator(intervals=[interval])
                        for row in ticks:
                            closed = generator.process_tick(exchange, symbol, row['trade_price'], row['trade_volume'], row['ask_bid'], row['trade_timestamp'])
                            for c in closed:
                                all_candles.append({'timestamp': c.timestamp, 'open': c.open, 'high': c.high, 'low': c.low, 'close': c.close, 'volume': c.volume})
                        
                        current = generator.get_current_candle(symbol, interval)
                        if current and (not all_candles or all_candles[-1]['timestamp'] < current.timestamp):
                            all_candles.append({'timestamp': current.timestamp, 'open': current.open, 'high': current.high, 'low': current.low, 'close': current.close, 'volume': current.volume})

            # 3. 결과 정제 및 지표 계산
            if all_candles:
                seen = set()
                unique_candles = []
                for c in sorted(all_candles, key=lambda x: x['timestamp']):
                    if c['timestamp'] not in seen:
                        unique_candles.append(c)
                        seen.add(c['timestamp'])
                
                unique_candles = unique_candles[-limit:]
                df = IndicatorCalculator.calculate_all_indicators(unique_candles)
                return df.replace({float('nan'): None}).to_dict(orient='records')
            
            return []

    async def get_recent_trades(
        self,
        exchange: str,
        symbol: str,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        async with get_db_conn() as db:
            query = "SELECT * FROM trades WHERE exchange = ? AND symbol = ? ORDER BY trade_timestamp DESC LIMIT ?"
            async with db.execute(query, [exchange, symbol, limit]) as cursor:
                rows = await cursor.fetchall()
                return [dict(r) for r in rows]

    async def get_restored_candles(
        self,
        exchange: Optional[str] = None,
        symbol: Optional[str] = None,
        limit_minutes: int = 1440
    ) -> List[Dict[str, Any]]:
        end_time = int(time.time())
        start_time = end_time - limit_minutes * 60
        current_minute_bucket = (end_time // 60) * 60
        
        async with get_db_conn() as db:
            # 1. DB에 존재하는 1분봉 타임스탬프 조회
            query_candles = "SELECT exchange, symbol, timestamp FROM candles WHERE interval = 60 AND timestamp BETWEEN ? AND ?"
            params_candles = [start_time, end_time]
            if exchange:
                query_candles += " AND exchange = ?"
                params_candles.append(exchange)
            if symbol:
                query_candles += " AND symbol = ?"
                params_candles.append(symbol)
                
            async with db.execute(query_candles, params_candles) as cursor:
                rows = await cursor.fetchall()
                db_timestamps = set((r[0], r[1], r[2]) for r in rows)
                
            # 2. 동일 시간대의 trades 조회
            query_trades = """
                SELECT exchange, symbol, trade_price, trade_volume, ask_bid, trade_timestamp FROM trades
                WHERE trade_timestamp BETWEEN ? AND ?
            """
            params_trades = [start_time * 1000, end_time * 1000]
            if exchange:
                query_trades += " AND exchange = ?"
                params_trades.append(exchange)
            if symbol:
                query_trades += " AND symbol = ?"
                params_trades.append(symbol)
                
            query_trades += " ORDER BY trade_timestamp ASC"
            
            async with db.execute(query_trades, params_trades) as cursor:
                rows_trades = await cursor.fetchall()
                
            if not rows_trades:
                return []
                
            # 3. 틱 데이터를 1분봉으로 조립하되 DB에 없는 경우만 추출
            restored = {}
            for r in rows_trades:
                ex, sym, price, volume, side, ts_ms = r[0], r[1], r[2], r[3], r[4], r[5]
                bucket = (ts_ms // 1000 // 60) * 60
                
                # 아직 완성되지 않은 현재 진행 중인 분봉은 누락 캔들로 판단하지 않음
                if bucket >= current_minute_bucket:
                    continue
                
                # DB에 이미 존재하는 캔들이면 복원 대상에서 제외
                if (ex, sym, bucket) in db_timestamps:
                    continue
                    
                key = (ex, sym, bucket)
                if key not in restored:
                    restored[key] = {
                        'exchange': ex,
                        'symbol': sym,
                        'timestamp': bucket,
                        'open': price,
                        'high': price,
                        'low': price,
                        'close': price,
                        'volume': volume,
                        'tick_count': 1
                    }
                else:
                    c = restored[key]
                    c['high'] = max(c['high'], price)
                    c['low'] = min(c['low'], price)
                    c['close'] = price
                    c['volume'] += volume
                    c['tick_count'] += 1
                    
            # 4. 최신순으로 정렬하여 반환
            sorted_restored = sorted(restored.values(), key=lambda x: x['timestamp'], reverse=True)
            return sorted_restored

    async def warm_up_kis_cache(self, symbol: str) -> Optional[Dict[str, Any]]:
        db_price = 0.0
        db_high = 0.0
        db_low = 0.0
        db_volume = 0.0
        db_change_rate = 0.0
        db_change_price = 0.0
        
        try:
            async with get_db_conn() as db:
                # 1. trades에서 최근 체결가 조회
                async with db.execute(
                    "SELECT trade_price FROM trades WHERE exchange = 'kis' AND symbol = ? ORDER BY trade_timestamp DESC LIMIT 1",
                    (symbol,)
                ) as cursor:
                    row = await cursor.fetchone()
                    if row:
                        db_price = row[0]
                
                # 2. candles(1분봉)에서 오늘 혹은 마지막 캔들 지표 획득
                async with db.execute(
                    "SELECT close, high, low, volume FROM candles WHERE exchange = 'kis' AND symbol = ? AND interval = 60 ORDER BY timestamp DESC LIMIT 1",
                    (symbol,)
                ) as cursor:
                    row = await cursor.fetchone()
                    if row:
                        if db_price == 0.0:
                            db_price = row[0]
                        db_high = row[1]
                        db_low = row[2]
                        db_volume = row[3]
                        
                # 3. 전일 종가와 비교하여 24h 변동률 근사치 추정
                async with db.execute(
                    "SELECT close FROM candles WHERE exchange = 'kis' AND symbol = ? AND interval = 60 AND timestamp < (SELECT COALESCE(MAX(timestamp), 0) FROM candles WHERE exchange = 'kis' AND symbol = ? AND interval = 60) - 24*3600 ORDER BY timestamp DESC LIMIT 1",
                    (symbol, symbol)
                ) as cursor:
                    row = await cursor.fetchone()
                    if row and row[0] > 0:
                        db_change_rate = (db_price - row[0]) / row[0]
                        db_change_price = db_price - row[0]

            return {
                'trade_price': db_price,
                'signed_change_rate': db_change_rate,
                'change_price': db_change_price,
                'high_price': db_high or db_price,
                'low_price': db_low or db_price,
                'acc_trade_price_24h': db_volume * db_price
            }
        except Exception as e:
            logger.warning(f"[KIS] Database warm-up failed for {symbol} in repository: {e}")
            return None




class InMemoryMarketDataRepository(BaseMarketDataRepository):
    """
    단위 테스트 및 오프라인 시뮬레이션용 초고속 인메모리 어댑터입니다.
    """
    def __init__(self):
        self.candles_store: Dict[str, List[Dict[str, Any]]] = {}
        self.trades_store: Dict[str, List[Dict[str, Any]]] = {}

    def _get_key(self, exchange: str, symbol: str) -> str:
        return f"{exchange}:{symbol}"

    def add_candle(self, exchange: str, symbol: str, candle: Dict[str, Any]):
        key = self._get_key(exchange, symbol)
        if key not in self.candles_store:
            self.candles_store[key] = []
        self.candles_store[key].append(candle)

    def add_trade(self, exchange: str, symbol: str, trade: Dict[str, Any]):
        key = self._get_key(exchange, symbol)
        if key not in self.trades_store:
            self.trades_store[key] = []
        self.trades_store[key].append(trade)

    async def get_candles(
        self,
        exchange: str,
        symbol: str,
        interval: int = 60,
        limit: int = 500,
        start_ts: Optional[int] = None,
        end_ts: Optional[int] = None,
        system_app_state_system: Optional[Any] = None
    ) -> List[Dict[str, Any]]:
        key = self._get_key(exchange, symbol)
        candles = self.candles_store.get(key, [])
        
        # 필터링
        filtered = candles
        if start_ts or end_ts:
            filtered = []
            for c in candles:
                ts = c['timestamp']
                if start_ts and ts < start_ts:
                    continue
                if end_ts and ts > end_ts:
                    continue
                filtered.append(c)

        sorted_candles = sorted(filtered, key=lambda x: x['timestamp'])
        trimmed = sorted_candles[-limit:]
        
        if trimmed:
            df = IndicatorCalculator.calculate_all_indicators(trimmed)
            return df.replace({float('nan'): None}).to_dict(orient='records')
        return []

    async def get_recent_trades(
        self,
        exchange: str,
        symbol: str,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        key = self._get_key(exchange, symbol)
        trades = self.trades_store.get(key, [])
        sorted_trades = sorted(trades, key=lambda x: x.get('trade_timestamp', 0), reverse=True)
        return sorted_trades[:limit]

    async def get_restored_candles(
        self,
        exchange: Optional[str] = None,
        symbol: Optional[str] = None,
        limit_minutes: int = 1440
    ) -> List[Dict[str, Any]]:
        return []

    async def warm_up_kis_cache(self, symbol: str) -> Optional[Dict[str, Any]]:
        return None




class SqliteTradingRepository:
    """
    [하위 호환성 자리표시자] TradingSystem 내부의 상태 관리를 위해 예약된 트레이딩 저장소 껍데기 클래스입니다.
    """
    def __init__(self, system=None):
        self.system = system
