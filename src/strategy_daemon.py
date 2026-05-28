import sys
import os
import asyncio
import signal
import aiohttp
from typing import Dict, List, Any

# 프로젝트 루트 경로 추가
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config.manager import ConfigManager
from src.database.connection import get_db_conn
from src.database.schema import init_db
from src.engine.portfolio import PortfolioManager
from src.engine.pipeline import ExecutionPipeline
from src.engine.trade_engine import TradeEngine
from src.engine.strategy import StrategyRegistry
from src.engine.loader import load_dynamic_strategies
from src.engine.collector_base import CollectorRegistry
from src.ipc.bus import EventBusPublisher, EventBusSubscriber
from src.engine.utils.telemetry import get_logger, setup_logging

# 각 거래소 수집기가 Registry에 등록되도록 import 수행 (종목 조회용)
import src.engine.collector_upbit
import src.engine.collector_kis
import src.engine.collector_bithumb

logger = get_logger("src.strategy_daemon")

async def fetch_exchange_symbols(exchange_id: str, config: Dict[str, Any]) -> List[str]:
    """설정에 정의된 symbols를 가져오거나, 없으면 거래소 API를 통해 전종목 리스트를 받아옵니다."""
    symbols = config.get('exchanges', {}).get(exchange_id, {}).get('symbols', [])
    if symbols:
        return symbols

    # 수집기 인스턴스를 활용해 동적 종목 로드
    collector = CollectorRegistry.create(exchange_id, processing_queue=asyncio.Queue())
    if not collector:
        logger.error(f"[Strategy Daemon] {exchange_id} 수집기 인스턴스 생성 실패")
        return []
    
    async with aiohttp.ClientSession() as session:
        collector.session = session
        try:
            fetched = await collector._fetch_symbols(config)
            logger.info(f"[Strategy Daemon] {exchange_id} API로부터 {len(fetched)}개 종목 조회 성공")
            return fetched
        except Exception as e:
            logger.error(f"[Strategy Daemon] {exchange_id} 종목 동적 로드 중 예외 발생: {e}")
            return []

async def main():
    setup_logging(log_file="ats.log")
    logger.info("=========================================")
    logger.info("실시간 전략 엔진 데몬(Strategy Engine Daemon) 기동 시작")
    logger.info("=========================================")

    # 1. 설정 로드
    config_path = "config/settings.yaml"
    config_manager = ConfigManager(config_path)
    db_path = config_manager.get('system.db_path', 'data/backtest.db')
    
    # 2. SQLite 스키마 초기화 확인
    await init_db(db_path)

    # 3. 동적 전략 클래스 로드 및 바인딩
    strategies_dir = config_manager.get('system.strategies_dir', 'src/engine/strategies')
    load_dynamic_strategies(strategies_dir)

    # 4. 포트폴리오 관리자 기동 및 로드
    portfolio_manager = PortfolioManager(db_path=db_path)
    await portfolio_manager.load_from_db(exclude_types=['simulationR', 'simulation_ended'])

    # 5. ZeroMQ Publisher 기동 (주문 신호 및 상태 알림 발행용)
    signal_publisher = EventBusPublisher("strategy_signal")

    # 6. 주문 실행 파이프라인(ExecutionPipeline) 구축 및 ZMQ 연동
    execution_pipeline = ExecutionPipeline(portfolio_manager)
    
    async def zmq_broadcast_callback(alert_data: dict):
        """매매 체결/보류 상태 알림 발생 시 ZeroMQ로 즉시 퍼블리시합니다."""
        await signal_publisher.publish("strategy_signal", alert_data)
        
    execution_pipeline.set_broadcast_callback(zmq_broadcast_callback)

    # 7. 전략 엔진 핫리로드 핵심 구조 설계
    trade_engines: Dict[str, TradeEngine] = {}
    current_portfolio_id = None
    stop_event = asyncio.Event()

    async def reload_trade_engines(portfolio):
        import json
        new_engines = {}
        if not portfolio:
            logger.info("[Strategy Daemon] 활성화된 실시간 모의투자 세션이 없습니다. 대기 상태로 유지합니다.")
            return new_engines

        logger.info(f"[Strategy Daemon] 모의투자 세션 감지 및 엔진 로드 시작: {portfolio.id} ({portfolio.name})")
        
        enabled_strategies = []
        if portfolio.strategy_info:
            try:
                meta = json.loads(portfolio.strategy_info)
                strategies_config = meta.get("applied_strategies", {})
                for s_id, s_conf in strategies_config.items():
                    if s_conf.get("enabled", False):
                        params = s_conf.get("params", {}).copy()
                        enabled_strategies.append((s_id, params))
                logger.info(f"[Strategy Daemon] 세션 활성 전략 목록: {[s[0] for s in enabled_strategies]}")
            except Exception as e:
                logger.error(f"[Strategy Daemon] 포트폴리오 전략 정보 파싱 에러: {e}")
                
        if not enabled_strategies:
            logger.warning(f"[Strategy Daemon] 세션 {portfolio.id}에 설정된 전략이 없습니다.")
            return new_engines

        exchanges_config = config_manager.get('exchanges', {})
        for exchange_id, exch_config in exchanges_config.items():
            if not exch_config.get('enabled', True):
                continue
                
            symbols = await fetch_exchange_symbols(exchange_id, config_manager.config)
            
            async def on_strategy_status(status_data: dict):
                await signal_publisher.publish("strategy_signal", status_data)

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

        logger.info(f"[Strategy Daemon] {len(new_engines)}개 종목에 대한 전략 엔진 동적 워밍업 개시...")
        for key, engine in new_engines.items():
            try:
                await engine.warm_up(db_path)
            except Exception as e:
                logger.error(f"[Strategy Daemon] {key} 워밍업 실패: {e}")
            await asyncio.sleep(0.002)
        logger.info("[Strategy Daemon] 모든 종목 전략 엔진 워밍업 완료")
        
        return new_engines

    # 첫 기동 시 초기 1회 로드 및 세션 구축
    try:
        active_p = portfolio_manager.get_active_simulation_portfolio()
        active_id = active_p.id if active_p else None
        current_portfolio_id = active_id
        if active_p:
            new_engs = await reload_trade_engines(active_p)
            trade_engines.clear()
            trade_engines.update(new_engs)
    except Exception as e:
        logger.error(f"[Strategy Daemon] 초기 세션 로드 중 예외: {e}")

    # ZMQ 제어 명령 수신 비동기 루프 (3초 Polling 대체)
    async def control_loop():
        nonlocal current_portfolio_id
        logger.info("[Strategy Daemon] ZMQ strategy_control 구독 수신 시작")
        while not stop_event.is_set():
            try:
                topic, data = await strategy_control_subscriber.receive()
                if not topic:
                    continue
                
                if data.get('type') == 'update_portfolio':
                    logger.info(f"[Strategy Daemon] ZMQ 제어 수신: 포트폴리오 업데이트 신호")
                    await portfolio_manager.load_from_db(exclude_types=['simulationR', 'simulation_ended'])
                    active_p = portfolio_manager.get_active_simulation_portfolio()
                    active_id = active_p.id if active_p else None
                    
                    if active_id != current_portfolio_id:
                        logger.info(f"[Strategy Daemon] 세션 변경 감지: {current_portfolio_id} -> {active_id}")
                        current_portfolio_id = active_id
                        
                        trade_engines.clear()
                        if active_p:
                            new_engs = await reload_trade_engines(active_p)
                            trade_engines.update(new_engs)
                        else:
                            logger.info("[Strategy Daemon] 활성 포트폴리오가 존재하지 않아 엔진 대기 모드로 전환합니다.")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[Strategy Daemon] 제어 루프 에러: {e}")
                await asyncio.sleep(0.1)

    control_task = asyncio.create_task(control_loop())

    # 전략 엔진 동작 상태 퍼블리시 루프 (3초 주기 및 기동 즉시 전송)
    async def status_broadcast_loop():
        # 기동 즉시 첫 하트비트 상태를 전송하여 ZMQ 구독 연결 지연으로 인한 유실 방지
        try:
            await signal_publisher.publish("strategy_signal", {
                "type": "strategy_status",
                "is_running": True,
                "active_engines": len(trade_engines),
                "error": None
            })
        except Exception as e:
            logger.error(f"[Strategy Daemon] 초기 상태 퍼블리시 중 에러: {e}")

        while not stop_event.is_set():
            try:
                await signal_publisher.publish("strategy_signal", {
                    "type": "strategy_status",
                    "is_running": True,
                    "active_engines": len(trade_engines),
                    "error": None
                })
            except Exception as e:
                logger.error(f"[Strategy Daemon] 상태 퍼블리시 중 에러: {e}")
            await asyncio.sleep(3.0)

    broadcast_task = asyncio.create_task(status_broadcast_loop())

    # 9. ZeroMQ Subscriber 기동
    market_subscriber = EventBusSubscriber("market_data")
    strategy_control_subscriber = EventBusSubscriber("strategy_control")

    # 10. 종료 처리용 시그널 핸들링
    def handle_shutdown():
        logger.info("[Strategy Daemon] 종료 시그널 감지. 자원 정리 및 안전 종료 절차를 진행합니다...")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, handle_shutdown)
        except NotImplementedError:
            pass

    # 11. 실시간 틱 구독 및 처리 루프 기동
    async def subscribe_loop():
        logger.info("[Strategy Daemon] 실시간 market_data 구독 수신 시작")
        while not stop_event.is_set():
            try:
                topic, data = await market_subscriber.receive()
                if not topic:
                    continue
                
                if data.get('type') == 'tick':
                    exchange = data.get('exchange')
                    symbol = data.get('code')
                    key = f"{exchange}:{symbol}"
                    
                    if key in trade_engines:
                        engine = trade_engines[key]
                        tick_payload = {
                            'trade_price': data['trade_price'],
                            'trade_volume': data['trade_volume'],
                            'ask_bid': data['ask_bid'],
                            'trade_timestamp': data['trade_timestamp']
                        }
                        
                        signals, _ = await engine.process_tick(tick_payload, portfolio_manager)
                        
                        for sig in signals:
                            logger.info(f"[Strategy Daemon] 전략 신호 감지: {sig.symbol} -> {sig.action}")
                            # 주문 체결 직전에 포트폴리오 최신 잔고를 DB로부터 로드 (수동 개입 반영)
                            await portfolio_manager.load_from_db(exclude_types=['simulationR', 'simulation_ended'])
                            await execution_pipeline.process_signal(sig, data['trade_price'])
                            
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[Strategy Daemon] 구독 루프 내 에러 발생: {e}")
                await asyncio.sleep(0.1)

    subscribe_task = asyncio.create_task(subscribe_loop())

    # 종료 이벤트 대기
    await stop_event.wait()

    # 상태 퍼블리시 루프 취소 및 종료 상태(False) ZMQ 퍼블리시
    logger.info("[Strategy Daemon] 상태 퍼블리시 루프 취소 및 종료 상태 전송 중...")
    broadcast_task.cancel()
    try:
        await broadcast_task
    except asyncio.CancelledError:
        pass

    try:
        await signal_publisher.publish("strategy_signal", {
            "type": "strategy_status",
            "is_running": False,
            "active_engines": 0,
            "error": None
        })
    except Exception as e:
        logger.error(f"[Strategy Daemon] 종료 상태 퍼블리시 에러: {e}")

    # 12. 정리 절차 (Graceful Shutdown)
    logger.info("[Strategy Daemon] ZMQ strategy_control 구독 루프 취소 중...")
    control_task.cancel()
    try:
        await control_task
    except asyncio.CancelledError:
        pass

    logger.info("[Strategy Daemon] 실시간 구독 루프 취소 중...")
    subscribe_task.cancel()
    try:
        await subscribe_task
    except asyncio.CancelledError:
        pass

    logger.info("[Strategy Daemon] ZeroMQ IPC 소켓 정리 중...")
    market_subscriber.close()
    strategy_control_subscriber.close()
    signal_publisher.close()

    logger.info("=========================================")
    logger.info("[Strategy Daemon] 실시간 전략 엔진 데몬 안전 종료 완료")
    logger.info("=========================================")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
