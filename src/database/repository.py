import abc
import time
from typing import List, Dict, Optional, Any
from src.database.connection import get_db_conn
from src.engine.candles import CandleGenerator
from src.engine.indicators import IndicatorCalculator
from src.engine.utils.telemetry import get_logger

logger = get_logger(__name__)

class ChampionCooldownBlockedError(ValueError):
    """Champion Cooldown 미달로 인한 승격 제한 시 발생하는 예외"""
    pass


def normalize_timestamp(value) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    try:
        from datetime import datetime
        val_str = str(value).strip()
        if val_str.isdigit():
            return int(val_str)
        try:
            return int(float(val_str))
        except ValueError:
            pass
        if ' ' in val_str:
            val_str = val_str.replace(' ', 'T')
        return int(datetime.fromisoformat(val_str).timestamp() * 1000)
    except Exception:
        try:
            from datetime import datetime
            for fmt in ('%Y-%m-%d', '%Y/%m/%d'):
                try:
                    return int(datetime.strptime(val_str, fmt).timestamp() * 1000)
                except ValueError:
                    pass
        except Exception:
            pass
        logger.warning(f"Failed to normalize timestamp value: {value} (type: {type(value)})")
        return None

class BaseMarketDataRepository(abc.ABC):
    """
    시장 데이터(Candle, Trade)를 조회하기 위한 추상 저장소 인터페이스(Seam)입니다.
    """
    @abc.abstractmethod
    async def get_candles(
        self,
        exchange_id: str,
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
        exchange_id: str,
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
        exchange_id: Optional[str] = None,
        symbol: Optional[str] = None,
        limit_minutes: int = 1440
    ) -> List[Dict[str, Any]]:
        """
        DB에 누락되었으나 trades 틱 테이블을 통해 복원 가능한 1분봉 캔들 리스트를 반환합니다.
        """
        pass

    @abc.abstractmethod
    async def get_ghost_candles(
        self,
        exchange_id: Optional[str] = None,
        symbol: Optional[str] = None,
        limit_minutes: int = 1440
    ) -> List[Dict[str, Any]]:
        """
        DB의 candles 테이블에는 존재하지만, trades 틱 테이블에는 체결 틱이 0건인 고스트 캔들 리스트를 반환합니다.
        """
        pass

    @abc.abstractmethod
    async def delete_candle(
        self,
        exchange_id: str,
        symbol: str,
        interval: int,
        timestamp: int
    ) -> bool:
        """
        지정한 캔들 데이터를 DB에서 영구 삭제합니다.
        """
        pass

    @abc.abstractmethod
    async def warm_up_kis_cache(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        0으로 마비된 KIS 실시간 캐시 복구를 위해 DB에서 최근 가격 및 변동 지표 데이터를 획득합니다.
        """
        pass

    @abc.abstractmethod
    async def get_latest_closed_candle_close(
        self, 
        symbol: str, 
        exchange_id: Optional[str] = None, 
        market_type: Optional[str] = None, 
        timeframe: Optional[str] = None
    ) -> Optional[float]:
        """특정 종목의 가장 최근 확정된(closed = 1) 캔들의 종가(close)를 조회합니다."""
        pass

    @abc.abstractmethod
    async def get_candle_close_at_or_before(
        self, 
        symbol: str, 
        timestamp_ms: int, 
        exchange_id: Optional[str] = None, 
        market_type: Optional[str] = None, 
        timeframe: Optional[str] = None
    ) -> Optional[float]:
        """특정 시점(timestamp_ms)과 같거나 그 이전에 확정된(closed = 1) 캔들 중 가장 최근의 종가(close)를 조회합니다."""
        pass





class SqliteMarketDataRepository(BaseMarketDataRepository):
    """
    실제 SQLite 데이터베이스 및 실시간 수집기의 메모리 상태를 연동하는 실거래용 어댑터입니다.
    """
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path

    async def get_candles(
        self,
        exchange_id: str,
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
                    query = "SELECT * FROM candles WHERE exchange_id = ? AND symbol = ? AND interval = 60"
                    params = [exchange_id, symbol]
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
                                    if getattr(col, 'exchange_id', '') == exchange_id:
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
                                
                                tick_query = "SELECT * FROM trades WHERE exchange_id = ? AND symbol = ? AND trade_timestamp >= ?"
                                tick_params = [exchange_id, symbol, start_time_ms]
                                
                                tick_query += " ORDER BY trade_timestamp DESC LIMIT ?"
                                tick_params.append(30000) # 최대 30,000틱까지 안전 수용
                                
                                async with db.execute(tick_query, tick_params) as tick_cursor:
                                    tick_rows = await tick_cursor.fetchall()
                                    if tick_rows:
                                        ticks = sorted([dict(tr) for tr in tick_rows], key=lambda x: x['trade_timestamp'])
                                        temp_generator = CandleGenerator(intervals=[interval])
                                        restored_candles = []
                                        for row in ticks:
                                            closed = temp_generator.process_tick(exchange_id, symbol, row['trade_price'], row['trade_volume'], row['ask_bid'], row['trade_timestamp'])
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
                    query = "SELECT * FROM candles WHERE exchange_id = ? AND symbol = ? AND interval = 60"
                    params = [exchange_id, symbol]
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
                                    if getattr(col, 'exchange_id', '') == exchange_id:
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
                query = "SELECT * FROM trades WHERE exchange_id = ? AND symbol = ? "
                params = [exchange_id, symbol]
                if start_ts and end_ts:
                    query += " AND trade_timestamp BETWEEN ? AND ?"
                    params.extend([start_ts * 1000, end_ts * 1000])
                else:
                    # 최신 체결 시간 기준 롤링 윈도우 계산 (주말/장마감 후에도 정상 표시 보장 및 KIS 성능 병목 해결)
                    latest_ts_ms = None
                    try:
                        async with db.execute(
                            "SELECT MAX(trade_timestamp) FROM trades WHERE exchange_id = ? AND symbol = ?",
                            (exchange_id, symbol)
                        ) as cur:
                            row = await cur.fetchone()
                            if row and row[0]:
                                latest_ts_ms = row[0]
                    except Exception as e:
                        logger.error(f"[get_candles] Failed to fetch latest trade timestamp: {e}")
                    
                    if latest_ts_ms:
                        query += " AND trade_timestamp >= ?"
                        params.append(latest_ts_ms - (limit * interval * 2.0 * 1000))
                    else:
                        query += " AND trade_timestamp > ?"
                        params.append(int((time.time() - (limit * interval * 2.0)) * 1000))
                
                query += " ORDER BY trade_timestamp DESC LIMIT ?"
                params.append(min(needed_ticks, 30000))
                
                async with db.execute(query, params) as cursor:
                    rows = await cursor.fetchall()
                    if rows:
                        ticks = sorted([dict(r) for r in rows], key=lambda x: x['trade_timestamp'])
                        generator = CandleGenerator(intervals=[interval])
                        for row in ticks:
                            closed = generator.process_tick(exchange_id, symbol, row['trade_price'], row['trade_volume'], row['ask_bid'], row['trade_timestamp'])
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
        exchange_id: str,
        symbol: str,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        async with get_db_conn() as db:
            query = "SELECT * FROM trades WHERE exchange_id = ? AND symbol = ? ORDER BY trade_timestamp DESC LIMIT ?"
            async with db.execute(query, [exchange_id, symbol, limit]) as cursor:
                rows = await cursor.fetchall()
                return [dict(r) for r in rows]

    async def get_restored_candles(
        self,
        exchange_id: Optional[str] = None,
        symbol: Optional[str] = None,
        limit_minutes: int = 1440
    ) -> List[Dict[str, Any]]:
        end_time = int(time.time() // 60) * 60
        start_time = end_time - limit_minutes * 60
        current_minute_bucket = end_time
        
        async with get_db_conn() as db:
            # 1. DB에 존재하는 1분봉 타임스탬프 조회
            query_candles = "SELECT exchange_id, symbol, timestamp FROM candles WHERE interval = 60 AND timestamp BETWEEN ? AND ?"
            params_candles = [start_time, end_time]
            if exchange_id:
                query_candles += " AND exchange_id = ?"
                params_candles.append(exchange_id)
            if symbol:
                query_candles += " AND symbol = ?"
                params_candles.append(symbol)
                
            async with db.execute(query_candles, params_candles) as cursor:
                rows = await cursor.fetchall()
                db_timestamps = set((r[0], r[1], r[2]) for r in rows)
                
            # 2. 동일 시간대의 trades 조회
            query_trades = """
                SELECT exchange_id, symbol, trade_price, trade_volume, ask_bid, trade_timestamp FROM trades
                WHERE trade_timestamp BETWEEN ? AND ?
            """
            params_trades = [start_time * 1000, end_time * 1000]
            if exchange_id:
                query_trades += " AND exchange_id = ?"
                params_trades.append(exchange_id)
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
                
                # 아직 완성되지 않은 현재 진행 중인 분봉 및 마감 직후 1분봉은 누락 캔들로 판단하지 않음
                if bucket >= current_minute_bucket - 60:
                    continue
                
                # DB에 이미 존재하는 캔들이면 복원 대상에서 제외
                if (ex, sym, bucket) in db_timestamps:
                    continue
                    
                key = (ex, sym, bucket)
                if key not in restored:
                    restored[key] = {
                        'exchange_id': ex,
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

    async def get_ghost_candles(
        self,
        exchange_id: Optional[str] = None,
        symbol: Optional[str] = None,
        limit_minutes: int = 1440
    ) -> List[Dict[str, Any]]:
        end_time = int(time.time() // 60) * 60
        start_time = end_time - limit_minutes * 60
        
        async with get_db_conn(self.db_path) as db:
            # 1. 대상 범위 내의 모든 1분봉 조회
            query_candles = """
                SELECT exchange_id, symbol, timestamp, open, high, low, close, volume 
                FROM candles 
                WHERE interval = 60 AND timestamp BETWEEN ? AND ?
            """
            params_candles = [start_time, end_time]
            if exchange_id:
                query_candles += " AND exchange_id = ?"
                params_candles.append(exchange_id)
            if symbol:
                query_candles += " AND symbol = ?"
                params_candles.append(symbol)
                
            async with db.execute(query_candles, params_candles) as cursor:
                rows_candles = await cursor.fetchall()
                
            if not rows_candles:
                return []
                
            # 2. 대상 범위 내의 trades 틱 그룹화하여 실제 체결이 발생한 분봉 버킷 구하기
            query_trades = """
                SELECT exchange_id, symbol, (trade_timestamp / 1000 / 60) * 60 AS bucket
                FROM trades
                WHERE trade_timestamp BETWEEN ? AND ?
            """
            params_trades = [start_time * 1000, end_time * 1000]
            if exchange_id:
                query_trades += " AND exchange_id = ?"
                params_trades.append(exchange_id)
            if symbol:
                query_trades += " AND symbol = ?"
                params_trades.append(symbol)
                
            query_trades += " GROUP BY exchange_id, symbol, bucket"
            
            async with db.execute(query_trades, params_trades) as cursor:
                rows_trades = await cursor.fetchall()
                valid_buckets = set((r[0], r[1], r[2]) for r in rows_trades)
                
            # 3. candles 중 valid_buckets에 없는 건들(즉, 체결 틱이 0건인 건들)만 고스트로 검출
            ghosts = []
            for r in rows_candles:
                ex, sym, ts, op, hp, lp, cp, vol = r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7]
                if (ex, sym, ts) not in valid_buckets:
                    ghosts.append({
                        'exchange_id': ex,
                        'symbol': sym,
                        'timestamp': ts,
                        'open': op,
                        'high': hp,
                        'low': lp,
                        'close': cp,
                        'volume': vol,
                        'tick_count': 0
                    })
                    
            ghosts.sort(key=lambda x: x['timestamp'], reverse=True)
            return ghosts

    async def delete_candle(
        self,
        exchange_id: str,
        symbol: str,
        interval: int,
        timestamp: int
    ) -> bool:
        async with get_db_conn(self.db_path) as db:
            query = """
                DELETE FROM candles 
                WHERE exchange_id = ? AND symbol = ? AND interval = ? AND timestamp = ?
            """
            cursor = await db.execute(query, [exchange_id, symbol, interval, timestamp])
            await db.commit()
            return cursor.rowcount > 0

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
                    "SELECT trade_price FROM trades WHERE exchange_id = 'kis' AND symbol = ? ORDER BY trade_timestamp DESC LIMIT 1",
                    (symbol,)
                ) as cursor:
                    row = await cursor.fetchone()
                    if row:
                        db_price = row[0]
                
                # 2. candles(1분봉)에서 오늘 혹은 마지막 캔들 지표 획득
                async with db.execute(
                    "SELECT close, high, low, volume FROM candles WHERE exchange_id = 'kis' AND symbol = ? AND interval = 60 ORDER BY timestamp DESC LIMIT 1",
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
                    "SELECT close FROM candles WHERE exchange_id = 'kis' AND symbol = ? AND interval = 60 AND timestamp < (SELECT COALESCE(MAX(timestamp), 0) FROM candles WHERE exchange_id = 'kis' AND symbol = ? AND interval = 60) - 24*3600 ORDER BY timestamp DESC LIMIT 1",
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

    async def get_latest_closed_candle_close(
        self, 
        symbol: str, 
        exchange_id: Optional[str] = None, 
        market_type: Optional[str] = None, 
        timeframe: Optional[str] = None
    ) -> Optional[float]:
        interval_val = None
        if timeframe:
            if timeframe.endswith('m'):
                interval_val = int(timeframe[:-1]) * 60
            elif timeframe.endswith('s'):
                interval_val = int(timeframe[:-1])
            elif timeframe.endswith('d'):
                interval_val = int(timeframe[:-1]) * 86400
            else:
                try:
                    interval_val = int(timeframe)
                except ValueError:
                    pass

        query = "SELECT close FROM candles WHERE symbol = ? AND is_closed = 1"
        params = [symbol]
        if exchange_id:
            query += " AND exchange_id = ?"
            params.append(exchange_id.lower())
        if interval_val is not None:
            query += " AND interval = ?"
            params.append(interval_val)
        
        query += " ORDER BY timestamp DESC LIMIT 1"

        try:
            async with get_db_conn(self.db_path) as db:
                async with db.execute(query, tuple(params)) as cursor:
                    row = await cursor.fetchone()
                    return row[0] if row else None
        except Exception as e:
            logger.error(f"[SqliteMarketDataRepository] Failed to query latest closed candle close for {symbol}: {e}")
            return None

    async def get_candle_close_at_or_before(
        self, 
        symbol: str, 
        timestamp_ms: int, 
        exchange_id: Optional[str] = None, 
        market_type: Optional[str] = None, 
        timeframe: Optional[str] = None
    ) -> Optional[float]:
        interval_val = None
        if timeframe:
            if timeframe.endswith('m'):
                interval_val = int(timeframe[:-1]) * 60
            elif timeframe.endswith('s'):
                interval_val = int(timeframe[:-1])
            elif timeframe.endswith('d'):
                interval_val = int(timeframe[:-1]) * 86400
            else:
                try:
                    interval_val = int(timeframe)
                except ValueError:
                    pass

        ts_s = int(timestamp_ms / 1000)

        query = "SELECT close FROM candles WHERE symbol = ? AND timestamp <= ? AND is_closed = 1"
        params = [symbol, ts_s]
        if exchange_id:
            query += " AND exchange_id = ?"
            params.append(exchange_id.lower())
        if interval_val is not None:
            query += " AND interval = ?"
            params.append(interval_val)

        query += " ORDER BY timestamp DESC LIMIT 1"

        try:
            async with get_db_conn(self.db_path) as db:
                async with db.execute(query, tuple(params)) as cursor:
                    row = await cursor.fetchone()
                    return row[0] if row else None
        except Exception as e:
            logger.error(f"[SqliteMarketDataRepository] Failed to query candle close at or before {timestamp_ms} for {symbol}: {e}")
            return None





class InMemoryMarketDataRepository(BaseMarketDataRepository):
    """
    단위 테스트 및 오프라인 시뮬레이션용 초고속 인메모리 어댑터입니다.
    """
    def __init__(self):
        self.candles_store: Dict[str, List[Dict[str, Any]]] = {}
        self.trades_store: Dict[str, List[Dict[str, Any]]] = {}

    def _get_key(self, exchange_id: str, symbol: str) -> str:
        return f"{exchange_id}:{symbol}"

    def add_candle(self, exchange_id: str, symbol: str, candle: Dict[str, Any]):
        key = self._get_key(exchange_id, symbol)
        if key not in self.candles_store:
            self.candles_store[key] = []
        self.candles_store[key].append(candle)

    def add_trade(self, exchange_id: str, symbol: str, trade: Dict[str, Any]):
        key = self._get_key(exchange_id, symbol)
        if key not in self.trades_store:
            self.trades_store[key] = []
        self.trades_store[key].append(trade)

    async def get_candles(
        self,
        exchange_id: str,
        symbol: str,
        interval: int = 60,
        limit: int = 500,
        start_ts: Optional[int] = None,
        end_ts: Optional[int] = None,
        system_app_state_system: Optional[Any] = None
    ) -> List[Dict[str, Any]]:
        key = self._get_key(exchange_id, symbol)
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
        exchange_id: str,
        symbol: str,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        key = self._get_key(exchange_id, symbol)
        trades = self.trades_store.get(key, [])
        sorted_trades = sorted(trades, key=lambda x: x.get('trade_timestamp', 0), reverse=True)
        return sorted_trades[:limit]

    async def get_restored_candles(
        self,
        exchange_id: Optional[str] = None,
        symbol: Optional[str] = None,
        limit_minutes: int = 1440
    ) -> List[Dict[str, Any]]:
        return []

    async def get_ghost_candles(
        self,
        exchange_id: Optional[str] = None,
        symbol: Optional[str] = None,
        limit_minutes: int = 1440
    ) -> List[Dict[str, Any]]:
        return []

    async def delete_candle(
        self,
        exchange_id: str,
        symbol: str,
        interval: int,
        timestamp: int
    ) -> bool:
        key = self._get_key(exchange_id, symbol)
        if key in self.candles_store:
            original_len = len(self.candles_store[key])
            self.candles_store[key] = [c for c in self.candles_store[key] if not (c.get('interval') == interval and c.get('timestamp') == timestamp)]
            return len(self.candles_store[key]) < original_len
        return False

    async def warm_up_kis_cache(self, symbol: str) -> Optional[Dict[str, Any]]:
        return None

    async def get_latest_closed_candle_close(
        self, 
        symbol: str, 
        exchange_id: Optional[str] = None, 
        market_type: Optional[str] = None, 
        timeframe: Optional[str] = None
    ) -> Optional[float]:
        keys_to_search = []
        if exchange_id:
            keys_to_search.append(self._get_key(exchange_id, symbol))
        else:
            for key in self.candles_store.keys():
                if key.endswith(f":{symbol}"):
                    keys_to_search.append(key)
        
        interval_val = None
        if timeframe:
            if timeframe.endswith('m'):
                interval_val = int(timeframe[:-1]) * 60
            elif timeframe.endswith('s'):
                interval_val = int(timeframe[:-1])
            elif timeframe.endswith('d'):
                interval_val = int(timeframe[:-1]) * 86400
            else:
                try:
                    interval_val = int(timeframe)
                except ValueError:
                    pass

        candidates = []
        for key in keys_to_search:
            candles = self.candles_store.get(key, [])
            for c in candles:
                if interval_val is not None and c.get('interval') != interval_val:
                    continue
                if c.get('is_closed', True) is False:
                    continue
                candidates.append(c)

        if not candidates:
            return None

        candidates.sort(key=lambda x: x.get('timestamp', 0))
        return candidates[-1].get('close')

    async def get_candle_close_at_or_before(
        self, 
        symbol: str, 
        timestamp_ms: int, 
        exchange_id: Optional[str] = None, 
        market_type: Optional[str] = None, 
        timeframe: Optional[str] = None
    ) -> Optional[float]:
        keys_to_search = []
        if exchange_id:
            keys_to_search.append(self._get_key(exchange_id, symbol))
        else:
            for key in self.candles_store.keys():
                if key.endswith(f":{symbol}"):
                    keys_to_search.append(key)
        
        interval_val = None
        if timeframe:
            if timeframe.endswith('m'):
                interval_val = int(timeframe[:-1]) * 60
            elif timeframe.endswith('s'):
                interval_val = int(timeframe[:-1])
            elif timeframe.endswith('d'):
                interval_val = int(timeframe[:-1]) * 86400
            else:
                try:
                    interval_val = int(timeframe)
                except ValueError:
                    pass

        ts_s = int(timestamp_ms / 1000)

        candidates = []
        for key in keys_to_search:
            candles = self.candles_store.get(key, [])
            for c in candles:
                if interval_val is not None and c.get('interval') != interval_val:
                    continue
                if c.get('is_closed', True) is False:
                    continue
                if c.get('timestamp', 0) > ts_s:
                    continue
                candidates.append(c)

        if not candidates:
            return None

        candidates.sort(key=lambda x: x.get('timestamp', 0))
        return candidates[-1].get('close')





class BaseTradingRepository(abc.ABC):
    """
    포트폴리오, 주문 내역, 알림 및 거래소 설정을 관리하기 위한 추상 저장소 인터페이스(Seam)입니다.
    """
    @abc.abstractmethod
    async def save_portfolio(self, portfolio: Any):
        pass

    @abc.abstractmethod
    async def load_portfolios(self, exclude_types: list = None) -> Dict[str, Any]:
        pass

    @abc.abstractmethod
    async def insert_order_history(self, portfolio_id: str, order: Dict[str, Any]):
        pass

    @abc.abstractmethod
    async def insert_alert(self, alert: Dict[str, Any]):
        pass

    @abc.abstractmethod
    async def load_exchange_configs(self) -> Dict[str, Dict[str, Any]]:
        pass

    @abc.abstractmethod
    async def insert_system_event(self, event_type: str, target: str, message: str, timestamp: Optional[int] = None, context: Optional[str] = None):
        pass

    @abc.abstractmethod
    async def get_system_events(self, limit: int = 20) -> List[Dict[str, Any]]:
        pass

    @abc.abstractmethod
    async def clean_old_system_events(self, retention_days: int = 7):
        pass

    @abc.abstractmethod
    async def upsert_universe_guard_state(self, exchange_id: str, market_type: str, symbol: str, status: str, blocked_reason: Optional[str], blocked_count: int, last_blocked_at: Optional[float], last_event_logged_reason: Optional[str]):
        pass

    @abc.abstractmethod
    async def get_universe_guard_state(self, exchange_id: str, market_type: str, symbol: str) -> Optional[Dict[str, Any]]:
        pass

    @abc.abstractmethod
    async def get_all_universe_guard_states(self) -> List[Dict[str, Any]]:
        pass

    @abc.abstractmethod
    async def get_proposal_evaluations(self, proposal_id: int) -> List[Dict[str, Any]]:
        pass

    @abc.abstractmethod
    async def check_and_report_previous_crash(self, target: str):
        pass

    @abc.abstractmethod
    async def get_all_strategy_versions(self) -> List[Dict[str, Any]]:
        pass

    @abc.abstractmethod
    async def get_strategy_version(self, strategy_id: str) -> Optional[Dict[str, Any]]:
        pass

    @abc.abstractmethod
    async def save_strategy_version(self, strategy_id: str, version_id: int, params: Dict[str, Any], applied_at: int, rollback_source_version: Optional[int] = None):
        pass

    @abc.abstractmethod
    async def insert_strategy_parameter_history(self, strategy_id: str, version_id: int, parent_version_id: Optional[int], old_params: Optional[str], new_params: str, proposal_id: Optional[int], is_current: int, changed_by: str, change_reason: str) -> int:
        pass

    @abc.abstractmethod
    async def get_strategy_parameter_history(self, strategy_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        pass

    @abc.abstractmethod
    async def get_strategy_parameter_version(self, strategy_id: str, version_id: int) -> Optional[Dict[str, Any]]:
        pass

    @abc.abstractmethod
    async def insert_strategy_performance_snapshot(self, snapshot_data: Dict[str, Any]):
        pass

    @abc.abstractmethod
    async def get_strategy_performance_snapshots(self, strategy_id: str, version_id: Optional[int] = None, limit: int = 100) -> List[Dict[str, Any]]:
        pass

    @abc.abstractmethod
    async def insert_strategy_proposal(self, proposal_data: Dict[str, Any]) -> int:
        pass

    @abc.abstractmethod
    async def update_strategy_proposal_status(self, proposal_id: int, status: str, outcome: Optional[str] = None, applied_at: Optional[int] = None, rolled_back_at: Optional[int] = None):
        pass

    @abc.abstractmethod
    async def get_strategy_proposal(self, proposal_id: int) -> Optional[Dict[str, Any]]:
        pass

    @abc.abstractmethod
    async def get_active_proposals(self, strategy_id: Optional[str] = None, status: Optional[str] = None) -> List[Dict[str, Any]]:
        pass

    @abc.abstractmethod
    async def get_counterfactual_targets(self, limit: int = 20) -> List[Dict[str, Any]]:
        pass

    @abc.abstractmethod
    async def update_counterfactual_metrics(self, proposal_id: int, roi: float, mdd: float, track_status: int):
        pass

    @abc.abstractmethod
    async def insert_proposal_evaluation(self, eval_data: Dict[str, Any], legacy_compat: bool = False) -> int:
        pass

    @abc.abstractmethod
    async def get_proposal_evaluation(self, proposal_id: int) -> Optional[Dict[str, Any]]:
        pass

    @abc.abstractmethod
    async def get_proposal_evaluations(self, proposal_id: int) -> List[Dict[str, Any]]:
        pass

    @abc.abstractmethod
    async def get_expired_pending_evaluations(self, now: int) -> List[Dict[str, Any]]:
        pass

    @abc.abstractmethod
    async def get_pending_evaluations_without_baseline(self, now: int) -> List[Dict[str, Any]]:
        pass

    @abc.abstractmethod
    async def update_baseline_snapshot(self, pe_id: int, value: float, ts: int, vol: int = 0):
        pass

    @abc.abstractmethod
    async def claim_evaluation(self, pe_id: int, locked_at: int) -> bool:
        pass

    @abc.abstractmethod
    async def complete_evaluation(self, pe_id: int, actual_roi: float, roi_div: float, actual_trades: int, trade_div: int, evaluated_at: int):
        pass

    @abc.abstractmethod
    async def fail_evaluation(self, pe_id: int, error_msg: str, retry_count: int, max_retries: int):
        pass

    @abc.abstractmethod
    async def get_stale_evaluating_evaluations(self, cutoff: int) -> List[Dict[str, Any]]:
        pass

    @abc.abstractmethod
    async def recover_stale_evaluation(self, pe_id: int, retry_count: int, max_retries: int, error_msg: str):
        pass

    @abc.abstractmethod
    async def get_unevaluated_applied_proposals(self) -> List[Dict[str, Any]]:
        pass

    @abc.abstractmethod
    async def get_orders_history(self, portfolio_id: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        pass

    @abc.abstractmethod
    async def get_orders_for_performance_replay(
        self, portfolio_id: str, strategy_id: str
    ) -> List[Dict[str, Any]]:
        """성능 스냅샷 리플레이를 위해 특정 포트폴리오와 전략의 모든 주문 이력을 시간 오름차순(ASC)으로 조회합니다."""
        pass

    @abc.abstractmethod
    async def get_orders_for_proposal_evaluation(
        self, portfolio_id: str, strategy_id: str, start_ts: int, end_ts: int
    ) -> List[Dict[str, Any]]:
        """제안 사후 평가를 위해 특정 기간 내에 전략이 체결한 주문 이력을 시간 오름차순(ASC)으로 조회합니다."""
        pass

    @abc.abstractmethod
    async def get_latest_feature_snapshot_for_proposal(
        self, proposal_id: str
    ) -> Optional[Dict[str, Any]]:
        """제안의 자동 승격 판단을 위해 promotion_event_log에서 가장 최근에 기록된 feature_snapshot의 JSON 파싱된 데이터를 반환합니다."""
        pass

    @abc.abstractmethod
    async def insert_planned_asset_event(
        self,
        exchange_id: str,
        symbol: str,
        event_type: str,
        scheduled_at: str,
        notice_url: Optional[str] = None
    ) -> int:
        """신규 상장/상폐 예정 이벤트를 planned_asset_events 테이블에 등록합니다."""
        pass

    @abc.abstractmethod
    async def get_planned_asset_events(
        self,
        status: Optional[str] = None,
        exchange_id: Optional[str] = None,
        event_id: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """특정 상태, 거래소, 또는 ID의 예정 이벤트 목록을 조회합니다."""
        pass

    @abc.abstractmethod
    async def get_executable_planned_events(
        self,
        before_minutes: int = 30
    ) -> List[Dict[str, Any]]:
        """실행 시점(scheduled_at - before_minutes)에 도달한 PLANNED 이벤트를 조회합니다."""
        pass

    @abc.abstractmethod
    async def update_planned_event_status(
        self,
        event_id: int,
        status: str
    ) -> bool:
        """예정 이벤트의 진행 상태를 업데이트합니다."""
        pass

    @abc.abstractmethod
    async def delete_planned_event(
        self,
        event_id: int
    ) -> bool:
        """지정된 ID의 예정 이벤트를 삭제합니다."""
        pass

    @abc.abstractmethod
    async def update_exchange_asset_status(
        self,
        exchange_id: str,
        symbol: str,
        is_active: int,
        is_delisted: int = 0
    ) -> bool:
        """exchange_assets 테이블의 자산 활성화 및 상장폐지 상태를 업데이트합니다."""
        pass

    @abc.abstractmethod
    async def upsert_asset_master_if_not_exists(
        self,
        symbol: str,
        korean_name: str,
        asset_type: str
    ) -> bool:
        """asset_master 테이블에 자산이 없을 경우 추가합니다."""
        pass




class SqliteTradingRepository(BaseTradingRepository):
    """
    실제 SQLite 데이터베이스를 연동하는 실거래용 트레이딩 저장소 어댑터입니다.
    """
    def __init__(
        self,
        system=None,
        db_path: Optional[str] = None,
        girs_shadow_mode_override: Optional[bool] = None,
        auto_strategy_promotion_enabled_override: Optional[bool] = None,
        champion_cooldown_days: float = 7.0,
        champion_cooldown_trades: int = 100
    ):
        import sys
        self.system = system
        self.db_path = db_path
        self.champion_cooldown_days = champion_cooldown_days
        self.champion_cooldown_trades = champion_cooldown_trades
        
        is_pytest = "pytest" in sys.modules
        self.girs_shadow_mode_override = girs_shadow_mode_override if girs_shadow_mode_override is not None else (False if is_pytest else None)
        self.auto_strategy_promotion_enabled_override = auto_strategy_promotion_enabled_override if auto_strategy_promotion_enabled_override is not None else (True if is_pytest else None)

    async def sync_portfolio_id_cache(self):
        """
        데이터베이스의 portfolios 테이블을 조회하여, 
        type='live'인 포트폴리오의 ID를 포함하여 등록된 모든 포트폴리오의 (name/type, id) 매핑을
        src.engine.portfolio의 캐시 맵에 동적으로 적재합니다.
        """
        from src.engine.portfolio import seed_portfolio_id_map
        async with get_db_conn(self.db_path) as db:
            # portfolios 테이블 존재 여부 확인
            cursor = await db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='portfolios'")
            table_exists = await cursor.fetchone()
            if not table_exists:
                return
                
            async with db.execute("SELECT id, name, type FROM portfolios") as cur:
                async for row in cur:
                    p_id = row['id']
                    p_name = row['name']
                    p_type = row['type']
                    
                    if p_name:
                        seed_portfolio_id_map(p_name, p_id)

    async def _resolve_portfolio_id(self, db, portfolio_id: Any) -> Optional[int]:
        if portfolio_id is None:
            raise ValueError("Portfolio ID cannot be None")
        if str(portfolio_id).strip() == "":
            raise ValueError("Portfolio ID cannot be empty")
            
        # 1. get_integer_portfolio_id를 통해 메모리상의 매핑된 정수 ID를 먼저 얻음
        from src.engine.portfolio import get_integer_portfolio_id
        try:
            pid = get_integer_portfolio_id(portfolio_id)
        except ValueError:
            pid = None
            
        # 2. 그 정수 ID가 실제로 DB portfolios 테이블에 존재하는지 확인 (Fail-Fast 적용)
        if pid is not None:
            cursor = await db.execute("SELECT id FROM portfolios WHERE id = ? LIMIT 1", (pid,))
            row = await cursor.fetchone()
            if row:
                return row[0]
                
        # 3. 만약 메모리 캐시에 없거나 DB에서 찾을 수 없다면, name 또는 type 컬럼에서 추가 검색 (하위 호환 및 복구용)
        cursor = await db.execute("SELECT id FROM portfolios WHERE name = ? LIMIT 1", (str(portfolio_id),))
        row = await cursor.fetchone()
        if row:
            return row[0]
            
        cursor = await db.execute("SELECT id FROM portfolios WHERE type = ? LIMIT 1", (str(portfolio_id),))
        row = await cursor.fetchone()
        if row:
            return row[0]
            
        raise ValueError(f"Portfolio not found for identifier: {portfolio_id}")

    async def save_portfolio(self, portfolio: Any):
        async with get_db_conn(self.db_path) as db:
            # 1. 포트폴리오 기본 정보 저장
            if portfolio.portfolio_type == 'live' or portfolio.id == 1:
                pid = 1
                await db.execute('''
                    INSERT INTO portfolios (id, name, type, strategy_info, duration, updated_at, ended_at)
                    VALUES (1, ?, 'live', ?, ?, datetime('now'), NULL)
                    ON CONFLICT(id) DO UPDATE SET
                        name = excluded.name,
                        strategy_info = excluded.strategy_info,
                        duration = COALESCE(excluded.duration, portfolios.duration),
                        updated_at = datetime('now'),
                        ended_at = NULL
                ''', (
                    portfolio.name,
                    getattr(portfolio, 'strategy_info', ''),
                    getattr(portfolio, 'duration', None)
                ))
                portfolio.id = 1
            else:
                pid = None
                if isinstance(portfolio.id, int):
                    pid = portfolio.id
                elif str(portfolio.id).isdigit():
                    pid = int(portfolio.id)
                
                ended_at = getattr(portfolio, 'ended_at', None)
                if pid is not None:
                    await db.execute('''
                        INSERT INTO portfolios (id, name, type, strategy_info, duration, updated_at, ended_at)
                        VALUES (?, ?, ?, ?, ?, datetime('now'), ?)
                        ON CONFLICT(id) DO UPDATE SET
                            name = excluded.name,
                            type = excluded.type,
                            strategy_info = excluded.strategy_info,
                            duration = COALESCE(excluded.duration, portfolios.duration),
                            updated_at = datetime('now'),
                            ended_at = excluded.ended_at
                    ''', (
                        pid,
                        portfolio.name,
                        portfolio.portfolio_type,
                        getattr(portfolio, 'strategy_info', ''),
                        getattr(portfolio, 'duration', None),
                        ended_at
                    ))
                else:
                    cursor = await db.execute('''
                        INSERT INTO portfolios (name, type, strategy_info, duration, updated_at, ended_at)
                        VALUES (?, ?, ?, ?, datetime('now'), ?)
                    ''', (
                        portfolio.name,
                        portfolio.portfolio_type,
                        getattr(portfolio, 'strategy_info', ''),
                        getattr(portfolio, 'duration', None),
                        ended_at
                    ))
                    pid = cursor.lastrowid
                    portfolio.id = pid

            # 1.5. 거래소별 격리 자금 정보 저장 (portfolio_exchanges)
            if hasattr(portfolio, 'exchange_cash') and portfolio.exchange_cash:
                for ex_id, ex_cash in portfolio.exchange_cash.items():
                    init_cash = 0.0
                    if hasattr(portfolio, 'exchange_initial_cash') and portfolio.exchange_initial_cash and ex_id in portfolio.exchange_initial_cash:
                        init_cash = float(portfolio.exchange_initial_cash[ex_id])
                    else:
                        init_cash = float(ex_cash)
                        if not hasattr(portfolio, 'exchange_initial_cash') or portfolio.exchange_initial_cash is None:
                            portfolio.exchange_initial_cash = {}
                        portfolio.exchange_initial_cash[ex_id] = init_cash

                    await db.execute('''
                        INSERT INTO portfolio_exchanges (portfolio_id, exchange_id, initial_cash, cash, updated_at)
                        VALUES (?, ?, ?, ?, datetime('now'))
                        ON CONFLICT(portfolio_id, exchange_id) DO UPDATE SET cash = ?, initial_cash = ?, updated_at = datetime('now')
                    ''', (pid, ex_id, init_cash, ex_cash, ex_cash, init_cash))

            # 2. 현재 포지션 정보 저장 (기존 포지션 삭제 후 재삽입)
            await db.execute("DELETE FROM positions WHERE portfolio_id = ?", (pid,))
            for pos in portfolio.positions.values():
                if pos.quantity > 0:
                    await db.execute('''
                        INSERT INTO positions (portfolio_id, exchange_id, symbol, quantity, avg_price, entry_time, peak_price, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
                    ''', (pid, pos.exchange_id, pos.symbol, pos.quantity, pos.avg_price, getattr(pos, 'entry_time', 0.0), getattr(pos, 'peak_price', 0.0)))
            
            await db.commit()

    async def load_portfolios(self, exclude_types: list = None, exclude_ended: bool = False) -> Dict[str, Any]:
        await self.sync_portfolio_id_cache()
        from src.engine.portfolio import Portfolio, Position, PortfolioDict
        loaded_portfolios = PortfolioDict()
        async with get_db_conn(self.db_path) as db:
            # 1. 포트폴리오 로드
            query = "SELECT * FROM portfolios"
            conditions = []
            params = []
            if exclude_types:
                placeholders = ",".join(["?"] * len(exclude_types))
                conditions.append(f"type NOT IN ({placeholders})")
                params.extend(exclude_types)
            if exclude_ended:
                conditions.append("ended_at IS NULL")
            if conditions:
                query += " WHERE " + " AND ".join(conditions)
                cursor = await db.execute(query, params)
            else:
                cursor = await db.execute(query)

            async with cursor:
                async for row in cursor:
                    p = Portfolio(
                        portfolio_id=row['id'], 
                        name=row['name'], 
                        portfolio_type=row['type'],
                        strategy_info=row['strategy_info'] if 'strategy_info' in row.keys() else ""
                    )
                    p.created_at = row['created_at'] if 'created_at' in row.keys() else None
                    p.updated_at = row['updated_at'] if 'updated_at' in row.keys() else None
                    p.ended_at = row['ended_at'] if 'ended_at' in row.keys() else None
                    key = str(p.id)
                    loaded_portfolios[key] = p
            
            # 2. 각 포트폴리오의 포지션 및 거래소 격리 자금 로드
            for key, p in loaded_portfolios.items():
                pid = p.id
                # 2.1. portfolio_exchanges 로드
                p.exchange_cash = {}
                p.exchange_initial_cash = {}
                async with db.execute("SELECT exchange_id, initial_cash, cash FROM portfolio_exchanges WHERE portfolio_id = ?", (pid,)) as cursor:
                    async for row in cursor:
                        p.exchange_cash[row['exchange_id']] = row['cash']
                        p.exchange_initial_cash[row['exchange_id']] = row['initial_cash']
                
                # 2.2. positions 로드
                async with db.execute("SELECT * FROM positions WHERE portfolio_id = ?", (pid,)) as cursor:
                    async for row in cursor:
                        ex_val = row['exchange_id'] if row['exchange_id'] else 'upbit'
                        p.positions[(ex_val.lower(), row['symbol'])] = Position(
                             exchange_id=ex_val,
                             symbol=row['symbol'],
                             quantity=row['quantity'],
                             avg_price=row['avg_price'],
                             updated_at=time.time(),
                             entry_time=row['entry_time'] if 'entry_time' in row.keys() else 0.0,
                             peak_price=row['peak_price'] if 'peak_price' in row.keys() else 0.0
                        )
                
                # 3. 최근 거래 내역 로드 (최근 100건)
                async with db.execute("SELECT * FROM orders_history WHERE portfolio_id = ? ORDER BY timestamp DESC LIMIT 100", (pid,)) as cursor:
                    rows = await cursor.fetchall()
                    orders = []
                    for r in reversed(rows):
                        order = dict(r)
                        order['portfolio_id'] = str(order['portfolio_id'])
                        orders.append(order)
                    p.history = orders
        return loaded_portfolios

    async def insert_order_history(self, portfolio_id: str, order: Dict[str, Any]):
        async with get_db_conn(self.db_path) as db:
            import json
            pid = await self._resolve_portfolio_id(db, portfolio_id)
            if not pid:
                raise ValueError(f"Could not resolve portfolio_id: {portfolio_id}")
            await db.execute('''
                INSERT INTO orders_history (portfolio_id, exchange_id, market, strategy_id, symbol, side, price, quantity, fee, tax, timestamp, reason, context)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                pid, 
                order['exchange_id'],
                order.get('market', 'KRW'),
                order.get('strategy_id', ""),
                order['symbol'], 
                order['side'], 
                order['price'], 
                order['quantity'], 
                order['fee'], 
                order.get('tax', 0.0),
                order.get('timestamp', int(time.time())), 
                order.get('reason', ""),
                json.dumps(order.get('context', {}) or {})
            ))
            await db.commit()

    async def get_orders_history(self, portfolio_id: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        if portfolio_id == '1' or portfolio_id == 1:
            async with get_db_conn(self.db_path) as db:
                query = """
                    SELECT * FROM real_orders
                    ORDER BY created_at DESC
                """
                if limit is not None and limit > 0:
                    query += f" LIMIT {limit}"
                
                async with db.execute(query) as cursor:
                    rows = await cursor.fetchall()
                    orders = []
                    from datetime import datetime
                    for r in rows:
                        ts = int(time.time())
                        created_at = r['created_at']
                        if created_at:
                            try:
                                ts = int(datetime.fromisoformat(created_at).timestamp())
                            except Exception:
                                pass
                        
                        orders.append({
                            'portfolio_id': '1',
                            'exchange_id': r['exchange_id'],
                            'market': 'KRW',
                            'strategy_id': 'live_auto',
                            'symbol': r['symbol'],
                            'side': r['side'],
                            'price': float(r['price'] or 0.0),
                            'quantity': float(r['executed_volume'] or 0.0),
                            'fee': float(r['fee'] or 0.0),
                            'tax': float(r['tax'] if 'tax' in r.keys() else 0.0),
                            'timestamp': ts,
                            'reason': '실거래 체결',
                            'context': {}
                        })
                    orders.reverse()
                    return orders

        async with get_db_conn(self.db_path) as db:
            import json
            pid = await self._resolve_portfolio_id(db, portfolio_id)
            if not pid:
                return []
            if limit is not None and limit > 0:
                query = """
                    SELECT * FROM (
                        SELECT * FROM orders_history 
                        WHERE portfolio_id = ? 
                        ORDER BY timestamp DESC 
                        LIMIT ?
                    ) ORDER BY timestamp ASC
                """
                params = (pid, limit)
            else:
                query = """
                    SELECT * FROM orders_history 
                    WHERE portfolio_id = ? 
                    ORDER BY timestamp ASC
                """
                params = (pid,)
                
            async with db.execute(query, params) as cursor:
                rows = await cursor.fetchall()
                orders = []
                for r in rows:
                    order = dict(r)
                    order['portfolio_id'] = str(order['portfolio_id'])
                    if 'context' in order and isinstance(order['context'], str) and order['context']:
                        try:
                            order['context'] = json.loads(order['context'])
                        except Exception:
                            pass
                    orders.append(order)
                return orders

    async def get_orders_for_performance_replay(
        self, portfolio_id: str, strategy_id: str
    ) -> List[Dict[str, Any]]:
        async with get_db_conn(self.db_path) as db:
            import json
            pid = await self._resolve_portfolio_id(db, portfolio_id)
            if not pid:
                return []
            async with db.execute(
                "SELECT * FROM orders_history WHERE portfolio_id = ? AND strategy_id = ? ORDER BY timestamp ASC",
                (pid, strategy_id)
            ) as cursor:
                rows = await cursor.fetchall()
                orders = []
                for r in rows:
                    order = dict(r)
                    order['portfolio_id'] = str(order['portfolio_id'])
                    if 'context' in order and isinstance(order['context'], str) and order['context']:
                        try:
                            order['context'] = json.loads(order['context'])
                        except Exception:
                            pass
                    orders.append(order)
                return orders

    async def get_orders_for_proposal_evaluation(
        self, portfolio_id: str, strategy_id: str, start_ts: int, end_ts: int
    ) -> List[Dict[str, Any]]:
        async with get_db_conn(self.db_path) as db:
            import json
            pid = await self._resolve_portfolio_id(db, portfolio_id)
            if not pid:
                return []
            async with db.execute(
                "SELECT * FROM orders_history "
                "WHERE portfolio_id = ? AND strategy_id = ? AND timestamp BETWEEN ? AND ? "
                "ORDER BY timestamp ASC",
                (pid, strategy_id, start_ts, end_ts)
            ) as cursor:
                rows = await cursor.fetchall()
                orders = []
                for r in rows:
                    order = dict(r)
                    order['portfolio_id'] = str(order['portfolio_id'])
                    if 'context' in order and isinstance(order['context'], str) and order['context']:
                        try:
                            order['context'] = json.loads(order['context'])
                        except Exception:
                            pass
                    orders.append(order)
                return orders

    async def get_latest_feature_snapshot_for_proposal(
        self, proposal_id: str
    ) -> Optional[Dict[str, Any]]:
        async with get_db_conn(self.db_path) as db:
            import json
            async with db.execute(
                "SELECT feature_snapshot, model_version, scaler_version FROM promotion_event_log "
                "WHERE proposal_id = ? ORDER BY global_sequence_no DESC LIMIT 1",
                (proposal_id,)
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    res = dict(row)
                    if res.get("feature_snapshot"):
                        try:
                            res["feature_snapshot"] = json.loads(res["feature_snapshot"])
                        except Exception as e:
                            logger.error(f"[SqliteTradingRepository] Failed to parse feature_snapshot JSON for proposal {proposal_id}: {e}")
                            res["feature_snapshot"] = None
                    return res
                return None

    async def insert_alert(self, alert: Dict[str, Any]):
        required_keys = ['exchange_id', 'code', 'price', 'msg', 'timestamp']
        missing_keys = [k for k in required_keys if k not in alert]
        if missing_keys:
            raise ValueError(f"Required fields missing from alert dictionary: {missing_keys}")

        async with get_db_conn(self.db_path) as db:
            await db.execute(
                "INSERT INTO alerts (exchange_id, symbol, price, msg, timestamp) VALUES (?, ?, ?, ?, ?)",
                (alert['exchange_id'], alert['code'], alert['price'], alert['msg'], alert['timestamp'])
            )
            await db.commit()

    async def load_exchange_configs(self) -> Dict[str, Dict[str, Any]]:
        configs = {}
        async with get_db_conn(self.db_path) as db:
            async with db.execute("SELECT * FROM exchanges") as cursor:
                async for row in cursor:
                    configs[row['id']] = dict(row)
        return configs

    async def insert_system_event(self, event_type: str, target: str, message: str, timestamp: Optional[int] = None, context: Optional[str] = None):
        async with get_db_conn(self.db_path) as db:
            ts = timestamp if timestamp is not None else int(time.time() * 1000)
            await db.execute('''
                INSERT INTO system_events (event_type, target, message, timestamp, context)
                VALUES (?, ?, ?, ?, ?)
            ''', (event_type, target, message, ts, context))
            await db.commit()

    async def get_system_events(self, limit: int = 20) -> List[Dict[str, Any]]:
        async with get_db_conn(self.db_path) as db:
            async with db.execute(
                "SELECT * FROM system_events ORDER BY timestamp DESC LIMIT ?",
                (limit,)
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(r) for r in rows]

    async def clean_old_system_events(self, retention_days: int = 7):
        async with get_db_conn(self.db_path) as db:
            # 1. 반복 차단 및 요약 이벤트 7일 TTL
            cutoff_ts_short = int((time.time() - retention_days * 24 * 3600) * 1000)
            await db.execute('''
                DELETE FROM system_events 
                WHERE event_type IN (
                    'PROMOTION_COOLDOWN_BLOCKED', 'PROMOTION_QUOTA_BLOCKED', 
                    'PROMOTION_LIMIT_BLOCKED', 'UNIVERSE_GUARD_SUMMARY'
                ) AND timestamp < ?
            ''', (cutoff_ts_short,))
            
            # 2. 상태 전환 이벤트 90일 TTL
            cutoff_ts_long = int((time.time() - 90 * 24 * 3600) * 1000)
            await db.execute('''
                DELETE FROM system_events 
                WHERE event_type IN ('UNIVERSE_PROMOTION', 'UNIVERSE_DEMOTION') 
                  AND timestamp < ?
            ''', (cutoff_ts_long,))
            
            await db.commit()
            logger.info(f"[Repository] 시스템 감사 로그를 정리하였습니다. (차단/요약 7일 기준: {cutoff_ts_short}, 전환 90일 기준: {cutoff_ts_long})")

    async def upsert_universe_guard_state(self, exchange_id: str, market_type: str, symbol: str, status: str, blocked_reason: Optional[str], blocked_count: int, last_blocked_at: Optional[float], last_event_logged_reason: Optional[str]):
        async with get_db_conn(self.db_path) as db:
            await db.execute('''
                INSERT INTO universe_guard_state 
                (exchange_id, market_type, symbol, status, blocked_reason, blocked_count, last_blocked_at, last_event_logged_reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(exchange_id, market_type, symbol) DO UPDATE SET
                    status = excluded.status,
                    blocked_reason = excluded.blocked_reason,
                    blocked_count = CASE WHEN COALESCE(universe_guard_state.blocked_reason, '') = COALESCE(excluded.blocked_reason, '') 
                                         THEN universe_guard_state.blocked_count + excluded.blocked_count 
                                         ELSE excluded.blocked_count END,
                    last_blocked_at = excluded.last_blocked_at,
                    last_event_logged_reason = excluded.last_event_logged_reason
            ''', (exchange_id, market_type, symbol, status, blocked_reason, blocked_count, last_blocked_at, last_event_logged_reason))
            await db.commit()

    async def get_universe_guard_state(self, exchange_id: str, market_type: str, symbol: str) -> Optional[Dict[str, Any]]:
        async with get_db_conn(self.db_path) as db:
            async with db.execute("SELECT * FROM universe_guard_state WHERE exchange_id = ? AND market_type = ? AND symbol = ?", (exchange_id, market_type, symbol)) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def get_all_universe_guard_states(self) -> List[Dict[str, Any]]:
        async with get_db_conn(self.db_path) as db:
            async with db.execute("SELECT * FROM universe_guard_state") as cursor:
                rows = await cursor.fetchall()
                return [dict(r) for r in rows]

    async def check_and_report_previous_crash(self, target: str):
        async with get_db_conn(self.db_path) as db:
            async with db.execute('''
                SELECT event_type, timestamp FROM system_events 
                WHERE target = ? AND event_type IN ('DAEMON_START', 'DAEMON_STOP', 'DAEMON_CRASHED')
                ORDER BY timestamp DESC LIMIT 1
            ''', (target,)) as cursor:
                row = await cursor.fetchone()
                if row and row['event_type'] == 'DAEMON_START':
                    crash_ts = row['timestamp'] + 1
                    from datetime import datetime
                    last_start_time = datetime.fromtimestamp(row['timestamp'] / 1000).strftime('%Y-%m-%d %H:%M:%S')
                    message = f"이전 데몬 프로세스가 정상 종료(DAEMON_STOP) 처리되지 못하고 비정상 종료(크래쉬)되었음을 감지하여 보완 기록합니다. (이전 정상 기동 시각: {last_start_time})"
                    await db.execute('''
                        INSERT INTO system_events (event_type, target, message, timestamp)
                        VALUES (?, ?, ?, ?)
                    ''', ('DAEMON_CRASHED', target, message, crash_ts))
                    await db.commit()
                    logger.warning(f"[{target}] 이전 프로세스의 비정상 종료 감지 및 DAEMON_CRASHED 보완 이력 적재 완료.")

    async def get_all_strategy_versions(self) -> List[Dict[str, Any]]:
        async with get_db_conn(self.db_path) as db:
            async with db.execute("SELECT * FROM strategy_versions") as cursor:
                rows = await cursor.fetchall()
                results = []
                for row in rows:
                    import json
                    res = dict(row)
                    res["current_params"] = json.loads(res["current_params"])
                    res["applied_at"] = normalize_timestamp(res.get("applied_at"))
                    results.append(res)
                return results

    async def get_strategy_version(self, strategy_id: str) -> Optional[Dict[str, Any]]:
        async with get_db_conn(self.db_path) as db:
            async with db.execute(
                "SELECT * FROM strategy_versions WHERE strategy_id = ?",
                (strategy_id,)
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    import json
                    res = dict(row)
                    res["current_params"] = json.loads(res["current_params"])
                    res["applied_at"] = normalize_timestamp(res.get("applied_at"))
                    return res
        return None

    async def save_strategy_version(self, strategy_id: str, version_id: int, params: Dict[str, Any], applied_at: int, rollback_source_version: Optional[int] = None):
        async with get_db_conn(self.db_path) as db:
            import json
            params_str = json.dumps(params)
            applied_at_norm = normalize_timestamp(applied_at) or int(time.time() * 1000)
            await db.execute('''
                INSERT INTO strategy_versions (strategy_id, current_version_id, current_params, rollback_source_version, applied_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(strategy_id) DO UPDATE SET
                    current_version_id = excluded.current_version_id,
                    current_params = excluded.current_params,
                    rollback_source_version = excluded.rollback_source_version,
                    applied_at = excluded.applied_at,
                    updated_at = excluded.updated_at
            ''', (strategy_id, version_id, params_str, rollback_source_version, applied_at_norm, int(time.time() * 1000)))
            await db.commit()

    async def insert_strategy_parameter_history(self, strategy_id: str, version_id: int, parent_version_id: Optional[int], old_params: Optional[str], new_params: str, proposal_id: Optional[int], is_current: int, changed_by: str, change_reason: str) -> int:
        async with get_db_conn(self.db_path) as db:
            if is_current == 1:
                await db.execute(
                    "UPDATE strategy_parameter_history SET is_current = 0 WHERE strategy_id = ?",
                    (strategy_id,)
                )
            
            cursor = await db.execute('''
                INSERT INTO strategy_parameter_history 
                (strategy_id, version_id, parent_version_id, old_params, new_params, proposal_id, is_current, changed_by, change_reason, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (strategy_id, version_id, parent_version_id, old_params, new_params, proposal_id, is_current, changed_by, change_reason, int(time.time() * 1000)))
            
            inserted_id = cursor.lastrowid
            await db.commit()
            return inserted_id

    async def get_strategy_parameter_history(self, strategy_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        async with get_db_conn(self.db_path) as db:
            async with db.execute(
                "SELECT * FROM strategy_parameter_history WHERE strategy_id = ? ORDER BY version_id DESC LIMIT ?",
                (strategy_id, limit)
            ) as cursor:
                rows = await cursor.fetchall()
                history_list = []
                for r in rows:
                    h = dict(r)
                    h["created_at"] = normalize_timestamp(h.get("created_at"))
                    history_list.append(h)
                return history_list

    async def get_strategy_parameter_version(self, strategy_id: str, version_id: int) -> Optional[Dict[str, Any]]:
        async with get_db_conn(self.db_path) as db:
            async with db.execute(
                "SELECT * FROM strategy_parameter_history WHERE strategy_id = ? AND version_id = ?",
                (strategy_id, version_id)
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    import json
                    res = dict(row)
                    if res.get("new_params"):
                        res["new_params"] = json.loads(res["new_params"])
                    if res.get("old_params"):
                        res["old_params"] = json.loads(res["old_params"])
                    res["created_at"] = normalize_timestamp(res.get("created_at"))
                    return res
        return None

    async def insert_strategy_performance_snapshot(self, snapshot_data: Dict[str, Any]):
        async with get_db_conn(self.db_path) as db:
            ts_norm = normalize_timestamp(snapshot_data["timestamp"]) or int(time.time() * 1000)
            await db.execute('''
                INSERT INTO strategy_performance_snapshots 
                (strategy_id, version_id, parameter_hash, snapshot_type, timestamp, roi, mdd, profit_factor, win_rate, trade_count, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                snapshot_data["strategy_id"],
                snapshot_data["version_id"],
                snapshot_data["parameter_hash"],
                snapshot_data["snapshot_type"],
                ts_norm,
                snapshot_data.get("roi"),
                snapshot_data.get("mdd"),
                snapshot_data.get("profit_factor"),
                snapshot_data.get("win_rate"),
                snapshot_data.get("trade_count"),
                int(time.time() * 1000)
            ))
            await db.commit()

    async def get_strategy_performance_snapshots(self, strategy_id: str, version_id: Optional[int] = None, limit: int = 100) -> List[Dict[str, Any]]:
        async with get_db_conn(self.db_path) as db:
            query = "SELECT * FROM strategy_performance_snapshots WHERE strategy_id = ?"
            params = [strategy_id]
            if version_id is not None:
                query += " AND version_id = ?"
                params.append(version_id)
            query += " ORDER BY timestamp DESC LIMIT ?"
            params.append(limit)
            
            async with db.execute(query, tuple(params)) as cursor:
                rows = await cursor.fetchall()
                snapshots_list = []
                for r in rows:
                    s = dict(r)
                    s["timestamp"] = normalize_timestamp(s.get("timestamp"))
                    s["created_at"] = normalize_timestamp(s.get("created_at"))
                    snapshots_list.append(s)
                return snapshots_list

    async def insert_strategy_proposal(self, proposal_data: Dict[str, Any]) -> int:
        status = proposal_data.get("status", "PENDING")
        confidence_score = proposal_data.get("confidence_score", 50)
        # Auto-pruning: 60점 미만인 경우 강제 PRUNED 처리
        if confidence_score < 60:
            status = "PRUNED"

        async with get_db_conn(self.db_path) as db:
            import json
            ts = int(time.time() * 1000)
            applied_at = normalize_timestamp(proposal_data.get("applied_at"))
            rolled_back_at = normalize_timestamp(proposal_data.get("rolled_back_at"))
            
            decision_path_hash = proposal_data.get("decision_path_hash")
            audit_log_json = json.dumps(proposal_data.get("audit_log_json", {})) if proposal_data.get("audit_log_json") else None
            is_counterfactual_tracked = proposal_data.get("is_counterfactual_tracked", 0)
            
            if status in ("PRUNED", "DEFERRED"):
                audit_data = proposal_data.get("audit_log_json", {})
                diversity_penalty = audit_data.get("diversity_penalty", 0)
                if (45 <= confidence_score < 60) or (diversity_penalty >= 10):
                    is_counterfactual_tracked = 1

            pid = await self._resolve_portfolio_id(db, proposal_data["portfolio_id"])

            cursor = await db.execute('''
                INSERT OR IGNORE INTO strategy_proposals 
                (insight_id, proposal_group_id, version, portfolio_id, strategy_id, status, outcome, 
                 original_params, proposed_params, metrics, mutation_trace, confidence_score, 
                 applied_at, rolled_back_at, decision_path_hash, audit_log_json, is_counterfactual_tracked, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                proposal_data.get("insight_id"),
                proposal_data["proposal_group_id"],
                proposal_data["version"],
                pid,
                proposal_data["strategy_id"],
                status,
                proposal_data.get("outcome", "RUNNING"),
                json.dumps(proposal_data["original_params"]),
                json.dumps(proposal_data["proposed_params"]),
                json.dumps(proposal_data.get("metrics", {})),
                json.dumps(proposal_data.get("mutation_trace", {})),
                confidence_score,
                applied_at,
                rolled_back_at,
                decision_path_hash,
                audit_log_json,
                is_counterfactual_tracked,
                ts,
                ts
            ))
            inserted_id = cursor.lastrowid
            if not inserted_id and decision_path_hash:
                async with db.execute('SELECT id FROM strategy_proposals WHERE decision_path_hash = ?', (decision_path_hash,)) as sel_cursor:
                    row = await sel_cursor.fetchone()
                    if row:
                        inserted_id = row[0]
            await db.commit()
            return inserted_id

    async def update_strategy_proposal_status(self, proposal_id: int, status: str, outcome: Optional[str] = None, applied_at: Optional[int] = None, rolled_back_at: Optional[int] = None):
        async with get_db_conn(self.db_path) as db:
            ts = int(time.time() * 1000)
            query = "UPDATE strategy_proposals SET status = ?, updated_at = ?"
            params = [status, ts]
            if outcome is not None:
                query += ", outcome = ?"
                params.append(outcome)
            if applied_at is not None:
                query += ", applied_at = ?"
                params.append(normalize_timestamp(applied_at))
            if rolled_back_at is not None:
                query += ", rolled_back_at = ?"
                params.append(normalize_timestamp(rolled_back_at))
            query += " WHERE id = ?"
            params.append(proposal_id)
            
            await db.execute(query, tuple(params))
            await db.commit()

    async def get_strategy_proposal(self, proposal_id: int) -> Optional[Dict[str, Any]]:
        async with get_db_conn(self.db_path) as db:
            async with db.execute(
                "SELECT * FROM strategy_proposals WHERE id = ?",
                (proposal_id,)
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    import json
                    res = dict(row)
                    res["original_params"] = json.loads(res["original_params"])
                    res["proposed_params"] = json.loads(res["proposed_params"])
                    res["metrics"] = json.loads(res["metrics"]) if res.get("metrics") else {}
                    res["mutation_trace"] = json.loads(res["mutation_trace"]) if res.get("mutation_trace") else {}
                    res["audit_log_json"] = json.loads(res["audit_log_json"]) if res.get("audit_log_json") else {}
                    res["created_at"] = normalize_timestamp(res.get("created_at"))
                    res["updated_at"] = normalize_timestamp(res.get("updated_at"))
                    res["applied_at"] = normalize_timestamp(res.get("applied_at"))
                    res["rolled_back_at"] = normalize_timestamp(res.get("rolled_back_at"))
                    return res
        return None

    async def get_active_proposals(self, strategy_id: Optional[str] = None, status: Optional[str] = None) -> List[Dict[str, Any]]:
        async with get_db_conn(self.db_path) as db:
            query = "SELECT * FROM strategy_proposals"
            filters = []
            params = []
            if strategy_id is not None:
                filters.append("strategy_id = ?")
                params.append(strategy_id)
            if status is not None:
                filters.append("status = ?")
                params.append(status)
                
            if filters:
                query += " WHERE " + " AND ".join(filters)
            query += " ORDER BY created_at DESC"
            
            async with db.execute(query, tuple(params)) as cursor:
                rows = await cursor.fetchall()
                import json
                res_list = []
                for r in rows:
                    res = dict(r)
                    res["original_params"] = json.loads(res["original_params"])
                    res["proposed_params"] = json.loads(res["proposed_params"])
                    res["metrics"] = json.loads(res["metrics"]) if res.get("metrics") else {}
                    res["mutation_trace"] = json.loads(res["mutation_trace"]) if res.get("mutation_trace") else {}
                    res["audit_log_json"] = json.loads(res["audit_log_json"]) if res.get("audit_log_json") else {}
                    res["created_at"] = normalize_timestamp(res.get("created_at"))
                    res["updated_at"] = normalize_timestamp(res.get("updated_at"))
                    res["applied_at"] = normalize_timestamp(res.get("applied_at"))
                    res["rolled_back_at"] = normalize_timestamp(res.get("rolled_back_at"))
                    res_list.append(res)
                return res_list

    async def get_counterfactual_targets(self, limit: int = 20) -> List[Dict[str, Any]]:
        async with get_db_conn(self.db_path) as db:
            async with db.execute('''
                SELECT * FROM strategy_proposals 
                WHERE is_counterfactual_tracked = 1 
                ORDER BY created_at DESC 
                LIMIT ?
            ''', (limit,)) as cursor:
                rows = await cursor.fetchall()
                import json
                res_list = []
                for r in rows:
                    res = dict(r)
                    res["original_params"] = json.loads(res["original_params"])
                    res["proposed_params"] = json.loads(res["proposed_params"])
                    res["metrics"] = json.loads(res["metrics"]) if res.get("metrics") else {}
                    res["mutation_trace"] = json.loads(res["mutation_trace"]) if res.get("mutation_trace") else {}
                    res["audit_log_json"] = json.loads(res["audit_log_json"]) if res.get("audit_log_json") else {}
                    res["created_at"] = normalize_timestamp(res.get("created_at"))
                    res["updated_at"] = normalize_timestamp(res.get("updated_at"))
                    res_list.append(res)
                return res_list

    async def update_counterfactual_metrics(self, proposal_id: int, roi: float, mdd: float, track_status: int):
        async with get_db_conn(self.db_path) as db:
            ts = int(time.time() * 1000)
            await db.execute('''
                UPDATE strategy_proposals 
                SET counterfactual_roi = ?, counterfactual_mdd = ?, is_counterfactual_tracked = ?, updated_at = ?
                WHERE id = ?
            ''', (roi, mdd, track_status, ts, proposal_id))
            await db.commit()

    async def insert_proposal_evaluation(self, eval_data: Dict[str, Any], legacy_compat: bool = False) -> int:
        horizon_name = eval_data.get("horizon_name")
        if not horizon_name or str(horizon_name).strip() == "":
            if not legacy_compat:
                raise ValueError("horizon_name is required for proposal evaluation")
            else:
                horizon_name = "7d"
                logger.warning(
                    f"[SqliteTradingRepository] LEGACY_HORIZON_DEFAULT_APPLIED: "
                    f"Proposal ID {eval_data.get('proposal_id')} has missing or empty horizon_name. "
                    f"Automatically defaulted to '7d'."
                )
                await self.insert_system_event(
                    event_type="LEGACY_HORIZON_DEFAULT_APPLIED",
                    target="proposal_evaluations",
                    message=f"Proposal ID {eval_data.get('proposal_id')} has missing or empty horizon_name. Defaulted to 7d.",
                    context=f"proposal_id={eval_data.get('proposal_id')}"
                )

        async with get_db_conn(self.db_path) as db:
            cursor = await db.execute('''
                INSERT INTO proposal_evaluations 
                (proposal_id, horizon_name, predicted_roi_7d, actual_roi_7d, roi_divergence, 
                 predicted_trade_count_7d, actual_trade_count_7d, trade_count_divergence, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ''', (
                eval_data["proposal_id"],
                horizon_name,
                eval_data["predicted_roi_7d"],
                eval_data["actual_roi_7d"],
                eval_data["roi_divergence"],
                eval_data["predicted_trade_count_7d"],
                eval_data["actual_trade_count_7d"],
                eval_data["trade_count_divergence"]
            ))
            inserted_id = cursor.lastrowid
            await db.commit()
            return inserted_id

    async def get_proposal_evaluation(self, proposal_id: int) -> Optional[Dict[str, Any]]:
        async with get_db_conn(self.db_path) as db:
            async with db.execute(
                "SELECT * FROM proposal_evaluations WHERE proposal_id = ?",
                (proposal_id,)
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    return dict(row)
        return None

    async def get_proposal_evaluations(self, proposal_id: int) -> List[Dict[str, Any]]:
        async with get_db_conn(self.db_path) as db:
            async with db.execute(
                "SELECT * FROM proposal_evaluations WHERE proposal_id = ?",
                (proposal_id,)
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(r) for r in rows]

    async def get_expired_pending_evaluations(self, now: int) -> List[Dict[str, Any]]:
        async with get_db_conn(self.db_path) as db:
            async with db.execute(
                "SELECT * FROM proposal_evaluations WHERE evaluation_status = 'PENDING' AND due_at <= ?",
                (now,)
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(r) for r in rows]

    async def get_pending_evaluations_without_baseline(self, now: int) -> List[Dict[str, Any]]:
        async with get_db_conn(self.db_path) as db:
            async with db.execute(
                "SELECT * FROM proposal_evaluations "
                "WHERE evaluation_status = 'PENDING' AND baseline_value IS NULL AND (due_at - horizon_value) <= ?",
                (now,)
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(r) for r in rows]

    async def update_baseline_snapshot(self, pe_id: int, value: float, ts: int, vol: int = 0):
        async with get_db_conn(self.db_path) as db:
            await db.execute(
                "UPDATE proposal_evaluations SET "
                "baseline_value = ?, "
                "baseline_timestamp = ?, "
                "baseline_volume = ? "
                "WHERE id = ?",
                (value, ts, vol, pe_id)
            )
            await db.commit()

    async def claim_evaluation(self, pe_id: int, locked_at: int) -> bool:
        async with get_db_conn(self.db_path) as db:
            cursor = await db.execute(
                "UPDATE proposal_evaluations "
                "SET evaluation_status = 'EVALUATING', locked_at = ? "
                "WHERE id = ? AND evaluation_status = 'PENDING'",
                (locked_at, pe_id)
            )
            await db.commit()
            return cursor.rowcount > 0

    async def complete_evaluation(self, pe_id: int, actual_roi: float, roi_div: float, actual_trades: int, trade_div: int, evaluated_at: int):
        async with get_db_conn(self.db_path) as db:
            await db.execute(
                "UPDATE proposal_evaluations SET "
                "evaluation_status = 'COMPLETED', "
                "evaluated_at = ?, "
                "actual_roi_7d = ?, " # Generic/Legacy 겸용 필드
                "roi_divergence = ?, "
                "actual_trade_count_7d = ?, "
                "trade_count_divergence = ?, "
                "locked_at = NULL "
                "WHERE id = ?",
                (evaluated_at, actual_roi, roi_div, actual_trades, trade_div, pe_id)
            )
            await db.commit()

    async def fail_evaluation(self, pe_id: int, error_msg: str, retry_count: int, max_retries: int):
        async with get_db_conn(self.db_path) as db:
            if retry_count < max_retries:
                await db.execute(
                    "UPDATE proposal_evaluations SET "
                    "evaluation_status = 'PENDING', "
                    "retry_count = ?, "
                    "locked_at = NULL, "
                    "last_error = ? "
                    "WHERE id = ?",
                    (retry_count + 1, error_msg, pe_id)
                )
            else:
                await db.execute(
                    "UPDATE proposal_evaluations SET "
                    "evaluation_status = 'ERROR', "
                    "locked_at = NULL, "
                    "last_error = ? "
                    "WHERE id = ?",
                    (error_msg, pe_id)
                )
            await db.commit()

    async def get_stale_evaluating_evaluations(self, cutoff: int) -> List[Dict[str, Any]]:
        async with get_db_conn(self.db_path) as db:
            async with db.execute(
                "SELECT * FROM proposal_evaluations WHERE evaluation_status = 'EVALUATING' AND locked_at < ?",
                (cutoff,)
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(r) for r in rows]

    async def recover_stale_evaluation(self, pe_id: int, retry_count: int, max_retries: int, error_msg: str):
        await self.fail_evaluation(pe_id, error_msg, retry_count, max_retries)

    async def get_unevaluated_applied_proposals(self) -> List[Dict[str, Any]]:
        async with get_db_conn(self.db_path) as db:
            # 실전 적용(APPLIED)된 지 7일이 경과했고 아직 평가가 안 끝난(outcome = 'RUNNING') 제안 조회
            seven_days_ago_ms = int((time.time() - 7 * 24 * 3600) * 1000)
            async with db.execute('''
                SELECT * FROM strategy_proposals 
                WHERE status = 'APPLIED' AND outcome = 'RUNNING' AND applied_at <= ?
            ''', (seven_days_ago_ms,)) as cursor:
                rows = await cursor.fetchall()
                import json
                res_list = []
                for r in rows:
                    res = dict(r)
                    res["original_params"] = json.loads(res["original_params"])
                    res["proposed_params"] = json.loads(res["proposed_params"])
                    res["metrics"] = json.loads(res["metrics"]) if res.get("metrics") else {}
                    res["mutation_trace"] = json.loads(res["mutation_trace"]) if res.get("mutation_trace") else {}
                    res["created_at"] = normalize_timestamp(res.get("created_at"))
                    res["updated_at"] = normalize_timestamp(res.get("updated_at"))
                    res["applied_at"] = normalize_timestamp(res.get("applied_at"))
                    res["rolled_back_at"] = normalize_timestamp(res.get("rolled_back_at"))
                    res["created_at"] = normalize_timestamp(res.get("created_at"))
                    res["updated_at"] = normalize_timestamp(res.get("updated_at"))
                    res["applied_at"] = normalize_timestamp(res.get("applied_at"))
                    res["rolled_back_at"] = normalize_timestamp(res.get("rolled_back_at"))
                    res_list.append(res)
                return res_list

    async def approve_proposal_atomic(self, proposal_id: int, applied_ts: int) -> Dict[str, Any]:
        """제안 승인 및 적용, 버전 생성, 성과 스냅샷 기본 생성을 단일 DB 트랜잭션으로 처리합니다."""
        # 섀도 모드/자동 승격 비활성화 시 실제 파라미터 업데이트 차단 (2차 안전 가드)
        from src.config.manager import ConfigManager
        config_manager = ConfigManager("config/settings.yaml")
        
        girs_shadow_mode = self.girs_shadow_mode_override
        if girs_shadow_mode is None:
            girs_shadow_mode = config_manager.get("system.girs_shadow_mode", False)
            
        auto_strategy_promotion_enabled = self.auto_strategy_promotion_enabled_override
        if auto_strategy_promotion_enabled is None:
            auto_strategy_promotion_enabled = config_manager.get("system.auto_strategy_promotion_enabled", False)
        
        if girs_shadow_mode or not auto_strategy_promotion_enabled:
            raise ValueError("Promotion blocked: Shadow operation mode active or auto promotion disabled")

        import json
        import hashlib
        async with get_db_conn(self.db_path) as db:
            # 1. 제안 조회
            async with db.execute("SELECT * FROM strategy_proposals WHERE id = ?", (proposal_id,)) as cursor:
                row = await cursor.fetchone()
                if not row:
                    raise ValueError("Proposal not found")
                proposal = dict(row)
                proposal["proposed_params"] = json.loads(proposal["proposed_params"])
                proposal["original_params"] = json.loads(proposal["original_params"])
                
            if proposal["status"] != "PENDING":
                raise ValueError(f"Only PENDING proposals can be approved. Current status: {proposal['status']}")
                
            strategy_id = proposal["strategy_id"]
            proposed_params = proposal["proposed_params"]
            
            # Champion Cooldown 검증 (최소 7일 경과 AND 최소 100건 거래(체결 완료) 완료)
            async with db.execute("SELECT applied_at FROM strategy_versions WHERE strategy_id = ?", (strategy_id,)) as cursor_ver:
                ver_row = await cursor_ver.fetchone()
                
            if ver_row:
                ver_dict = dict(ver_row)
                applied_at_ms = ver_dict["applied_at"]
                
                # applied_ts는 승격 요청 시점의 ms epoch 타임스탬프
                elapsed_seconds = (applied_ts - applied_at_ms) / 1000.0
                elapsed_days = elapsed_seconds / (24 * 3600.0)
                
                # applied_at_ms 이후 체결 완료 주문(orders_history 내 quantity > 0 및 price > 0, portfolio_id 격리) 건수 쿼리
                portfolio_id = proposal["portfolio_id"]
                applied_at_sec = applied_at_ms / 1000.0
                async with db.execute(
                    "SELECT COUNT(*) FROM orders_history WHERE strategy_id = ? AND portfolio_id = ? AND timestamp >= ? AND quantity > 0 AND price > 0",
                    (strategy_id, portfolio_id, applied_at_sec)
                ) as cursor_count:
                    count_row = await cursor_count.fetchone()
                    trade_count = count_row[0] if count_row else 0
                    
                if elapsed_days < self.champion_cooldown_days or trade_count < self.champion_cooldown_trades:
                    raise ChampionCooldownBlockedError(
                        f"Promotion blocked by Champion Cooldown: Strategy {strategy_id} is in cooldown. "
                        f"Active for {elapsed_days:.2f} days and {trade_count} trades. "
                        f"Required: >= {self.champion_cooldown_days} days and >= {self.champion_cooldown_trades} trades."
                    )
            
            # 2. 현재 적용중인 버전 정보 획득
            async with db.execute("SELECT * FROM strategy_versions WHERE strategy_id = ?", (strategy_id,)) as cursor:
                ver_row = await cursor.fetchone()
                
            parent_version_id = None
            old_params_str = None
            new_version_id = 1
            if ver_row:
                ver_dict = dict(ver_row)
                new_version_id = ver_dict["current_version_id"] + 1
                parent_version_id = ver_dict["current_version_id"]
                old_params_str = ver_dict["current_params"]

            # 3. parameter history 기록 (PROPOSAL_APPLY)
            await db.execute("UPDATE strategy_parameter_history SET is_current = 0 WHERE strategy_id = ?", (strategy_id,))
            await db.execute('''
                INSERT INTO strategy_parameter_history 
                (strategy_id, version_id, parent_version_id, old_params, new_params, proposal_id, is_current, changed_by, change_reason, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (strategy_id, new_version_id, parent_version_id, old_params_str, json.dumps(proposed_params), proposal_id, 1, 'USER', 'PROPOSAL_APPLY', applied_ts))
            
            # 4. strategy version 갱신
            await db.execute('''
                INSERT INTO strategy_versions (strategy_id, current_version_id, current_params, rollback_source_version, applied_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(strategy_id) DO UPDATE SET
                    current_version_id = excluded.current_version_id,
                    current_params = excluded.current_params,
                    rollback_source_version = excluded.rollback_source_version,
                    applied_at = excluded.applied_at,
                    updated_at = excluded.updated_at
            ''', (strategy_id, new_version_id, json.dumps(proposed_params), None, applied_ts, applied_ts))

            # 5. 제안 상태 업데이트
            await db.execute('''
                UPDATE strategy_proposals 
                SET status = 'APPLIED', outcome = 'RUNNING', applied_at = ?, updated_at = ?
                WHERE id = ?
            ''', (applied_ts, applied_ts, proposal_id))

            # 6. 성과 스냅샷 기본 생성 (SYNC)
            param_hash = hashlib.md5(json.dumps(proposed_params, sort_keys=True).encode('utf-8')).hexdigest()
            cursor = await db.execute('''
                INSERT INTO strategy_performance_snapshots 
                (strategy_id, version_id, parameter_hash, snapshot_type, timestamp, roi, mdd, profit_factor, win_rate, trade_count, created_at)
                VALUES (?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, 0, ?)
            ''', (strategy_id, new_version_id, param_hash, 'PARAMETER_CHANGE', applied_ts, applied_ts))
            snapshot_id = cursor.lastrowid

            await db.commit()
            
            return {
                "strategy_id": strategy_id,
                "portfolio_id": proposal["portfolio_id"],
                "new_version_id": new_version_id,
                "proposed_params": proposed_params,
                "snapshot_id": snapshot_id
            }

    async def rollback_strategy_atomic(self, strategy_id: str, version_id: int, applied_ts: int) -> Dict[str, Any]:
        """지정 버전 롤백, 버전 갱신, 제안 ROLLED_BACK 처리, 성과 스냅샷 기본 생성을 단일 DB 트랜잭션으로 처리합니다."""
        import json
        import hashlib
        async with get_db_conn(self.db_path) as db:
            # 1. 대상 버전 설정 조회
            async with db.execute(
                "SELECT * FROM strategy_parameter_history WHERE strategy_id = ? AND version_id = ?",
                (strategy_id, version_id)
            ) as cursor:
                hist_row = await cursor.fetchone()
                if not hist_row:
                    raise ValueError(f"Target parameter version {version_id} not found for strategy {strategy_id}")
                target_version = dict(hist_row)
                target_version["new_params"] = json.loads(target_version["new_params"])

            # 2. 현재 적용중인 버전 정보 획득
            async with db.execute("SELECT * FROM strategy_versions WHERE strategy_id = ?", (strategy_id,)) as cursor:
                ver_row = await cursor.fetchone()
                if not ver_row:
                    raise ValueError(f"No active strategy version found to rollback for strategy {strategy_id}")
                current_version = dict(ver_row)
                current_version["current_params"] = json.loads(current_version["current_params"])

            current_version_id = current_version["current_version_id"]
            new_version_id = current_version_id + 1

            # 3. parameter history 기록 (ROLLBACK)
            await db.execute("UPDATE strategy_parameter_history SET is_current = 0 WHERE strategy_id = ?", (strategy_id,))
            await db.execute('''
                INSERT INTO strategy_parameter_history 
                (strategy_id, version_id, parent_version_id, old_params, new_params, proposal_id, is_current, changed_by, change_reason, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (strategy_id, new_version_id, version_id, json.dumps(current_version["current_params"]), json.dumps(target_version["new_params"]), None, 1, 'USER', 'ROLLBACK', applied_ts))

            # 4. strategy version 갱신
            await db.execute('''
                INSERT INTO strategy_versions (strategy_id, current_version_id, current_params, rollback_source_version, applied_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(strategy_id) DO UPDATE SET
                    current_version_id = excluded.current_version_id,
                    current_params = excluded.current_params,
                    rollback_source_version = excluded.rollback_source_version,
                    applied_at = excluded.applied_at,
                    updated_at = excluded.updated_at
            ''', (strategy_id, new_version_id, json.dumps(target_version["new_params"]), current_version_id, applied_ts, applied_ts))

            # 5. 롤백 원인이 된 문제 버전(current_version_id)과 연계된 제안 ROLLED_BACK 처리
            async with db.execute(
                "SELECT proposal_id FROM strategy_parameter_history WHERE strategy_id = ? AND version_id = ?",
                (strategy_id, current_version_id)
            ) as cursor:
                curr_hist_row = await cursor.fetchone()
            
            prop_id = None
            if curr_hist_row and dict(curr_hist_row).get("proposal_id"):
                prop_id = dict(curr_hist_row)["proposal_id"]
                await db.execute('''
                    UPDATE strategy_proposals 
                    SET status = 'ROLLED_BACK', outcome = 'ROLLED_BACK', rolled_back_at = ?, updated_at = ?
                    WHERE id = ?
                ''', (applied_ts, applied_ts, prop_id))

            # 6. 성과 스냅샷 기본 생성 (SYNC)
            param_hash = hashlib.md5(json.dumps(target_version["new_params"], sort_keys=True).encode('utf-8')).hexdigest()
            cursor = await db.execute('''
                INSERT INTO strategy_performance_snapshots 
                (strategy_id, version_id, parameter_hash, snapshot_type, timestamp, roi, mdd, profit_factor, win_rate, trade_count, created_at)
                VALUES (?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, 0, ?)
            ''', (strategy_id, new_version_id, param_hash, 'ROLLBACK', applied_ts, applied_ts))
            snapshot_id = cursor.lastrowid

            await db.commit()

            return {
                "strategy_id": strategy_id,
                "new_version_id": new_version_id,
                "rollback_version_id": version_id,
                "target_params": target_version["new_params"],
                "associated_proposal_id": prop_id,
                "snapshot_id": snapshot_id
            }

    async def enrich_snapshot_metrics_async(self, snapshot_id: int, portfolio_id: str):
        """비동기 백그라운드 태스크로 성과 스냅샷의 ROI/MDD/PF 등의 지표를 계산하여 업데이트합니다."""
        try:
            portfolio = None
            if self.system and hasattr(self.system, 'portfolio_manager'):
                portfolio = self.system.portfolio_manager.portfolios.get(portfolio_id)
            
            if not portfolio:
                from src.engine.portfolio import PortfolioManager
                pm = PortfolioManager(db_path=self.db_path)
                loaded = await pm.repository.load_portfolios()
                portfolio = loaded.get(portfolio_id)
                
            if not portfolio:
                logger.warning(f"[Snapshot Enrichment] Portfolio {portfolio_id} not found. Cannot calculate metrics.")
                return

            current_prices = {}
            for pos_key, pos in portfolio.positions.items():
                symbol = pos.symbol
                if self.system and hasattr(self.system, 'get_latest_price'):
                    price_info = self.system.get_latest_price(pos.exchange_id, symbol)
                    if price_info and price_info.get("trade_price"):
                        current_prices[symbol] = price_info["trade_price"]
                if symbol not in current_prices:
                    current_prices[symbol] = pos.avg_price

            from src.engine.utils.performance import calculate_performance_metrics
            metrics = calculate_performance_metrics(
                history=portfolio.history,
                initial_cash=portfolio.initial_cash,
                current_cash=portfolio.cash,
                positions=portfolio.positions,
                current_prices=current_prices
            )

            async with get_db_conn(self.db_path) as db:
                await db.execute('''
                    UPDATE strategy_performance_snapshots
                    SET roi = ?, mdd = ?, profit_factor = ?, win_rate = ?, trade_count = ?
                    WHERE id = ?
                ''', (
                    metrics["roi"],
                    metrics["mdd"],
                    metrics["profit_factor"],
                    metrics["win_rate"],
                    metrics["trade_count"],
                    snapshot_id
                ))
                await db.commit()
                
            logger.info(f"[Snapshot Enrichment] Snapshot #{snapshot_id} enriched successfully: ROI={metrics['roi']}%")
            
            if self.system and self.system.broadcast_callback:
                await self.system.broadcast_callback({
                    "type": "snapshot_enriched",
                    "snapshot_id": snapshot_id,
                    "metrics": metrics
                })
        except Exception as e:
            logger.error(f"[Snapshot Enrichment] Failed to enrich snapshot #{snapshot_id}: {e}")

    async def insert_girs_shadow_metric(self, metric_data: Dict[str, Any]) -> int:
        async with get_db_conn(self.db_path) as db:
            cursor = await db.execute('''
                INSERT INTO girs_shadow_metrics 
                (timestamp, proposal_id, strategy_id, model_risk_score, fallback_risk_score, 
                 final_promotion_score, shadow_risk_score, replay_drift, correction_active,
                 operation_mode, model_version, scaler_version, strategy_version_id,
                 simulation_session_id, decision_type, blocked_reason,
                 trade_age_ms, orderbook_age_ms, indicator_age_ms, is_fresh, stale_reason,
                 snapshot_version, snapshot_hash, feature_vector_hash, orderbook_available,
                 market_type, session_state, volatility_regime, liquidity_regime, exchange_id,
                 tps, trade_count, volume, idle_time)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                metric_data["timestamp"],
                metric_data.get("proposal_id"),
                metric_data.get("strategy_id"),
                metric_data.get("model_risk_score"),
                metric_data.get("fallback_risk_score"),
                metric_data.get("final_promotion_score"),
                metric_data.get("shadow_risk_score"),
                metric_data.get("replay_drift"),
                1 if metric_data.get("correction_active", False) else 0,
                metric_data.get("operation_mode"),
                metric_data.get("model_version"),
                metric_data.get("scaler_version"),
                metric_data.get("strategy_version_id"),
                metric_data.get("simulation_session_id"),
                metric_data.get("decision_type"),
                metric_data.get("blocked_reason"),
                metric_data.get("trade_age_ms"),
                metric_data.get("orderbook_age_ms"),
                metric_data.get("indicator_age_ms"),
                metric_data.get("is_fresh", 1),
                metric_data.get("stale_reason"),
                metric_data.get("snapshot_version"),
                metric_data.get("snapshot_hash"),
                metric_data.get("feature_vector_hash"),
                metric_data.get("orderbook_available", 0),
                metric_data.get("market_type"),
                metric_data.get("session_state"),
                metric_data.get("volatility_regime"),
                metric_data.get("liquidity_regime"),
                metric_data.get("exchange_id") or metric_data.get("exchange"),
                metric_data.get("tps"),
                metric_data.get("trade_count"),
                metric_data.get("volume"),
                metric_data.get("idle_time")
            ))
            inserted_id = cursor.lastrowid
            await db.commit()
            return inserted_id

    async def get_girs_shadow_metrics(self, limit: int = 1000) -> List[Dict[str, Any]]:
        async with get_db_conn(self.db_path) as db:
            async with db.execute("SELECT * FROM girs_shadow_metrics ORDER BY timestamp DESC LIMIT ?", (limit,)) as cursor:
                rows = await cursor.fetchall()
                return [dict(r) for r in rows]

    async def insert_planned_asset_event(
        self,
        exchange_id: str,
        symbol: str,
        event_type: str,
        scheduled_at: str,
        notice_url: Optional[str] = None
    ) -> int:
        async with get_db_conn(self.db_path) as db:
            cursor = await db.execute('''
                INSERT OR IGNORE INTO planned_asset_events (exchange_id, symbol, event_type, scheduled_at, notice_url, status)
                VALUES (?, ?, ?, ?, ?, 'PLANNED')
            ''', (exchange_id, symbol, event_type, scheduled_at, notice_url))
            inserted_id = cursor.lastrowid
            row_count = cursor.rowcount
            await db.commit()
            return inserted_id if row_count > 0 else 0

    async def get_planned_asset_events(
        self,
        status: Optional[str] = None,
        exchange_id: Optional[str] = None,
        event_id: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        async with get_db_conn(self.db_path) as db:
            query = "SELECT * FROM planned_asset_events WHERE 1=1"
            params = []
            if status:
                query += " AND status = ?"
                params.append(status)
            if exchange_id:
                query += " AND exchange_id = ?"
                params.append(exchange_id)
            if event_id is not None:
                query += " AND id = ?"
                params.append(event_id)
            query += " ORDER BY scheduled_at ASC"
            async with db.execute(query, params) as cursor:
                rows = await cursor.fetchall()
                return [dict(r) for r in rows]

    async def get_executable_planned_events(
        self,
        before_minutes: int = 30
    ) -> List[Dict[str, Any]]:
        import datetime
        limit_time = (datetime.datetime.now() + datetime.timedelta(minutes=before_minutes)).strftime('%Y-%m-%d %H:%M:%S')
        async with get_db_conn(self.db_path) as db:
            async with db.execute('''
                SELECT * FROM planned_asset_events 
                WHERE status = 'PLANNED' AND scheduled_at <= ?
                ORDER BY scheduled_at ASC
            ''', (limit_time,)) as cursor:
                rows = await cursor.fetchall()
                return [dict(r) for r in rows]

    async def update_planned_event_status(
        self,
        event_id: int,
        status: str
    ) -> bool:
        async with get_db_conn(self.db_path) as db:
            cursor = await db.execute('''
                UPDATE planned_asset_events 
                SET status = ?, updated_at = datetime('now', 'localtime')
                WHERE id = ?
            ''', (status, event_id))
            await db.commit()
            return cursor.rowcount > 0

    async def delete_planned_event(
        self,
        event_id: int
    ) -> bool:
        async with get_db_conn(self.db_path) as db:
            cursor = await db.execute('''
                DELETE FROM planned_asset_events 
                WHERE id = ? AND status = 'PLANNED'
            ''', (event_id,))
            await db.commit()
            return cursor.rowcount > 0

    async def update_exchange_asset_status(
        self,
        exchange_id: str,
        symbol: str,
        is_active: int,
        is_delisted: int = 0
    ) -> bool:
        async with get_db_conn(self.db_path) as db:
            async with db.execute('''
                SELECT 1 FROM exchange_assets WHERE exchange_id = ? AND symbol = ?
            ''', (exchange_id, symbol)) as cursor:
                exists = await cursor.fetchone()
            
            if exists:
                cursor = await db.execute('''
                    UPDATE exchange_assets 
                    SET is_active = ?, is_delisted = ?, updated_at = datetime('now', 'localtime')
                    WHERE exchange_id = ? AND symbol = ?
                ''', (is_active, is_delisted, exchange_id, symbol))
            else:
                cursor = await db.execute('''
                    INSERT INTO exchange_assets (exchange_id, symbol, is_active, is_delisted, created_at, updated_at)
                    VALUES (?, ?, ?, ?, datetime('now', 'localtime'), datetime('now', 'localtime'))
                ''', (exchange_id, symbol, is_active, is_delisted))
            await db.commit()
            return cursor.rowcount > 0

    async def upsert_asset_master_if_not_exists(
        self,
        symbol: str,
        korean_name: str,
        asset_type: str
    ) -> bool:
        async with get_db_conn(self.db_path) as db:
            async with db.execute('''
                SELECT 1 FROM asset_master WHERE symbol = ?
            ''', (symbol,)) as cursor:
                exists = await cursor.fetchone()
            
            if not exists:
                cursor = await db.execute('''
                    INSERT INTO asset_master (symbol, korean_name, asset_type, created_at, updated_at)
                    VALUES (?, ?, ?, datetime('now', 'localtime'), datetime('now', 'localtime'))
                ''', (symbol, korean_name, asset_type))
                await db.commit()
                return cursor.rowcount > 0
            return False


class InMemoryTradingRepository(BaseTradingRepository):
    """
    단위 테스트 및 오프라인 시뮬레이션용 초고속 인메모리 트레이딩 저장소 어댑터입니다.
    """
    def __init__(
        self,
        girs_shadow_mode_override: Optional[bool] = None,
        auto_strategy_promotion_enabled_override: Optional[bool] = None,
        champion_cooldown_days: float = 7.0,
        champion_cooldown_trades: int = 100
    ):
        import sys
        self.champion_cooldown_days = champion_cooldown_days
        self.champion_cooldown_trades = champion_cooldown_trades
        is_pytest = "pytest" in sys.modules
        self.girs_shadow_mode_override = girs_shadow_mode_override if girs_shadow_mode_override is not None else (False if is_pytest else None)
        self.auto_strategy_promotion_enabled_override = auto_strategy_promotion_enabled_override if auto_strategy_promotion_enabled_override is not None else (True if is_pytest else None)
        
        self.portfolios: Dict[str, Any] = {}
        self.exchange_configs: Dict[str, Dict[str, Any]] = {}
        self.order_histories: List[Dict[str, Any]] = []
        self.alerts: List[Dict[str, Any]] = []
        self.system_events: List[Dict[str, Any]] = []
        self.strategy_versions: Dict[str, Dict[str, Any]] = {}
        self.strategy_parameter_histories: Dict[str, List[Dict[str, Any]]] = {}
        self.strategy_performance_snapshots: Dict[str, List[Dict[str, Any]]] = {}
        self.strategy_proposals: Dict[int, Dict[str, Any]] = {}
        self.proposal_evaluations: Dict[int, Dict[str, Any]] = {}
        self.proposal_evaluations_v2: Dict[int, Dict[str, Any]] = {}
        self.universe_guard_states: Dict[str, Dict[str, Any]] = {}
        self.promotion_event_logs: List[Dict[str, Any]] = []
        self.next_proposal_id = 1
        self.next_eval_id = 1
        self.next_history_id = 1
        self.planned_asset_events: List[Dict[str, Any]] = []
        self.exchange_assets: Dict[tuple, Dict[str, Any]] = {}
        self.asset_master: Dict[str, Dict[str, Any]] = {}

    async def save_portfolio(self, portfolio: Any):
        self.portfolios[portfolio.id] = portfolio

    async def load_portfolios(self, exclude_types: list = None) -> Dict[str, Any]:
        if exclude_types:
            return {k: v for k, v in self.portfolios.items() if v.portfolio_type not in exclude_types}
        return self.portfolios

    async def insert_order_history(self, portfolio_id: str, order: Dict[str, Any]):
        order_copy = dict(order)
        order_copy['portfolio_id'] = portfolio_id
        self.order_histories.append(order_copy)
        if portfolio_id in self.portfolios:
            self.portfolios[portfolio_id].history.append(order_copy)

    async def get_orders_history(self, portfolio_id: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        filtered = [t for t in self.order_histories if t.get('portfolio_id') == portfolio_id]
        sorted_desc = sorted(filtered, key=lambda x: x.get('timestamp', 0), reverse=True)
        if limit is not None and limit > 0:
            sliced = sorted_desc[:limit]
        else:
            sliced = sorted_desc
        return sorted(sliced, key=lambda x: x.get('timestamp', 0))

    async def insert_alert(self, alert: Dict[str, Any]):
        required_keys = ['exchange_id', 'code', 'price', 'msg', 'timestamp']
        missing_keys = [k for k in required_keys if k not in alert]
        if missing_keys:
            raise ValueError(f"Required fields missing from alert dictionary: {missing_keys}")
        self.alerts.append(alert)

    async def load_exchange_configs(self) -> Dict[str, Dict[str, Any]]:
        return self.exchange_configs

    async def insert_system_event(self, event_type: str, target: str, message: str, timestamp: Optional[int] = None, context: Optional[str] = None):
        ts = timestamp if timestamp is not None else int(time.time() * 1000)
        self.system_events.append({
            "event_type": event_type,
            "target": target,
            "message": message,
            "timestamp": ts,
            "context": context
        })

    async def get_system_events(self, limit: int = 20) -> List[Dict[str, Any]]:
        sorted_events = sorted(self.system_events, key=lambda x: x['timestamp'], reverse=True)
        return sorted_events[:limit]

    async def clean_old_system_events(self, retention_days: int = 7):
        cutoff_ts_short = int((time.time() - retention_days * 24 * 3600) * 1000)
        cutoff_ts_long = int((time.time() - 90 * 24 * 3600) * 1000)
        
        filtered = []
        for e in self.system_events:
            evt_type = e.get("event_type")
            ts = e.get("timestamp")
            if evt_type in ('PROMOTION_COOLDOWN_BLOCKED', 'PROMOTION_QUOTA_BLOCKED', 'PROMOTION_LIMIT_BLOCKED', 'UNIVERSE_GUARD_SUMMARY'):
                if ts >= cutoff_ts_short:
                    filtered.append(e)
            elif evt_type in ('UNIVERSE_PROMOTION', 'UNIVERSE_DEMOTION'):
                if ts >= cutoff_ts_long:
                    filtered.append(e)
            else:
                filtered.append(e)
        self.system_events = filtered

    async def upsert_universe_guard_state(self, exchange_id: str, market_type: str, symbol: str, status: str, blocked_reason: Optional[str], blocked_count: int, last_blocked_at: Optional[float], last_event_logged_reason: Optional[str]):
        key = (exchange_id, market_type, symbol)
        existing = self.universe_guard_states.get(key, {})
        prev_reason = existing.get("blocked_reason")
        
        if prev_reason == blocked_reason:
            new_count = existing.get("blocked_count", 0) + blocked_count
        else:
            new_count = blocked_count
            
        self.universe_guard_states[key] = {
            "exchange_id": exchange_id,
            "market_type": market_type,
            "symbol": symbol,
            "status": status,
            "blocked_reason": blocked_reason,
            "blocked_count": new_count,
            "last_blocked_at": last_blocked_at,
            "last_event_logged_reason": last_event_logged_reason
        }

    async def get_universe_guard_state(self, exchange_id: str, market_type: str, symbol: str) -> Optional[Dict[str, Any]]:
        key = (exchange_id, market_type, symbol)
        return self.universe_guard_states.get(key)

    async def get_all_universe_guard_states(self) -> List[Dict[str, Any]]:
        return list(self.universe_guard_states.values())

    async def check_and_report_previous_crash(self, target: str):
        daemon_events = [e for e in self.system_events if e['target'] == target and e['event_type'] in ('DAEMON_START', 'DAEMON_STOP', 'DAEMON_CRASHED')]
        if daemon_events:
            sorted_daemon = sorted(daemon_events, key=lambda x: x['timestamp'], reverse=True)
            last_event = sorted_daemon[0]
            if last_event['event_type'] == 'DAEMON_START':
                crash_ts = last_event['timestamp'] + 1
                from datetime import datetime
                last_start_time = datetime.fromtimestamp(last_event['timestamp'] / 1000).strftime('%Y-%m-%d %H:%M:%S')
                message = f"이전 데몬 프로세스가 정상 종료(DAEMON_STOP) 처리되지 못하고 비정상 종료(크래쉬)되었음을 감지하여 보완 기록합니다. (이전 정상 기동 시각: {last_start_time})"
                self.system_events.append({
                    "event_type": "DAEMON_CRASHED",
                    "target": target,
                    "message": message,
                    "timestamp": crash_ts
                })

    async def get_all_strategy_versions(self) -> List[Dict[str, Any]]:
        results = []
        for s_id, ver in self.strategy_versions.items():
            results.append({
                "strategy_id": s_id,
                "current_version_id": ver["current_version_id"],
                "current_params": ver["current_params"],
                "rollback_source_version": ver["rollback_source_version"],
                "applied_at": ver["applied_at"]
            })
        return results

    async def get_strategy_version(self, strategy_id: str) -> Optional[Dict[str, Any]]:
        return self.strategy_versions.get(strategy_id)

    async def save_strategy_version(self, strategy_id: str, version_id: int, params: Dict[str, Any], applied_at: int, rollback_source_version: Optional[int] = None):
        self.strategy_versions[strategy_id] = {
            "strategy_id": strategy_id,
            "current_version_id": version_id,
            "current_params": params,
            "rollback_source_version": rollback_source_version,
            "applied_at": applied_at
        }

    async def insert_strategy_parameter_history(self, strategy_id: str, version_id: int, parent_version_id: Optional[int], old_params: Optional[str], new_params: str, proposal_id: Optional[int], is_current: int, changed_by: str, change_reason: str) -> int:
        if is_current == 1:
            if strategy_id in self.strategy_parameter_histories:
                for h in self.strategy_parameter_histories[strategy_id]:
                    h["is_current"] = 0
                    
        history_id = self.next_history_id
        self.next_history_id += 1
        
        history_item = {
            "id": history_id,
            "strategy_id": strategy_id,
            "version_id": version_id,
            "parent_version_id": parent_version_id,
            "old_params": old_params,
            "new_params": new_params,
            "proposal_id": proposal_id,
            "is_current": is_current,
            "changed_by": changed_by,
            "change_reason": change_reason,
            "created_at": time.time()
        }
        
        if strategy_id not in self.strategy_parameter_histories:
            self.strategy_parameter_histories[strategy_id] = []
        self.strategy_parameter_histories[strategy_id].append(history_item)
        return history_id

    async def get_strategy_parameter_history(self, strategy_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        hist = self.strategy_parameter_histories.get(strategy_id, [])
        sorted_hist = sorted(hist, key=lambda x: x["version_id"], reverse=True)
        return sorted_hist[:limit]

    async def get_strategy_parameter_version(self, strategy_id: str, version_id: int) -> Optional[Dict[str, Any]]:
        hist = self.strategy_parameter_histories.get(strategy_id, [])
        for h in hist:
            if h["version_id"] == version_id:
                import json
                res = dict(h)
                if isinstance(res.get("new_params"), str):
                    res["new_params"] = json.loads(res["new_params"])
                if isinstance(res.get("old_params"), str):
                    res["old_params"] = json.loads(res["old_params"])
                return res
        return None

    async def insert_strategy_performance_snapshot(self, snapshot_data: Dict[str, Any]):
        strategy_id = snapshot_data["strategy_id"]
        if strategy_id not in self.strategy_performance_snapshots:
            self.strategy_performance_snapshots[strategy_id] = []
        self.strategy_performance_snapshots[strategy_id].append(dict(snapshot_data))

    async def get_strategy_performance_snapshots(self, strategy_id: str, version_id: Optional[int] = None, limit: int = 100) -> List[Dict[str, Any]]:
        snapshots = self.strategy_performance_snapshots.get(strategy_id, [])
        if version_id is not None:
            snapshots = [s for s in snapshots if s["version_id"] == version_id]
        sorted_snaps = sorted(snapshots, key=lambda x: x["timestamp"], reverse=True)
        return sorted_snaps[:limit]

    async def insert_strategy_proposal(self, proposal_data: Dict[str, Any]) -> int:
        proposal_id = self.next_proposal_id
        self.next_proposal_id += 1
        
        prop_copy = dict(proposal_data)
        prop_copy["id"] = proposal_id
        
        if prop_copy.get("confidence_score", 50) < 60:
            prop_copy["status"] = "PRUNED"
            
        self.strategy_proposals[proposal_id] = prop_copy
        return proposal_id

    async def get_counterfactual_targets(self, limit: int = 20) -> List[Dict[str, Any]]:
        props = [p for p in self.strategy_proposals.values() if p.get("is_counterfactual_tracked") == 1]
        props_sorted = sorted(props, key=lambda x: x.get("created_at", 0), reverse=True)
        return props_sorted[:limit]

    async def update_counterfactual_metrics(self, proposal_id: int, roi: float, mdd: float, track_status: int):
        if proposal_id in self.strategy_proposals:
            p = self.strategy_proposals[proposal_id]
            p["counterfactual_roi"] = roi
            p["counterfactual_mdd"] = mdd
            p["is_counterfactual_tracked"] = track_status
            p["updated_at"] = time.time()

    async def update_strategy_proposal_status(self, proposal_id: int, status: str, outcome: Optional[str] = None, applied_at: Optional[int] = None, rolled_back_at: Optional[int] = None):
        if proposal_id in self.strategy_proposals:
            p = self.strategy_proposals[proposal_id]
            p["status"] = status
            p["updated_at"] = time.time()
            if outcome is not None:
                p["outcome"] = outcome
            if applied_at is not None:
                p["applied_at"] = applied_at
            if rolled_back_at is not None:
                p["rolled_back_at"] = rolled_back_at

    async def get_strategy_proposal(self, proposal_id: int) -> Optional[Dict[str, Any]]:
        return self.strategy_proposals.get(proposal_id)

    async def get_active_proposals(self, strategy_id: Optional[str] = None, status: Optional[str] = None) -> List[Dict[str, Any]]:
        props = list(self.strategy_proposals.values())
        if strategy_id is not None:
            props = [p for p in props if p["strategy_id"] == strategy_id]
        if status is not None:
            props = [p for p in props if p["status"] == status]
        return sorted(props, key=lambda x: x.get("created_at", 0), reverse=True)

    async def insert_proposal_evaluation(self, eval_data: Dict[str, Any], legacy_compat: bool = False) -> int:
        horizon_name = eval_data.get("horizon_name")
        if not horizon_name or str(horizon_name).strip() == "":
            if not legacy_compat:
                raise ValueError("horizon_name is required for proposal evaluation")
            else:
                horizon_name = "7d"
                logger.warning(
                    f"[InMemoryTradingRepository] LEGACY_HORIZON_DEFAULT_APPLIED: "
                    f"Proposal ID {eval_data.get('proposal_id')} has missing or empty horizon_name. "
                    f"Automatically defaulted to '7d'."
                )
                await self.insert_system_event(
                    event_type="LEGACY_HORIZON_DEFAULT_APPLIED",
                    target="proposal_evaluations",
                    message=f"Proposal ID {eval_data.get('proposal_id')} has missing or empty horizon_name. Defaulted to 7d.",
                    context=f"proposal_id={eval_data.get('proposal_id')}"
                )

        eval_copy = dict(eval_data)
        eval_copy["horizon_name"] = horizon_name
        self.proposal_evaluations[eval_copy["proposal_id"]] = eval_copy
        
        eval_copy["id"] = self.next_eval_id
        if "evaluation_status" not in eval_copy:
            eval_copy["evaluation_status"] = "PENDING"
        if "retry_count" not in eval_copy:
            eval_copy["retry_count"] = 0
        self.proposal_evaluations_v2[self.next_eval_id] = eval_copy
        self.next_eval_id += 1
        return eval_copy["id"]

    async def get_proposal_evaluation(self, proposal_id: int) -> Optional[Dict[str, Any]]:
        return self.proposal_evaluations.get(proposal_id)

    async def get_proposal_evaluations(self, proposal_id: int) -> List[Dict[str, Any]]:
        res = []
        for v in self.proposal_evaluations_v2.values():
            if v.get("proposal_id") == proposal_id:
                res.append(dict(v))
        if not res:
            for v in self.proposal_evaluations.values():
                if v.get("proposal_id") == proposal_id:
                    res.append(dict(v))
        return res

    async def get_expired_pending_evaluations(self, now: int) -> List[Dict[str, Any]]:
        res = []
        for v in self.proposal_evaluations_v2.values():
            due_at = v.get("due_at", 0)
            if v.get("evaluation_status") == "PENDING" and due_at <= now:
                res.append(dict(v))
        return res

    async def get_pending_evaluations_without_baseline(self, now: int) -> List[Dict[str, Any]]:
        res = []
        for v in self.proposal_evaluations_v2.values():
            due_at = v.get("due_at", 0)
            hz_val = v.get("horizon_value", 0)
            if v.get("evaluation_status") == "PENDING" and v.get("baseline_value") is None and (due_at - hz_val) <= now:
                res.append(dict(v))
        return res

    async def update_baseline_snapshot(self, pe_id: int, value: float, ts: int, vol: int = 0):
        v = self.proposal_evaluations_v2.get(pe_id)
        if v:
            v["baseline_value"] = value
            v["baseline_timestamp"] = ts
            v["baseline_volume"] = vol

    async def claim_evaluation(self, pe_id: int, locked_at: int) -> bool:
        v = self.proposal_evaluations_v2.get(pe_id)
        if v and v.get("evaluation_status") == "PENDING":
            v["evaluation_status"] = "EVALUATING"
            v["locked_at"] = locked_at
            return True
        return False

    async def complete_evaluation(self, pe_id: int, actual_roi: float, roi_div: float, actual_trades: int, trade_div: int, evaluated_at: int):
        v = self.proposal_evaluations_v2.get(pe_id)
        if v:
            v["evaluation_status"] = "COMPLETED"
            v["evaluated_at"] = evaluated_at
            v["actual_roi_7d"] = actual_roi
            v["roi_divergence"] = roi_div
            v["actual_trade_count_7d"] = actual_trades
            v["trade_count_divergence"] = trade_div
            v["locked_at"] = None

    async def fail_evaluation(self, pe_id: int, error_msg: str, retry_count: int, max_retries: int):
        v = self.proposal_evaluations_v2.get(pe_id)
        if v:
            if retry_count < max_retries:
                v["evaluation_status"] = "PENDING"
                v["retry_count"] = retry_count + 1
                v["locked_at"] = None
                v["last_error"] = error_msg
            else:
                v["evaluation_status"] = "ERROR"
                v["locked_at"] = None
                v["last_error"] = error_msg

    async def get_stale_evaluating_evaluations(self, cutoff: int) -> List[Dict[str, Any]]:
        res = []
        for v in self.proposal_evaluations_v2.values():
            locked = v.get("locked_at")
            if v.get("evaluation_status") == "EVALUATING" and locked is not None and locked < cutoff:
                res.append(dict(v))
        return res

    async def recover_stale_evaluation(self, pe_id: int, retry_count: int, max_retries: int, error_msg: str):
        await self.fail_evaluation(pe_id, error_msg, retry_count, max_retries)

    async def get_unevaluated_applied_proposals(self) -> List[Dict[str, Any]]:
        props = list(self.strategy_proposals.values())
        seven_days_ago_ms = int((time.time() - 7 * 24 * 3600) * 1000)
        res = []
        for p in props:
            applied_at = p.get("applied_at")
            if p["status"] == "APPLIED" and p.get("outcome") == "RUNNING" and applied_at and applied_at <= seven_days_ago_ms:
                res.append(p)
        return res

    async def approve_proposal_atomic(self, proposal_id: int, applied_ts: int) -> Dict[str, Any]:
        # 섀도 모드/자동 승격 비활성화 시 실제 파라미터 업데이트 차단 (2차 안전 가드)
        from src.config.manager import ConfigManager
        config_manager = ConfigManager("config/settings.yaml")
        
        girs_shadow_mode = self.girs_shadow_mode_override
        if girs_shadow_mode is None:
            girs_shadow_mode = config_manager.get("system.girs_shadow_mode", False)
            
        auto_strategy_promotion_enabled = self.auto_strategy_promotion_enabled_override
        if auto_strategy_promotion_enabled is None:
            auto_strategy_promotion_enabled = config_manager.get("system.auto_strategy_promotion_enabled", False)
            
        if girs_shadow_mode or not auto_strategy_promotion_enabled:
            raise ValueError("Promotion blocked: Shadow operation mode active or auto promotion disabled")

        import json
        import hashlib
        p = self.strategy_proposals.get(proposal_id)
        if not p:
            raise ValueError("Proposal not found")
            
        strategy_id = p["strategy_id"]
        curr_ver = self.strategy_versions.get(strategy_id)
        if curr_ver:
            applied_at_ms = curr_ver["applied_at"]
            elapsed_seconds = (applied_ts - applied_at_ms) / 1000.0
            elapsed_days = elapsed_seconds / (24 * 3600.0)
            
            # orders_history (self.order_histories)에서 체결 완료 건수 계산 (quantity > 0 및 price > 0, portfolio_id 격리)
            portfolio_id = p.get("portfolio_id")
            applied_at_sec = applied_at_ms / 1000.0
            trade_count = sum(
                1 for o in self.order_histories
                if o.get("strategy_id") == strategy_id 
                and o.get("portfolio_id") == portfolio_id
                and o.get("timestamp", 0) >= applied_at_sec 
                and o.get("quantity", 0.0) > 0.0
                and o.get("price", 0.0) > 0.0
            )
            
            if elapsed_days < self.champion_cooldown_days or trade_count < self.champion_cooldown_trades:
                raise ChampionCooldownBlockedError(
                    f"Promotion blocked by Champion Cooldown: Strategy {strategy_id} is in cooldown. "
                    f"Active for {elapsed_days:.2f} days and {trade_count} trades. "
                    f"Required: >= {self.champion_cooldown_days} days and >= {self.champion_cooldown_trades} trades."
                )
        p["status"] = "APPLIED"
        p["outcome"] = "RUNNING"
        p["applied_at"] = applied_ts
        p["updated_at"] = applied_ts
        
        strategy_id = p["strategy_id"]
        new_version_id = 1
        curr_ver = self.strategy_versions.get(strategy_id)
        if curr_ver:
            new_version_id = curr_ver["current_version_id"] + 1
            
        self.strategy_versions[strategy_id] = {
            "strategy_id": strategy_id,
            "current_version_id": new_version_id,
            "current_params": p["proposed_params"],
            "rollback_source_version": None,
            "applied_at": applied_ts
        }
        
        await self.insert_strategy_parameter_history(
            strategy_id=strategy_id,
            version_id=new_version_id,
            parent_version_id=new_version_id - 1 if new_version_id > 1 else None,
            old_params=json.dumps(p["original_params"]),
            new_params=json.dumps(p["proposed_params"]),
            proposal_id=proposal_id,
            is_current=1,
            changed_by="USER",
            change_reason="PROPOSAL_APPLY"
        )
        
        param_hash = hashlib.md5(json.dumps(p["proposed_params"], sort_keys=True).encode('utf-8')).hexdigest()
        snapshot_item = {
            "strategy_id": strategy_id,
            "version_id": new_version_id,
            "parameter_hash": param_hash,
            "snapshot_type": "PARAMETER_CHANGE",
            "timestamp": applied_ts,
            "roi": None,
            "mdd": None,
            "profit_factor": None,
            "win_rate": None,
            "trade_count": 0,
            "created_at": applied_ts
        }
        if strategy_id not in self.strategy_performance_snapshots:
            self.strategy_performance_snapshots[strategy_id] = []
        self.strategy_performance_snapshots[strategy_id].append(snapshot_item)
        
        return {
            "strategy_id": strategy_id,
            "portfolio_id": p["portfolio_id"],
            "new_version_id": new_version_id,
            "proposed_params": p["proposed_params"],
            "snapshot_id": len(self.strategy_performance_snapshots[strategy_id])
        }

    async def rollback_strategy_atomic(self, strategy_id: str, version_id: int, applied_ts: int) -> Dict[str, Any]:
        import json
        import hashlib
        hist = self.strategy_parameter_histories.get(strategy_id, [])
        target_hist = None
        for h in hist:
            if h["version_id"] == version_id:
                target_hist = h
                break
        if not target_hist:
            raise ValueError("Target version not found")
            
        curr_ver = self.strategy_versions.get(strategy_id)
        current_version_id = curr_ver["current_version_id"] if curr_ver else 1
        new_version_id = current_version_id + 1
        
        self.strategy_versions[strategy_id] = {
            "strategy_id": strategy_id,
            "current_version_id": new_version_id,
            "current_params": json.loads(target_hist["new_params"]) if isinstance(target_hist["new_params"], str) else target_hist["new_params"],
            "rollback_source_version": current_version_id,
            "applied_at": applied_ts
        }
        
        await self.insert_strategy_parameter_history(
            strategy_id=strategy_id,
            version_id=new_version_id,
            parent_version_id=version_id,
            old_params=json.dumps(curr_ver["current_params"]) if curr_ver else None,
            new_params=target_hist["new_params"] if isinstance(target_hist["new_params"], str) else json.dumps(target_hist["new_params"]),
            proposal_id=None,
            is_current=1,
            changed_by="USER",
            change_reason="ROLLBACK"
        )
        
        prop_id = None
        for p in self.strategy_proposals.values():
            if p["strategy_id"] == strategy_id and p["status"] == "APPLIED":
                p["status"] = "ROLLED_BACK"
                p["outcome"] = "ROLLED_BACK"
                p["rolled_back_at"] = applied_ts
                p["updated_at"] = applied_ts
                prop_id = p["id"]
                break
                
        param_hash = hashlib.md5(json.dumps(self.strategy_versions[strategy_id]["current_params"], sort_keys=True).encode('utf-8')).hexdigest()
        snapshot_item = {
            "strategy_id": strategy_id,
            "version_id": new_version_id,
            "parameter_hash": param_hash,
            "snapshot_type": "ROLLBACK",
            "timestamp": applied_ts,
            "roi": None,
            "mdd": None,
            "profit_factor": None,
            "win_rate": None,
            "trade_count": 0,
            "created_at": applied_ts
        }
        if strategy_id not in self.strategy_performance_snapshots:
            self.strategy_performance_snapshots[strategy_id] = []
        self.strategy_performance_snapshots[strategy_id].append(snapshot_item)
        
        return {
            "strategy_id": strategy_id,
            "new_version_id": new_version_id,
            "rollback_version_id": version_id,
            "target_params": self.strategy_versions[strategy_id]["current_params"],
            "associated_proposal_id": prop_id,
            "snapshot_id": len(self.strategy_performance_snapshots[strategy_id])
        }

    async def enrich_snapshot_metrics_async(self, snapshot_id: int, portfolio_id: str):
        if portfolio_id in self.portfolios:
            # 적당히 모의 ROI 업데이트
            snaps = self.strategy_performance_snapshots.get("rsistrategy", [])
            for s in snaps:
                if s["roi"] is None:
                    s["roi"] = 3.5
                    s["mdd"] = 0.8
                    s["profit_factor"] = 1.3
                    s["win_rate"] = 62.0
                    s["trade_count"] = 8

    async def insert_girs_shadow_metric(self, metric_data: Dict[str, Any]) -> int:
        if not hasattr(self, "girs_shadow_metrics"):
            self.girs_shadow_metrics = []
        self.girs_shadow_metrics.append(dict(metric_data))
        return len(self.girs_shadow_metrics)

    async def get_girs_shadow_metrics(self, limit: int = 1000) -> List[Dict[str, Any]]:
        metrics = getattr(self, "girs_shadow_metrics", [])
        sorted_metrics = sorted(metrics, key=lambda x: x["timestamp"], reverse=True)
        return sorted_metrics[:limit]

    async def get_orders_for_performance_replay(
        self, portfolio_id: str, strategy_id: str
    ) -> List[Dict[str, Any]]:
        import json
        filtered = [
            o for o in self.order_histories
            if o.get("portfolio_id") == portfolio_id and o.get("strategy_id") == strategy_id
        ]
        sorted_orders = sorted(filtered, key=lambda x: x.get("timestamp", 0))
        res_list = []
        for o in sorted_orders:
            order = dict(o)
            if 'context' in order and isinstance(order['context'], str) and order['context']:
                try:
                    order['context'] = json.loads(order['context'])
                except Exception:
                    pass
            res_list.append(order)
        return res_list

    async def get_orders_for_proposal_evaluation(
        self, portfolio_id: str, strategy_id: str, start_ts: int, end_ts: int
    ) -> List[Dict[str, Any]]:
        import json
        filtered = [
            o for o in self.order_histories
            if o.get("portfolio_id") == portfolio_id 
            and o.get("strategy_id") == strategy_id
            and start_ts <= o.get("timestamp", 0) <= end_ts
        ]
        sorted_orders = sorted(filtered, key=lambda x: x.get("timestamp", 0))
        res_list = []
        for o in sorted_orders:
            order = dict(o)
            if 'context' in order and isinstance(order['context'], str) and order['context']:
                try:
                    order['context'] = json.loads(order['context'])
                except Exception:
                    pass
            res_list.append(order)
        return res_list

    async def get_latest_feature_snapshot_for_proposal(
        self, proposal_id: str
    ) -> Optional[Dict[str, Any]]:
        import json
        # proposal_id는 문자열로 전달될 수도 있고 숫자로 전달될 수도 있으므로 문자열 변환 비교
        pid_str = str(proposal_id)
        filtered = [
            log for log in self.promotion_event_logs
            if str(log.get("proposal_id")) == pid_str
        ]
        if not filtered:
            return None
        
        # global_sequence_no 기준으로 정렬, 없으면 리스트 순서(입력 순서) 유지
        sorted_logs = sorted(
            filtered,
            key=lambda x: x.get("global_sequence_no", 0),
            reverse=True
        )
        latest_log = sorted_logs[0]
        
        feature_snap = latest_log.get("feature_snapshot")
        if isinstance(feature_snap, str) and feature_snap:
            try:
                feature_snap = json.loads(feature_snap)
            except Exception as e:
                logger.error(f"[InMemoryTradingRepository] Failed to parse feature_snapshot JSON for proposal {proposal_id}: {e}")
                feature_snap = None
        
        return {
            "feature_snapshot": feature_snap,
            "model_version": latest_log.get("model_version"),
            "scaler_version": latest_log.get("scaler_version")
        }

    async def insert_planned_asset_event(
        self,
        exchange_id: str,
        symbol: str,
        event_type: str,
        scheduled_at: str,
        notice_url: Optional[str] = None
    ) -> int:
        for ev in self.planned_asset_events:
            if ev["exchange_id"] == exchange_id and ev["symbol"] == symbol and ev["event_type"] == event_type and ev["scheduled_at"] == scheduled_at:
                return 0
        new_id = len(self.planned_asset_events) + 1
        self.planned_asset_events.append({
            "id": new_id,
            "exchange_id": exchange_id,
            "symbol": symbol,
            "event_type": event_type,
            "scheduled_at": scheduled_at,
            "notice_url": notice_url,
            "status": "PLANNED",
            "created_at": scheduled_at,
            "updated_at": scheduled_at
        })
        return new_id

    async def get_planned_asset_events(
        self,
        status: Optional[str] = None,
        exchange_id: Optional[str] = None,
        event_id: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        res = []
        for ev in self.planned_asset_events:
            if status and ev["status"] != status:
                continue
            if exchange_id and ev["exchange_id"] != exchange_id:
                continue
            if event_id is not None and ev["id"] != event_id:
                continue
            res.append(dict(ev))
        res.sort(key=lambda x: x["scheduled_at"])
        return res

    async def get_executable_planned_events(
        self,
        before_minutes: int = 30
    ) -> List[Dict[str, Any]]:
        import datetime
        limit_time = (datetime.datetime.now() + datetime.timedelta(minutes=before_minutes)).strftime('%Y-%m-%d %H:%M:%S')
        res = []
        for ev in self.planned_asset_events:
            if ev["status"] == "PLANNED" and ev["scheduled_at"] <= limit_time:
                res.append(dict(ev))
        res.sort(key=lambda x: x["scheduled_at"])
        return res

    async def update_planned_event_status(
        self,
        event_id: int,
        status: str
    ) -> bool:
        for ev in self.planned_asset_events:
            if ev["id"] == event_id:
                ev["status"] = status
                import datetime
                ev["updated_at"] = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                return True
        return False

    async def delete_planned_event(
        self,
        event_id: int
    ) -> bool:
        for i, ev in enumerate(self.planned_asset_events):
            if ev["id"] == event_id and ev["status"] == "PLANNED":
                self.planned_asset_events.pop(i)
                return True
        return False

    async def update_exchange_asset_status(
        self,
        exchange_id: str,
        symbol: str,
        is_active: int,
        is_delisted: int = 0
    ) -> bool:
        key = (exchange_id, symbol)
        import datetime
        now_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        self.exchange_assets[key] = {
            "exchange_id": exchange_id,
            "symbol": symbol,
            "is_active": is_active,
            "is_delisted": is_delisted,
            "updated_at": now_str
        }
        return True

    async def upsert_asset_master_if_not_exists(
        self,
        symbol: str,
        korean_name: str,
        asset_type: str
    ) -> bool:
        if symbol not in self.asset_master:
            import datetime
            now_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            self.asset_master[symbol] = {
                "symbol": symbol,
                "korean_name": korean_name,
                "asset_type": asset_type,
                "created_at": now_str,
                "updated_at": now_str
            }
            return True
        return False



