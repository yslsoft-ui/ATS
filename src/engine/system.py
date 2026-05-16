import asyncio
import os
import logging
import time
from typing import Optional, List, Dict, Callable, Any
from src.database.connection import get_db_conn
from src.engine.collector import UpbitCollector
from src.engine.portfolio import PortfolioManager, Portfolio
from src.engine.strategy import StrategyRegistry
from src.engine.loader import load_dynamic_strategies

logger = logging.getLogger(__name__)

from src.config.manager import ConfigManager
from src.engine.pipeline import ExecutionPipeline

class TradingSystem:
    """
    트레이딩 시스템의 모든 컴포넌트와 백그라운드 서비스를 총괄하는 슈퍼바이저입니다.
    """
    def __init__(self, config_path: str):
        # 1. 설정 매니저 초기화 및 로드
        self.config_manager = ConfigManager(config_path)
        
        self.db_path = self.config_manager.get('system.db_path', 'data/backtest.db')
        self.strategies_dir = self.config_manager.get('system.strategies_dir', 'src/engine/strategies')
        
        # 전역 큐 관리
        self.db_queue = asyncio.Queue()
        self.candle_queue = asyncio.Queue()
        self.processing_queue = asyncio.Queue()
        
        # 컴포넌트 초기화
        self.portfolio_manager = PortfolioManager()
        self.execution_pipeline = ExecutionPipeline(self.portfolio_manager) # [NEW]
        self.collector: Optional[UpbitCollector] = None
        
        # 전략 설정 동기화
        self.strategy_configs = self.config_manager.get('strategies', {})
        self.config_manager.subscribe(self._on_config_changed)
        
        # 백그라운드 태스크 관리
        self.tasks: List[asyncio.Task] = []
        self.is_running = False
        
        # 외부 브로드캐스트 콜백 (웹소켓 등)
        self.broadcast_callback: Optional[Callable] = None

    async def boot(self):
        """시스템의 모든 구성 요소를 올바른 순서로 기동합니다."""
        if self.is_running:
            return
        self.is_running = True
        
        logger.info("TradingSystem booting...")
        
        # 설정 감시 시작
        await self.config_manager.start_watching()
        
        # 1. DB 초기화 및 포트폴리오 로드
        from src.database.schema import init_db
        await init_db()
        await self.portfolio_manager.load_from_db()
        
        # 기본 포트폴리오 보장
        if 'default' not in self.portfolio_manager.portfolios:
            p = Portfolio("default", "기본 시뮬레이션", 10000000.0, "upbit")
            self.portfolio_manager.add_portfolio(p)
            await self.portfolio_manager.save_to_db("default")

        # 2. 전략 동적 로드 및 동기화 [NEW]
        load_dynamic_strategies(self.strategies_dir)
        self.sync_strategies()
        
        # 3. 수집기 초기화 및 콜백 설정
        self.collector = UpbitCollector(
            processing_queue=self.processing_queue,
            db_queue=self.db_queue,
            candle_queue=self.candle_queue,
            portfolio_manager=self.portfolio_manager,
            on_data_callback=self._handle_market_data,
            on_signal_callback=self._handle_strategy_signal,
            on_status_callback=self._handle_strategy_status # [NEW]
        )
        
        # 4. 백그라운드 서비스 시작
        self.tasks.append(asyncio.create_task(self._db_writer_loop()))
        self.tasks.append(asyncio.create_task(self._candle_writer_loop()))
        
        # 5. 수집기 시작
        full_config = self.config_manager.config.copy()
        full_config['db_path'] = self.db_path  # 시스템 DB 경로 주입
        await self.collector.start(full_config)
        
        logger.info("TradingSystem all components started.")

    def sync_strategies(self):
        """디스크의 전략 파일과 설정 파일(YAML)을 동기화합니다."""
        logger.info("Syncing strategies between disk and config...")
        all_metadata = StrategyRegistry.get_all_metadata()
        current_configs = self.config_manager.get('strategies', {})
        
        changed = False
        for meta in all_metadata:
            s_id = meta['id']
            if s_id not in current_configs:
                logger.info(f"New strategy detected: {s_id}. Adding to config.")
                # 기본값 추출 (UI용 상세 구조에서 값만 추출)
                default_vals = {k: v['default'] if isinstance(v, dict) else v for k, v in meta['params'].items()}
                current_configs[s_id] = {
                    "enabled": False,
                    "params": default_vals
                }
                changed = True
        
        if changed:
            self.config_manager.update('strategies', current_configs)
            self.strategy_configs = current_configs
            logger.info("Strategy configuration updated and persisted.")

    async def shutdown(self):
        """모든 서비스를 안전하게 종료하고 데이터를 플러시합니다."""
        logger.info("TradingSystem shutting down...")
        self.is_running = False
        
        # 설정 감시 중지
        await self.config_manager.stop_watching()
        
        # 1. 수집기 중지
        if self.collector:
            await self.collector.stop()
        
        # 2. 모든 포트폴리오 상태 저장
        for pid in self.portfolio_manager.portfolios:
            await self.portfolio_manager.save_to_db(pid)
            
        # 3. 백그라운드 태스크 종료 및 잔여 데이터 처리
        for task in self.tasks:
            task.cancel()
        
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks = []
        
        logger.info("TradingSystem shutdown complete.")

    async def _handle_market_data(self, data: dict):
        """수집된 시장 데이터를 외부로 브로드캐스트합니다."""
        if self.broadcast_callback:
            await self.broadcast_callback(data)

    async def _handle_strategy_signal(self, signal, price: float):
        """전략 엔진에서 발생한 신호를 처리합니다."""
        # 주문 실행 파이프라인에 위임
        await self.execution_pipeline.process_signal(signal, price)

    async def _handle_strategy_status(self, status: dict):
        """전략의 실시간 상태 정보(Audit Log)를 처리합니다."""
        if self.broadcast_callback:
            await self.broadcast_callback(status)

    def set_broadcast_callback(self, callback: Callable):
        """외부 브로드캐스트 콜백을 설정합니다."""
        self.broadcast_callback = callback
        self.execution_pipeline.set_broadcast_callback(callback)

    async def _on_config_changed(self, new_config: Dict[str, Any]):
        """설정 파일 변경 시 전략 파라미터를 실시간으로 업데이트합니다."""
        logger.info("Applying new strategy configurations...")
        self.strategy_configs = new_config.get('strategies', {})
        
        if not self.collector:
            return
            
        # 모든 수집기 엔진의 전략 파라미터 업데이트
        for symbol, engine in self.collector.trade_engines.items():
            for strategy_id, config in self.strategy_configs.items():
                if config.get('enabled', False):
                    params = config.get('params', {})
                    engine.update_strategy_params(strategy_id, params)
        
        logger.info("Strategy parameters hot-reloaded successfully.")

    async def _db_writer_loop(self):
        """틱 데이터를 DB에 배치 저장합니다 (main.py 로직 이관)."""
        while self.is_running:
            try:
                async with get_db_conn() as db:
                    while self.is_running:
                        buffer = []
                        try:
                            while len(buffer) < 500:
                                item = await asyncio.wait_for(self.db_queue.get(), timeout=1.0)
                                buffer.append((item['code'], item['trade_price'], item['trade_volume'], item['ask_bid'], item['trade_timestamp']))
                                self.db_queue.task_done()
                        except asyncio.TimeoutError:
                            pass

                        if buffer:
                            await db.executemany("INSERT INTO trades (symbol, trade_price, trade_volume, ask_bid, trade_timestamp) VALUES (?, ?, ?, ?, ?)", buffer)
                            await db.commit()
                            await asyncio.sleep(0.01)
            except asyncio.CancelledError:
                # 종료 시 잔여 데이터 플러시 로직 (필요 시 추가)
                break
            except Exception as e:
                logger.error(f"DB Writer Loop Error: {e}")
                await asyncio.sleep(1)

    async def _candle_writer_loop(self):
        """캔들 데이터를 DB에 저장합니다 (main.py 로직 이관)."""
        while self.is_running:
            try:
                async with get_db_conn() as db:
                    while self.is_running:
                        candle = await self.candle_queue.get()
                        await db.execute(
                            "INSERT OR REPLACE INTO candles (symbol, interval, timestamp, open, high, low, close, volume) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                            (candle.symbol, candle.interval, candle.timestamp, candle.open, candle.high, candle.low, candle.close, candle.volume)
                        )
                        await db.commit()
                        self.candle_queue.task_done()
                        await asyncio.sleep(0.01)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Candle Writer Loop Error: {e}")
                await asyncio.sleep(1)
