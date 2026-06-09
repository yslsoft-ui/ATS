import asyncio
import time
import json
import aiohttp
from typing import Dict, List, Any, Optional
from src.engine.daemon_supervisor import DaemonService, EventBus, EventBusSubscriberInterface
from src.config.manager import ConfigManager
from src.engine.portfolio import PortfolioManager
from src.engine.pipeline import ExecutionPipeline
from src.engine.girs_scorer import GIRSScorer, MockONNXModel
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
        
        self.girs_scorer: Optional[GIRSScorer] = None
        self.current_model_version: Optional[str] = None

        self.universe_status: Dict[str, str] = {}  # symbol -> status (WATCHED, CANDIDATE)
        self.symbol_last_candidate_time: Dict[str, float] = {}  # symbol -> timestamp
        self.daily_proposal_count = 0
        self.last_proposal_reset_date = ""
        
        # 차단 및 요약 통계 관련 필드
        self.cooldown_blocked_count = 0
        self.quota_blocked_count = 0
        self.limit_blocked_count = 0
        self.promotion_count = 0
        self.demotion_count = 0
        self.last_universe_summary_time = time.time()

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
                    # [V1] DB에 저장된 활성 버전 파라미터가 있는지 조회
                    version_info = await self.portfolio_manager.repository.get_strategy_version(s_id)
                    if version_info and version_info.get("current_params"):
                        logger.info(f"[StrategyService] DB에서 전략 {s_id}의 최신 파라미터 복원 적용 (버전: {version_info['current_version_id']})")
                        params = version_info["current_params"]
                    else:
                        params = s_params
                        # 최초 기동이므로 DB에 버전 1로 초기 등록
                        await self.portfolio_manager.repository.save_strategy_version(
                            strategy_id=s_id,
                            version_id=1,
                            params=params,
                            applied_at=int(time.time() * 1000)
                        )
                        await self.portfolio_manager.repository.insert_strategy_parameter_history(
                            strategy_id=s_id,
                            version_id=1,
                            parent_version_id=None,
                            old_params=None,
                            new_params=json.dumps(params),
                            proposal_id=None,
                            is_current=1,
                            changed_by='AUTO',
                            change_reason='STARTUP_RESTORE'
                        )
                        logger.info(f"[StrategyService] 전략 {s_id} 최초 기동 파라미터를 버전 1로 등록 완료")

                    # [V1] 기동 시점 STARTUP 스냅샷 기록
                    latest_version = version_info['current_version_id'] if version_info else 1
                    await self.record_performance_snapshot(
                        strategy_id=s_id,
                        version_id=latest_version,
                        snapshot_type='STARTUP',
                        params=params
                    )

                    strat = StrategyRegistry.create_strategy(s_id, params)
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

        # GIRSScorer 싱글톤 인스턴스 초기 생성
        onnx_path = self.config_manager.get("system.onnx_model_path", None)
        model_ver = self.config_manager.get("system.model_version", "mock_v1")
        self.girs_scorer = GIRSScorer(
            model=MockONNXModel(model_version=model_ver),
            onnx_model_path=onnx_path
        )
        self.current_model_version = model_ver

        # 5. 수신 리스너 기동
        self.market_sub = await self.event_bus.subscribe("market_data")
        self.signal_sub = await self.event_bus.subscribe("signal_data")
        
        self._tasks.append(asyncio.create_task(self._market_data_loop()))
        self._tasks.append(asyncio.create_task(self._signal_data_loop()))
        self._tasks.append(asyncio.create_task(self._periodic_performance_snapshot_loop()))
        self._tasks.append(asyncio.create_task(self._periodic_proposal_generation_loop()))
        self._tasks.append(asyncio.create_task(self._periodic_proposal_evaluation_loop()))
        self._tasks.append(asyncio.create_task(self._girs_shadow_metrics_collector_loop()))
        self._tasks.append(asyncio.create_task(self._periodic_shadow_report_loop()))
        
        # 초기 감사 로그 청소 비동기 기동 (7일 보존 설정)
        if self.portfolio_manager and self.portfolio_manager.repository:
            async def run_clean():
                try:
                    await self.portfolio_manager.repository.clean_old_system_events(retention_days=7)
                except Exception as e:
                    logger.error(f"[StrategyService] 초기 감사 로그 청소 중 오류: {e}")
            self._tasks.append(asyncio.create_task(run_clean()))

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
        elif data.get('type') == 'apply_params':
            strategy_id = data.get('strategy_id')
            version_id = data.get('version_id')
            params = data.get('params')
            reason = data.get('reason', 'MANUAL_UPDATE')
            
            if strategy_id and params and version_id:
                logger.info(f"[StrategyService] 전략 파라미터 동적 갱신 수신: strategy_id={strategy_id}, version={version_id}, params={params}")
                # 1. 모든 실행 중인 trade_engine의 해당 전략 파라미터를 즉시 갱신
                for key, engine in self.trade_engines.items():
                    engine.update_strategy_params(strategy_id, params)
                
                # 2. 성과 스냅샷 생성 및 기록 (PARAMETER_CHANGE 또는 ROLLBACK)
                snap_type = 'ROLLBACK' if reason == 'ROLLBACK' else 'PARAMETER_CHANGE'
                await self.record_performance_snapshot(
                    strategy_id=strategy_id,
                    version_id=version_id,
                    snapshot_type=snap_type,
                    params=params
                )
                
                # 3. ZMQ로도 전략 갱신 알림 전송 (UI 전파용)
                await self.event_bus.publish("strategy_signal", {
                    "type": "strategy_param_updated",
                    "strategy_id": strategy_id,
                    "version_id": version_id,
                    "params": params,
                    "reason": reason,
                    "timestamp": int(time.time() * 1000)
                })
                return True
            return False
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

    async def record_performance_snapshot(self, strategy_id: str, version_id: int, snapshot_type: str, params: dict):
        if not self.current_portfolio_id:
            return

        import hashlib
        from src.database.connection import get_db_conn
        from src.engine.portfolio import Position
        from src.engine.utils.performance import calculate_performance_metrics

        try:
            # 1. 파라미터 해시 생성
            param_str = json.dumps(params, sort_keys=True)
            param_hash = hashlib.sha256(param_str.encode('utf-8')).hexdigest()
            
            # 2. 포트폴리오 로드
            await self.portfolio_manager.load_from_db(exclude_types=['simulationR', 'simulation_ended'])
            portfolio = self.portfolio_manager.portfolios.get(self.current_portfolio_id)
            if not portfolio:
                return
                
            # 3. 이 전략이 체결한 거래 내역 조회
            trades = []
            async with get_db_conn(self.db_path) as db:
                async with db.execute(
                    "SELECT * FROM orders_history WHERE portfolio_id = ? AND strategy_id = ? ORDER BY timestamp ASC",
                    (self.current_portfolio_id, strategy_id)
                ) as cursor:
                    rows = await cursor.fetchall()
                    trades = [dict(r) for r in rows]
                    
            # 4. 최신 가격 데이터 획득
            current_prices = {}
            async with get_db_conn(self.db_path) as db:
                for t in trades:
                    sym = t['symbol']
                    if sym not in current_prices:
                        async with db.execute(
                            "SELECT close FROM candles WHERE symbol = ? ORDER BY timestamp DESC LIMIT 1",
                            (sym,)
                        ) as cur:
                            row = await cur.fetchone()
                            if row:
                                current_prices[sym] = row[0]
                                
            # 5. 가상 잔고 및 포지션 복구
            temp_positions = {}
            temp_cash = 10000000.0  # 가상 시작 자금 (1천만원)
            for tx in trades:
                ex = tx.get('exchange', '').lower()
                sym = tx.get('symbol', '')
                side = tx.get('side', '')
                price = tx.get('price', 0.0)
                qty = tx.get('quantity', 0.0)
                fee = tx.get('fee', 0.0)
                
                pos_key = (ex, sym)
                if pos_key not in temp_positions:
                    temp_positions[pos_key] = Position(exchange=ex, symbol=sym, quantity=0.0, avg_price=0.0)
                    
                pos = temp_positions[pos_key]
                if side == 'BUY':
                    total_cost = (pos.avg_price * pos.quantity) + (price * qty)
                    pos.quantity += qty
                    if pos.quantity > 0:
                        pos.avg_price = total_cost / pos.quantity
                    temp_cash -= (price * qty) + fee
                else:
                    pos.quantity -= qty
                    temp_cash += (price * qty) - fee
                    if pos.quantity <= 0:
                        pos.quantity = 0.0
                        pos.avg_price = 0.0
            
            # 6. 성과 지표 계산
            metrics = calculate_performance_metrics(
                history=trades,
                initial_cash=10000000.0,
                current_cash=temp_cash,
                positions=temp_positions,
                current_prices=current_prices
            )
            
            # 7. 스냅샷 DB 저장
            snapshot_data = {
                "strategy_id": strategy_id,
                "version_id": version_id,
                "parameter_hash": param_hash,
                "snapshot_type": snapshot_type,
                "timestamp": int(time.time() * 1000),
                "roi": metrics["roi"],
                "mdd": metrics["mdd"],
                "profit_factor": metrics["profit_factor"],
                "win_rate": metrics["win_rate"],
                "trade_count": metrics["trade_count"]
            }
            await self.portfolio_manager.repository.insert_strategy_performance_snapshot(snapshot_data)
            logger.info(f"[StrategyService] 성과 스냅샷 기록 성공: strategy_id={strategy_id}, version={version_id}, type={snapshot_type}, ROI={metrics['roi']}%")
        except Exception as e:
            logger.error(f"[StrategyService] 성능 스냅샷 기록 중 예외 발생: {e}")

    async def _periodic_performance_snapshot_loop(self):
        """1시간마다 활성 전략들의 실시간 성과 스냅샷(PERIODIC)을 기록합니다."""
        logger.info("[StrategyService] 주기적 성능 스냅샷 기록 루프 기동")
        try:
            # 첫 기동 후 10분 뒤에 첫 기록 수행
            await asyncio.sleep(600) 
            while True:
                if self.current_portfolio_id:
                    active_p = self.portfolio_manager.get_active_simulation_portfolio()
                    if active_p and active_p.strategy_info:
                        try:
                            meta = json.loads(active_p.strategy_info)
                            strategies_config = meta.get("applied_strategies", {})
                            for s_id, s_conf in strategies_config.items():
                                if s_conf.get("enabled", False):
                                    version_info = await self.portfolio_manager.repository.get_strategy_version(s_id)
                                    if version_info:
                                        v_id = version_info["current_version_id"]
                                        params = version_info["current_params"]
                                        await self.record_performance_snapshot(
                                            strategy_id=s_id,
                                            version_id=v_id,
                                            snapshot_type='PERIODIC',
                                            params=params
                                        )
                        except Exception as e:
                            logger.error(f"[StrategyService] 주기적 성능 스냅샷 연산 중 예외: {e}")
                # 1시간 주기
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            pass

    async def _periodic_proposal_generation_loop(self):
        """AI 자동 제안 스케줄러 루프 (enable_auto_proposal 스위치 오프로 기본 비활성화)"""
        logger.info("[StrategyService] AI 자동 제안 스케줄러 루프 기동")
        try:
            # 첫 기동 후 15분 뒤에 첫 검사 시작 (그 뒤 24시간 간격)
            await asyncio.sleep(900)
            while True:
                enable_auto = self.config_manager.get("system.enable_auto_proposal", False)
                if not enable_auto:
                    logger.info("[StrategyService] system.enable_auto_proposal 스위치가 비활성화(False) 상태입니다. AI 제안 생성을 스킵합니다.")
                else:
                    if self.current_portfolio_id:
                        active_p = self.portfolio_manager.get_active_simulation_portfolio()
                        if active_p and active_p.strategy_info:
                            try:
                                # 1. 가설 분석기 초기화
                                from src.engine.analyzer import StrategyHypothesisAnalyzer
                                from src.engine.shadow_backtest import ShadowBacktestEngine
                                
                                logger.info("[StrategyService] 활성 전략에 대한 AI 실패 분석 및 Shadow Backtest 구동 개시")
                                analyzer = StrategyHypothesisAnalyzer(db_path=self.db_path)
                                backtester = ShadowBacktestEngine(db_path=self.db_path)
                                
                                # 2. 적용중인 활성 전략들 실패 통계 분석
                                meta = json.loads(active_p.strategy_info)
                                strategies_config = meta.get("applied_strategies", {})
                                for s_id, s_conf in strategies_config.items():
                                    if s_conf.get("enabled", False):
                                        candidates = await analyzer.analyze_failures(self.current_portfolio_id, s_id)
                                        if candidates:
                                            # 백테스트 및 적격 조건 필터 후 제안 자동 등록
                                            inserted_ids = await backtester.run_shadow_backtest(candidates)
                                            if inserted_ids:
                                                for pid in inserted_ids:
                                                    await self.event_bus.publish("strategy_signal", {
                                                        "type": "proposal_created",
                                                        "proposal_id": pid
                                                    })
                            except Exception as e:
                                logger.error(f"[StrategyService] AI 자동 제안 생성 중 예외 발생: {e}")
                # 24시간 주기
                await asyncio.sleep(86400)
        except asyncio.CancelledError:
            pass

    async def _periodic_proposal_evaluation_loop(self):
        """실전 적용된 제안의 7일 사후 성과(ROI 및 거래량)를 분석해 예측값과의 괴리율을 기록합니다."""
        logger.info("[StrategyService] 제안 사후 평가 스케줄러 루프 기동")
        from src.database.connection import get_db_conn
        from src.engine.utils.performance import calculate_performance_metrics
        from src.engine.portfolio import Position
        try:
            # 첫 기동 후 20분 뒤에 첫 평가 검사 시작 (그 뒤 24시간 간격)
            await asyncio.sleep(1200)
            while True:
                try:
                    # 1. 사후 평가 대상 제안(적용 7일 경과 및 outcome = RUNNING) 조회
                    eval_targets = await self.portfolio_manager.repository.get_unevaluated_applied_proposals()
                    
                    for prop in eval_targets:
                        prop_id = prop["id"]
                        strategy_id = prop["strategy_id"]
                        portfolio_id = prop["portfolio_id"]
                        applied_at_ms = prop["applied_at"]
                        
                        # 적용 시점부터 7일간의 범위
                        seven_days_ms = 7 * 24 * 3600 * 1000
                        applied_ts = int(applied_at_ms / 1000)
                        end_ts = applied_ts + (7 * 24 * 3600)
                        
                        # 2. 해당 기간동안 이 전략이 실제 체결한 거래 내역 조회
                        trades = []
                        async with get_db_conn(self.db_path) as db:
                            async with db.execute(
                                "SELECT * FROM orders_history "
                                "WHERE portfolio_id = ? AND strategy_id = ? AND timestamp BETWEEN ? AND ? "
                                "ORDER BY timestamp ASC",
                                (portfolio_id, strategy_id, applied_ts, end_ts)
                            ) as cursor:
                                rows = await cursor.fetchall()
                                trades = [dict(r) for r in rows]
                                
                        # 3. 실측 성과 계산을 위한 간이 가상 자산 평가
                        current_prices = {}
                        async with get_db_conn(self.db_path) as db:
                            for t in trades:
                                sym = t['symbol']
                                if sym not in current_prices:
                                    async with db.execute(
                                        "SELECT close FROM candles WHERE symbol = ? AND timestamp <= ? ORDER BY timestamp DESC LIMIT 1",
                                        (sym, end_ts)
                                    ) as cur:
                                        row = await cur.fetchone()
                                        if row:
                                            current_prices[sym] = row[0]
                                            
                        temp_positions = {}
                        temp_cash = 10000000.0
                        for tx in trades:
                            ex = tx.get('exchange', '').lower()
                            sym = tx.get('symbol', '')
                            side = tx.get('side', '')
                            price = tx.get('price', 0.0)
                            qty = tx.get('quantity', 0.0)
                            fee = tx.get('fee', 0.0)
                            
                            pos_key = (ex, sym)
                            if pos_key not in temp_positions:
                                temp_positions[pos_key] = Position(exchange=ex, symbol=sym, quantity=0.0, avg_price=0.0)
                                
                            pos = temp_positions[pos_key]
                            if side == 'BUY':
                                total_cost = (pos.avg_price * pos.quantity) + (price * qty)
                                pos.quantity += qty
                                if pos.quantity > 0:
                                    pos.avg_price = total_cost / pos.quantity
                                temp_cash -= (price * qty) + fee
                            else:
                                pos.quantity -= qty
                                temp_cash += (price * qty) - fee
                                if pos.quantity <= 0:
                                    pos.quantity = 0.0
                                    pos.avg_price = 0.0
                                    
                        metrics = calculate_performance_metrics(
                            history=trades,
                            initial_cash=10000000.0,
                            current_cash=temp_cash,
                            positions=temp_positions,
                            current_prices=current_prices
                        )
                        
                        actual_roi = metrics["roi"]
                        actual_trades = metrics["trade_count"]
                        
                        # 4. 제안 당시 예측값 획득
                        predicted_roi = prop["metrics"].get("roi_7d", 0.0)
                        predicted_trades = prop["metrics"].get("trade_count_7d", 0)
                        
                        # 5. 괴리율 계산
                        roi_div = round(actual_roi - predicted_roi, 2)
                        trade_div = actual_trades - predicted_trades
                        
                        eval_data = {
                            "proposal_id": prop_id,
                            "predicted_roi_7d": predicted_roi,
                            "actual_roi_7d": actual_roi,
                            "roi_divergence": roi_div,
                            "predicted_trade_count_7d": predicted_trades,
                            "actual_trade_count_7d": actual_trades,
                            "trade_count_divergence": trade_div
                        }
                        
                        # 6. DB 적재 및 제안 마감(COMPLETED) 처리
                        await self.portfolio_manager.repository.insert_proposal_evaluation(eval_data)
                        await self.portfolio_manager.repository.update_strategy_proposal_status(
                            proposal_id=prop_id,
                            status="APPLIED",
                            outcome="COMPLETED"
                        )
                        
                        logger.info(
                            f"[StrategyService] 제안 #{prop_id} 사후 평가 완료 및 결과 적재 완료. "
                            f"(예상 ROI: {predicted_roi}%, 실제 ROI: {actual_roi}%, 괴리율: {roi_div}%)"
                        )
                except Exception as e:
                    logger.error(f"[StrategyService] 제안 사후 평가 연산 중 예외 발생: {e}")
                # 24시간 주기
                await asyncio.sleep(86400)
        except asyncio.CancelledError:
            pass

    def reload_girs_scorer_if_needed(self):
        """설정에서 모델 버전이 변경되었는지 감지하고 GIRSScorer를 리로드합니다."""
        model_ver = self.config_manager.get("system.model_version", "mock_v1")
        if self.current_model_version != model_ver:
            logger.info(f"[StrategyService] 모델 버전 변경 감지: {self.current_model_version} -> {model_ver}. GIRSScorer를 리로드합니다.")
            onnx_path = self.config_manager.get("system.onnx_model_path", None)
            self.girs_scorer = GIRSScorer(
                model=MockONNXModel(model_version=model_ver),
                onnx_model_path=onnx_path
            )
            self.current_model_version = model_ver

    async def _girs_shadow_metrics_collector_loop(self):
        """30초 주기로 GIRS 섀도 메트릭 및 추적 필드들을 수집하여 DB에 적재하고, 유니버스 FSM 상태를 제어합니다."""
        logger.info("[StrategyService] GIRS Shadow Metrics & Universe Control 루프 기동")
        from src.database.connection import get_db_conn
        from src.engine.girs_types import FeatureSnapshot
        from datetime import datetime
        
        try:
            while True:
                # GIRSScorer 싱글톤 리로드 검사
                self.reload_girs_scorer_if_needed()
                
                if self.girs_scorer:
                    # 1. PENDING 제안 목록 조회
                    pending_proposals = []
                    try:
                        async with get_db_conn(self.db_path) as db:
                            async with db.execute("SELECT * FROM strategy_proposals WHERE status = 'PENDING'") as cursor:
                                rows = await cursor.fetchall()
                                pending_proposals = [dict(r) for r in rows]
                    except Exception as e:
                        logger.error(f"[StrategyService] PENDING 제안 조회 실패: {e}")
                    
                    if pending_proposals:
                        active_p = self.portfolio_manager.get_active_simulation_portfolio()
                        sim_session_id = active_p.id if active_p else None
                        
                        op_mode = self.config_manager.get("system.operation_mode", "shadow")
                        girs_shadow_mode = self.config_manager.get("system.girs_shadow_mode", True)
                        model_ver = self.config_manager.get("system.model_version", "mock_v1")
                        scaler_ver = self.config_manager.get("system.scaler_version", "mock_v1")
                        
                        blocked_reason = "SHADOW_MODE_ACTIVE" if girs_shadow_mode else None
                        
                        # GIRS 가드 제어 설정 로드
                        exchange_quota = self.config_manager.get("system.exchange_quota", {"upbit": 20, "kis": 20})
                        symbol_cooldown_seconds = self.config_manager.get("system.symbol_cooldown_seconds", 3600)
                        daily_proposal_limit = self.config_manager.get("system.daily_proposal_limit", 100)

                        # 일일 제안 한도 리셋 체크
                        today_date = datetime.now().strftime("%Y-%m-%d")
                        if self.last_proposal_reset_date != today_date:
                            self.daily_proposal_count = 0
                            self.last_proposal_reset_date = today_date

                        passed_symbols_dict = {}

                        try:
                            async with get_db_conn(self.db_path) as db:
                                for prop in pending_proposals:
                                    proposal_id_str = str(prop["id"])
                                    strategy_id = prop["strategy_id"]
                                    
                                    # 2. promotion_event_log 에서 feature_snapshot 조회
                                    async with db.execute(
                                        "SELECT feature_snapshot, model_version, scaler_version FROM promotion_event_log "
                                        "WHERE proposal_id = ? ORDER BY global_sequence_no DESC LIMIT 1",
                                        (proposal_id_str,)
                                    ) as cur:
                                        log_row = await cur.fetchone()
                                    
                                    snapshot = None
                                    if log_row and log_row["feature_snapshot"]:
                                        try:
                                            feat_dict = json.loads(log_row["feature_snapshot"])
                                            snapshot = FeatureSnapshot(
                                                price_features=feat_dict.get("price_features", {}),
                                                liquidity_features=feat_dict.get("liquidity_features", {}),
                                                regime_features=feat_dict.get("regime_features", {}),
                                                schema_version=feat_dict.get("schema_version", "1.0"),
                                                feature_hash=feat_dict.get("feature_hash", ""),
                                                generated_at=feat_dict.get("generated_at", time.time()),
                                                exchange=feat_dict.get("exchange", "upbit"),
                                                symbol=feat_dict.get("symbol", "BTC"),
                                                market_type=feat_dict.get("market_type", "crypto")
                                            )
                                        except Exception as e:
                                            logger.error(f"[StrategyService] FeatureSnapshot 파싱 실패: {e}")
                                    
                                    if not snapshot:
                                        snapshot = FeatureSnapshot(
                                            price_features={"close": 50000.0, "returns": 0.0, "volatility": 0.1},
                                            liquidity_features={"spread": 0.001, "volume": 1000.0, "depth": 1000.0},
                                            regime_features={"regime_index": 1.0},
                                            exchange="upbit",
                                            symbol="BTC",
                                            market_type="crypto"
                                        )
                                    
                                    # 3. GIRSScorer 계산 수행
                                    model_risk_score = self.girs_scorer.model.predict(snapshot)
                                    
                                    volatility = snapshot.price_features.get("volatility", 0.1)
                                    spread = snapshot.liquidity_features.get("spread", 0.001)
                                    volume = snapshot.liquidity_features.get("volume", 1000.0)
                                    depth = snapshot.liquidity_features.get("depth", 1000.0)
                                    regime_risk = snapshot.regime_features.get("regime_index", 1.0)
                                    
                                    limits = {
                                        "max_spread": 0.05,
                                        "max_volume": 1000000.0,
                                        "max_depth": 1000000.0,
                                        "max_volatility": 1.0,
                                        "max_drawdown": 0.5
                                    }
                                    
                                    fallback_risk_score = self.girs_scorer.calculate_fallback_risk(
                                        volatility=volatility,
                                        drawdown=0.0,
                                        regime_risk=regime_risk,
                                        spread=spread,
                                        volume=volume,
                                        depth=depth,
                                        limits=limits
                                    )
                                    
                                    rank_stab = self.girs_scorer.calculate_rank_stability(proposal_id_str, current_confirmed_rank=1, N=10)
                                    market_stab = self.girs_scorer.calculate_market_stability(proposal_id_str, volatility)
                                    system_stab = self.girs_scorer.calculate_system_stability(0.01)
                                    
                                    stability_score = self.girs_scorer.calculate_stability_score(rank_stab, market_stab, system_stab)
                                    
                                    girs_p, fallback_p, final_promotion_score, meta_score = self.girs_scorer.calculate_final_score(
                                        model_risk_score=model_risk_score,
                                        fallback_risk_score=fallback_risk_score,
                                        stability_score=stability_score,
                                        snapshot=snapshot
                                    )
                                    
                                    shadow_risk_score = meta_score.get("shadow_risk_score")
                                    
                                    # Tps, idle_time 등 로직 예시
                                    tps = snapshot.tps if hasattr(snapshot, 'tps') else 1.0
                                    idle_time = snapshot.idle_time if hasattr(snapshot, 'idle_time') else 0.0
                                    
                                    # 4. strategy_versions에서 현재 버전 획득
                                    async with db.execute("SELECT current_version_id FROM strategy_versions WHERE strategy_id = ?", (strategy_id,)) as cur:
                                        ver_row = await cur.fetchone()
                                        strategy_version_id = ver_row["current_version_id"] if ver_row else 1
                                    
                                    # 5. DB 적재
                                    metric_data = {
                                        "timestamp": time.time(),
                                        "proposal_id": proposal_id_str,
                                        "strategy_id": strategy_id,
                                        "model_risk_score": model_risk_score,
                                        "fallback_risk_score": fallback_risk_score,
                                        "final_promotion_score": final_promotion_score,
                                        "shadow_risk_score": shadow_risk_score,
                                        "replay_drift": 0.0,
                                        "correction_active": False,
                                        "operation_mode": op_mode,
                                        "model_version": model_ver,
                                        "scaler_version": scaler_ver,
                                        "strategy_version_id": strategy_version_id,
                                        "simulation_session_id": sim_session_id,
                                        "decision_type": "SHADOW",
                                        "blocked_reason": blocked_reason,
                                        "trade_age_ms": snapshot.trade_age_ms,
                                        "orderbook_age_ms": snapshot.orderbook_age_ms,
                                        "indicator_age_ms": snapshot.indicator_age_ms,
                                        "is_fresh": 1 if snapshot.is_fresh else 0,
                                        "stale_reason": snapshot.stale_reason,
                                        "snapshot_version": snapshot.snapshot_version,
                                        "snapshot_hash": snapshot.snapshot_hash,
                                        "feature_vector_hash": snapshot.feature_vector_hash,
                                        "orderbook_available": 1 if snapshot.orderbook_available else 0,
                                        "market_type": snapshot.market_type,
                                        "session_state": snapshot.session_state,
                                        "volatility_regime": snapshot.volatility_regime,
                                        "liquidity_regime": snapshot.liquidity_regime,
                                        "exchange": snapshot.exchange,
                                        "tps": tps,
                                        "trade_count": int(volume),
                                        "volume": volume,
                                        "idle_time": idle_time
                                    }
                                    await self.portfolio_manager.repository.insert_girs_shadow_metric(metric_data)
                                    logger.info(f"[StrategyService] 섀도 메트릭 적재 완료: proposal_id={proposal_id_str}, score={final_promotion_score:.4f}")

                                    # 6. 승격 후보 판별 및 강등 처리
                                    price = snapshot.price_features.get("close", 0.0)
                                    value = volume * price  # 거래대금
                                    
                                    if snapshot.is_fresh and tps >= 0.2 and idle_time < 30.0 and volume > 10.0 and value > 100000.0:
                                        existing = passed_symbols_dict.get(snapshot.symbol)
                                        if not existing or value > existing[0]:
                                            passed_symbols_dict[snapshot.symbol] = (value, snapshot)
                                    else:
                                        current_status = self.universe_status.get(snapshot.symbol, "WATCHED")
                                        if current_status == "CANDIDATE":
                                            self.universe_status[snapshot.symbol] = "WATCHED"
                                            self.demotion_count += 1
                                            msg = f"기준 미달 강등 (fresh={snapshot.is_fresh}, tps={tps:.2f}, idle={idle_time:.1f}s, volume={volume:.1f}, value={value:,.0f}원)"
                                            
                                            await self.portfolio_manager.repository.insert_system_event(
                                                "UNIVERSE_DEMOTION", snapshot.symbol, msg
                                            )
                                            await self.portfolio_manager.repository.upsert_universe_guard_state(
                                                symbol=snapshot.symbol,
                                                status="WATCHED",
                                                blocked_reason=None,
                                                blocked_count=0,
                                                last_blocked_at=None,
                                                last_event_logged_reason=msg
                                            )
                                            logger.info(f"[StrategyService] [Universe] {snapshot.symbol} CANDIDATE -> WATCHED 강등 ({msg})")

                            # 7. 거래소별 Quota & Cooldown 랭킹 기반 승격/유지 관리
                            by_exchange = {}
                            for sym, (val, snap) in passed_symbols_dict.items():
                                ex = snap.exchange.lower()
                                by_exchange.setdefault(ex, []).append((sym, val, snap))

                            for ex, items in by_exchange.items():
                                quota = exchange_quota.get(ex, 20)
                                items.sort(key=lambda x: x[1], reverse=True)
                                
                                candidate_candidates = items[:quota]
                                downgraded_candidates = items[quota:]
                                
                                # 7.1. Quota 밖으로 밀려난 후보들 강등/차단
                                for sym, val, snap in downgraded_candidates:
                                    current_status = self.universe_status.get(sym, "WATCHED")
                                    if current_status == "CANDIDATE":
                                        self.universe_status[sym] = "WATCHED"
                                        self.demotion_count += 1
                                        msg = "Quota 초과 및 순위 밀림 강등"
                                        await self.portfolio_manager.repository.insert_system_event("UNIVERSE_DEMOTION", sym, msg)
                                        await self.portfolio_manager.repository.upsert_universe_guard_state(
                                            symbol=sym,
                                            status="WATCHED",
                                            blocked_reason=None,
                                            blocked_count=0,
                                            last_blocked_at=None,
                                            last_event_logged_reason=msg
                                        )
                                        logger.info(f"[StrategyService] [Universe] {sym} CANDIDATE -> WATCHED 강등 ({msg})")
                                    else:
                                        self.quota_blocked_count += 1
                                        prev_state = await self.portfolio_manager.repository.get_universe_guard_state(sym)
                                        prev_reason = prev_state.get("blocked_reason") if prev_state else None
                                        prev_count = prev_state.get("blocked_count", 0) if prev_state else 0
                                        
                                        current_time_s = time.time()
                                        if prev_reason != "QUOTA":
                                            await self.portfolio_manager.repository.insert_system_event(
                                                "PROMOTION_QUOTA_BLOCKED", sym, "Quota 초과 및 순위 밀림으로 승격 차단"
                                            )
                                            new_count = 1
                                        else:
                                            new_count = prev_count + 1
                                            
                                        await self.portfolio_manager.repository.upsert_universe_guard_state(
                                            symbol=sym,
                                            status="WATCHED",
                                            blocked_reason="QUOTA",
                                            blocked_count=new_count,
                                            last_blocked_at=current_time_s,
                                            last_event_logged_reason="QUOTA"
                                        )

                                # 7.2. Quota 내 후보들 승격/유지
                                for sym, val, snap in candidate_candidates:
                                    current_status = self.universe_status.get(sym, "WATCHED")
                                    
                                    if current_status == "WATCHED":
                                        last_cand_time = self.symbol_last_candidate_time.get(sym, 0.0)
                                        current_time_s = time.time()
                                        
                                        # 7.2.1. 쿨다운 체크
                                        if current_time_s - last_cand_time < symbol_cooldown_seconds:
                                            self.cooldown_blocked_count += 1
                                            prev_state = await self.portfolio_manager.repository.get_universe_guard_state(sym)
                                            prev_reason = prev_state.get("blocked_reason") if prev_state else None
                                            prev_count = prev_state.get("blocked_count", 0) if prev_state else 0
                                            
                                            if prev_reason != "COOLDOWN":
                                                await self.portfolio_manager.repository.insert_system_event(
                                                    "PROMOTION_COOLDOWN_BLOCKED", sym, 
                                                    f"재승격 쿨다운 미경과 (남은시간: {symbol_cooldown_seconds - (current_time_s - last_cand_time):.1f}초)"
                                                )
                                                new_count = 1
                                            else:
                                                new_count = prev_count + 1
                                                
                                            await self.portfolio_manager.repository.upsert_universe_guard_state(
                                                symbol=sym,
                                                status="WATCHED",
                                                blocked_reason="COOLDOWN",
                                                blocked_count=new_count,
                                                last_blocked_at=current_time_s,
                                                last_event_logged_reason="COOLDOWN"
                                            )
                                            
                                        # 7.2.2. 일일 제안 한도 체크
                                        elif self.daily_proposal_count >= daily_proposal_limit:
                                            self.limit_blocked_count += 1
                                            prev_state = await self.portfolio_manager.repository.get_universe_guard_state(sym)
                                            prev_reason = prev_state.get("blocked_reason") if prev_state else None
                                            prev_count = prev_state.get("blocked_count", 0) if prev_state else 0
                                            
                                            if prev_reason != "LIMIT":
                                                await self.portfolio_manager.repository.insert_system_event(
                                                    "PROMOTION_LIMIT_BLOCKED", sym, f"일일 제안 한도({daily_proposal_limit}) 초과로 승격 보류"
                                                )
                                                new_count = 1
                                            else:
                                                new_count = prev_count + 1
                                                
                                            await self.portfolio_manager.repository.upsert_universe_guard_state(
                                                symbol=sym,
                                                status="WATCHED",
                                                blocked_reason="LIMIT",
                                                blocked_count=new_count,
                                                last_blocked_at=current_time_s,
                                                last_event_logged_reason="LIMIT"
                                            )
                                            logger.warning(f"[StrategyService] [Universe] 일일 제안 한도({daily_proposal_limit}) 초과로 {sym} 승격 보류")
                                            
                                        # 7.2.3. 승격 성공
                                        else:
                                            self.universe_status[sym] = "CANDIDATE"
                                            self.symbol_last_candidate_time[sym] = current_time_s
                                            self.daily_proposal_count += 1
                                            self.promotion_count += 1
                                            
                                            msg = f"WATCHED -> CANDIDATE 승격 (거래대금={val:,.0f}원)"
                                            await self.portfolio_manager.repository.insert_system_event("UNIVERSE_PROMOTION", sym, msg)
                                            await self.portfolio_manager.repository.upsert_universe_guard_state(
                                                symbol=sym,
                                                status="CANDIDATE",
                                                blocked_reason=None,
                                                blocked_count=0,
                                                last_blocked_at=None,
                                                last_event_logged_reason=msg
                                            )
                                            logger.info(f"[StrategyService] [Universe] {sym} WATCHED -> CANDIDATE 승격 (거래대금={val:,.0f}원, daily_proposal={self.daily_proposal_count}/{daily_proposal_limit})")
                                    else:
                                        # 이미 CANDIDATE인 경우 상태 유지
                                        pass

                            # 1시간 주기 UNIVERSE_GUARD_SUMMARY 적재 및 카운터 리셋
                            current_time_s = time.time()
                            if current_time_s - self.last_universe_summary_time >= 3600:
                                summary_msg = json.dumps({
                                    "cooldown_blocked_count": self.cooldown_blocked_count,
                                    "quota_blocked_count": self.quota_blocked_count,
                                    "limit_blocked_count": self.limit_blocked_count,
                                    "promotion_count": self.promotion_count,
                                    "demotion_count": self.demotion_count
                                })
                                await self.portfolio_manager.repository.insert_system_event(
                                    "UNIVERSE_GUARD_SUMMARY", "universe_control", summary_msg
                                )
                                logger.info(f"[StrategyService] [Universe] 1시간 주기 요약 적재 완료: {summary_msg}")
                                
                                # 카운터 및 시간 리셋
                                self.cooldown_blocked_count = 0
                                self.quota_blocked_count = 0
                                self.limit_blocked_count = 0
                                self.promotion_count = 0
                                self.demotion_count = 0
                                self.last_universe_summary_time = current_time_s
                                
                                # 비동기 감사 로그 정리
                                try:
                                    await self.portfolio_manager.repository.clean_old_system_events(retention_days=7)
                                except Exception as ex:
                                    logger.error(f"[StrategyService] 주기적 감사 로그 청소 중 오류: {ex}")
                        except Exception as e:
                            logger.error(f"[StrategyService] 섀도 메트릭 DB 및 FSM 처리 에러: {e}")
                
                # 30초 대기
                await asyncio.sleep(30)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"[StrategyService] _girs_shadow_metrics_collector_loop 치명적 에러: {e}")

    async def _periodic_shadow_report_loop(self):
        """24시간 주기로 GIRS Shadow Operation 검증 리포트를 자동 생성합니다."""
        logger.info("[StrategyService] GIRS Shadow Report 생성 루프 기동")
        try:
            # 첫 기동 후 30분 뒤에 첫 리포트 생성 (그 뒤 24시간 간격)
            await asyncio.sleep(1800)
            while True:
                try:
                    from scratch.generate_shadow_report import generate_report
                    generate_report(self.db_path, "logs/girs_shadow_report.md")
                    logger.info("[StrategyService] GIRS Shadow Operation 검증 리포트 생성 완료 (logs/girs_shadow_report.md)")
                except Exception as e:
                    logger.error(f"[StrategyService] GIRS 검증 리포트 생성 실패: {e}")
                # 24시간 주기
                await asyncio.sleep(86400)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"[StrategyService] _periodic_shadow_report_loop 치명적 에러: {e}")
