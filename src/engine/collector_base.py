import asyncio
import time
import aiohttp
from typing import List, Dict, Optional, Any, Callable, TypedDict
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


class ConnectionMetadata(TypedDict):
    operating_hours: str
    websocket_url: str
    api_url: str


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
        # 외부에서 주입된 큐가 있으면 이를 활용하고, 없으면 신규 큐를 생성합니다.
        self.processing_queue = processing_queue if processing_queue is not None else asyncio.Queue()
        self.db_queue = db_queue
        self.candle_queue = candle_queue
        self.portfolio_manager = portfolio_manager
        self.on_data_callback = on_data_callback
        self.on_signal_callback = on_signal_callback
        self.on_status_callback = on_status_callback
        self.repository = kwargs.get('repository')
        self.on_backfill_complete = kwargs.get('on_backfill_complete')
        
        self.task: Optional[asyncio.Task] = None
        self.is_running = False

        self.session: Optional[aiohttp.ClientSession] = None
        self.available_symbols: List[str] = []
        self.total_processed_count = 0
        self.total_dropped_count = 0  # [NEW] 큐 오버로드로 드롭된 틱 카운트
        self.last_tick: Optional[dict] = None  # [NEW] 마지막 수신 틱 캐싱
        self.last_raw: Optional[str] = None  # [NEW] 마지막 수신 원본 raw 데이터 캐싱
        self.last_error: Optional[str] = None
        self.ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self.status = "STOPPED"
        self.status_reason: Optional[str] = None
        
    @property
    @abstractmethod
    def exchange_id(self) -> str:
        """거래소 식별 ID (예: 'upbit', 'bithumb', 'kis')"""
        pass

    @abstractmethod
    def get_connection_metadata(self, config: Dict[str, Any]) -> ConnectionMetadata:
        """수집기 접속 및 장 운영 명세를 반환합니다."""
        pass
        
    async def start(self, config: Dict[str, Any] = None):
        if self.is_running:
            return
        self.is_running = True
        self.status = "RUNNING"
        self.status_reason = None
        self.task = asyncio.create_task(self.run(config))

    async def stop(self):
        self.is_running = False
        self.status = "STOPPED"
        self.status_reason = None
        if self.task:
            self.task.cancel()
            if self.session:
                await self.session.close()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
            self.task = None
            self.session = None



    async def _fetch_active_symbols_from_db(self, config: Dict[str, Any]) -> List[str]:
        """
        데이터베이스의 exchange_assets 테이블에서
        해당 거래소(self.exchange_id)의 활성화된(is_active = 1) 종목 목록을 조회합니다.
        """
        db_path = config.get('db_path', 'data/backtest.db') if config else 'data/backtest.db'
        from src.database.connection import get_db_conn
        
        active_symbols = []
        try:
            async with get_db_conn(db_path) as db:
                async with db.execute(
                    "SELECT symbol FROM exchange_assets WHERE exchange_id = ? AND is_active = 1",
                    (self.exchange_id,)
                ) as cursor:
                    rows = await cursor.fetchall()
                    active_symbols = sorted([row['symbol'] for row in rows])
            logger.info(f"[{self.exchange_id.upper()}] DB exchange_assets에서 {len(active_symbols)}개 활성 종목 로드 완료")
        except Exception as e:
            logger.error(f"[{self.exchange_id.upper()}] DB 활성 종목 조회 실패: {e}")
            
        return active_symbols

    async def reload_symbols(self, config: Dict[str, Any] = None):
        """DB에서 활성 종목을 실시간 재조회하여 소켓 구독 목록을 실시간으로 갱신합니다."""
        # 1. DB에서 active 종목 재조회
        old_symbols = set(self.available_symbols)
        new_symbols = await self._fetch_active_symbols_from_db(config or self.config)
        self.available_symbols = new_symbols
        
        added_symbols = [s for s in new_symbols if s not in old_symbols]
        
        # 2. WebSocket 구독 재전송
        if self.ws and not self.ws.closed:
            try:
                await self._subscribe(self.ws, config or self.config)
                logger.info(f"[{self.exchange_id.upper()}] 활성 구독 목록 실시간 리로드 완료: {len(self.available_symbols)}개 종목")
            except Exception as e:
                logger.error(f"[{self.exchange_id.upper()}] 실시간 구독 리로드 실패: {e}")

        # 새로 추가된 종목에 대해 비동기 백필 실행
        if added_symbols:
            logger.info(f"[{self.exchange_id.upper()}] 실시간 리로드 중 신규 추가 종목 감지되어 백필을 실행합니다: {added_symbols}")
            for symbol in added_symbols:
                asyncio.create_task(self.backfill_symbol(symbol, config or self.config))

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

    async def backfill_symbol(self, symbol: str, config: Dict[str, Any]):
        """특정 종목에 대해 로컬 DB의 누락된 빈 틈(gap)들을 탐색하여 과거 1분봉 데이터를 수집하고 백필합니다."""
        bf_config = config.get('collector', {}).get('backfill', {})
        if not bf_config.get('enabled', True):
            logger.info(f"[{self.exchange_id.upper()}] 백필 기능이 비활성화되어 있습니다.")
            return

        db_path = config.get('db_path', 'data/backtest.db')
        max_hours = bf_config.get('max_hours', 24)

        from src.database.connection import get_db_conn
        
        current_time = int(time.time() // 60) * 60
        max_lookback = current_time - (max_hours * 3600)

        try:
            # 0. DB 내 해당 종목의 최신 캔들 시각을 조회하여 백필 룩백 범위를 좁힙니다.
            last_db_time = None
            try:
                async with get_db_conn(db_path) as db:
                    cursor = await db.execute(
                        "SELECT MAX(timestamp) FROM candles WHERE exchange_id = ? AND symbol = ? AND interval = 60",
                        (self.exchange_id, symbol)
                    )
                    row = await cursor.fetchone()
                    if row and row[0]:
                        last_db_time = row[0]
            except Exception as e:
                logger.error(f"[{self.exchange_id.upper()}] {symbol} 최근 DB 캔들 조회 실패: {e}")

            # 1. 기대 타임스탬프 목록 생성 (마감된 분봉까지만 조회: 현재 분의 1분 전까지)
            end_time = current_time - 60
            raw_expected = range(max_lookback, end_time + 60, 60)

            if self.exchange_id == 'kis':
                from zoneinfo import ZoneInfo
                from datetime import datetime
                kst = ZoneInfo('Asia/Seoul')
                expected_timestamps = []
                current_date = datetime.fromtimestamp(current_time, tz=kst).date()
                for ts in raw_expected:
                    dt = datetime.fromtimestamp(ts, tz=kst)
                    # KIS API는 당일 분봉만 제공하므로 당일 타임스탬프만 수집 대상으로 한정
                    if dt.date() != current_date:
                        continue
                    # 주말 제외
                    if dt.weekday() >= 5:
                        continue
                    # KIS 거래 시간(정규+대체): KST 08:00 ~ 20:00 (480분 ~ 1200분)
                    m_val = dt.hour * 60 + dt.minute
                    if 480 <= m_val < 1200:
                        expected_timestamps.append(ts)
            else:
                expected_timestamps = list(raw_expected)

            if not expected_timestamps:
                return

            # 2. DB에 이미 존재하는 타임스탬프 조회
            existing_timestamps = set()
            try:
                async with get_db_conn(db_path) as db:
                    cursor = await db.execute(
                        "SELECT timestamp FROM candles WHERE exchange_id = ? AND symbol = ? AND interval = 60 AND timestamp >= ? AND timestamp <= ?",
                        (self.exchange_id, symbol, max_lookback, end_time)
                    )
                    rows = await cursor.fetchall()
                    existing_timestamps = {r[0] for r in rows}
            except Exception as e:
                logger.error(f"[{self.exchange_id.upper()}] {symbol} DB 조회 실패: {e}")
                return

            # 3. 누락된 타임스탬프 추출
            missing_timestamps = [ts for ts in expected_timestamps if ts not in existing_timestamps]
            if not missing_timestamps:
                logger.debug(f"[{self.exchange_id.upper()}] {symbol} 백필 불필요 (누락된 구간 없음)")
                # 이미 디비에 다 차있어도 콜백은 호출해 주는 것이 혹시 모를 타이밍 이슈(전략 warm_up) 방지에 좋음
                if hasattr(self, 'on_backfill_complete') and self.on_backfill_complete:
                    try:
                        if asyncio.iscoroutinefunction(self.on_backfill_complete):
                            await self.on_backfill_complete(symbol)
                        else:
                            self.on_backfill_complete(symbol)
                    except Exception as cb_err:
                        logger.error(f"[{self.exchange_id.upper()}] 백필 완료 콜백 실행 오류 ({symbol}): {cb_err}")
                return

            # 4. 전체 누락 구간의 최소값과 최대값 추출 (잘게 쪼개지 않고 전체 범위를 한 번에 벌크 호출)
            start_t = min(missing_timestamps)
            end_t = max(missing_timestamps)

            logger.info(f"[{self.exchange_id.upper()}] {symbol} 백필 수행 구간: "
                        f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(start_t))} ~ "
                        f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(end_t))} (누락 캔들수: {len(missing_timestamps)}개)")

            try:
                # 거래소별 REST API 호출
                candles = await self._fetch_historical_candles(symbol, start_t, end_t)
                if not candles:
                    logger.debug(f"[{self.exchange_id.upper()}] {symbol} 복구할 과거 캔들이 존재하지 않습니다. (구간: {start_t} ~ {end_t})")
                else:
                    # 시간 순서로 정렬
                    candles.sort(key=lambda x: x.timestamp)

                    # 중복 저장 방지를 위해 구간 내 이미 존재하는 타임스탬프 로드
                    existing_segment_timestamps = set()
                    try:
                        async with get_db_conn(db_path) as db:
                            cursor = await db.execute(
                                "SELECT timestamp FROM candles WHERE exchange_id = ? AND symbol = ? AND interval = 60 AND timestamp >= ? AND timestamp <= ?",
                                (self.exchange_id, symbol, start_t, end_t)
                            )
                            rows = await cursor.fetchall()
                            existing_segment_timestamps = {r[0] for r in rows}
                    except Exception as e:
                        logger.error(f"[{self.exchange_id.upper()}] {symbol} 중복 검사용 DB 조회 실패: {e}")

                    # 캔들 발행 큐에 적재 (중복 필터링)
                    count = 0
                    for candle in candles:
                        if candle.interval == 60 and candle.timestamp not in existing_segment_timestamps:
                            if self.candle_queue:
                                candle.is_backfill = True
                                await self.candle_queue.put(candle)
                                count += 1
                    
                    logger.info(f"[{self.exchange_id.upper()}] {symbol} 백필 캔들 큐 적재 완료: {count}개 (API 반환: {len(candles)}개)")

                # 백필 완료 콜백 실행
                if hasattr(self, 'on_backfill_complete') and self.on_backfill_complete:
                    try:
                        if asyncio.iscoroutinefunction(self.on_backfill_complete):
                            await self.on_backfill_complete(symbol)
                        else:
                            self.on_backfill_complete(symbol)
                    except Exception as cb_err:
                        logger.error(f"[{self.exchange_id.upper()}] 백필 완료 콜백 실행 오류 ({symbol}): {cb_err}")

            except Exception as e:
                logger.error(f"[{self.exchange_id.upper()}] {symbol} 백필 수행 중 에러 발생: {e}")

        except Exception as e:
            logger.error(f"[{self.exchange_id.upper()}] {symbol} 백필 과정에서 에러 발생: {e}")

    async def backfill_candles(self, config: Dict[str, Any]):
        """로컬 DB의 누락된 빈 틈(gap)들을 탐색하여 누락된 분봉을 수집하고 백필합니다."""
        bf_config = config.get('collector', {}).get('backfill', {})
        if not bf_config.get('enabled', True):
            logger.info(f"[{self.exchange_id.upper()}] 백필 기능이 비활성화되어 있습니다.")
            return

        db_path = config.get('db_path', 'data/backtest.db')
        max_hours = bf_config.get('max_hours', 24)
        
        # 거래소별 Throttling 딜레이 추출
        delays = bf_config.get('delays', {})
        delay = delays.get(self.exchange_id, 0.2)

        logger.info(f"[{self.exchange_id.upper()}] 백필 작업 기동. 대상 종목: {self.available_symbols}, 최대 복구: {max_hours}시간, API 딜레이: {delay}초")

        for symbol in self.available_symbols:
            if not self.is_running:
                break
            await self.backfill_symbol(symbol, config)
            # 종목 간 Throttling 딜레이 적용
            await asyncio.sleep(delay)

    async def update_subscription(self, code: str, is_collected: bool):
        """ZMQ IPC 시그널 수신 시 동적으로 실시간 웹소켓 구독을 추가/해제하고 백필을 트리거합니다."""
        if is_collected:
            if code not in self.available_symbols:
                self.available_symbols.append(code)
                logger.info(f"[{self.exchange_id.upper()}] 동적 수집 종목 추가: {code}")
                # 신규 등록 종목에 대한 즉시 백필 기동 (백그라운드 비동기 태스크)
                asyncio.create_task(self.backfill_symbol(code, self.config))
        else:
            if code in self.available_symbols:
                self.available_symbols.remove(code)
                logger.info(f"[{self.exchange_id.upper()}] 동적 수집 종목 제거: {code}")

        # WebSocket 구독 갱신 (전체 구독 목록을 다시 전송하는 거래소용)
        if self.ws and not self.ws.closed:
            try:
                await self._subscribe(self.ws, self.config)
                logger.info(f"[{self.exchange_id.upper()}] 웹소켓 실시간 구독 갱신 완료 ({code})")
            except Exception as e:
                logger.error(f"[{self.exchange_id.upper()}] 웹소켓 구독 갱신 실패 ({code}): {e}")

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
        logger.error(f"[{self.exchange_id.upper()}] Collector Connection Error: {error}. Reconnecting in 5s...")
        await asyncio.sleep(5)

    async def run(self, config: Dict[str, Any] = None):
        """메인 실행 루프 (템플릿 메서드)"""
        if config is None:
            config = {}
        self.config = config
            
        # 1. 종목 로드
        self.available_symbols = await self._fetch_symbols(config)
        
        # 2. 추가 작업 시작
        await self._start_additional_tasks(config)

        # 3. 기동 시 누락 캔들 백필 수행 (실시간 수집 지연을 방지하기 위해 백그라운드 비동기 태스크로 구동)
        try:
            asyncio.create_task(self.backfill_candles(config))
        except Exception as e:
            logger.error(f"[{self.exchange_id.upper()}] 백필 중 치명적 오류 발생: {e}")

        # 4. WebSocket 연결 및 수신 루프 기동
        await self._connect_and_listen(config)

    async def _connect_and_listen(self, config: Dict[str, Any]):
        """WebSocket 연결 및 메시지 수신 무한 루프를 처리합니다."""
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
                        logger.info(f"[{self.exchange_id.upper()}] ws 재연결: 종목 목록 갱신 {len(self.available_symbols)} → {len(latest_symbols)}개")
                        self.available_symbols = latest_symbols
                    await self._subscribe(ws, config)
                    logger.info(f"[{self.exchange_id.upper()}] Collector Connected - {len(self.available_symbols)} symbols")

                    async for msg in ws:
                        if not self.is_running: break
                        
                        # [NEW] 마지막 수신 원본 raw 데이터 캐싱
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            self.last_raw = msg.data
                        elif msg.type == aiohttp.WSMsgType.BINARY:
                            try:
                                self.last_raw = msg.data.decode('utf-8', errors='ignore')
                            except Exception:
                                self.last_raw = str(msg.data)
                        
                        tick_data = self._parse_message(msg)
                        if tick_data:
                            # [NEW] 마지막 수신 틱 캐싱 및 처리 건수 카운팅
                            last_item = tick_data[-1] if isinstance(tick_data, list) else tick_data
                            self.last_tick = last_item
                            
                            if isinstance(tick_data, list):
                                self.total_processed_count += len(tick_data)
                                for tick in tick_data:
                                    self.processing_queue.put_nowait(tick)
                            else:
                                self.total_processed_count += 1
                                self.processing_queue.put_nowait(tick_data)

                self.ws = None
                # 정상적으로 소켓 루프가 종료(끊김)되었을 때도 즉각 재연결 폭주를 방지하기 위해 5초 대기 적용
                if self.is_running:
                    logger.warning(f"[{self.exchange_id.upper()}] WebSocket connection closed. Reconnecting in 5s...")
                    await asyncio.sleep(5)

            except Exception as e:
                self.ws = None
                if self.is_running:
                    await self._handle_connection_error(e)
