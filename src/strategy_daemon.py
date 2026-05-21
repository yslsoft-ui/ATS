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
from src.engine.utils.telemetry import get_logger

# 각 거래소 수집기가 Registry에 등록되도록 import 수행 (종목 조회용)
import src.engine.collector
import src.engine.collector_kis
import src.engine.collector_bithumb

logger = get_logger("strategy_daemon")

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
    logger.info("=========================================")
    logger.info("실시간 전략 엔진 데몬(Strategy Engine Daemon) 기동 시작")
    logger.info("=========================================")

    # 1. 설정 로드
    config_path = "config/settings.yaml"
    config_manager = ConfigManager(config_path)
    db_path = config_manager.get('system.db_path', 'data/backtest.db')
    strategies_dir = config_manager.get('system.strategies_dir', 'src/engine/strategies')

    # 2. SQLite 스키마 초기화 확인
    await init_db(db_path)

    # 3. 동적 전략 클래스 로드 및 바인딩
    load_dynamic_strategies(strategies_dir)

    # 4. 포트폴리오 관리자 기동 및 로드
    portfolio_manager = PortfolioManager(db_path=db_path)
    await portfolio_manager.load_from_db()

    # 5. ZeroMQ Publisher 기동 (주문 신호 및 상태 알림 발행용)
    signal_publisher = EventBusPublisher("signal_data")

    # 6. 주문 실행 파이프라인(ExecutionPipeline) 구축 및 ZMQ 연동
    execution_pipeline = ExecutionPipeline(portfolio_manager)
    
    async def zmq_broadcast_callback(alert_data: dict):
        """매매 체결/보류 상태 알림 발생 시 ZeroMQ로 즉시 퍼블리시합니다."""
        await signal_publisher.publish("signal_data", alert_data)
        
    execution_pipeline.set_broadcast_callback(zmq_broadcast_callback)

    # 7. 거래소별 종목 목록 로드 및 TradeEngine 인스턴스 생성
    trade_engines: Dict[str, TradeEngine] = {}
    
    exchanges_config = config_manager.get('exchanges', {})
    for exchange_id, exch_config in exchanges_config.items():
        if not exch_config.get('enabled', True):
            continue
            
        # 종목 로드 (config 고정값 또는 API 동적 조회)
        symbols = await fetch_exchange_symbols(exchange_id, config_manager.config)
        
        # 활성화된 전략 목록 및 파라미터 파싱
        strategy_configs = config_manager.get('strategies', {})
        enabled_strategies = []
        for s_id, s_conf in strategy_configs.items():
            if s_conf.get('enabled', False):
                params = s_conf.get('params', {}).copy()
                overrides = s_conf.get('overrides', {}).get(exchange_id, {}).get('params', {})
                params.update(overrides)
                enabled_strategies.append((s_id, params))

        logger.info(f"[Strategy Daemon] {exchange_id} 전략 기동 대상 종목 수: {len(symbols)}")
        
        async def on_strategy_status(status_data: dict):
            """전략 상태 Audit 로그 발생 시 ZeroMQ로 즉시 퍼블리시합니다."""
            await signal_publisher.publish("signal_data", status_data)

        # 각 종목별로 독립된 TradeEngine 세팅
        for symbol in symbols:
            instances = []
            for s_id, s_params in enabled_strategies:
                strat = StrategyRegistry.create_strategy(s_id, s_params)
                if strat:
                    instances.append(strat)
            
            key = f"{exchange_id}:{symbol}"
            engine = TradeEngine(
                exchange=exchange_id,
                symbol=symbol,
                strategies=instances,
                on_status_callback=on_strategy_status
            )
            trade_engines[key] = engine

    # 8. 백그라운드 워밍업 실행
    async def run_warmup():
        logger.info(f"[Strategy Daemon] {len(trade_engines)}개 종목에 대한 전략 엔진 백그라운드 워밍업 개시...")
        for key, engine in trade_engines.items():
            try:
                await engine.warm_up(db_path)
            except Exception as e:
                logger.error(f"[Strategy Daemon] {key} 워밍업 실패: {e}")
            # 루프 제어권 잠시 넘김
            await asyncio.sleep(0.01)
        logger.info("[Strategy Daemon] 모든 종목 전략 엔진 워밍업 완료")

    asyncio.create_task(run_warmup())

    # 9. ZeroMQ Subscriber 기동 (market_data 토픽 구독)
    market_subscriber = EventBusSubscriber("market_data")

    # 10. 종료 처리용 시그널 핸들링
    stop_event = asyncio.Event()

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
                # 0.1초 타임아웃을 주어 루프가 종료 플래그를 정기적으로 체크할 수 있게 함
                topic, data = await market_subscriber.receive()
                if not topic:
                    # 빈 메시지 무시
                    continue
                
                # 오직 tick 데이터만 수신하여 처리 (캔들은 수집 데몬 발행용이므로 전략에서는 자체 candle_gen 활용)
                if data.get('type') == 'tick':
                    exchange = data.get('exchange')
                    symbol = data.get('code')
                    key = f"{exchange}:{symbol}"
                    
                    if key in trade_engines:
                        engine = trade_engines[key]
                        # 틱 데이터 포맷을 TradeEngine의 process_tick 명세와 맞춤
                        tick_payload = {
                            'trade_price': data['trade_price'],
                            'trade_volume': data['trade_volume'],
                            'ask_bid': data['ask_bid'],
                            'trade_timestamp': data['trade_timestamp']
                        }
                        
                        # 전략 평가 실행
                        signals, _ = await engine.process_tick(tick_payload, portfolio_manager)
                        
                        # 신호 발생 시 주문 실행 파이프라인 가동
                        for sig in signals:
                            logger.info(f"[Strategy Daemon] 전략 신호 감지: {sig.symbol} -> {sig.action}")
                            # 주문 체결 직전에 포트폴리오 최신 잔고를 DB로부터 로드 (수동 개입 반영)
                            await portfolio_manager.load_from_db()
                            # execution_pipeline 내부에서 DB 쓰기 및 ZMQ 알림 발행까지 원스톱 처리됨
                            await execution_pipeline.process_signal(sig, data['trade_price'])
                            
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[Strategy Daemon] 구독 루프 내 에러 발생: {e}")
                await asyncio.sleep(0.1)

    # 구독 루프를 태스크로 기동
    subscribe_task = asyncio.create_task(subscribe_loop())

    # 종료 이벤트 대기
    await stop_event.wait()

    # 12. 정리 절차 (Graceful Shutdown)
    logger.info("[Strategy Daemon] 실시간 구독 루프 취소 중...")
    subscribe_task.cancel()
    try:
        await subscribe_task
    except asyncio.CancelledError:
        pass

    logger.info("[Strategy Daemon] 포트폴리오 최신 상태 DB 영속화 중...")
    for pid in portfolio_manager.portfolios:
        try:
            await portfolio_manager.save_to_db(pid)
        except Exception as e:
            logger.error(f"[Strategy Daemon] 포트폴리오 {pid} 저장 실패: {e}")

    logger.info("[Strategy Daemon] ZeroMQ IPC 소켓 정리 중...")
    market_subscriber.close()
    signal_publisher.close()

    logger.info("=========================================")
    logger.info("[Strategy Daemon] 실시간 전략 엔진 데몬 안전 종료 완료")
    logger.info("=========================================")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
