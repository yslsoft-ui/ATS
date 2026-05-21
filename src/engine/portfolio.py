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
    def __init__(self, portfolio_id: str, name: str, initial_cash: float = 1000000.0, exchange_id: str = 'upbit'):
        self.id = portfolio_id
        self.name = name
        self.exchange_id = exchange_id # [NEW]
        self.initial_cash = initial_cash
        self.cash = initial_cash
        self.positions: Dict[str, Position] = {}
        self.history: List[Dict] = []

    def update_position(self, exchange: str, symbol: str, side: str, price: float, quantity: float, fee: float, strategy_id: str = "", reason: str = "", context: Dict = None):
        """체결된 결과를 바탕으로 포지션과 잔고를 업데이트합니다."""
        if symbol not in self.positions:
            self.positions[symbol] = Position(exchange=exchange, symbol=symbol)
        
        pos = self.positions[symbol]
        
        if side == 'BUY':
            # 매수: 평균 단가 갱신 및 수량 증가
            total_cost = (pos.avg_price * pos.quantity) + (price * quantity)
            pos.quantity += quantity
            if pos.quantity > 0:
                pos.avg_price = total_cost / pos.quantity
            self.cash -= (price * quantity) + fee
        else:
            # 매도: 수량 감소
            pos.quantity -= quantity
            self.cash += (price * quantity) - fee
            if pos.quantity <= 0:
                pos.quantity = 0
                pos.avg_price = 0
        
        pos.updated_at = time.time()
        
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
        pos_value = sum(pos.quantity * current_prices.get(symbol, pos.avg_price) 
                        for symbol, pos in self.positions.items())
        return self.cash + pos_value

class OrderExecutor(ABC):
    """
    주문 실행 인터페이스입니다. (가상/실제 공통)
    """
    @abstractmethod
    async def execute_order(self, portfolio: Portfolio, exchange: str, symbol: str, side: str, quantity: float, **kwargs) -> Optional[Dict]:
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

    async def execute_order(self, portfolio: Portfolio, exchange: str, symbol: str, side: str, quantity: float, **kwargs) -> Optional[Dict]:
        orderbook = kwargs.get('orderbook')
        trade_price = kwargs.get('trade_price')
        strategy_id = kwargs.get('strategy_id', "")
        reason = kwargs.get('reason', "")
        context = kwargs.get('context', {})
        
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
        
        portfolio.update_position(exchange, symbol, side, vwap, executed_qty, fee, strategy_id=strategy_id, reason=reason, context=context)
        
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

    def get_portfolio_summary(self, symbol: str, portfolio_id: str = "default") -> Dict[str, Any]:
        """
        특정 포트폴리오의 현재 현금 및 특정 종목의 포지션 요약을 반환합니다.
        (전략 컨텍스트 공급용)
        """
        portfolio = self.portfolios.get(portfolio_id)
        if not portfolio:
            return {"cash": 0.0, "quantity": 0.0, "avg_price": 0.0}
            
        pos = portfolio.positions.get(symbol)
        return {
            "cash": portfolio.cash,
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
        
        # 현재 보유 중인 모든 종목 추출 (수량이 0보다 큰 것만)
        symbols_to_sell = [s for s, pos in portfolio.positions.items() if pos.quantity > 0]
        
        for symbol in symbols_to_sell:
            pos = portfolio.positions[symbol]
            qty = pos.quantity
            
            # 실시간 가격 정보가 없더라도 가상 체결기에서 ticker 조회 등을 수행하므로 
            # 여기서는 symbol과 수량 정보만 넘겨도 executor가 처리할 수 있도록 설계됨
            # (main.py의 ticker 조회 로직을 executor 내부로 옮기거나 호출 시 주입 필요)
            
            # 간단하게 처리하기 위해 빈 kwargs를 넘기고 executor에서 ticker를 조회하도록 유도하거나
            # handle_signal 처럼 외부에서 주입받는 구조 유지
            result = await executor.execute_order(
                portfolio=portfolio,
                exchange=portfolio.exchange_id,
                symbol=symbol,
                side='SELL',
                quantity=qty,
                trade_price=0 
            )
            if result:
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

        # 거래소 수수료율 적용
        exchange_config = self.exchange_configs.get(portfolio.exchange_id, {})
        fee_rate = exchange_config.get('fee_rate', 0.0005)
        
        executor = self.executors.get('simulation')
        if isinstance(executor, VirtualExecutor):
            executor.set_fee_rate(fee_rate)

        result = await executor.execute_order(
            portfolio=portfolio,
            exchange=signal.exchange,
            symbol=signal.symbol,
            side=signal.action,
            quantity=quantity,
            orderbook=orderbook_data,
            trade_price=execution_price,
            strategy_id=getattr(signal, 'strategy_id', ""),
            reason=getattr(signal, 'reason', ""),
            context=getattr(signal, 'context', {})
        )
        
        if result:
            logger.info(f"TRADE EXECUTION: {portfolio.name}: {result['side']} {result['symbol']} @ {result['price']:.2f} (Qty: {result['quantity']:.4f})")
            
            async with get_db_conn(self.db_path) as db:
                import json
                await db.execute('''
                    INSERT INTO orders_history (portfolio_id, exchange, strategy_id, symbol, side, price, quantity, fee, timestamp, reason, context)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    portfolio_id, 
                    result['exchange'],
                    result.get('strategy_id', ""),
                    result['symbol'], 
                    result['side'], 
                    result['price'], 
                    result['quantity'], 
                    result['fee'], 
                    int(time.time()), 
                    signal.reason if hasattr(signal, 'reason') else "",
                    json.dumps(result.get('context', {}))
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
        if signal.exchange == 'kis':
            portfolio_id = 'stock_default'
        elif signal.exchange == 'upbit':
            portfolio_id = 'default'
        elif signal.exchange == 'bithumb':
            portfolio_id = 'bithumb_default'

        portfolio = self.portfolios.get(portfolio_id)
        if not portfolio:
            portfolio = self.portfolios.get('default')
            if not portfolio: return None

        # 고정 수량 계산
        if signal.action == 'BUY':
            quantity = (portfolio.cash * 0.1) / trade_price
        elif signal.action == 'SELL':
            pos = portfolio.positions.get(signal.symbol)
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
                INSERT OR REPLACE INTO portfolios (id, name, type, exchange_id, initial_cash, cash, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
            ''', (portfolio.id, portfolio.name, 'simulation', portfolio.exchange_id, portfolio.initial_cash, portfolio.cash))

            # 2. 현재 포지션 정보 저장 (기존 포지션 삭제 후 재삽입)
            await db.execute("DELETE FROM positions WHERE portfolio_id = ?", (portfolio_id,))
            for symbol, pos in portfolio.positions.items():
                if pos.quantity > 0:
                    await db.execute('''
                        INSERT INTO positions (portfolio_id, exchange, symbol, quantity, avg_price, updated_at)
                        VALUES (?, ?, ?, ?, ?, datetime('now'))
                    ''', (portfolio_id, pos.exchange, symbol, pos.quantity, pos.avg_price))

            # 3. 거래 히스토리 저장 (최근 내역만 중복되지 않게)
            # (간단하게 하기 위해 모든 history를 저장하거나, execute_order 시점에 한 건씩 저장하는 것이 좋음)
            # 여기서는 execute_order 성공 시 별도로 orders_history에 남기는 로직을 handle_signal에 넣는 것이 더 깔끔함
            
            await db.commit()

    async def load_exchange_configs(self):
        """DB에서 거래소 설정을 로드하여 메모리에 캐싱합니다."""
        async with get_db_conn(self.db_path) as db:
            async with db.execute("SELECT * FROM exchanges") as cursor:
                async for row in cursor:
                    self.exchange_configs[row['id']] = dict(row)
        logger.info(f"{len(self.exchange_configs)}개의 거래소 설정을 로드했습니다.")

    async def load_from_db(self):
        """DB에서 저장된 모든 포트폴리오 정보를 불러옵니다."""
        await self.load_exchange_configs() # 거래소 설정 먼저 로드

        async with get_db_conn(self.db_path) as db:
            # 1. 포트폴리오 로드
            async with db.execute("SELECT * FROM portfolios") as cursor:
                async for row in cursor:
                    p = Portfolio(row['id'], row['name'], row['initial_cash'], row['exchange_id'])
                    p.cash = row['cash']
                    self.portfolios[p.id] = p
            
            # 2. 각 포트폴리오의 포지션 로드
            for pid, p in self.portfolios.items():
                async with db.execute("SELECT * FROM positions WHERE portfolio_id = ?", (pid,)) as cursor:
                    async for row in cursor:
                        p.positions[row['symbol']] = Position(
                            exchange=row['exchange'],
                            symbol=row['symbol'],
                            quantity=row['quantity'],
                            avg_price=row['avg_price'],
                            updated_at=time.time() 
                        )
                
                # 3. 최근 거래 내역 로드 (최근 100건)
                async with db.execute("SELECT * FROM orders_history WHERE portfolio_id = ? ORDER BY timestamp DESC LIMIT 100", (pid,)) as cursor:
                    rows = await cursor.fetchall()
                    p.history = [dict(r) for r in reversed(rows)]
        
        logger.info(f"{len(self.portfolios)}개의 포트폴리오를 DB에서 로드했습니다.")
