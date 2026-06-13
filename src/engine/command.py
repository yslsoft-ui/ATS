import uuid
import json
import time
from enum import Enum
from datetime import datetime
from typing import Dict, Any, Callable, Optional
from src.engine.utils.telemetry import get_logger

logger = get_logger(__name__)

class UserCommand(Enum):
    COLLECTOR_START = "collector_start"
    COLLECTOR_STOP = "collector_stop"
    COLLECTOR_RESTART_DAEMON = "collector_restart_daemon"
    STRATEGY_ENABLE = "strategy_enable"
    STRATEGY_DISABLE = "strategy_disable"
    STRATEGY_UPDATE_PARAMS = "strategy_update_params"
    STRATEGY_RESTART_DAEMON = "strategy_restart_daemon"
    PORTFOLIO_START = "portfolio_start"
    PORTFOLIO_END = "portfolio_end"
    PORTFOLIO_PANIC = "portfolio_panic"

class UserCommandDispatcher:
    """
    웹 API의 조작 요청을 받아 일관된 감사 로그(REQUEST/SUCCESS/FAILED)를 남기고,
    해당되는 비즈니스 로직을 매핑된 핸들러를 통해 실행하는 유저 명령 디스패처입니다.
    """
    
    _EVENT_NAME_MAP = {
        UserCommand.COLLECTOR_START: "COLLECTOR_START",
        UserCommand.COLLECTOR_STOP: "COLLECTOR_STOP",
        UserCommand.COLLECTOR_RESTART_DAEMON: "DAEMON_RESTART_SIGNAL",
        UserCommand.STRATEGY_ENABLE: "STRATEGY_ENABLE",
        UserCommand.STRATEGY_DISABLE: "STRATEGY_DISABLE",
        UserCommand.STRATEGY_UPDATE_PARAMS: "STRATEGY_UPDATE_PARAMS",
        UserCommand.STRATEGY_RESTART_DAEMON: "DAEMON_RESTART_SIGNAL",
        UserCommand.PORTFOLIO_START: "STRATEGY_SESSION_START",
        UserCommand.PORTFOLIO_END: "STRATEGY_SESSION_END",
        UserCommand.PORTFOLIO_PANIC: "STRATEGY_SESSION_PANIC",
    }

    def __init__(
        self,
        repository,
        config_manager,
        portfolio_manager,
        control_publisher=None,
        strategy_control_publisher=None
    ):
        self.repository = repository
        self.config_manager = config_manager
        self.portfolio_manager = portfolio_manager
        self.control_publisher = control_publisher
        self.strategy_control_publisher = strategy_control_publisher

        # handlers 매핑 테이블
        self.handlers: Dict[UserCommand, Callable[[str, Dict[str, Any]], Any]] = {
            UserCommand.COLLECTOR_START: self._handle_collector_start,
            UserCommand.COLLECTOR_STOP: self._handle_collector_stop,
            UserCommand.COLLECTOR_RESTART_DAEMON: self._handle_collector_restart_daemon,
            UserCommand.STRATEGY_ENABLE: self._handle_strategy_enable,
            UserCommand.STRATEGY_DISABLE: self._handle_strategy_disable,
            UserCommand.STRATEGY_UPDATE_PARAMS: self._handle_strategy_update_params,
            UserCommand.STRATEGY_RESTART_DAEMON: self._handle_strategy_restart_daemon,
            UserCommand.PORTFOLIO_START: self._handle_portfolio_start,
            UserCommand.PORTFOLIO_END: self._handle_portfolio_end,
            UserCommand.PORTFOLIO_PANIC: self._handle_portfolio_panic,
        }

    def set_publishers(self, control_publisher, strategy_control_publisher):
        """웹서버 기동 시 ZMQ 퍼블리셔 의존성을 주입합니다."""
        self.control_publisher = control_publisher
        self.strategy_control_publisher = strategy_control_publisher

    async def dispatch(self, command: UserCommand, payload: Dict[str, Any]) -> Any:
        """
        단일 명령 디스패치 진입점입니다.
        감사 로그 생명주기(REQUEST -> SUCCESS / FAILED) 및 Fail-Fast 에러 처리를 제어합니다.
        """
        if command not in self.handlers:
            raise ValueError(f"Unknown command: {command}")

        command_id = payload.get("command_id", str(uuid.uuid4()))
        # [NEW] payload에 command_id 주입하여 하위 ZMQ 전달 보장
        payload["command_id"] = command_id
        
        # 1. REQUEST 감사 로그 기록
        await self._log_event(command, "REQUEST", command_id, payload)
        
        try:
            # 2. 매핑된 핸들러 실행
            handler = self.handlers[command]
            result = await handler(command_id, payload)
            
            # 3. SUCCESS 감사 로그 기록
            await self._log_event(command, "SUCCESS", command_id, payload)
            return result
        except Exception as e:
            # 4. FAILED 감사 로그 기록
            await self._log_event(command, "FAILED", command_id, payload, error=str(e))
            raise e

    async def _log_event(self, command: UserCommand, status: str, command_id: str, payload: Dict[str, Any], error: str = None):
        base_event_name = self._EVENT_NAME_MAP.get(command)
        if not base_event_name:
            return
        
        event_type = f"{base_event_name}_{status}"
        
        # target_id 추출 및 포맷팅
        target_id = "system"
        if command in [UserCommand.COLLECTOR_START, UserCommand.COLLECTOR_STOP]:
            target_id = payload.get("exchange", "all")
        elif command in [UserCommand.COLLECTOR_RESTART_DAEMON, UserCommand.STRATEGY_RESTART_DAEMON]:
            target_id = payload.get("target", "daemon")
        elif command in [UserCommand.STRATEGY_ENABLE, UserCommand.STRATEGY_DISABLE, UserCommand.STRATEGY_UPDATE_PARAMS]:
            target_id = payload.get("strategy_id", "all")
        elif command in [UserCommand.PORTFOLIO_START, UserCommand.PORTFOLIO_END, UserCommand.PORTFOLIO_PANIC]:
            target_id = payload.get("portfolio_id", "default")
            
        # 가독성 높은 한국어 설명 메시지 구성
        status_kr = "요청" if status == "REQUEST" else ("성공" if status == "SUCCESS" else "실패")
        cmd_kr = {
            UserCommand.COLLECTOR_START: "수집기 기동",
            UserCommand.COLLECTOR_STOP: "수집기 중단",
            UserCommand.COLLECTOR_RESTART_DAEMON: "수집기 데몬 재기동 신호 송신",
            UserCommand.STRATEGY_ENABLE: "전략 활성화",
            UserCommand.STRATEGY_DISABLE: "전략 비활성화",
            UserCommand.STRATEGY_UPDATE_PARAMS: "전략 설정 갱신",
            UserCommand.STRATEGY_RESTART_DAEMON: "전략 데몬 재기동 신호 송신",
            UserCommand.PORTFOLIO_START: "모의투자 세션 시작",
            UserCommand.PORTFOLIO_END: "모의투자 세션 종료",
            UserCommand.PORTFOLIO_PANIC: "긴급 전량 매도 및 비상 정지"
        }.get(command, command.value)

        msg = f"사용자 요청으로 {cmd_kr} {status_kr} (대상: {target_id.upper()})"
        if error:
            msg += f" - 에러: {error}"
            
        context = {
            "command_id": command_id,
            "command": command.value,
            "payload": payload
        }
        if error:
            context["error"] = error
            
        await self.repository.insert_system_event(
            event_type=event_type,
            target=target_id,
            message=msg,
            context=json.dumps(context)
        )

    # --- 구체적 핸들러 메서드 구현 ---

    async def _handle_collector_start(self, command_id: str, payload: Dict[str, Any]):
        exchange = payload.get("exchange")
        if not exchange:
            raise ValueError("Exchange parameter is missing")
            
        if exchange == "all":
            exchanges_config = self.config_manager.get('exchanges', {})
            for exch in exchanges_config.keys():
                self.config_manager.update(f"exchanges.{exch}.enabled", True)
        else:
            config_key = f"exchanges.{exchange}"
            exch_config = self.config_manager.get(config_key)
            if exch_config is None:
                raise ValueError(f"Configuration for exchange '{exchange}' not found")
            self.config_manager.update(f"{config_key}.enabled", True)
            
        # [NEW] ZMQ 제어 채널로 명령 송출하여 데몬에서 기동 확인 및 ACK 방출 유도
        if self.control_publisher:
            await self.control_publisher.publish("collector_control", {
                "type": "collector_start",
                "exchange": exchange,
                "command_id": command_id
            })

    async def _handle_collector_stop(self, command_id: str, payload: Dict[str, Any]):
        exchange = payload.get("exchange")
        if not exchange:
            raise ValueError("Exchange parameter is missing")
            
        if exchange == "all":
            exchanges_config = self.config_manager.get('exchanges', {})
            for exch in exchanges_config.keys():
                self.config_manager.update(f"exchanges.{exch}.enabled", False)
        else:
            config_key = f"exchanges.{exchange}"
            exch_config = self.config_manager.get(config_key)
            if exch_config is None:
                raise ValueError(f"Configuration for exchange '{exchange}' not found")
            self.config_manager.update(f"{config_key}.enabled", False)
            
        # [NEW] ZMQ 제어 채널로 명령 송출하여 데몬에서 정지 확인 및 ACK 방출 유도
        if self.control_publisher:
            await self.control_publisher.publish("collector_control", {
                "type": "collector_stop",
                "exchange": exchange,
                "command_id": command_id
            })

    async def _handle_collector_restart_daemon(self, command_id: str, payload: Dict[str, Any]):
        if not self.control_publisher:
            raise RuntimeError("ZMQ Control Publisher is not initialized")
        # [NEW] command_id를 포함하여 restart_daemon 발행
        await self.control_publisher.publish("collector_control", {
            "type": "restart_daemon",
            "command_id": command_id
        })

    def _get_official_config_key(self, strategy_id: str) -> str:
        strategies_config = self.config_manager.get('strategies', {}) or {}
        for k in strategies_config.keys():
            if k.lower() == strategy_id.lower():
                return k
        return strategy_id

    async def _handle_strategy_enable(self, command_id: str, payload: Dict[str, Any]):
        strategy_id = payload.get("strategy_id")
        if not strategy_id:
            raise ValueError("Strategy ID parameter is missing")
        s_id = self._get_official_config_key(strategy_id)
        self.config_manager.update(f"strategies.{s_id}.enabled", True)
        await self._sync_active_portfolio_strategies(s_id, True)

    async def _handle_strategy_disable(self, command_id: str, payload: Dict[str, Any]):
        strategy_id = payload.get("strategy_id")
        if not strategy_id:
            raise ValueError("Strategy ID parameter is missing")
        s_id = self._get_official_config_key(strategy_id)
        self.config_manager.update(f"strategies.{s_id}.enabled", False)
        await self._sync_active_portfolio_strategies(s_id, False)

    async def _sync_active_portfolio_strategies(self, strategy_id: str, enabled: bool):
        active_p = self.portfolio_manager.get_active_simulation_portfolio()
        if not active_p:
            active_p = self.portfolio_manager.portfolios.get('live')
            
        if active_p and active_p.strategy_info:
            try:
                meta = json.loads(active_p.strategy_info)
                applied = meta.get("applied_strategies", {})
                
                from src.engine.strategy import StrategyRegistry
                strat_cls = StrategyRegistry.get_strategy_class(strategy_id)
                official_name = strat_cls.__name__ if strat_cls else strategy_id
                
                version_info = await self.repository.get_strategy_version(official_name)
                
                if enabled:
                    if official_name not in applied:
                        if version_info and version_info.get("current_params"):
                            params = version_info["current_params"]
                        else:
                            s_conf = self.config_manager.get(f"strategies.{strategy_id}") or {}
                            params = s_conf.get("params", {})
                        applied[official_name] = {
                            "enabled": True,
                            "params": params
                        }
                    else:
                        applied[official_name]["enabled"] = True
                else:
                    if official_name in applied:
                        applied[official_name]["enabled"] = False
                
                meta["applied_strategies"] = applied
                active_p.strategy_info = json.dumps(meta)
                
                await self.portfolio_manager.save_to_db(active_p.id)
                
                if self.strategy_control_publisher:
                    await self.strategy_control_publisher.publish("strategy_control", {
                        "type": "update_portfolio",
                        "portfolio_id": active_p.id
                    })
            except Exception as e:
                logger.error(f"[Dispatcher] Active portfolio strategy sync failed: {e}")

    async def _handle_strategy_update_params(self, command_id: str, payload: Dict[str, Any]):
        strategy_id = payload.get("strategy_id")
        params = payload.get("params")
        if not strategy_id or params is None:
            raise ValueError("Strategy ID or params parameter is missing")
        s_id = self._get_official_config_key(strategy_id)
        for pk, pv in params.items():
            self.config_manager.update(f"strategies.{s_id}.params.{pk}", pv)

    async def _handle_strategy_restart_daemon(self, command_id: str, payload: Dict[str, Any]):
        if not self.strategy_control_publisher:
            raise RuntimeError("ZMQ Strategy Control Publisher is not initialized")
        await self.strategy_control_publisher.publish("strategy_control", {"type": "restart_daemon"})

    async def _handle_portfolio_start(self, command_id: str, payload: Dict[str, Any]):
        initial_cash = payload.get("initial_cash")
        strategies = payload.get("strategies")
        if initial_cash is None:
            raise ValueError("initial_cash is required to start portfolio session")

        # strategies가 생략되었거나 비어있는 경우 DB 챔피언 전략 자동 로드
        if not strategies:
            strategies = {}
            try:
                db_strategies = await self.repository.get_all_strategy_versions()
                strategies_config = self.config_manager.get('strategies', {}) or {}
                for s_ver in db_strategies:
                    s_id = s_ver["strategy_id"]
                    # settings.yaml을 확인하여 전역적으로 켜져 있는 전략인지 교차 체크 (대소문자 무관 비교 및 공식 키 조회)
                    official_key = self._get_official_config_key(s_id)
                    s_config = strategies_config.get(official_key)
                    if s_config and s_config.get("enabled", False):
                        from src.engine.strategy import StrategyRegistry
                        strat_cls = StrategyRegistry.get_strategy_class(s_id)
                        official_name = strat_cls.__name__ if strat_cls else s_id
                        
                        current_ver_id = s_ver.get("current_version_id", 0)
                        existing = strategies.get(official_name)
                        if existing:
                            existing_ver_id = existing.get("_version_id", 0)
                            if current_ver_id <= existing_ver_id:
                                continue
                                
                        strategies[official_name] = {
                            "enabled": True,
                            "params": s_ver["current_params"],
                            "_version_id": current_ver_id
                        }
                # 임시 버전 키 정리
                for s_name in list(strategies.keys()):
                    strategies[s_name].pop("_version_id", None)

                # DB 챔피언 기록이 없어 누락되었으나 settings.yaml에 enabled=true인 신규 전략들 병합 (덮어쓰지 않음)
                for s_id, s_conf in strategies_config.items():
                    if s_conf.get('enabled', False):
                        from src.engine.strategy import StrategyRegistry
                        strat_cls = StrategyRegistry.get_strategy_class(s_id)
                        official_name = strat_cls.__name__ if strat_cls else s_id
                        
                        if official_name not in strategies:
                            strategies[official_name] = {
                                "enabled": True,
                                "params": s_conf.get('params', {})
                            }
            except Exception as e:
                logger.error(f"[Dispatcher] DB 챔피언 전략 로드 중 예외 발생: {e}")

        # 1. 기존 활성 모의투자 세션이 있다면 자동 종료 처리
        active_p = self.portfolio_manager.get_active_simulation_portfolio()
        if active_p:
            try:
                logger.info(f"기존 활성화된 모의투자 세션 자동 종료 처리 중: {active_p.id}")
                await self._end_portfolio_session_internal(active_p.id)
            except Exception as e:
                logger.error(f"기존 활성 세션 자동 종료 중 에러: {e}")
                
        # 2. 신규 포트폴리오 생성 및 거래소별 자금 분배
        portfolio_id = payload.get("portfolio_id") or f"simulation_{int(time.time())}"
        p_name = f"실시간 모의투자 ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')})"
        initial_cash_input = initial_cash
        exchange_cash_map = {}
        total_cash = 0.0

        enabled_exchanges = []
        exchanges_config = self.config_manager.get('exchanges', {})
        for ex_id, exch_config in exchanges_config.items():
            if exch_config.get('enabled', True):
                enabled_exchanges.append(ex_id.lower())
                
        if not enabled_exchanges:
            enabled_exchanges = ['upbit']

        if isinstance(initial_cash_input, dict):
            for ex, cash_val in initial_cash_input.items():
                ex_lower = ex.lower()
                if ex_lower in enabled_exchanges:
                    val = float(cash_val)
                    exchange_cash_map[ex_lower] = val
                    total_cash += val
            
            if not exchange_cash_map:
                total_cash = 30000000.0
                each_cash = total_cash / len(enabled_exchanges)
                exchange_cash_map = {ex: each_cash for ex in enabled_exchanges}
        else:
            total_cash = float(initial_cash_input)
            each_cash = total_cash / len(enabled_exchanges)
            exchange_cash_map = {ex: each_cash for ex in enabled_exchanges}

        from src.engine.portfolio import Portfolio
        p = Portfolio(
            portfolio_id=portfolio_id,
            name=p_name,
            initial_cash=total_cash,
            exchange_id='all',
            portfolio_type='simulation'
        )
        p.cash = total_cash
        p.exchange_cash = exchange_cash_map
        
        # 4. 선택 전략 메타 정보 기재
        meta_info = {
            "applied_strategies": strategies,
            "initial_cash": initial_cash
        }
        p.strategy_info = json.dumps(meta_info)
        
        # 5. 메모리 등록 및 DB 영구 저장
        self.portfolio_manager.add_portfolio(p)
        await self.portfolio_manager.save_to_db(portfolio_id)
        
        # ZMQ IPC 메시지 발행
        if self.strategy_control_publisher:
            try:
                msg = {
                    "type": "update_portfolio",
                    "portfolio_id": portfolio_id
                }
                await self.strategy_control_publisher.publish("strategy_control", msg)
                logger.info(f"[Dispatcher] ZMQ strategy control message published: {msg}")
            except Exception as e:
                logger.error(f"[Dispatcher] Failed to publish ZMQ message: {e}")
                
        return {"portfolio_id": portfolio_id, "name": p_name}

    async def _handle_portfolio_end(self, command_id: str, payload: Dict[str, Any]):
        portfolio_id = payload.get("portfolio_id")
        if not portfolio_id:
            raise ValueError("Portfolio ID parameter is missing")
            
        await self._end_portfolio_session_internal(portfolio_id)
        
        # ZMQ IPC 메시지 발행
        if self.strategy_control_publisher:
            try:
                msg = {
                    "type": "update_portfolio",
                    "portfolio_id": portfolio_id
                }
                await self.strategy_control_publisher.publish("strategy_control", msg)
                logger.info(f"[Dispatcher] ZMQ strategy control message published: {msg}")
            except Exception as e:
                logger.error(f"[Dispatcher] Failed to publish ZMQ message: {e}")

    async def _handle_portfolio_panic(self, command_id: str, payload: Dict[str, Any]):
        portfolio_id = payload.get("portfolio_id")
        if not portfolio_id:
            raise ValueError("Portfolio ID parameter is missing")
            
        portfolio = self.portfolio_manager.portfolios.get(portfolio_id)
        if not portfolio:
            raise ValueError(f"Portfolio {portfolio_id} not found")

        # 1. 청산할 종목들 추출
        positions_to_sell = [(pos.exchange, pos.symbol, pos.quantity) 
                             for pos in portfolio.positions.values() if pos.quantity > 0]
        if not positions_to_sell:
            return {"message": "청산할 포지션이 없습니다.", "data": []}

        # 2. 가격 구성
        prices = {}
        upbit_symbols = [sym for ex, sym, qty in positions_to_sell if ex.lower() == 'upbit']
        
        # Upbit 가격 조회
        if upbit_symbols:
            import aiohttp
            try:
                formatted = [f"KRW-{s}" if not s.startswith("KRW-") else s for s in upbit_symbols]
                url = f"https://api.upbit.com/v1/ticker?markets={','.join(formatted)}"
                async with aiohttp.ClientSession() as session:
                    async with session.get(url) as resp:
                        if resp.status == 200:
                            tickers = await resp.json()
                            for t in tickers:
                                clean_sym = t['market'].replace("KRW-", "")
                                prices[('upbit', clean_sym)] = float(t['trade_price'])
            except Exception as e:
                logger.error(f"Failed to fetch upbit tickers for panic sell: {e}")

        # KIS 및 기타 가격 조회 (DB 캔들 조회)
        from src.database.connection import get_db_conn
        async with get_db_conn(self.portfolio_manager.db_path) as db:
            for ex, sym, qty in positions_to_sell:
                ex_key = ex.lower()
                if ex_key != 'upbit':
                    try:
                        async with db.execute(
                            "SELECT close FROM candles WHERE exchange = ? AND symbol = ? ORDER BY timestamp DESC LIMIT 1",
                            (ex_key, sym)
                        ) as cursor:
                            row = await cursor.fetchone()
                            if row:
                                prices[(ex_key, sym)] = row['close']
                            else:
                                pos_key = (ex_key, sym)
                                prices[(ex_key, sym)] = portfolio.positions[pos_key].avg_price
                    except Exception as e:
                        logger.error(f"Failed to query panic sell price for {ex_key}:{sym}: {e}")
                        pos_key = (ex_key, sym)
                        prices[(ex_key, sym)] = portfolio.positions[pos_key].avg_price
                else:
                    if ('upbit', sym) not in prices:
                        prices[('upbit', sym)] = portfolio.positions[('upbit', sym)].avg_price

        # 3. 각 종목별 청산 실행
        results = []
        executor = self.portfolio_manager.executors.get('simulation')
        for ex, symbol, qty in positions_to_sell:
            ex_key = ex.lower()
            price = prices.get((ex_key, symbol), 0)
            if price == 0:
                continue
            
            res = await executor.execute_order(
                exchange=ex,
                symbol=symbol,
                side='SELL',
                quantity=qty,
                trade_price=price
            )
            if res:
                results.append(res)
                # 1. 포트폴리오 상태 갱신
                portfolio.update_position(
                    exchange=res['exchange'],
                    symbol=res['symbol'],
                    side=res['side'],
                    price=res['price'],
                    quantity=res['quantity'],
                    fee=res['fee'],
                    strategy_id="panic_sell",
                    reason="긴급 손절 (Panic Sell)"
                )
                
                # 2. DB 거래 내역 저장
                from src.database.connection import get_db_conn
                async with get_db_conn(self.portfolio_manager.db_path) as db:
                    await db.execute('''
                        INSERT INTO orders_history (portfolio_id, exchange, strategy_id, symbol, side, price, quantity, fee, timestamp, reason, context)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        portfolio_id, 
                        res['exchange'],
                        "panic_sell", 
                        res['symbol'], 
                        res['side'], 
                        res['price'], 
                        res['quantity'], 
                        res['fee'], 
                        int(time.time()), 
                        "긴급 손절 (Panic Sell)", 
                        "{}"
                    ))
                    await db.commit()

                # 3. 긴급 알림 브로드캐스트
                alert = {
                    "type": "alert",
                    "alert_type": "panic",
                    "exchange": ex,
                    "code": symbol,
                    "price": price,
                    "msg": f"🚨 [긴급손절] {symbol} ({ex}) 전량 매도 완료"
                }
                
                # WS Manager 임포트 & 브로드캐스트 (WS 기동 여부 상관없이 예외 가드 처리)
                try:
                    from src.server.websocket import manager as ws_manager
                    await ws_manager.broadcast_global(alert)
                except Exception as ws_err:
                    logger.debug(f"Websocket broadcast skipped in non-web context: {ws_err}")
                
                # DB Alert 및 System Event 추가 적재
                await self.repository.insert_system_event(
                    event_type='ALERT_PANIC',
                    target=symbol,
                    message=alert["msg"],
                    context=json.dumps(alert)
                )

        # 4. 변경된 포트폴리오 상태 DB 영구 저장
        await self.portfolio_manager.save_to_db(portfolio_id)

        # ZMQ IPC 메시지 발행
        if self.strategy_control_publisher:
            try:
                msg = {
                    "type": "update_portfolio",
                    "portfolio_id": portfolio_id
                }
                await self.strategy_control_publisher.publish("strategy_control", msg)
                logger.info(f"[Dispatcher] ZMQ strategy control message published: {msg}")
            except Exception as e:
                logger.error(f"[Dispatcher] Failed to publish ZMQ message: {e}")

        return {"message": f"{len(results)}개 종목 청산 완료", "data": results}

    # --- 헬퍼 메서드 ---

    async def _end_portfolio_session_internal(self, portfolio_id: str):
        """모의투자 마감 내부 공통 처리 메서드 (미실현 평가가 고정)"""
        portfolio = self.portfolio_manager.portfolios.get(portfolio_id)
        if not portfolio:
            raise ValueError(f"Portfolio {portfolio_id} not found")
            
        # 1. 각 종목별 최종 평가가(현재 실시간 시세) 산출
        class MockSystem:
            latest_prices = {}
        current_prices = await self.portfolio_manager.get_portfolio_current_prices(portfolio_id, MockSystem())

        # 2. 누적 수수료 및 거래 건수 집계
        from src.database.connection import get_db_conn
        async with get_db_conn(self.portfolio_manager.db_path) as db:
            async with db.execute("SELECT COUNT(*), SUM(fee) FROM orders_history WHERE portfolio_id = ?", (portfolio_id,)) as cursor:
                row = await cursor.fetchone()
                trade_count = row[0] if row else 0
                total_fee = row[1] if row and row[1] is not None else 0.0

        # 3. 최종 평가 금액 및 메타데이터 구성
        total_value = portfolio.get_total_value(current_prices)
        
        meta = {}
        if portfolio.strategy_info:
            try:
                meta = json.loads(portfolio.strategy_info)
            except Exception:
                pass
                
        meta["final_prices"] = current_prices
        meta["summary"] = {
            "initial_cash": portfolio.initial_cash,
            "final_value": total_value,
            "profit": total_value - portfolio.initial_cash,
            "roi": round(((total_value - portfolio.initial_cash) / portfolio.initial_cash * 100), 2) if portfolio.initial_cash > 0 else 0.0,
            "fee": round(total_fee, 2),
            "trade_count": trade_count
        }
        
        # 4. 타입 변경 및 저장
        portfolio.strategy_info = json.dumps(meta)
        portfolio.portfolio_type = 'simulation_ended'
        
        # DB 영구 저장
        await self.portfolio_manager.save_to_db(portfolio_id)
