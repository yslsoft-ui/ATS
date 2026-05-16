import asyncio
import json
import aiohttp
import logging
from typing import List, Dict, Optional, Any, Callable
from src.engine.trade_engine import TradeEngine
from src.engine.strategy import StrategyRegistry
from src.engine.indicators import IndicatorCalculator

logger = logging.getLogger(__name__)

class UpbitCollector:
    """
    업비트 API로부터 실시간 체결 데이터를 수집하고 분석 엔진으로 배분합니다.
    """
    def __init__(
        self, 
        processing_queue: asyncio.Queue,
        db_queue: asyncio.Queue,
        candle_queue: asyncio.Queue,
        portfolio_manager: Any,
        on_data_callback: Optional[Callable] = None,
        on_signal_callback: Optional[Callable] = None,
        on_status_callback: Optional[Callable] = None
    ):
        self.processing_queue = processing_queue
        self.db_queue = db_queue
        self.candle_queue = candle_queue
        self.portfolio_manager = portfolio_manager
        self.on_data_callback = on_data_callback
        self.on_signal_callback = on_signal_callback
        self.on_status_callback = on_status_callback
        
        self.task: Optional[asyncio.Task] = None
        self.is_running = False
        self.trade_engines: Dict[str, TradeEngine] = {}
        self.indicator_calculators: Dict[str, IndicatorCalculator] = {}
        self.session: Optional[aiohttp.ClientSession] = None
        self.processor_tasks: List[asyncio.Task] = []
        self.available_symbols: List[str] = []
        self.total_processed_count = 0

    async def start(self, config: Dict[str, Any] = None):
        if self.is_running:
            return
        self.is_running = True
        self.task = asyncio.create_task(self.run(config))

    async def stop(self):
        self.is_running = False
        if self.task:
            # 워커 태스크 종료
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
        """종목별 워밍업을 백그라운드에서 하나씩 천천히 수행합니다."""
        engines = list(self.trade_engines.values())
        if not engines: return

        logger.info(f"{len(engines)}개 종목 백그라운드 워밍업 시작 (순차 진행)")
        for i, engine in enumerate(engines):
            if not self.is_running: break
            try:
                await engine.warm_up(db_path)
            except Exception as e:
                logger.error(f"Warmup failed for {engine.symbol}: {e}")
            await asyncio.sleep(0.05)
            if (i + 1) % 20 == 0:
                logger.info(f"워밍업 진행 중... ({i + 1}/{len(engines)})")
        logger.info(f"전체 {len(engines)}개 종목 백그라운드 워밍업 완료")

    async def data_processor_worker(self):
        """큐에서 데이터를 꺼내 분석 및 전략 실행을 담당하는 워커"""
        while self.is_running:
            try:
                data = await self.processing_queue.get()
                self.total_processed_count += 1
                
                symbol = data['code']
                
                # 1. 지표 업데이트 (Legacy 지원용, 필요 시 유지)
                if symbol in self.indicator_calculators:
                    self.indicator_calculators[symbol].update(data['trade_price'])
                
                # 2. 콜백 호출 (브로드캐스트 등)
                if self.on_data_callback:
                    await self.on_data_callback(data)
                
                # 3. DB 저장 큐에 삽입
                await self.db_queue.put(data)
                
                # 4. 트레이드 엔진 처리
                if symbol in self.trade_engines:
                    engine = self.trade_engines[symbol]
                    # process_tick에서 신호와 완성된 캔들을 받아옴 (비동기 호출)
                    signals, closed_candles = await engine.process_tick(data, self.portfolio_manager)
                    
                    for candle in closed_candles:
                        await self.candle_queue.put(candle)
                        
                    for sig in signals:
                        if self.on_signal_callback:
                            await self.on_signal_callback(sig, data['trade_price'])
                
                self.processing_queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Data Processor Worker Error: {e}")
                await asyncio.sleep(0.1)

    async def run(self, config: Dict[str, Any] = None):
        # 1. 종목 목록 로드
        try:
            if not self.session or self.session.closed:
                self.session = aiohttp.ClientSession()
            
            async with self.session.get("https://api.upbit.com/v1/market/all") as resp:
                markets = await resp.json()
                self.available_symbols = sorted([m['market'] for m in markets if m['market'].startswith('KRW-')])
                logger.info(f"{len(self.available_symbols)}개 KRW 종목 로드 완료")
        except Exception as e:
            logger.error(f"종목 조회 실패: {e}")
            if not self.available_symbols:
                self.available_symbols = ["KRW-BTC", "KRW-ETH", "KRW-XRP"]

        # 2. 엔진 객체 및 지표 계산기 초기화
        strategy_configs = config.get('strategies', {}) if config else {}
        enabled_strategies = []
        for s_id, s_conf in strategy_configs.items():
            if s_conf.get('enabled', False):
                enabled_strategies.append((s_id, s_conf.get('params', {})))

        for symbol in self.available_symbols:
            if symbol not in self.trade_engines:
                # 활성화된 전략 인스턴스 생성
                instances = []
                for s_id, s_params in enabled_strategies:
                    strat = StrategyRegistry.create_strategy(s_id, s_params)
                    if strat:
                        instances.append(strat)
                
                self.trade_engines[symbol] = TradeEngine(symbol, instances, on_status_callback=self.on_status_callback)
            
            if symbol not in self.indicator_calculators:
                self.indicator_calculators[symbol] = IndicatorCalculator(window_size=20)

        # 3. 백그라운드 워밍업 시작 (설정 기반)
        warmup_enabled = config.get('warmup_enabled', True) if config else True
        if warmup_enabled:
            # 상위 레벨의 db_path를 찾거나 기본값 사용
            db_path = config.get('db_path', 'data/backtest.db')
            asyncio.create_task(self.background_warmup(db_path))

        # 4. 처리 워커 시작 (설정 기반)
        worker_count = config.get('worker_count', 2) if config else 2
        for _ in range(worker_count): 
            self.processor_tasks.append(asyncio.create_task(self.data_processor_worker()))

        # 5. 웹소켓 수신 루프
        url = "wss://api.upbit.com/websocket/v1"
        subscribe_data = [{"ticket": "collector"}, {"type": "trade", "codes": self.available_symbols}]

        while self.is_running:
            try:
                if not self.session or self.session.closed:
                    self.session = aiohttp.ClientSession()
                
                async with self.session.ws_connect(url, heartbeat=30.0) as ws:
                    await ws.send_json(subscribe_data)
                    logger.info(f"Collector Connected - {len(self.available_symbols)} symbols")

                    async for msg in ws:
                        if not self.is_running: break
                        if msg.type == aiohttp.WSMsgType.BINARY:
                            try:
                                data = json.loads(msg.data.decode('utf-8'))
                                self.processing_queue.put_nowait(data) 
                            except Exception as parse_error:
                                logger.error(f"Msg Parse Error: {parse_error}")
                        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            break
            except Exception as e:
                if self.is_running:
                    logger.error(f"Collector Connection Error: {e}. Reconnecting in 5s...")
                    await asyncio.sleep(5)
