from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple, Any
from src.engine.utils.telemetry import get_logger

logger = get_logger(__name__)
from abc import ABC, abstractmethod
import time
import asyncio
from src.database.connection import get_db_conn
from src.database.retry import with_db_retry
from src.engine.matching import OrderbookMatchingEngine

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

    def update_position(self, exchange: str, symbol: str, side: str, price: float, quantity: float, fee: float, strategy_id: str = "", reason: str = "", context: Dict = None):
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

class VirtualExecutor(OrderExecutor):
    """
    OrderbookMatchingEngine을 사용하여 가상 주문을 체결합니다.
    """
    def __init__(self, default_fee_rate: float = 0.0005):
        # 기본 수수료율은 설정되어 있으나, 실행 시 포트폴리오 설정을 우선함
        self.matching_engine = OrderbookMatchingEngine(fee_rate=default_fee_rate)

    def set_fee_rate(self, fee_rate: float):
        """실행 시점에 수수료율을 동적으로 변경합니다."""
        self.matching_engine.fee_rate = fee_rate

    async def execute_order(self, exchange: str, symbol: str, side: str, quantity: float, **kwargs) -> Optional[Dict]:
        orderbook = kwargs.get('orderbook')
        trade_price = kwargs.get('trade_price')
        
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
            logger.error(f"VirtualExecutor: Both orderbook and trade_price missing for {symbol}")
            return None
        
        if vwap == 0 or executed_qty <= 0:
            return None
        
        return {
            'exchange': exchange,
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
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path
        self.portfolios: Dict[str, Portfolio] = {}
        self.exchange_configs: Dict[str, Dict] = {} # [NEW] 거래소별 수수료 등 설정 캐시
        self.executors: Dict[str, OrderExecutor] = {
            'simulation': VirtualExecutor()
        }

    def add_portfolio(self, portfolio: Portfolio):
        self.portfolios[portfolio.id] = portfolio

    def get_active_simulation_portfolio(self) -> Optional[Portfolio]:
        """현재 활성화된(즉 type이 'simulation'인) 가장 최근의 모의투자 포트폴리오 객체를 반환합니다."""
        sim_ports = [p for p in self.portfolios.values() if p.portfolio_type == 'simulation']
        if not sim_ports:
            return None
        # ID가 simulation_1716629910 형식의 타임스탬프 기반이므로 정렬하여 가장 최신 것을 반환
        sim_ports.sort(key=lambda x: x.id, reverse=True)
        return sim_ports[0]

    def get_portfolio_summary(self, symbol: str, portfolio_id: str = "default", exchange: Optional[str] = None) -> Dict[str, Any]:
        """
        특정 포트폴리오의 현재 현금 및 특정 종목의 포지션 요약을 반환합니다.
        (전략 컨텍스트 공급용)
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

    async def liquidate_all(self, portfolio_id: str) -> List[Dict]:
        """포트폴리오의 모든 포지션을 즉시 시장가로 청산합니다."""
        portfolio = self.portfolios.get(portfolio_id)
        if not portfolio:
            return []
            
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
                    reason="전체 청산 (Liquidate All)"
                )
                results.append(result)
                
        return results

    @with_db_retry()
    async def execute_pipeline_order(self, portfolio_id: str, signal, quantity: float, execution_price: float, orderbook_data: Optional[Dict] = None):
        """
        ExecutionPipeline에 의해 계산되고 검증 완료된 주문을 실제로 실행하고 영구 저장합니다.
        """
        portfolio = self.portfolios.get(portfolio_id)
        if not portfolio:
            logger.error(f"Portfolio {portfolio_id} not found for executing pipeline order.")
            return None

        # 거래소 수수료율 적용 (다중 종목 대응을 위해 signal.exchange 우선 고려)
        exchange_key = getattr(signal, 'exchange', portfolio.exchange_id)
        if not exchange_key or exchange_key == 'all':
            exchange_key = portfolio.exchange_id
            
        exchange_config = self.exchange_configs.get(exchange_key, {})
        fee_rate = exchange_config.get('fee_rate', 0.0005)
        
        executor = self.executors.get('simulation')
        if isinstance(executor, VirtualExecutor):
            executor.set_fee_rate(fee_rate)

        result = await executor.execute_order(
            exchange=signal.exchange,
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
                context=getattr(signal, 'context', {})
            )
            
            logger.info(f"TRADE EXECUTION: {portfolio.name}: {result['side']} {result['symbol']} @ {result['price']:.2f} (Qty: {result['quantity']:.4f})")
            
            async with get_db_conn(self.db_path) as db:
                import json
                await db.execute('''
                    INSERT INTO orders_history (portfolio_id, exchange, strategy_id, symbol, side, price, quantity, fee, timestamp, reason, context)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    portfolio_id, 
                    result['exchange'],
                    getattr(signal, 'strategy_id', ""),
                    result['symbol'], 
                    result['side'], 
                    result['price'], 
                    result['quantity'], 
                    result['fee'], 
                    int(time.time()), 
                    getattr(signal, 'reason', ""),
                    json.dumps(result.get('context', {}) or getattr(signal, 'context', {}))
                ))
                await db.commit()
                
            await self.save_to_db(portfolio_id)
            return result
        return None

    async def handle_signal(self, portfolio_id: str, signal, trade_price: float, orderbook_data: Optional[Dict] = None):
        """
        [DEPRECATED] 하위 호환성을 유지하기 위한 래퍼입니다. 
        실제 주문 처리는 ExecutionPipeline.process_signal()을 통하시기 바랍니다.
        """
        if not portfolio_id or portfolio_id in ['stock_default', 'bithumb_default']:
            portfolio_id = 'default'

        portfolio = self.portfolios.get(portfolio_id)
        if not portfolio:
            portfolio = self.portfolios.get('default')
            if not portfolio: return None

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

        async with get_db_conn(self.db_path) as db:
            # 1. 포트폴리오 기본 정보 저장
            await db.execute('''
                INSERT INTO portfolios (id, name, type, exchange_id, initial_cash, cash, strategy_info, duration, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    type = excluded.type,
                    exchange_id = excluded.exchange_id,
                    initial_cash = excluded.initial_cash,
                    cash = excluded.cash,
                    strategy_info = excluded.strategy_info,
                    duration = COALESCE(excluded.duration, portfolios.duration),
                    updated_at = datetime('now')
            ''', (
                portfolio.id,
                portfolio.name,
                portfolio.portfolio_type,
                portfolio.exchange_id,
                portfolio.initial_cash,
                portfolio.cash,
                getattr(portfolio, 'strategy_info', ''),
                getattr(portfolio, 'duration', None)
            ))

            # 1.5. 거래소별 격리 자금 정보 저장 (portfolio_exchanges)
            if hasattr(portfolio, 'exchange_cash') and portfolio.exchange_cash:
                for ex_id, ex_cash in portfolio.exchange_cash.items():
                    await db.execute('''
                        INSERT INTO portfolio_exchanges (portfolio_id, exchange_id, initial_cash, cash, updated_at)
                        VALUES (?, ?, 10000000.0, ?, datetime('now'))
                        ON CONFLICT(portfolio_id, exchange_id) DO UPDATE SET cash = ?, updated_at = datetime('now')
                    ''', (portfolio_id, ex_id, ex_cash, ex_cash))

            # 2. 현재 포지션 정보 저장 (기존 포지션 삭제 후 재삽입)
            await db.execute("DELETE FROM positions WHERE portfolio_id = ?", (portfolio_id,))
            for pos in portfolio.positions.values():
                if pos.quantity > 0:
                    await db.execute('''
                        INSERT INTO positions (portfolio_id, exchange, symbol, quantity, avg_price, updated_at)
                        VALUES (?, ?, ?, ?, ?, datetime('now'))
                    ''', (portfolio_id, pos.exchange, pos.symbol, pos.quantity, pos.avg_price))
            
            await db.commit()

    async def load_exchange_configs(self):
        """DB에서 거래소 설정을 로드하여 메모리에 캐싱합니다."""
        async with get_db_conn(self.db_path) as db:
            async with db.execute("SELECT * FROM exchanges") as cursor:
                async for row in cursor:
                    self.exchange_configs[row['id']] = dict(row)
        logger.info(f"{len(self.exchange_configs)}개의 거래소 설정을 로드했습니다.")

    async def load_from_db(self, exclude_types: list = None):
        """DB에서 저장된 포트폴리오 정보를 불러옵니다."""
        await self.load_exchange_configs() # 거래소 설정 먼저 로드

        loaded_portfolios = {}
        async with get_db_conn(self.db_path) as db:
            # 1. 포트폴리오 로드
            query = "SELECT * FROM portfolios"
            if exclude_types:
                # 안전한 IN 절 생성
                placeholders = ",".join(["?"] * len(exclude_types))
                query += f" WHERE type NOT IN ({placeholders})"
                cursor = await db.execute(query, exclude_types)
            else:
                cursor = await db.execute(query)

            async with cursor:
                async for row in cursor:
                    p = Portfolio(
                        portfolio_id=row['id'], 
                        name=row['name'], 
                        initial_cash=row['initial_cash'], 
                        exchange_id=row['exchange_id'],
                        portfolio_type=row['type'],
                        strategy_info=row['strategy_info'] if 'strategy_info' in row.keys() else ""
                    )
                    p.cash = row['cash']
                    loaded_portfolios[p.id] = p
            
            # 2. 각 포트폴리오의 포지션 및 거래소 격리 자금 로드
            for pid, p in loaded_portfolios.items():
                # 2.1. portfolio_exchanges 로드
                p.exchange_cash = {}
                async with db.execute("SELECT exchange_id, cash FROM portfolio_exchanges WHERE portfolio_id = ?", (pid,)) as cursor:
                    async for row in cursor:
                        p.exchange_cash[row['exchange_id']] = row['cash']
                
                # 2.2. positions 로드
                async with db.execute("SELECT * FROM positions WHERE portfolio_id = ?", (pid,)) as cursor:
                    async for row in cursor:
                        ex_val = row['exchange'] if row['exchange'] else 'upbit'
                        p.positions[(ex_val.lower(), row['symbol'])] = Position(
                             exchange=ex_val,
                             symbol=row['symbol'],
                             quantity=row['quantity'],
                             avg_price=row['avg_price'],
                             updated_at=time.time() 
                        )
                
                # 3. 최근 거래 내역 로드 (최근 100건)
                async with db.execute("SELECT * FROM orders_history WHERE portfolio_id = ? ORDER BY timestamp DESC LIMIT 100", (pid,)) as cursor:
                    rows = await cursor.fetchall()
                    p.history = [dict(r) for r in reversed(rows)]
        
        # 원자적 참조 교체로 메모리 동기화 및 기존 stale 세션 날리기 완수
        self.portfolios = loaded_portfolios
        logger.info(f"{len(self.portfolios)}개의 포트폴리오를 DB에서 로드했습니다.")
