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
                    # 최신 체결 시간 기준 롤링 윈도우 계산 (주말/장마감 후에도 정상 표시 보장 및 KIS 성능 병목 해결)
                    latest_ts_ms = None
                    try:
                        async with db.execute(
                            "SELECT MAX(trade_timestamp) FROM trades WHERE exchange = ? AND symbol = ?",
                            (exchange, symbol)
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
        end_time = int(time.time() // 60) * 60
        start_time = end_time - limit_minutes * 60
        current_minute_bucket = end_time
        
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
                
                # 아직 완성되지 않은 현재 진행 중인 분봉 및 마감 직후 1분봉은 누락 캔들로 판단하지 않음
                if bucket >= current_minute_bucket - 60:
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
    async def check_and_report_previous_crash(self, target: str):
        pass


class SqliteTradingRepository(BaseTradingRepository):
    """
    실제 SQLite 데이터베이스를 연동하는 실거래용 트레이딩 저장소 어댑터입니다.
    """
    def __init__(self, system=None, db_path: Optional[str] = None):
        self.system = system
        self.db_path = db_path

    async def save_portfolio(self, portfolio: Any):
        async with get_db_conn(self.db_path) as db:
            # 1. 포트폴리오 기본 정보 저장
            await db.execute('''
                INSERT INTO portfolios (id, name, type, exchange_id, initial_cash, cash, strategy_info, duration, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    type = excluded.type,
                    exchange_id = excluded.exchange_id,
                    initial_cash = excluded.initial_cash,
                    cash = excluded.cash,
                    strategy_info = excluded.strategy_info,
                    duration = COALESCE(excluded.duration, portfolios.duration),
                    updated_at = datetime('now')
            ''', (
                portfolio.id,
                portfolio.name,
                portfolio.portfolio_type,
                portfolio.exchange_id,
                portfolio.initial_cash,
                portfolio.cash,
                getattr(portfolio, 'strategy_info', ''),
                getattr(portfolio, 'duration', None)
            ))

            # 1.5. 거래소별 격리 자금 정보 저장 (portfolio_exchanges)
            if hasattr(portfolio, 'exchange_cash') and portfolio.exchange_cash:
                for ex_id, ex_cash in portfolio.exchange_cash.items():
                    await db.execute('''
                        INSERT INTO portfolio_exchanges (portfolio_id, exchange_id, initial_cash, cash, updated_at)
                        VALUES (?, ?, 10000000.0, ?, datetime('now'))
                        ON CONFLICT(portfolio_id, exchange_id) DO UPDATE SET cash = ?, updated_at = datetime('now')
                    ''', (portfolio.id, ex_id, ex_cash, ex_cash))

            # 2. 현재 포지션 정보 저장 (기존 포지션 삭제 후 재삽입)
            await db.execute("DELETE FROM positions WHERE portfolio_id = ?", (portfolio.id,))
            for pos in portfolio.positions.values():
                if pos.quantity > 0:
                    await db.execute('''
                        INSERT INTO positions (portfolio_id, exchange, symbol, quantity, avg_price, updated_at)
                        VALUES (?, ?, ?, ?, ?, datetime('now'))
                    ''', (portfolio.id, pos.exchange, pos.symbol, pos.quantity, pos.avg_price))
            
            await db.commit()

    async def load_portfolios(self, exclude_types: list = None) -> Dict[str, Any]:
        from src.engine.portfolio import Portfolio, Position
        loaded_portfolios = {}
        async with get_db_conn(self.db_path) as db:
            # 1. 포트폴리오 로드
            query = "SELECT * FROM portfolios"
            if exclude_types:
                placeholders = ",".join(["?"] * len(exclude_types))
                query += f" WHERE type NOT IN ({placeholders})"
                cursor = await db.execute(query, exclude_types)
            else:
                cursor = await db.execute(query)

            async with cursor:
                async for row in cursor:
                    p = Portfolio(
                        portfolio_id=row['id'], 
                        name=row['name'], 
                        initial_cash=row['initial_cash'], 
                        exchange_id=row['exchange_id'],
                        portfolio_type=row['type'],
                        strategy_info=row['strategy_info'] if 'strategy_info' in row.keys() else ""
                    )
                    p.cash = row['cash']
                    loaded_portfolios[p.id] = p
            
            # 2. 각 포트폴리오의 포지션 및 거래소 격리 자금 로드
            for pid, p in loaded_portfolios.items():
                # 2.1. portfolio_exchanges 로드
                p.exchange_cash = {}
                async with db.execute("SELECT exchange_id, cash FROM portfolio_exchanges WHERE portfolio_id = ?", (pid,)) as cursor:
                    async for row in cursor:
                        p.exchange_cash[row['exchange_id']] = row['cash']
                
                # 2.2. positions 로드
                async with db.execute("SELECT * FROM positions WHERE portfolio_id = ?", (pid,)) as cursor:
                    async for row in cursor:
                        ex_val = row['exchange'] if row['exchange'] else 'upbit'
                        p.positions[(ex_val.lower(), row['symbol'])] = Position(
                             exchange=ex_val,
                             symbol=row['symbol'],
                             quantity=row['quantity'],
                             avg_price=row['avg_price'],
                             updated_at=time.time() 
                        )
                
                # 3. 최근 거래 내역 로드 (최근 100건)
                async with db.execute("SELECT * FROM orders_history WHERE portfolio_id = ? ORDER BY timestamp DESC LIMIT 100", (pid,)) as cursor:
                    rows = await cursor.fetchall()
                    p.history = [dict(r) for r in reversed(rows)]
        return loaded_portfolios

    async def insert_order_history(self, portfolio_id: str, order: Dict[str, Any]):
        async with get_db_conn(self.db_path) as db:
            import json
            await db.execute('''
                INSERT INTO orders_history (portfolio_id, exchange, market, strategy_id, symbol, side, price, quantity, fee, timestamp, reason, context)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                portfolio_id, 
                order['exchange'],
                order.get('market', 'KRW'),
                order.get('strategy_id', ""),
                order['symbol'], 
                order['side'], 
                order['price'], 
                order['quantity'], 
                order['fee'], 
                order.get('timestamp', int(time.time())), 
                order.get('reason', ""),
                json.dumps(order.get('context', {}) or {})
            ))
            await db.commit()

    async def insert_alert(self, alert: Dict[str, Any]):
        async with get_db_conn(self.db_path) as db:
            await db.execute(
                "INSERT INTO alerts (exchange, symbol, price, msg, timestamp) VALUES (?, ?, ?, ?, ?)",
                (alert['exchange'], alert['code'], alert['price'], alert['msg'], alert['timestamp'])
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


class InMemoryTradingRepository(BaseTradingRepository):
    """
    단위 테스트 및 오프라인 시뮬레이션용 초고속 인메모리 트레이딩 저장소 어댑터입니다.
    """
    def __init__(self):
        self.portfolios: Dict[str, Any] = {}
        self.exchange_configs: Dict[str, Dict[str, Any]] = {}
        self.order_histories: List[Dict[str, Any]] = []
        self.alerts: List[Dict[str, Any]] = []
        self.system_events: List[Dict[str, Any]] = []

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

    async def insert_alert(self, alert: Dict[str, Any]):
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

