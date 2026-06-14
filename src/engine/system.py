import asyncio
import os
import aiohttp
from src.engine.utils.telemetry import get_logger
import time
from typing import Optional, List, Dict, Callable, Any
from src.database.connection import get_db_conn
from src.database.repository import SqliteTradingRepository
from src.engine.portfolio import PortfolioManager, Portfolio
from src.engine.strategy import StrategyRegistry
from src.engine.loader import load_dynamic_strategies
from src.engine.utils.stock_mapper import stock_mapper
from src.engine.market.upbit import UpbitMarketAdapter
from src.engine.market.bithumb import BithumbMarketAdapter
from src.engine.market.kis import KisMarketAdapter


logger = get_logger(__name__)

from src.config.manager import ConfigManager
from src.engine.pipeline import ExecutionPipeline
from src.engine.credentials import CredentialProvider

class TradingSystem:
    """
    트레이딩 시스템의 모든 컴포넌트와 백그라운드 서비스를 총괄하는 슈퍼바이저입니다.
    """
    def __init__(self, config_path: str, db_path: Optional[str] = None):
        # 1. 설정 매니저 초기화 및 로드
        self.config_manager = ConfigManager(config_path)
        
        # 명시적으로 주입된 db_path가 있으면 최우선으로 사용하고, 없으면 설정 매니저 기본값 사용
        self.db_path = db_path if db_path is not None else self.config_manager.get('system.db_path', 'data/backtest.db')
        self.strategies_dir = self.config_manager.get('system.strategies_dir', 'src/engine/strategies')
        
        # 전역 리소스를 통합 래핑하는 레포지토리 초기화
        cooldown_days = self.config_manager.get('system.champion_cooldown_days', 7.0)
        cooldown_trades = self.config_manager.get('system.champion_cooldown_trades', 100)
        self.repository = SqliteTradingRepository(
            system=self,
            db_path=self.db_path,
            champion_cooldown_days=cooldown_days,
            champion_cooldown_trades=cooldown_trades
        )
        
        # 전역 큐 관리 (레거시 호환 및 내부 버퍼용)
        self.processing_queue = asyncio.Queue()
        
        # 컴포넌트 초기화 - 주입된 DB 경로 관통 주입
        self.portfolio_manager = PortfolioManager(db_path=self.db_path)
        self.execution_pipeline = ExecutionPipeline(self.portfolio_manager) # [NEW]
        
        # 사용자 명령 디스패처 초기화 (의존성 분리 주입)
        from src.engine.command import UserCommandDispatcher
        self.dispatcher = UserCommandDispatcher(
            repository=self.repository,
            config_manager=self.config_manager,
            portfolio_manager=self.portfolio_manager
        )
        self.cred_provider = CredentialProvider(self.config_manager.config)
        
        # 실시간 가격 캐시 [NEW]
        self.latest_prices: Dict[str, Dict[str, Any]] = {} # { "exchange:symbol": {price, change_rate, ...} }
        
        # 전략 설정 동기화
        self.strategy_configs = self.config_manager.get('strategies', {})
        self.config_manager.subscribe(self._on_config_changed)
        
        # 백그라운드 태스크 관리
        self.tasks: List[asyncio.Task] = []
        self.is_running = False
        
        # 외부 브로드캐스트 콜백 (웹소켓 등)
        self.broadcast_callback: Optional[Callable] = None

        # 수집기 및 전략 엔진 상태 전역 캐시 (Web-only 기동 시 ZMQ 통신으로 채워짐)
        self.collector_statuses: Dict[str, Dict[str, Any]] = {
            "upbit": {"is_running": False, "error": None},
            "bithumb": {"is_running": False, "error": None},
            "kis": {"is_running": False, "error": None}
        }
        self.strategy_status: Dict[str, Any] = {
            "is_running": False,
            "active_engines": 0,
            "last_heartbeat": 0.0,
            "error": None
        }
        self.queue_status: Dict[str, Any] = {
            "processing": 0,
            "database": 0,
            "candle": 0,
            "total": 0
        }
        # [NEW] 동적 active_symbols 목록 보관용 캐시
        self.collector_active_symbols: Dict[str, Dict[str, Any]] = {}
        # [NEW] 수집기 데몬 상세 실시간 상태 보관용 캐시
        self.collector_daemon_detail: Dict[str, Any] = {}
        # [NEW] 클린업 데몬 상세 실시간 상태 보관용 캐시
        self.cleanup_status: Dict[str, Any] = {}


    async def boot(self):
        """시스템의 모든 구성 요소를 올바른 순서로 기동합니다."""
        if self.is_running:
            return
        self.is_running = True
        
        logger.info("TradingSystem booting in Web-only mode...")
        
        # 설정 감시 시작
        await self.config_manager.start_watching()
        
        # 1. DB 초기화 및 포트폴리오 로드
        from src.database.schema import init_db
        await init_db(self.db_path)
        await stock_mapper.load_from_db(self.db_path)
        await self.portfolio_manager.load_from_db(exclude_types=['backtest'])
        
        # 2. 전략 동적 로드 및 동기화 [NEW]
        load_dynamic_strategies(self.strategies_dir)
        self.sync_strategies()
        
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
        
        # 1.1 인증 세션 종료
        await self.cred_provider.close()
        
        # 3. 백그라운드 태스크 종료 및 잔여 데이터 처리
        for task in self.tasks:
            task.cancel()
        
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks = []
        
        logger.info("TradingSystem shutdown complete.")

    async def _handle_market_data(self, data: dict):
        """수집된 시장 데이터를 외부로 브로드캐스트하고 캐싱합니다."""
        if data.get('type') == 'tick':
            ex = data.get('exchange_id', 'upbit')
            sym = data.get('code', '')
            key = f"{ex}:{sym}"
            
            # 기존 캐시값 활용 (변동률, 고가, 저가, 거래대금의 유실 방지)
            prev = self.latest_prices.get(key, {})
            
            # 실시간 변동액 파싱/정규화
            change_price = data.get('change_price')
            if change_price is None:
                change_price = data.get('signed_change_price')
                
            self.latest_prices[key] = {
                'exchange': ex,
                'market': sym,
                'trade_price': data.get('trade_price') if data.get('trade_price') is not None else prev.get('trade_price'),
                'signed_change_rate': data.get('signed_change_rate') if data.get('signed_change_rate') is not None else prev.get('signed_change_rate', 0),
                'change_price': change_price if change_price is not None else prev.get('change_price', 0.0),
                'timestamp': data.get('trade_timestamp') if data.get('trade_timestamp') is not None else prev.get('timestamp'),
                'high_price': data.get('high_price') if data.get('high_price') is not None else prev.get('high_price'),
                'low_price': data.get('low_price') if data.get('low_price') is not None else prev.get('low_price'),
                'acc_trade_price_24h': data.get('acc_trade_price_24h') if data.get('acc_trade_price_24h') is not None else prev.get('acc_trade_price_24h', 0)
            }
            
            # 브로드캐스트 데이터에도 change_price 필드 공통화 적용
            if 'change_price' not in data:
                data['change_price'] = change_price or 0.0
        
        elif data.get('type') == 'rank':
            # KIS 등에서 넘어온 랭킹 데이터 처리
            ex = data.get('exchange', 'kis')
            rank_list = data.get('data', [])
            for item in rank_list:
                sym = item.get('code')
                key = f"{ex}:{sym}"
                self.latest_prices[key] = {
                    'exchange': ex,
                    'market': sym,
                    'trade_price': item.get('price'),
                    'signed_change_rate': item.get('change_rate'),
                    'change_price': item.get('change_price', 0.0),
                    'korean_name': item.get('name'),
                    'acc_trade_price_24h': item.get('volume'),
                    'rank': item.get('rank')
                }

        if self.broadcast_callback:
            await self.broadcast_callback(data)

    def get_latest_price(self, exchange: str, symbol: str) -> Dict[str, Any]:
        """특정 종목의 최신 가격 정보를 반환합니다."""
        return self.latest_prices.get(f"{exchange}:{symbol}", {})

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
        from src.engine.utils.telemetry import update_broadcast_callback
        self.broadcast_callback = callback
        self.execution_pipeline.set_broadcast_callback(callback)
        update_broadcast_callback(callback)

    async def _on_config_changed(self, new_config: Dict[str, Any]):
        """설정 파일 변경 시 전략 파라미터 및 인증 정보를 실시간으로 업데이트합니다."""
        logger.info("Applying new configurations...")
        self.strategy_configs = new_config.get('strategies', {})
        
        # 인증 정보 동기화
        self.cred_provider.config = new_config

    async def get_all_market_data(self) -> Dict[str, Any]:
        """전체 마켓(Upbit, Bithumb, KIS) 종목 정보를 비동기 병렬로 취합하여 반환합니다."""
        results = []
        latency = {"upbit": 0, "bithumb": 0, "kis": 0}
        try:
            async with aiohttp.ClientSession() as session:
                adapters = [
                    ("upbit", UpbitMarketAdapter()),
                    ("bithumb", BithumbMarketAdapter()),
                    ("kis", KisMarketAdapter())
                ]

                async def measure_adapter(name, adapter):
                    start = time.time()
                    try:
                        res = await adapter.fetch_market_data(session, self)
                        ms = int((time.time() - start) * 1000)
                        return name, res, ms
                    except Exception as e:
                        ms = int((time.time() - start) * 1000)
                        return name, e, ms

                tasks = [measure_adapter(name, adapter) for name, adapter in adapters]
                adapter_results = await asyncio.gather(*tasks)
                
                for name, res, ms in adapter_results:
                    latency[name] = ms
                    if isinstance(res, Exception):
                        logger.error(f"Market adapter {name} failed: {res}")
                        continue
                    
                    for dto in res:
                        # 업비트의 한글명 신규 매핑 건은 가드 조건 후 캐시/DB 기록
                        if dto.exchange == "upbit":
                            if stock_mapper.get_name('upbit', dto.market) != dto.korean_name:
                                await stock_mapper.add_mapping_async('upbit', dto.market, dto.korean_name, self.db_path)
                                
                        results.append(dto.model_dump())
                        
            sorted_tickers = sorted(results, key=lambda x: x['acc_trade_price_24h'], reverse=True)
            
            import datetime
            now_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            
            return {
                "tickers": sorted_tickers,
                "latency": latency,
                "timestamp": now_str
            }
        except Exception as e:
            logger.error(f"Error in get_all_market_data: {e}")
            import datetime
            return {
                "tickers": results,
                "latency": latency,
                "timestamp": datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }



