from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple, Any
from src.engine.utils.telemetry import get_logger

logger = get_logger(__name__)
from abc import ABC, abstractmethod
import time
import asyncio
from src.database.retry import with_db_retry
from src.engine.matching import OrderbookMatchingEngine
from src.database.repository import BaseTradingRepository, SqliteTradingRepository
from src.config.manager import ConfigManager

@dataclass
class Position:
    exchange: str
    symbol: str
    quantity: float = 0.0
    avg_price: float = 0.0
    updated_at: float = 0.0

class Portfolio:
    """
    개별 포트폴리오의 자산 상태(현금, 포지션)를 관리합니다.
    """
    def __init__(self, portfolio_id: str, name: str, initial_cash: float = 1000000.0, exchange_id: str = 'upbit', portfolio_type: str = 'simulation', strategy_info: str = ""):
        self.id = portfolio_id
        self.name = name
        self.portfolio_type = portfolio_type
        self.exchange_id = exchange_id # [NEW]
        self.initial_cash = initial_cash
        self.cash = initial_cash
        self.positions: Dict[Tuple[str, str], Position] = {} # Key is (exchange.lower(), symbol)
        self.exchange_cash: Dict[str, float] = {} # [NEW] 거래소별 가용 자금 격리 관리
        self.history: List[Dict] = []
        self.strategy_info = strategy_info

    def update_position(self, exchange: str, symbol: str, side: str, price: float, quantity: float, fee: float, strategy_id: str = "", reason: str = "", context: Dict = None, market: str = None):
        """체결된 결과를 바탕으로 포지션과 잔고를 업데이트합니다."""
        ex_key = exchange.lower()
        pos_key = (ex_key, symbol)
        if pos_key not in self.positions:
            self.positions[pos_key] = Position(exchange=exchange, symbol=symbol)
        
        pos = self.positions[pos_key]
        
        # exchange_cash 맵 초기화 fallback
        if self.exchange_cash is not None and ex_key not in self.exchange_cash:
            self.exchange_cash[ex_key] = 10000000.0  # 기본 거래소 가용자산은 1,000만 원 설정
        
        if side == 'BUY':
            # 매수: 평균 단가 갱신 및 수량 증가
            total_cost = (pos.avg_price * pos.quantity) + (price * quantity)
            pos.quantity += quantity
            if pos.quantity > 0:
                pos.avg_price = total_cost / pos.quantity
            self.cash -= (price * quantity) + fee
            if self.exchange_cash:
                self.exchange_cash[ex_key] -= (price * quantity) + fee
        else:
            # 매도: 수량 감소
            pos.quantity -= quantity
            self.cash += (price * quantity) - fee
            if self.exchange_cash:
                self.exchange_cash[ex_key] += (price * quantity) - fee
            if pos.quantity <= 0:
                pos.quantity = 0
                pos.avg_price = 0
        
        pos.updated_at = time.time()
        
        # 거래소별 격리가 수행 중이라면, 전체 현금 잔액은 개별 잔액의 합산으로 최종 정렬함
        if self.exchange_cash:
            self.cash = sum(self.exchange_cash.values())

        # 히스토리 기록
        self.history.append({
            'exchange': exchange,
            'market': market,
            'symbol': symbol,
            'side': side,
            'price': price,
            'quantity': quantity,
            'fee': fee,
            'timestamp': time.time(),
            'cash_after': self.cash,
            'strategy_id': strategy_id,
            'reason': reason,
            'context': context or {}
        })

    def get_total_value(self, current_prices: Dict[str, float]) -> float:
        """현재 가를 반영한 총 자산 가치를 계산합니다."""
        pos_value = sum(pos.quantity * current_prices.get(pos.symbol, pos.avg_price) 
                        for pos in self.positions.values())
        return self.cash + pos_value

class OrderExecutor(ABC):
    """
    주문 실행 인터페이스입니다. (가상/실제 공통)
    """
    @abstractmethod
    async def execute_order(self, exchange: str, symbol: str, side: str, quantity: float, **kwargs) -> Optional[Dict]:
        pass

class VirtualOrderExecutorAdapter(OrderExecutor):
    """
    OrderbookMatchingEngine을 완전히 내포하여 슬리피지 및 수수료가 반영된 가상 주문 체결을 집행하는 어댑터입니다.
    """
    def __init__(self, fee_rate: float = 0.0005):
        self.matching_engine = OrderbookMatchingEngine(fee_rate=fee_rate)

    async def execute_order(self, exchange: str, symbol: str, side: str, quantity: float, **kwargs) -> Optional[Dict]:
        orderbook = kwargs.get('orderbook')
        trade_price = kwargs.get('trade_price')
        market = kwargs.get('market', 'KRW')
        
        if orderbook:
            # OrderbookMatchingEngine 형식에 맞춰 데이터 준비
            asks = [{'price': p, 'size': s} for p, s in orderbook.get('asks', [])]
            bids = [{'price': p, 'size': s} for p, s in orderbook.get('bids', [])]
            
            vwap, cash_flow, remaining = self.matching_engine.execute_market_order(
                order_type=side,
                quantity=quantity,
                orderbook_asks=asks,
                orderbook_bids=bids
            )
            executed_qty = quantity - remaining
            # 수수료 산출: 실제 현금흐름과 순수 체결가치의 차이
            executed_value = vwap * executed_qty
            fee = abs(abs(cash_flow) - executed_value)
        elif trade_price:
            # Orderbook이 없으면 현재 trade_price로 즉시 전량 체결 (슬리피지 없음)
            vwap = trade_price
            executed_qty = quantity
            executed_value = vwap * executed_qty
            fee = executed_value * self.matching_engine.fee_rate
        else:
            logger.error(f"VirtualOrderExecutorAdapter: Both orderbook and trade_price missing for {symbol}")
            return None
        
        if vwap == 0 or executed_qty <= 0:
            return None
        
        return {
            'exchange': exchange,
            'market': market,
            'symbol': symbol,
            'side': side,
            'price': vwap,
            'quantity': executed_qty,
            'fee': fee,
            'executed_value': executed_value,
            'timestamp': int(time.time() * 1000)
        }

class PortfolioManager:
    """
    여러 포트폴리오를 관리하고 전략 신호를 주문으로 연결합니다.
    """
    def __init__(self, db_path: Optional[str] = None, repository: Optional[BaseTradingRepository] = None):
        self.db_path = db_path
        self.repository = repository or SqliteTradingRepository(db_path=db_path)
        self.config_manager = ConfigManager("config/settings.yaml")
        self.portfolios: Dict[str, Portfolio] = {}
        self.exchange_configs: Dict[str, Dict] = {} # [NEW] 거래소별 수수료 등 설정 캐시
        self.executors: Dict[str, OrderExecutor] = {
            'simulation': VirtualOrderExecutorAdapter()
        }
        self.collector_statuses: Dict[str, Dict[str, Any]] = {}
        self.broadcast_callback = None

    def add_portfolio(self, portfolio: Portfolio):
        self.portfolios[portfolio.id] = portfolio

    def get_active_simulation_portfolio(self) -> Optional[Portfolio]:
        """현재 활성화된(즉 type이 'simulation'인) 가장 최근의 모의투자 포트폴리오 객체를 반환합니다."""
        sim_ports = [p for p in self.portfolios.values() if p.portfolio_type == 'simulation']
        if not sim_ports:
            return None
        sim_ports.sort(key=lambda x: x.id, reverse=True)
        return sim_ports[0]

    def get_portfolio_summary(self, symbol: str, portfolio_id: str = "default", exchange: Optional[str] = None) -> Dict[str, Any]:
        """
        특정 포트폴리오의 현재 현금 및 특정 종목의 포지션 요약을 반환합니다.
        """
        portfolio = None
        if portfolio_id == "default" or not portfolio_id:
            portfolio = self.get_active_simulation_portfolio()
        else:
            portfolio = self.portfolios.get(portfolio_id)
            
        if not portfolio:
            return {"cash": 0.0, "quantity": 0.0, "avg_price": 0.0}
            
        ex_key = (exchange or portfolio.exchange_id or 'upbit').lower()
        pos = portfolio.positions.get((ex_key, symbol))
        
        cash_val = portfolio.exchange_cash.get(ex_key, portfolio.cash)
        
        return {
            "cash": cash_val,
            "quantity": pos.quantity if pos else 0.0,
            "avg_price": pos.avg_price if pos else 0.0
        }

    async def check_live_trading_blocked(self, portfolio: Portfolio, signal: Any = None) -> Optional[Dict[str, Any]]:
        """
        실거래가 차단되었는지 검사하는 공통 가드입니다.
        portfolio_type == 'live'이거나, live_trading_enabled 설정이 False인 경우 실거래 주문을 차단합니다.
        """
        live_trading_enabled = self.config_manager.get("system.live_trading_enabled", False)
        
        if portfolio.portfolio_type == 'live' and not live_trading_enabled:
            symbol = getattr(signal, 'symbol', 'UNKNOWN') if signal else 'UNKNOWN'
            side = getattr(signal, 'action', 'UNKNOWN') if signal else 'UNKNOWN'
            
            logger.warning(f"[PortfolioManager] 실거래 주문 차단: live_trading_enabled가 false입니다. ({portfolio.id} - {side} {symbol})")
            
            # 시스템 이벤트 기록
            msg = f"Live order blocked: live_trading_enabled is False. (Portfolio: {portfolio.id}, Symbol: {symbol}, Side: {side})"
            await self.repository.insert_system_event(
                event_type='BLOCKED_LIVE_ORDER',
                target=portfolio.id,
                message=msg,
                timestamp=int(time.time())
            )
            
            # 브로드캐스트 알림
            if self.broadcast_callback:
                try:
                    await self.broadcast_callback({
                        "type": "order_blocked",
                        "portfolio_id": portfolio.id,
                        "exchange": getattr(signal, 'exchange', portfolio.exchange_id),
                        "symbol": symbol,
                        "side": side,
                        "reason": "실계좌 거래 비활성화 (live_trading_enabled: false)"
                    })
                except Exception as e:
                    logger.error(f"Failed to broadcast order blocked alert: {e}")
                    
            return {"status": "BLOCKED", "reason": "LIVE_TRADING_DISABLED"}
        return None

    async def liquidate_all(self, portfolio_id: str) -> List[Dict]:
        """포트폴리오의 모든 포지션을 즉시 시장가로 청산합니다."""
        portfolio = self.portfolios.get(portfolio_id)
        if not portfolio:
            return []
            
        # 실거래 차단 가드 적용
        blocked_result = await self.check_live_trading_blocked(portfolio)
        if blocked_result:
            return [blocked_result]
            
        results = []
        executor = self.executors.get('simulation')
        
        positions_to_sell = [(pos.exchange, pos.symbol, pos.quantity) for pos in portfolio.positions.values() if pos.quantity > 0]
        
        for ex, symbol, qty in positions_to_sell:
            result = await executor.execute_order(
                exchange=ex,
                symbol=symbol,
                side='SELL',
                quantity=qty,
                trade_price=0 
            )
            if result:
                portfolio.update_position(
                    exchange=result['exchange'],
                    symbol=result['symbol'],
                    side=result['side'],
                    price=result['price'],
                    quantity=result['quantity'],
                    fee=result['fee'],
                    strategy_id="liquidate",
                    reason="전체 청산 (Liquidate All)",
                    market=result.get('market')
                )
                results.append(result)
                
        return results

    async def cancel_all_orders(self, exchange: str):
        """거래소 정지 상태 진입 시, 해당 거래소의 모든 미체결 주문을 일괄 취소합니다."""
        logger.warning(f"[PortfolioManager] 거래소 {exchange} 정지 상태 감지: 미체결 주문 일괄 취소를 요청합니다.")
        
        live_trading_enabled = self.config_manager.get("system.live_trading_enabled", False)
        if not live_trading_enabled:
            live_portfolios = [p for p in self.portfolios.values() if p.portfolio_type == 'live' and p.exchange_id.lower() == exchange.lower()]
            if live_portfolios:
                for lp in live_portfolios:
                    logger.warning(f"[PortfolioManager] 실거래 주문 취소 차단: live_trading_enabled가 false입니다. (Portfolio: {lp.id})")
                    msg = f"Live order cancel blocked: live_trading_enabled is False. (Portfolio: {lp.id}, Exchange: {exchange})"
                    await self.repository.insert_system_event(
                        event_type='BLOCKED_LIVE_ORDER',
                        target=lp.id,
                        message=msg,
                        timestamp=int(time.time())
                    )
                return {"status": "BLOCKED", "reason": "LIVE_TRADING_DISABLED"}
        
        # 가상 매칭 엔진의 경우 즉시 전량 체결되므로 미체결 주문 관리가 없으나,
        # 실거래 API 확장 및 시스템 방어 인터페이스 제공을 위해 로그 및 빈 구조 구현.
        pass

    @with_db_retry()
    async def execute_pipeline_order(self, portfolio_id: str, signal, quantity: float, execution_price: float, orderbook_data: Optional[Dict] = None):
        """
        ExecutionPipeline에 의해 계산되고 검증 완료된 주문을 실제로 실행하고 영구 저장합니다.
        """
        portfolio = self.portfolios.get(portfolio_id)
        if not portfolio:
            logger.error(f"Portfolio {portfolio_id} not found for executing pipeline order.")
            return None

        # 실거래 주문 차단 가드 체크
        blocked_result = await self.check_live_trading_blocked(portfolio, signal)
        if blocked_result:
            return blocked_result

        # 거래소 수수료율 적용 (다중 종목 대응을 위해 signal.exchange 우선 고려)
        exchange_key = getattr(signal, 'exchange', portfolio.exchange_id)
        if not exchange_key or exchange_key == 'all':
            exchange_key = portfolio.exchange_id

        # 거래소 정지 상태(SUSPENDED) 여부 체크 및 주문 차단
        status_info = self.collector_statuses.get(exchange_key.lower(), {})
        if status_info.get('status') == 'SUSPENDED':
            reason = status_info.get('status_reason', '정지 사유 미지정')
            logger.warning(f"[PortfolioManager] 주문 전송 차단: 거래소 {exchange_key}가 SUSPENDED 상태입니다. 사유: {reason}")
            if self.broadcast_callback:
                await self.broadcast_callback({
                    "type": "order_blocked",
                    "portfolio_id": portfolio_id,
                    "exchange": exchange_key,
                    "symbol": signal.symbol,
                    "side": signal.action,
                    "reason": f"거래소 정지 상태 (사유: {reason})"
                })
            return None
            
        exchange_config = self.exchange_configs.get(exchange_key, {})
        fee_rate = exchange_config.get('fee_rate', 0.0005)
        
        # [방안 B] 거래소 설정별로 주입된 어댑터 캐싱 및 다형적 호출
        executor_key = f"simulation_{exchange_key.lower()}"
        if executor_key not in self.executors:
            self.executors[executor_key] = VirtualOrderExecutorAdapter(fee_rate=fee_rate)
        executor = self.executors[executor_key]

        market_val = getattr(signal, 'market', None)
        if not market_val:
            if exchange_key == 'kis':
                market_val = 'SOR'
            else:
                market_val = 'KRW'

        result = await executor.execute_order(
            exchange=signal.exchange,
            market=market_val,
            symbol=signal.symbol,
            side=signal.action,
            quantity=quantity,
            orderbook=orderbook_data,
            trade_price=execution_price
        )
        
        if result:
            # 포트폴리오 상태 갱신
            portfolio.update_position(
                exchange=result['exchange'],
                symbol=result['symbol'],
                side=result['side'],
                price=result['price'],
                quantity=result['quantity'],
                fee=result['fee'],
                strategy_id=getattr(signal, 'strategy_id', ""),
                reason=getattr(signal, 'reason', ""),
                context=getattr(signal, 'context', {}),
                market=result.get('market')
            )
            
            logger.info(f"TRADE EXECUTION: {portfolio.name}: {result['side']} {result['symbol']} @ {result['price']:.2f} (Qty: {result['quantity']:.4f})")
            
            order_data = {
                'exchange': result['exchange'],
                'market': result.get('market', market_val),
                'strategy_id': getattr(signal, 'strategy_id', ""),
                'symbol': result['symbol'],
                'side': result['side'],
                'price': result['price'],
                'quantity': result['quantity'],
                'fee': result['fee'],
                'timestamp': int(time.time()),
                'reason': getattr(signal, 'reason', ""),
                'context': result.get('context', {}) or getattr(signal, 'context', {})
            }
            await self.repository.insert_order_history(portfolio_id, order_data)
                
            await self.save_to_db(portfolio_id)
            return result
        return None

    async def handle_signal(self, portfolio_id: str, signal, trade_price: float, orderbook_data: Optional[Dict] = None):
        """
        [DEPRECATED] 하위 호환성을 유지하기 위한 래퍼입니다. 
        실제 주문 처리는 ExecutionPipeline.process_signal()을 통하시기 바랍니다.
        """
        if not portfolio_id or portfolio_id in ['default', 'stock_default', 'bithumb_default']:
            portfolio = self.get_active_simulation_portfolio()
        else:
            portfolio = self.portfolios.get(portfolio_id)

        if not portfolio:
            return None

        # 고정 수량 계산
        if signal.action == 'BUY':
            quantity = (portfolio.cash * 0.1) / trade_price
        elif signal.action == 'SELL':
            ex_key = (signal.exchange or portfolio.exchange_id or 'upbit').lower()
            pos = portfolio.positions.get((ex_key, signal.symbol))
            if not pos or pos.quantity <= 0: return None
            quantity = pos.quantity
        else:
            return None

        return await self.execute_pipeline_order(portfolio.id, signal, quantity, trade_price, orderbook_data)

    @with_db_retry()
    async def save_to_db(self, portfolio_id: str):
        """포트폴리오 상태를 DB에 영구 저장합니다."""
        portfolio = self.portfolios.get(portfolio_id)
        if not portfolio:
            return
        await self.repository.save_portfolio(portfolio)

    async def load_exchange_configs(self):
        """DB에서 거래소 설정을 로드하여 메모리에 캐싱합니다."""
        self.exchange_configs = await self.repository.load_exchange_configs()
        logger.info(f"{len(self.exchange_configs)}개의 거래소 설정을 로드했습니다.")

    async def load_from_db(self, exclude_types: list = None):
        """DB에서 저장된 포트폴리오 정보를 불러옵니다."""
        await self.load_exchange_configs() # 거래소 설정 먼저 로드

        loaded_portfolios = await self.repository.load_portfolios(exclude_types=exclude_types)
        
        # 원자적 참조 교체로 메모리 동기화 및 기존 stale 세션 날리기 완수
        self.portfolios = loaded_portfolios
        logger.info(f"{len(self.portfolios)}개의 포트폴리오를 DB에서 로드했습니다.")

    async def get_portfolio_current_prices(self, portfolio_id: str, system) -> dict:
        """
        포트폴리오의 보유 종목들에 대한 현재가(종가) 맵을 공통으로 산출합니다.
        1순위: 진행 중인 경우 system.latest_prices 메모리 캐시 참조.
               완료/백테스트인 경우 portfolios.strategy_info 의 final_prices 참조.
        2순위: 로컬 DB candles 테이블 최신 종가 조회.
        3순위: 업비트 API 직접 조회 (업비트 종목만).
        4순위: 포지션 평균 매수가(avg_price).
        """
        portfolio = self.portfolios.get(portfolio_id)
        if not portfolio:
            return {}

        current_prices = {}
        is_active = portfolio.portfolio_type == 'simulation'
        
        # 1순위: 완료/백테스트의 경우 strategy_info 내 final_prices 확인
        meta = {}
        if not is_active and getattr(portfolio, 'strategy_info', ''):
            try:
                import json
                meta = json.loads(portfolio.strategy_info)
                if isinstance(meta, dict) and "final_prices" in meta:
                    # 마감된 가격 복원
                    for k, v in meta["final_prices"].items():
                        clean_k = k.replace("KIS-", "").replace("KRW-", "").replace("UPB-", "")
                        current_prices[clean_k] = float(v)
            except Exception as e:
                logger.error(f"Failed to parse strategy_info final_prices: {e}")

        # 2순위: 진행중이거나 meta에 종가 정보가 없는 경우 ➔ system.latest_prices 메모리 캐시 확인
        upbit_symbols = []
        for pos_key, pos in portfolio.positions.items():
            if pos.quantity <= 0:
                continue
            ex_key, sym = pos_key
            if sym in current_prices:
                continue

            # 메모리 캐시 조회
            cached = system.latest_prices.get(f"{ex_key.lower()}:{sym}")
            if cached and cached.get('trade_price') is not None:
                current_prices[sym] = float(cached['trade_price'])
                continue

            if ex_key.lower() == 'upbit':
                upbit_symbols.append(sym)
            else:
                current_prices[sym] = pos.avg_price  # 4순위 기본 폴백

        # 3순위: 로컬 DB candles 조회 (업비트가 아니거나 메모리 캐시에 없는 종목 대상)
        from src.database.connection import get_db_conn
        async with get_db_conn(self.db_path) as db:
            for pos_key, pos in portfolio.positions.items():
                if pos.quantity <= 0:
                    continue
                ex_key, sym = pos_key
                if sym in current_prices:
                    continue
                if ex_key.lower() == 'upbit':
                    continue  # 업비트는 API로 조회할 것임
                
                try:
                    async with db.execute(
                        "SELECT close FROM candles WHERE exchange = ? AND symbol = ? ORDER BY timestamp DESC LIMIT 1",
                        (ex_key.lower(), sym)
                    ) as cursor:
                        row = await cursor.fetchone()
                        if row:
                            current_prices[sym] = float(row['close'])
                except Exception as e:
                    logger.error(f"Failed to query end candle price for {ex_key}:{sym}: {e}")

        # 4순위: 업비트 API 직접 조회 (메모리에 없었던 업비트 종목들)
        if upbit_symbols:
            needed_upbit = [s for s in upbit_symbols if s not in current_prices]
            if needed_upbit:
                import aiohttp
                try:
                    formatted = [f"KRW-{s}" if not s.startswith("KRW-") else s for s in needed_upbit]
                    url = f"https://api.upbit.com/v1/ticker?markets={','.join(formatted)}"
                    async with aiohttp.ClientSession() as session:
                        async with session.get(url) as resp:
                            if resp.status == 200:
                                tickers = await resp.json()
                                for t in tickers:
                                    clean_sym = t['market'].replace("KRW-", "")
                                    current_prices[clean_sym] = float(t['trade_price'])
                except Exception as e:
                    logger.error(f"Failed to fetch upbit prices for {needed_upbit}: {e}")

        # 최종 폴백: 여전히 없는 종목들은 포지션의 평균 매수가로 채움
        for pos_key, pos in portfolio.positions.items():
            if pos.quantity <= 0:
                continue
            _, sym = pos_key
            if sym not in current_prices:
                current_prices[sym] = pos.avg_price

        return current_prices

    async def get_portfolio_report_data(self, portfolio_id: str, system) -> dict:
        """
        포트폴리오의 실시간/정적 성과 통계 및 요약 보고서 데이터를 빌드합니다.
        기존 backtest.py와 portfolio-adapter.js에 파편화되어 있던 성과 데이터 구조를 단일화합니다.
        실제 데이터 가공 및 성과 통계 계산은 PerformanceAnalyzer로 위임합니다.
        """
        portfolio = self.portfolios.get(portfolio_id)
        if not portfolio:
            return {
                "status": "success",
                "id": "",
                "portfolio_id": "",
                "name": "등록된 포트폴리오 없음",
                "initial_cash": 0.0,
                "cash": 0.0,
                "total_value": 0.0,
                "type": "none",
                "exchanges": [],
                "positions": [],
                "history": [],
                "summary": {
                    "initial_cash": 0.0,
                    "final_value": 0.0,
                    "profit": 0.0,
                    "roi": 0.0,
                    "fee": 0.0,
                    "trade_count": 0
                },
                "results": []
            }

        # 1. 최신 시세 획득
        current_prices = await self.get_portfolio_current_prices(portfolio_id, system)

        # 2. 저장소 레이어를 통해 시간 오름차순으로 정렬된 거래 내역 조회
        trades = await self.repository.get_orders_history(portfolio_id)

        # 3. PerformanceAnalyzer 위임 호출
        from src.engine.performance_analyzer import PerformanceAnalyzer
        return PerformanceAnalyzer.calculate_report(
            portfolio=portfolio,
            trades=trades,
            current_prices=current_prices
        )
