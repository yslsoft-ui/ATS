import asyncio
import os
import aiohttp
from src.engine.utils.telemetry import get_logger
import time
from typing import Optional, List, Dict, Callable, Any
from src.database.connection import get_db_conn
from src.database.repository import SqliteTradingRepository
from src.database.writer import DatabaseWriter
from src.engine.collector_base import CollectorRegistry
import src.engine.collector
import src.engine.collector_kis
import src.engine.collector_bithumb
from src.engine.portfolio import PortfolioManager, Portfolio
from src.engine.strategy import StrategyRegistry
from src.engine.loader import load_dynamic_strategies
from src.engine.utils.stock_mapper import stock_mapper

logger = get_logger(__name__)

from src.config.manager import ConfigManager
from src.engine.pipeline import ExecutionPipeline
from src.engine.credentials import CredentialProvider

class TradingSystem:
    """
    트레이딩 시스템의 모든 컴포넌트와 백그라운드 서비스를 총괄하는 슈퍼바이저입니다.
    """
    def __init__(self, config_path: str, db_path: Optional[str] = None, is_web_only: bool = False):
        self.is_web_only = is_web_only
        # 1. 설정 매니저 초기화 및 로드
        self.config_manager = ConfigManager(config_path)
        
        # 명시적으로 주입된 db_path가 있으면 최우선으로 사용하고, 없으면 설정 매니저 기본값 사용
        self.db_path = db_path if db_path is not None else self.config_manager.get('system.db_path', 'data/backtest.db')
        self.strategies_dir = self.config_manager.get('system.strategies_dir', 'src/engine/strategies')
        
        # 전역 리소스를 통합 래핑하는 레포지토리 초기화
        self.repository = SqliteTradingRepository(system=self)
        
        # 비동기 데이터베이스 영속화 라이터 (Candidate 1)
        self.db_writer = DatabaseWriter(db_path=self.db_path)
        
        # 전역 큐 관리 (레거시 호환 및 내부 버퍼용)
        self.processing_queue = asyncio.Queue()
        
        # 컴포넌트 초기화 - 주입된 DB 경로 관통 주입
        self.portfolio_manager = PortfolioManager(db_path=self.db_path)
        self.execution_pipeline = ExecutionPipeline(self.portfolio_manager) # [NEW]
        self.collectors: List[Any] = []
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

    async def boot(self):
        """시스템의 모든 구성 요소를 올바른 순서로 기동합니다."""
        if self.is_running:
            return
        self.is_running = True
        
        logger.info("TradingSystem booting with DatabaseWriter...")
        if not self.is_web_only:
            await self.db_writer.start()
        
        # 설정 감시 시작
        await self.config_manager.start_watching()
        
        # 1. DB 초기화 및 포트폴리오 로드
        from src.database.schema import init_db
        await init_db(self.db_path)
        await self.portfolio_manager.load_from_db()
        
        # 기본 포트폴리오 보장 (코인)
        if 'default' not in self.portfolio_manager.portfolios:
            p = Portfolio("default", "기본 모의투자(코인)", 10000000.0, "upbit")
            self.portfolio_manager.add_portfolio(p)
            await self.portfolio_manager.save_to_db("default")

        # 주식용 기본 포트폴리오 보장
        if 'stock_default' not in self.portfolio_manager.portfolios:
            p = Portfolio("stock_default", "기본 모의투자(주식)", 50000000.0, "kis")
            self.portfolio_manager.add_portfolio(p)
            await self.portfolio_manager.save_to_db("stock_default")

        # 빗썸용 기본 포트폴리오 보장
        if 'bithumb_default' not in self.portfolio_manager.portfolios:
            p = Portfolio("bithumb_default", "기본 모의투자(빗썸)", 10000000.0, "bithumb")
            self.portfolio_manager.add_portfolio(p)
            await self.portfolio_manager.save_to_db("bithumb_default")

        # 2. 전략 동적 로드 및 동기화 [NEW]
        load_dynamic_strategies(self.strategies_dir)
        self.sync_strategies()
        
        # 3. 수집기들 초기화 및 레포지토리 의존성 주입 [MODIFIED]
        common_kwargs = {
            'processing_queue': self.processing_queue,
            'db_queue': self.db_writer.db_queue,
            'candle_queue': self.db_writer.candle_queue,
            'repository': self.repository,
            'portfolio_manager': self.portfolio_manager,
            'on_data_callback': self._handle_market_data,
            'on_signal_callback': self._handle_strategy_signal,
            'on_status_callback': self._handle_strategy_status
        }
        
        if not self.is_web_only:
            exchanges_config = self.config_manager.get('exchanges', {})
            for exchange_id, exch_config in exchanges_config.items():
                if not exch_config.get('enabled', True):
                    continue
                collector = CollectorRegistry.create(exchange_id, **common_kwargs)
                if collector:
                    self.collectors.append(collector)
                    logger.info(f"Collector registered: {exchange_id}")
            
            # 🌟 백그라운드 DB 벌크 라이터는 이제 레포지토리가 담당하므로 레거시 루프 미사용!
            
            # 5. 수집기 시작
            full_config = self.config_manager.config.copy()
            full_config['db_path'] = self.db_path  # 시스템 DB 경로 주입
            for collector in self.collectors:
                await collector.start(full_config)
        
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
        
        # 데이터베이스 영속화 라이터 중단 및 안전 플러시
        if not self.is_web_only:
            await self.db_writer.stop()
        
        # 설정 감시 중지
        await self.config_manager.stop_watching()
        
        # 1. 수집기들 중지
        if not self.is_web_only:
            for collector in self.collectors:
                await collector.stop()
        
        # 1.1 인증 세션 종료
        await self.cred_provider.close()
        
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
        """수집된 시장 데이터를 외부로 브로드캐스트하고 캐싱합니다."""
        if data.get('type') == 'tick':
            ex = data.get('exchange', 'upbit')
            sym = data.get('code', '')
            key = f"{ex}:{sym}"
            
            # 기존 캐시값 활용 (변동률, 고가, 저가, 거래대금의 유실 방지)
            prev = self.latest_prices.get(key, {})
            
            self.latest_prices[key] = {
                'exchange': ex,
                'market': sym,
                'trade_price': data.get('trade_price') if data.get('trade_price') is not None else prev.get('trade_price'),
                'signed_change_rate': data.get('signed_change_rate') if data.get('signed_change_rate') is not None else prev.get('signed_change_rate', 0),
                'timestamp': data.get('trade_timestamp') if data.get('trade_timestamp') is not None else prev.get('timestamp'),
                'high_price': data.get('high_price') if data.get('high_price') is not None else prev.get('high_price'),
                'low_price': data.get('low_price') if data.get('low_price') is not None else prev.get('low_price'),
                'acc_trade_price_24h': data.get('acc_trade_price_24h') if data.get('acc_trade_price_24h') is not None else prev.get('acc_trade_price_24h', 0)
            }
        
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
        
        if not self.collectors:
            return
            
        # 모든 수집기 엔진의 전략 파라미터 업데이트
        for collector in self.collectors:
            exch = getattr(collector, 'exchange', 'upbit')
            for symbol, engine in collector.trade_engines.items():
                for strategy_id, config in self.strategy_configs.items():
                    if config.get('enabled', False):
                        # 기본 파라미터
                        params = config.get('params', {}).copy()
                        
                        # 거래소별 오버라이드 적용 [NEW]
                        overrides = config.get('overrides', {}).get(exch, {})
                        if 'params' in overrides:
                            params.update(overrides['params'])
                            
                        engine.update_strategy_params(strategy_id, params)
        
        logger.info(f"Strategy parameters hot-reloaded for {len(self.collectors)} collectors.")

        # (비동기 DB/Candle 영속화 기능은 이제 self.db_writer가 전담하여 격리 수행합니다)
        pass

    async def get_all_market_data(self) -> List[Dict[str, Any]]:
        """전체 마켓(Upbit, Bithumb, KIS) 종목 정보를 취합하여 반환합니다."""
        results = []
        try:
            async with aiohttp.ClientSession() as session:
                # 1. Upbit API를 사용하여 실시간으로 현재가 조회
                async with session.get("https://api.upbit.com/v1/market/all?is_details=false") as resp:
                    all_markets = await resp.json()
                krw_markets = [m for m in all_markets if m['market'].startswith('KRW-')]
                market_codes = [m['market'] for m in krw_markets]

                tickers = []
                for i in range(0, len(market_codes), 100):
                    batch = ','.join(market_codes[i:i+100])
                    async with session.get(f"https://api.upbit.com/v1/ticker?markets={batch}") as resp:
                        tickers.extend(await resp.json())

                market_map = {m['market']: m['korean_name'] for m in krw_markets}
                for t in tickers:
                    m_code = t['market'].replace('KRW-', '')
                    korean_name = market_map.get(t['market'], t['market'])
                    
                    # stock_mapper에 최신 한글명을 동적 주입 및 캐시화 [NEW]
                    stock_mapper.add_mapping('upbit', m_code, korean_name)
                    
                    results.append({
                        "exchange": "upbit",
                        "market": m_code,
                        "korean_name": korean_name,
                        "trade_price": t.get('trade_price', 0),
                        "signed_change_rate": t.get('signed_change_rate', 0),
                        "acc_trade_price_24h": t.get('acc_trade_price_24h', 0),
                        "high_price": t.get('high_price', 0),
                        "low_price": t.get('low_price', 0),
                    })

                # 2. Bithumb 가상자산 종목 추가 (신형 V1 REST Ticker API 실시간 연동 최적화)
                bithumb_symbols = set()
                for collector in self.collectors:
                    if getattr(collector, 'exchange', '') == 'bithumb':
                        bithumb_symbols.update(getattr(collector, 'available_symbols', []))
                for key in self.latest_prices.keys():
                    if key.startswith('bithumb:'):
                        bithumb_symbols.add(key.split(':')[1])

                if bithumb_symbols:
                    bithumb_config = self.config_manager.get('exchanges.bithumb', {})
                    bithumb_api_url = bithumb_config.get('api_url', 'https://api.bithumb.com/v1')
                    bithumb_markets = [f"KRW-{s}" for s in bithumb_symbols]
                    
                    bithumb_tickers = []
                    try:
                        for i in range(0, len(bithumb_markets), 100):
                            batch = ",".join(bithumb_markets[i:i+100])
                            async with session.get(f"{bithumb_api_url}/ticker?markets={batch}") as resp:
                                if resp.status == 200:
                                    bithumb_tickers.extend(await resp.json())
                    except Exception as e:
                        logger.error(f"Failed to fetch Bithumb tickers in get_all_market_data: {e}")

                    ticker_map = {t['market'].replace('KRW-', ''): t for t in bithumb_tickers if 'market' in t}
                    
                    for s_code in bithumb_symbols:
                        t = ticker_map.get(s_code, {})
                        if t:
                            key = f"bithumb:{s_code}"
                            prev = self.latest_prices.get(key, {})
                            self.latest_prices[key] = {
                                'exchange': 'bithumb',
                                'market': s_code,
                                'trade_price': float(t.get('trade_price') if t.get('trade_price') is not None else prev.get('trade_price', 0)),
                                'signed_change_rate': float(t.get('signed_change_rate') if t.get('signed_change_rate') is not None else prev.get('signed_change_rate', 0)),
                                'timestamp': int(t.get('timestamp') if t.get('timestamp') is not None else prev.get('timestamp', time.time() * 1000)),
                                'high_price': float(t.get('high_price') if t.get('high_price') is not None else prev.get('high_price', 0)),
                                'low_price': float(t.get('low_price') if t.get('low_price') is not None else prev.get('low_price', 0)),
                                'acc_trade_price_24h': float(t.get('acc_trade_price_24h') if t.get('acc_trade_price_24h') is not None else prev.get('acc_trade_price_24h', 0))
                            }
                        
                        latest = self.get_latest_price('bithumb', s_code)
                        results.append({
                            "exchange": "bithumb",
                            "market": s_code,
                            "korean_name": stock_mapper.get_name('bithumb', s_code),
                            "trade_price": latest.get('trade_price', 0),
                            "signed_change_rate": latest.get('signed_change_rate', 0),
                            "acc_trade_price_24h": latest.get('acc_trade_price_24h', 0),
                            "high_price": latest.get('high_price', 0),
                            "low_price": latest.get('low_price', 0)
                        })

                # 3. 국내 주식 종목 추가 (KIS)
                kis_symbols = set()
                for collector in self.collectors:
                    if getattr(collector, 'exchange', '') == 'kis':
                        kis_symbols.update(getattr(collector, 'available_symbols', []))
                for key in self.latest_prices.keys():
                    if key.startswith('kis:'):
                        kis_symbols.add(key.split(':')[1])

                for s_code in kis_symbols:
                    key = f"kis:{s_code}"
                    latest = self.get_latest_price('kis', s_code)
                    
                    # 0으로 마비된 KIS 실시간 캐시 긴급 Warm-Up 복구 (DB 역조회)
                    if not latest or latest.get('trade_price', 0) == 0:
                        db_price = 0.0
                        db_high = 0.0
                        db_low = 0.0
                        db_volume = 0.0
                        db_change_rate = 0.0
                        
                        try:
                            async with get_db_conn() as db:
                                # 1. trades에서 최근 체결가 조회
                                async with db.execute(
                                    "SELECT trade_price FROM trades WHERE exchange = 'kis' AND symbol = ? ORDER BY trade_timestamp DESC LIMIT 1",
                                    (s_code,)
                                ) as cursor:
                                    row = await cursor.fetchone()
                                    if row:
                                        db_price = row[0]
                                
                                # 2. candles(1분봉)에서 오늘 혹은 마지막 캔들 지표 획득
                                async with db.execute(
                                    "SELECT close, high, low, volume FROM candles WHERE exchange = 'kis' AND symbol = ? AND interval = 60 ORDER BY timestamp DESC LIMIT 1",
                                    (s_code,)
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
                                    (s_code, s_code)
                                ) as cursor:
                                    row = await cursor.fetchone()
                                    if row and row[0] > 0:
                                        db_change_rate = (db_price - row[0]) / row[0]
                        except Exception as e:
                            logger.warning(f"[KIS] Database warm-up failed for {s_code}: {e}")

                        # 캐시 저장 및 갱신
                        self.latest_prices[key] = {
                            'exchange': 'kis',
                            'market': s_code,
                            'trade_price': db_price,
                            'signed_change_rate': db_change_rate,
                            'timestamp': int(time.time() * 1000),
                            'high_price': db_high or db_price,
                            'low_price': db_low or db_price,
                            'acc_trade_price_24h': db_volume * db_price
                        }
                        latest = self.latest_prices[key]

                    results.append({
                        "exchange": "kis",
                        "market": s_code,
                        "korean_name": stock_mapper.get_name('kis', s_code),
                        "trade_price": latest.get('trade_price', 0),
                        "signed_change_rate": latest.get('signed_change_rate', 0),
                        "acc_trade_price_24h": latest.get('acc_trade_price_24h', 0),
                        "high_price": latest.get('high_price', 0),
                        "low_price": latest.get('low_price', 0)
                    })

            logger.debug(f"get_all_market_data: Total {len(results)} items (Bithumb: {len(bithumb_symbols)}, KIS: {len(kis_symbols)})")
            return sorted(results, key=lambda x: x['acc_trade_price_24h'], reverse=True)
        except Exception as e:
            logger.error(f"Error in get_all_market_data: {e}")
            return results
