import asyncio
import time
import json
import aiohttp
from typing import Dict, List, Any, Optional
from src.engine.daemon_supervisor import DaemonService, EventBus, EventBusSubscriberInterface
from src.config.manager import ConfigManager
from src.engine.portfolio import PortfolioManager
from src.engine.pipeline import ExecutionPipeline
from src.engine.trade_engine import TradeEngine
from src.engine.strategy import StrategyRegistry
from src.engine.loader import load_dynamic_strategies
from src.engine.collector_base import CollectorRegistry
from src.engine.utils.telemetry import get_logger

# 각 거래소 수집기가 Registry에 등록되도록 import 수행
import src.engine.collector_upbit
import src.engine.collector_kis
import src.engine.collector_bithumb

logger = get_logger("strategy_service")

class StrategyService(DaemonService):
    """전략 인스턴스 핫리로드, 포트폴리오 모니터링, 실시간 틱 연산 및 매매 집행 도메인 서비스"""
    def __init__(self, config_manager: ConfigManager, event_bus: EventBus):
        self.config_manager = config_manager
        self.event_bus = event_bus
        
        self.db_path = self.config_manager.get('system.db_path', 'data/backtest.db')
        self.portfolio_manager: Optional[PortfolioManager] = None
        self.execution_pipeline: Optional[ExecutionPipeline] = None
        
        self.trade_engines: Dict[str, TradeEngine] = {}
        self.current_portfolio_id = None
        self._status_counter = 0

        self.market_sub: Optional[EventBusSubscriberInterface] = None
        self.signal_sub: Optional[EventBusSubscriberInterface] = None
        self._tasks: List[asyncio.Task] = []

    async def fetch_exchange_symbols(self, exchange_id: str, config: Dict[str, Any]) -> List[str]:
        symbols = config.get('exchanges', {}).get(exchange_id, {}).get('symbols', [])
        if symbols:
            return symbols

        collector = CollectorRegistry.create(exchange_id, processing_queue=asyncio.Queue())
        if not collector:
            logger.error(f"[StrategyService] {exchange_id} 수집기 인스턴스 생성 실패")
            return []
        
        async with aiohttp.ClientSession() as session:
            collector.session = session
            try:
                fetched = await collector._fetch_symbols(config)
                logger.info(f"[StrategyService] {exchange_id} API로부터 {len(fetched)}개 종목 조회 성공")
                return fetched
            except Exception as e:
                logger.error(f"[StrategyService] {exchange_id} 종목 동적 로드 중 예외 발생: {e}")
                return []

    async def reload_trade_engines(self, portfolio):
        new_engines = {}
        if not portfolio:
            logger.info("[StrategyService] 활성화된 실시간 모의투자 세션이 없습니다. 대기 상태로 유지합니다.")
            return new_engines

        logger.info(f"[StrategyService] 모의투자 세션 감지 및 엔진 로드 시작: {portfolio.id} ({portfolio.name})")
        
        enabled_strategies = []
        if portfolio.strategy_info:
            try:
                meta = json.loads(portfolio.strategy_info)
                strategies_config = meta.get("applied_strategies", {})
                for s_id, s_conf in strategies_config.items():
                    if s_conf.get("enabled", False):
                        params = s_conf.get("params", {}).copy()
                        enabled_strategies.append((s_id, params))
                logger.info(f"[StrategyService] 세션 활성 전략 목록: {[s[0] for s in enabled_strategies]}")
            except Exception as e:
                logger.error(f"[StrategyService] 포트폴리오 전략 정보 파싱 에러: {e}")
                
        if not enabled_strategies:
            logger.warning(f"[StrategyService] 세션 {portfolio.id}에 설정된 전략이 없습니다.")
            return new_engines

        exchanges_config = self.config_manager.get('exchanges', {})
        for exchange_id, exch_config in exchanges_config.items():
            if not exch_config.get('enabled', True):
                continue
                
            symbols = await self.fetch_exchange_symbols(exchange_id, self.config_manager.config)
            
            async def on_strategy_status(status_data: dict):
                await self.event_bus.publish("strategy_signal", status_data)

            for symbol in symbols:
                instances = []
                for s_id, s_params in enabled_strategies:
                    strat = StrategyRegistry.create_strategy(s_id, s_params)
                    if strat:
                        instances.append(strat)
                
                if not instances:
                    continue
                    
                key = f"{exchange_id}:{symbol}"
                engine = TradeEngine(
                    exchange=exchange_id,
                    symbol=symbol,
                    strategies=instances,
                    on_status_callback=on_strategy_status
                )
                new_engines[key] = engine

        logger.info(f"[StrategyService] {len(new_engines)}개 종목에 대한 전략 엔진 동적 워밍업 개시...")
        for key, engine in new_engines.items():
            try:
                await engine.warm_up(self.db_path)
            except Exception as e:
                logger.error(f"[StrategyService] {key} 워밍업 실패: {e}")
            await asyncio.sleep(0.002)
        logger.info("[StrategyService] 모든 종목 전략 엔진 워밍업 완료")
        
        await self.record_strategy_event('STRATEGY_SESSION_LOAD', f"전략 세션 활성화 및 웜업 완료 (세션 ID: {portfolio.id})")
        
        return new_engines

    async def start(self):
        # 1. 동적 전략 클래스 로드
        strategies_dir = self.config_manager.get('system.strategies_dir', 'src/engine/strategies')
        load_dynamic_strategies(strategies_dir)

        # 2. 포트폴리오 매니저 기동
        self.portfolio_manager = PortfolioManager(db_path=self.db_path)
        await self.portfolio_manager.load_from_db(exclude_types=['simulationR', 'simulation_ended'])

        # 3. ExecutionPipeline 연동
        self.execution_pipeline = ExecutionPipeline(self.portfolio_manager)
        
        async def event_broadcast_callback(alert_data: dict):
            await self.event_bus.publish("strategy_signal", alert_data)
            
        self.execution_pipeline.set_broadcast_callback(event_broadcast_callback)
        self.portfolio_manager.broadcast_callback = event_broadcast_callback

        # 4. 초기 세션 활성화 및 엔진 로딩
        try:
            active_p = self.portfolio_manager.get_active_simulation_portfolio()
            self.current_portfolio_id = active_p.id if active_p else None
            if active_p:
                new_engs = await self.reload_trade_engines(active_p)
                self.trade_engines.clear()
                self.trade_engines.update(new_engs)
        except Exception as e:
            logger.error(f"[StrategyService] 초기 세션 로드 예외: {e}")

        # 5. 수신 리스너 기동
        self.market_sub = await self.event_bus.subscribe("market_data")
        self.signal_sub = await self.event_bus.subscribe("signal_data")
        
        self._tasks.append(asyncio.create_task(self._market_data_loop()))
        self._tasks.append(asyncio.create_task(self._signal_data_loop()))

    async def stop(self):
        # 1. 리스너 중단
        for task in self._tasks:
            if not task.done():
                task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
            self._tasks.clear()

        if self.market_sub:
            self.market_sub.close()
        if self.signal_sub:
            self.signal_sub.close()

    async def handle_config_change(self, new_config: dict):
        # 전략 데몬의 설정 파일 실시간 감시 대응은 별도로 기술하지 않음
        pass

    async def handle_control_message(self, topic: str, data: dict) -> bool:
        if data.get('type') == 'update_portfolio':
            logger.info(f"[StrategyService] 포트폴리오 업데이트 제어 신호 수신")
            await self.portfolio_manager.load_from_db(exclude_types=['simulationR', 'simulation_ended'])
            active_p = self.portfolio_manager.get_active_simulation_portfolio()
            active_id = active_p.id if active_p else None
            
            if active_id != self.current_portfolio_id:
                logger.info(f"[StrategyService] 세션 변경 감지: {self.current_portfolio_id} -> {active_id}")
                self.current_portfolio_id = active_id
                
                self.trade_engines.clear()
                if active_p:
                    new_engs = await self.reload_trade_engines(active_p)
                    self.trade_engines.update(new_engs)
                else:
                    logger.info("[StrategyService] 활성 세션이 없어 대기 상태로 진입합니다.")
            return True
        return False

    def get_status_payloads(self) -> List[tuple[str, dict]]:
        payloads = []
        self._status_counter += 1
        if self._status_counter >= 3:
            self._status_counter = 0
            payloads.append(("strategy_signal", {
                "type": "strategy_status",
                "is_running": True,
                "active_engines": len(self.trade_engines),
                "error": None
            }))
        return payloads

    async def _market_data_loop(self):
        """실시간 틱 구독 및 처리 루프"""
        logger.info("[StrategyService] 실시간 market_data 수신 시작")
        try:
            while True:
                topic, data = await self.market_sub.receive()
                if not topic or not data:
                    await asyncio.sleep(0.1)
                    continue

                if data.get('type') == 'tick':
                    exchange = data.get('exchange')
                    symbol = data.get('code')
                    key = f"{exchange}:{symbol}"
                    
                    if key in self.trade_engines:
                        engine = self.trade_engines[key]
                        tick_payload = {
                            'trade_price': data['trade_price'],
                            'trade_volume': data['trade_volume'],
                            'ask_bid': data['ask_bid'],
                            'trade_timestamp': data['trade_timestamp']
                        }
                        
                        signals, _ = await engine.process_tick(tick_payload, self.portfolio_manager)
                        
                        for sig in signals:
                            logger.info(f"[StrategyService] 전략 신호 감지: {sig.symbol} -> {sig.action}")
                            # DB로부터 포트폴리오 정보 동기화 (수동 개입 등)
                            await self.portfolio_manager.load_from_db(exclude_types=['simulationR', 'simulation_ended'])
                            await self.execution_pipeline.process_signal(sig, data['trade_price'])
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"[StrategyService] market_data 수신 루프 예외: {e}")

    async def _signal_data_loop(self):
        """실시간 signal_data(수집기 상태 등) 수신 루프"""
        logger.info("[StrategyService] 실시간 signal_data 수신 시작")
        try:
            while True:
                topic, data = await self.signal_sub.receive()
                if not topic or not data:
                    await asyncio.sleep(0.1)
                    continue

                if data.get('type') == 'collector_status':
                    exchange = data.get('exchange')
                    if exchange and self.portfolio_manager:
                        ex_lower = exchange.lower()
                        prev_status = self.portfolio_manager.collector_statuses.get(ex_lower, {}).get('status')
                        current_status = data.get('status', 'STOPPED')
                        reason = data.get('status_reason')
                        
                        self.portfolio_manager.collector_statuses[ex_lower] = {
                            "status": current_status,
                            "status_reason": reason,
                            "is_running": data.get('is_running', False)
                        }
                        
                        # 거래소 정지 상태 진입 시 미체결 취소 트리거 실행
                        if current_status == 'SUSPENDED' and prev_status != 'SUSPENDED':
                            logger.warning(f"[StrategyService] {exchange} 정지 상태 감지! 미체결 주문 일괄 취소 실행.")
                            await self.portfolio_manager.cancel_all_orders(ex_lower)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"[StrategyService] signal_data 수신 루프 예외: {e}")

    async def record_strategy_event(self, event_type: str, message: str):
        ts = int(time.time() * 1000)
        try:
            if self.portfolio_manager and self.portfolio_manager.repository:
                await self.portfolio_manager.repository.insert_system_event(event_type, "strategy_daemon", message, ts)
        except Exception as e:
            logger.error(f"[StrategyService] 시스템 이벤트 DB 적재 실패: {e}")
        try:
            await self.event_bus.publish("strategy_signal", {
                "type": "system_event",
                "event_type": event_type,
                "target": "strategy_daemon",
                "message": message,
                "timestamp": ts
            })
        except Exception as e:
            logger.error(f"[StrategyService] 이벤트 버스 발행 실패: {e}")
