import asyncio
import time
from typing import Dict, List, Optional, Any, Callable
from src.engine.utils.telemetry import get_logger
from src.engine.candles import CandleGenerator, Candle
from src.engine.trade_engine import TradeEngine
from src.engine.strategy import StrategyRegistry

logger = get_logger("market_data_processor")

class MarketDataProcessor:
    """
    수집 큐로부터 데이터를 꺼내 1분봉 조립, DB 적재 큐 전송, 
    및 자동매매 전략 엔진(TradeEngine) 실행을 담당하는 실시간 가공 처리기입니다.
    """
    def __init__(
        self,
        exchange: str,
        processing_queue: asyncio.Queue,
        db_queue: Optional[Any] = None,
        candle_queue: Optional[Any] = None,
        on_data_callback: Optional[Callable] = None,
        on_signal_callback: Optional[Callable] = None,
        on_status_callback: Optional[Callable] = None,
    ):
        self.exchange = exchange
        self.processing_queue = processing_queue
        self.db_queue = db_queue
        self.candle_queue = candle_queue
        self.on_data_callback = on_data_callback
        self.on_signal_callback = on_signal_callback
        self.on_status_callback = on_status_callback

        self.is_running = False
        self.trade_engines: Dict[str, TradeEngine] = {}
        self.candle_generator = CandleGenerator(intervals=[60])
        self.candle_lock = asyncio.Lock()
        self.total_processed_count = 0
        self.processor_tasks: List[asyncio.Task] = []
        self._flush_task: Optional[asyncio.Task] = None  # [NEW] 분 경계 flush 태스크
        self.available_symbols: List[str] = []
        self.config: Dict[str, Any] = {}

    async def start(self, config: Dict[str, Any], worker_count: int = 2):
        if self.is_running:
            return
        self.config = config
        self.is_running = True
        
        # 1. 엔진 초기화
        self._init_trade_engines(config)

        # 2. 백그라운드 워밍업 기동
        warmup_enabled = config.get('exchanges', {}).get(self.exchange, {}).get('warmup_enabled', config.get('warmup_enabled', True))
        if warmup_enabled:
            db_path = config.get('db_path', 'data/backtest.db')
            asyncio.create_task(self.background_warmup(db_path))

        # 3. 데이터 가공 워커 풀 기동
        for _ in range(worker_count):
            self.processor_tasks.append(asyncio.create_task(self.data_processor_worker()))
            
        # 4. 분 경계 캔들 강제 flush 태스크 기동
        self._flush_task = asyncio.create_task(self._candle_flush_loop())
        
        logger.info(f"[{self.exchange.upper()}] MarketDataProcessor started with {worker_count} workers.")

    async def stop(self):
        self.is_running = False
        
        if self._flush_task and not self._flush_task.done():
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
            self._flush_task = None

        for t in self.processor_tasks:
            t.cancel()
        if self.processor_tasks:
            await asyncio.gather(*self.processor_tasks, return_exceptions=True)
            self.processor_tasks.clear()
        logger.info(f"[{self.exchange.upper()}] MarketDataProcessor stopped.")

    async def background_warmup(self, db_path: str = "data/backtest.db"):
        engines = list(self.trade_engines.values())
        if not engines:
            return

        logger.info(f"[{self.exchange.upper()}] {len(engines)}개 종목 전략 호스트 백그라운드 워밍업 시작 (Processor)")
        for i, engine in enumerate(engines):
            if not self.is_running:
                break
            try:
                await engine.warm_up(db_path)
            except Exception as e:
                logger.error(f"[{self.exchange.upper()}] Warmup failed for {engine.symbol}: {e}")
            await asyncio.sleep(0.05)
            if (i + 1) % 100 == 0:
                logger.info(f"[{self.exchange.upper()}] 전략 워밍업 진행 중... ({i + 1}/{len(engines)})")
        logger.info(f"[{self.exchange.upper()}] 전체 {len(engines)}개 종목 전략 호스트 백그라운드 워밍업 완료")

    async def data_processor_worker(self):
        while self.is_running:
            try:
                data = await self.processing_queue.get()
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

                # 2. 완성된 캔들이 있는 경우 DB/ZMQ 영속화 큐 주입
                for candle in closed_candles:
                    if self.candle_queue:
                        await self.candle_queue.put(candle)
                    else:
                        logger.warning(f"[{self.exchange.upper()}] candle_queue is None! Cannot enqueue candle for {symbol}")

                # 3. 데이터 브로드캐스트 콜백 실행 및 DB 적재 큐 주입
                if self.on_data_callback:
                    await self.on_data_callback(data)

                if self.db_queue:
                    await self.db_queue.put(data)

                # 4. 자동매매 전략 구동 종목에 대한 시그널 연산
                if symbol in self.trade_engines:
                    engine = self.trade_engines[symbol]
                    signals, _ = await engine.process_tick(data, None)
                    for sig in signals:
                        if self.on_signal_callback:
                            await self.on_signal_callback(sig, data['trade_price'])

                self.processing_queue.task_done()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[{self.exchange.upper()}] Processor Worker Error: {e}")
                await asyncio.sleep(0.1)

    def _init_trade_engines(self, config: Dict[str, Any]):
        strategy_configs = config.get('strategies', {}) if config else {}
        enabled_strategies = []
        for s_id, s_conf in strategy_configs.items():
            if s_conf.get('enabled', False):
                params = s_conf.get('params', {}).copy()
                overrides = s_conf.get('overrides', {}).get(self.exchange, {}).get('params', {})
                params.update(overrides)
                enabled_strategies.append((s_id, params))

        # 1. 현재 수집 대상이 아닌 기존 엔진 정리
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
                self.trade_engines[symbol] = TradeEngine(
                    self.exchange, 
                    symbol, 
                    instances, 
                    on_status_callback=self.on_status_callback
                )

    async def reload_symbols(self, config: Dict[str, Any], new_symbols: List[str]):
        self.config = config
        self.available_symbols = new_symbols
        self._init_trade_engines(config)
        logger.info(f"[{self.exchange.upper()}] Processor reloaded with {len(new_symbols)} symbols.")

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
