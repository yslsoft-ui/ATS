import asyncio
import time
import aiohttp
from typing import List, Dict, Optional, Any, Callable
from abc import ABC, abstractmethod

from src.engine.utils.telemetry import get_logger
from src.engine.candles import CandleGenerator, Candle
from src.engine.trade_engine import TradeEngine
from src.engine.strategy import StrategyRegistry

logger = get_logger(__name__)

class CollectorRegistry:
    _collectors: Dict[str, type] = {}

    @classmethod
    def register(cls, exchange_id: str):
        def decorator(collector_cls):
            cls._collectors[exchange_id] = collector_cls
            return collector_cls
        return decorator

    @classmethod
    def create(cls, exchange_id: str, **kwargs):
        collector_cls = cls._collectors.get(exchange_id)
        return collector_cls(**kwargs) if collector_cls else None
        
    @classmethod
    def available(cls) -> List[str]:
        return list(cls._collectors.keys())

class BaseCollector(ABC):
    """
    모든 거래소 수집기의 공통 로직을 처리하는 깊은 모듈입니다.
    """
    def __init__(
        self,
        processing_queue: asyncio.Queue,
        db_queue: Optional[asyncio.Queue] = None,  # 레거시 호환성 확보 및 Null 안정성 보장
        candle_queue: Optional[asyncio.Queue] = None,  # 레거시 호환성 확보 및 Null 안정성 보장
        portfolio_manager: Any = None,  # 레거시 호환성 확보 및 Null 안정성 보장
        on_data_callback: Optional[Callable] = None,
        on_signal_callback: Optional[Callable] = None,
        on_status_callback: Optional[Callable] = None,
        **kwargs  # 시스템 부트스트래퍼가 주입하는 추가 의존성(예: repository)을 유연하게 흡수
    ):
        # 공유 큐 가로채기(Message Stealing) 버그 원천 해결을 위해 인스턴스 전용 격리 큐를 할당합니다.
        self.processing_queue = asyncio.Queue()
        self.db_queue = db_queue
        self.candle_queue = candle_queue
        self.portfolio_manager = portfolio_manager
        self.on_data_callback = on_data_callback
        self.on_signal_callback = on_signal_callback
        self.on_status_callback = on_status_callback
        
        self.task: Optional[asyncio.Task] = None
        self.is_running = False

        self.trade_engines: Dict[str, TradeEngine] = {}
        self.session: Optional[aiohttp.ClientSession] = None
        self.processor_tasks: List[asyncio.Task] = []
        self._flush_task: Optional[asyncio.Task] = None
        self.available_symbols: List[str] = []
        # 1분봉 공용 캔들 조립기 탑재 (전략 가동 여부 무관 100% 실시간 생성 보장)
        self.candle_generator = CandleGenerator(intervals=[60])
        self.candle_lock = asyncio.Lock()  # 동시성 제어용 락 추가
        self.total_processed_count = 0
        self.last_error: Optional[str] = None
        self.ws: Optional[aiohttp.ClientWebSocketResponse] = None
        
    @property
    @abstractmethod
    def exchange(self) -> str:
        """거래소 식별 ID (예: 'upbit', 'bithumb', 'kis')"""
        pass
        
    async def start(self, config: Dict[str, Any] = None):
        if self.is_running:
            return
        self.is_running = True
        self.task = asyncio.create_task(self.run(config))

    async def stop(self):
        self.is_running = False
        if self._flush_task and not self._flush_task.done():
            self._flush_task.cancel()
        if self.task:
            for t in self.processor_tasks:
                t.cancel()
            self.task.cancel()
            if self.session:
                await self.session.close()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
            self.task = None
            self.session = None

    async def background_warmup(self, db_path: str = "data/backtest.db"):
        """종목별 워밍업 및 과거 캔들 히스토리 로드를 수행합니다."""
        # 전략 엔진들의 워밍업 진행
        engines = list(self.trade_engines.values())
        if not engines: return

        logger.info(f"[{self.exchange.upper()}] {len(engines)}개 종목 전략 호스트 백그라운드 워밍업 시작 (순차 진행)")
        for i, engine in enumerate(engines):
            if not self.is_running: break
            try:
                await engine.warm_up(db_path)
            except Exception as e:
                logger.error(f"[{self.exchange.upper()}] Warmup failed for {engine.symbol}: {e}")
            await asyncio.sleep(0.05)
            if (i + 1) % 100 == 0:
                logger.info(f"[{self.exchange.upper()}] 전략 워밍업 진행 중... ({i + 1}/{len(engines)})")
        logger.info(f"[{self.exchange.upper()}] 전체 {len(engines)}개 종목 전략 호스트 백그라운드 워밍업 완료")

    async def data_processor_worker(self):
        """큐에서 데이터를 꺼내 분석 및 전략 실행을 담당하는 공통 워커"""
        while self.is_running:
            try:
                data = await self.processing_queue.get()
                # 🚨 자기 거래소의 데이터가 아니면 즉시 큐 완료 처리 후 건너 뛰어 교차 오염을 원천 격리!
                if data.get('exchange') != self.exchange:
                    self.processing_queue.task_done()
                    continue
                
                self.total_processed_count += 1
                symbol = data['code']
                
                # 1. 1분봉 실시간 캔들 조립 처리 (동시성 락 적용)
                async with self.candle_lock:
                    closed_candles = self.candle_generator.process_tick(
                        exchange=self.exchange,
                        symbol=symbol,
                        price=data['trade_price'],
                        volume=data['trade_volume'],
                        side=data['ask_bid'],
                        timestamp_ms=data['trade_timestamp']
                    )
                
                # 2. 완성된 캔들이 있는 경우 DB 영속화 큐 주입
                if closed_candles:
                    logger.info(f"[{self.exchange.upper()}] {symbol} completed {len(closed_candles)} candles. candle_queue={self.candle_queue}")
                for candle in closed_candles:
                    if self.candle_queue:
                        await self.candle_queue.put(candle)
                        logger.info(f"[{self.exchange.upper()}] Enqueued candle for {symbol} (ts: {candle.timestamp})")
                    else:
                        logger.warning(f"[{self.exchange.upper()}] candle_queue is None! Cannot enqueue candle for {symbol}")
                
                # 4. 데이터 브로드캐스트 콜백 실행 및 DB 적재
                if self.on_data_callback:
                    await self.on_data_callback(data)
                
                if self.db_queue:
                    await self.db_queue.put(data)
                
                # 5. 자동매매 전략 구동 종목에 대한 시그널 연산
                if symbol in self.trade_engines:
                    engine = self.trade_engines[symbol]
                    signals, _ = await engine.process_tick(data, self.portfolio_manager)
                    
                    for sig in signals:
                        if self.on_signal_callback:
                            await self.on_signal_callback(sig, data['trade_price'])
                
                self.processing_queue.task_done()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[{self.exchange.upper()}] Data Processor Worker Error: {e}")
                await asyncio.sleep(0.1)

    def _init_trade_engines(self, config: Dict[str, Any]):
        strategy_configs = config.get('strategies', {}) if config else {}
        enabled_strategies = []
        for s_id, s_conf in strategy_configs.items():
            if s_conf.get('enabled', False):
                params = s_conf.get('params', {}).copy()
                # 거래소별 오버라이드
                overrides = s_conf.get('overrides', {}).get(self.exchange, {}).get('params', {})
                params.update(overrides)
                enabled_strategies.append((s_id, params))

        # 1. [NEW] 현재 수집 대상이 아닌 기존 엔진 정리 (재기동 시 메모리 누수 및 오염 방지)
        active_symbols = set(self.available_symbols)
        for sym in list(self.trade_engines.keys()):
            if sym not in active_symbols:
                del self.trade_engines[sym]

        # 2. 필요한 신규 엔진 생성
        for symbol in self.available_symbols:
            if symbol not in self.trade_engines:
                instances = []
                for s_id, s_params in enabled_strategies:
                    strat = StrategyRegistry.create_strategy(s_id, s_params)
                    if strat:
                        instances.append(strat)
                self.trade_engines[symbol] = TradeEngine(self.exchange, symbol, instances, on_status_callback=self.on_status_callback)

    async def _fetch_active_symbols_from_db(self, config: Dict[str, Any]) -> List[str]:
        """
        데이터베이스의 exchange_assets 테이블에서
        해당 거래소(self.exchange)의 활성화된(is_active = 1) 종목 목록을 조회합니다.
        """
        db_path = config.get('db_path', 'data/backtest.db') if config else 'data/backtest.db'
        from src.database.connection import get_db_conn
        
        active_symbols = []
        try:
            async with get_db_conn(db_path) as db:
                async with db.execute(
                    "SELECT symbol FROM exchange_assets WHERE exchange = ? AND is_active = 1",
                    (self.exchange,)
                ) as cursor:
                    rows = await cursor.fetchall()
                    active_symbols = sorted([row['symbol'] for row in rows])
            logger.info(f"[{self.exchange.upper()}] DB exchange_assets에서 {len(active_symbols)}개 활성 종목 로드 완료")
        except Exception as e:
            logger.error(f"[{self.exchange.upper()}] DB 활성 종목 조회 실패: {e}")
            
        return active_symbols

    async def reload_symbols(self, config: Dict[str, Any] = None):
        """DB에서 활성 종목을 실시간 재조회하여 소켓 구독 목록을 실시간으로 갱신합니다."""
        # 1. DB에서 active 종목 재조회
        new_symbols = await self._fetch_active_symbols_from_db(config or self.config)
        self.available_symbols = new_symbols
        
        # 2. 거래소 전략 엔진 재구성 (제거된 종목 엔진은 자동 정리됨)
        self._init_trade_engines(config or self.config)
        
        # 3. WebSocket 구독 재전송
        if self.ws and not self.ws.closed:
            try:
                await self._subscribe(self.ws, config or self.config)
                logger.info(f"[{self.exchange.upper()}] 활성 구독 목록 실시간 리로드 완료: {len(self.available_symbols)}개 종목")
            except Exception as e:
                logger.error(f"[{self.exchange.upper()}] 실시간 구독 리로드 실패: {e}")

    def _group_consecutive_timestamps(self, timestamps: List[int], interval=60) -> List[tuple]:
        """연속된 타임스탬프들을 시작과 끝 시각의 튜플 리스트로 그룹화합니다."""
        if not timestamps:
            return []
        sorted_ts = sorted(timestamps)
        intervals = []
        start = sorted_ts[0]
        prev = start
        for ts in sorted_ts[1:]:
            if ts == prev + interval:
                prev = ts
            else:
                intervals.append((start, prev))
                start = ts
                prev = ts
        intervals.append((start, prev))
        return intervals

    @abstractmethod
    async def _fetch_symbols(self, config: Dict[str, Any]) -> List[str]:
        """종목 목록 로드 (REST API 또는 config)"""
        pass

    @abstractmethod
    async def _fetch_historical_candles(self, symbol: str, start_time: int, end_time: int) -> List[Candle]:
        """각 거래소별 REST API를 호출하여 누락된 1분봉 데이터를 조회하여 반환합니다."""
        pass

    async def backfill_candles(self, config: Dict[str, Any]):
        """로컬 DB의 누락된 빈 틈(gap)들을 탐색하여 누락된 분봉을 수집하고 백필합니다."""
        bf_config = config.get('collector', {}).get('backfill', {})
        if not bf_config.get('enabled', True):
            logger.info(f"[{self.exchange.upper()}] 백필 기능이 비활성화되어 있습니다.")
            return

        db_path = config.get('db_path', 'data/backtest.db')
        max_hours = bf_config.get('max_hours', 24)
        
        # 거래소별 Throttling 딜레이 추출
        delays = bf_config.get('delays', {})
        delay = delays.get(self.exchange, 0.2)

        logger.info(f"[{self.exchange.upper()}] 백필 작업 기동. 대상 종목: {self.available_symbols}, 최대 복구: {max_hours}시간, API 딜레이: {delay}초")

        from src.database.connection import get_db_conn
        
        current_time = int(time.time() // 60) * 60
        max_lookback = current_time - (max_hours * 3600)

        for symbol in self.available_symbols:
            if not self.is_running:
                break
            
            try:
                # 1. 기대 타임스탬프 목록 생성 (마감된 분봉까지만 조회: 현재 분의 1분 전까지)
                end_time = current_time - 60
                expected_timestamps = list(range(max_lookback, end_time + 60, 60))
                if not expected_timestamps:
                    continue

                # 2. DB에 이미 존재하는 타임스탬프 조회
                existing_timestamps = set()
                try:
                    async with get_db_conn(db_path) as db:
                        cursor = await db.execute(
                            "SELECT timestamp FROM candles WHERE exchange = ? AND symbol = ? AND interval = 60 AND timestamp >= ? AND timestamp <= ?",
                            (self.exchange, symbol, max_lookback, end_time)
                        )
                        rows = await cursor.fetchall()
                        existing_timestamps = {r[0] for r in rows}
                except Exception as e:
                    logger.error(f"[{self.exchange.upper()}] {symbol} DB 조회 실패: {e}")
                    continue

                # 3. 누락된 타임스탬프 추출
                missing_timestamps = [ts for ts in expected_timestamps if ts not in existing_timestamps]
                if not missing_timestamps:
                    logger.debug(f"[{self.exchange.upper()}] {symbol} 백필 불필요 (누락된 구간 없음)")
                    continue

                # 4. 전체 누락 구간의 최소값과 최대값 추출 (잘게 쪼개지 않고 전체 범위를 한 번에 벌크 호출)
                start_t = min(missing_timestamps)
                end_t = max(missing_timestamps)

                logger.info(f"[{self.exchange.upper()}] {symbol} 백필 수행 구간: "
                            f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(start_t))} ~ "
                            f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(end_t))} (누락 캔들수: {len(missing_timestamps)}개)")

                try:
                    # 거래소별 REST API 호출
                    candles = await self._fetch_historical_candles(symbol, start_t, end_t)
                    if not candles:
                        logger.debug(f"[{self.exchange.upper()}] {symbol} 복구할 과거 캔들이 존재하지 않습니다. (구간: {start_t} ~ {end_t})")
                        continue

                    # 시간 순서로 정렬
                    candles.sort(key=lambda x: x.timestamp)

                    # 중복 저장 방지를 위해 구간 내 이미 존재하는 타임스탬프 로드
                    existing_segment_timestamps = set()
                    try:
                        async with get_db_conn(db_path) as db:
                            cursor = await db.execute(
                                "SELECT timestamp FROM candles WHERE exchange = ? AND symbol = ? AND interval = 60 AND timestamp >= ? AND timestamp <= ?",
                                (self.exchange, symbol, start_t, end_t)
                            )
                            rows = await cursor.fetchall()
                            existing_segment_timestamps = {r[0] for r in rows}
                    except Exception as e:
                        logger.error(f"[{self.exchange.upper()}] {symbol} 중복 검사용 DB 조회 실패: {e}")

                    # 캔들 발행 큐에 적재 (중복 필터링)
                    count = 0
                    for candle in candles:
                        if candle.interval == 60 and candle.timestamp not in existing_segment_timestamps:
                            if self.candle_queue:
                                await self.candle_queue.put(candle)
                                count += 1
                    
                    logger.info(f"[{self.exchange.upper()}] {symbol} 백필 캔들 큐 적재 완료: {count}개 (API 반환: {len(candles)}개)")

                except Exception as e:
                    logger.error(f"[{self.exchange.upper()}] {symbol} 백필 수행 중 에러 발생: {e}")

            except Exception as e:
                logger.error(f"[{self.exchange.upper()}] {symbol} 백필 과정에서 에러 발생: {e}")
            finally:
                # 종목 간 Throttling 딜레이 적용
                await asyncio.sleep(delay)

    @abstractmethod
    def _get_websocket_url(self, config: Dict[str, Any]) -> str:
        """WebSocket URL 반환"""
        pass

    @abstractmethod
    async def _subscribe(self, ws, config: Dict[str, Any]):
        """WebSocket 구독 메시지 전송"""
        pass

    @abstractmethod
    def _parse_message(self, msg) -> Optional[Dict]:
        """수신 메시지를 내부 tick_data로 변환 (None 반환 시 무시)"""
        pass

    # --- 훅 메서드 ---
    async def _pre_connect_check(self) -> float:
        """연결 전 사전 체크. 반환된 초(float)만큼 대기합니다."""
        return 0.0

    async def _prepare_connection(self, config: Dict[str, Any]) -> bool:
        """연결 전 준비 (인증키 발급 등). False 반환 시 연결 루프에서 대기 후 재시도."""
        return True

    async def _start_additional_tasks(self, config: Dict[str, Any]):
        """추가 백그라운드 태스크 시작"""
        pass
        
    async def _handle_connection_error(self, error: Exception):
        """연결 중 에러 처리"""
        logger.error(f"[{self.exchange.upper()}] Collector Connection Error: {error}. Reconnecting in 5s...")
        await asyncio.sleep(5)

    async def _candle_flush_loop(self):
        """매 분 경계(xx:01초)에 CandleGenerator의 미마감 캔들을 강제 close하여 candle_queue에 적재합니다."""
        while self.is_running:
            try:
                # 다음 분 경계 + 1초까지 대기 (xx:01초에 실행하여 분 전환 직후 안전하게 수거)
                now = time.time()
                next_min_boundary = ((int(now) // 60) + 1) * 60 + 1
                sleep_sec = max(0, next_min_boundary - now)
                await asyncio.sleep(sleep_sec)

                if not self.is_running:
                    break

                current_min_start = (int(time.time()) // 60) * 60
                flushed = 0

                async with self.candle_lock:
                    for interval, symbols_dict in self.candle_generator.current_candles.items():
                        expired_symbols = []
                        for symbol, candle in symbols_dict.items():
                            # 캔들의 시작 시각이 현재 분보다 이전이면 → 이미 완료된 분봉
                            if candle.timestamp < current_min_start:
                                candle.is_closed = True
                                if self.candle_queue:
                                    await self.candle_queue.put(candle)
                                expired_symbols.append(symbol)
                                flushed += 1
                        # 수거 완료된 캔들을 메모리에서 제거
                        for symbol in expired_symbols:
                            del symbols_dict[symbol]

                if flushed > 0:
                    logger.info(f"[{self.exchange.upper()}] 분 경계 flush: 미마감 캔들 {flushed}개 강제 close 및 DB 큐 적재")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[{self.exchange.upper()}] 캔들 flush 루프 예외: {e}")
                await asyncio.sleep(5)

    async def run(self, config: Dict[str, Any] = None):
        """메인 실행 루프 (템플릿 메서드)"""
        if config is None:
            config = {}
        self.config = config
            
        # 1. 종목 로드
        self.available_symbols = await self._fetch_symbols(config)
        
        # 2. 엔진 초기화
        self._init_trade_engines(config)
        
        # 3. 추가 작업 시작
        await self._start_additional_tasks(config)

        # 4. 워밍업
        warmup_enabled = config.get('exchanges', {}).get(self.exchange, {}).get('warmup_enabled', config.get('warmup_enabled', True))
        if warmup_enabled:
            db_path = config.get('db_path', 'data/backtest.db')
            asyncio.create_task(self.background_warmup(db_path))

        # 5. 기존 가동 중이던 좀비 워커 태스크가 있다면 완벽하게 소멸시키고 리스트 초기화
        if self.processor_tasks:
            for t in self.processor_tasks:
                if not t.done():
                    t.cancel()
            self.processor_tasks.clear()

        # 6. 깨끗한 상태에서 정품 워커 가동
        worker_count = config.get('worker_count', 2)
        for _ in range(worker_count): 
            self.processor_tasks.append(asyncio.create_task(self.data_processor_worker()))

        # 7. 분 경계 캔들 강제 flush 태스크 기동
        self._flush_task = asyncio.create_task(self._candle_flush_loop())

        # 8. 기동 시 누락 캔들 백필 수행 (실시간 수집 지연을 방지하기 위해 백그라운드 비동기 태스크로 구동)
        try:
            asyncio.create_task(self.backfill_candles(config))
        except Exception as e:
            logger.error(f"[{self.exchange.upper()}] 백필 중 치명적 오류 발생: {e}")

        # 9. WebSocket 연결 루프
        url = self._get_websocket_url(config)
        self.ws = None

        while self.is_running:
            try:
                wait_time = await self._pre_connect_check()
                if wait_time > 0:
                    await asyncio.sleep(wait_time)
                    continue
                
                if not self.is_running: return
                
                ready = await self._prepare_connection(config)
                if not ready:
                    await asyncio.sleep(10)
                    continue

                if not self.session or self.session.closed:
                    self.session = aiohttp.ClientSession()
                
                async with self.session.ws_connect(url, heartbeat=30.0) as ws:
                    self.ws = ws
                    # 재연결 시 DB에서 최신 활성 종목 목록을 재로드하여
                    # 그 사이 해제(uncheck)된 종목이 재구독되는 버그 방지
                    latest_symbols = await self._fetch_symbols(config)
                    if latest_symbols != self.available_symbols:
                        logger.info(f"[{self.exchange.upper()}] ws 재연결: 종목 목록 갱신 {len(self.available_symbols)} → {len(latest_symbols)}개")
                        self.available_symbols = latest_symbols
                        self._init_trade_engines(config)
                    await self._subscribe(ws, config)
                    logger.info(f"[{self.exchange.upper()}] Collector Connected - {len(self.available_symbols)} symbols")

                    async for msg in ws:
                        if not self.is_running: break
                        
                        tick_data = self._parse_message(msg)
                        if tick_data:
                            if isinstance(tick_data, list):
                                for tick in tick_data:
                                    self.processing_queue.put_nowait(tick)
                            else:
                                self.processing_queue.put_nowait(tick_data)

                self.ws = None
                # 정상적으로 소켓 루프가 종료(끊김)되었을 때도 즉각 재연결 폭주를 방지하기 위해 5초 대기 적용
                if self.is_running:
                    logger.warning(f"[{self.exchange.upper()}] WebSocket connection closed. Reconnecting in 5s...")
                    await asyncio.sleep(5)

            except Exception as e:
                self.ws = None
                if self.is_running:
                    await self._handle_connection_error(e)
