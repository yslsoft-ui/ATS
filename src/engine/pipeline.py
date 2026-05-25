from src.engine.utils.telemetry import get_logger
import time
from typing import Optional, Callable, Dict, Any, Tuple
from src.engine.portfolio import PortfolioManager
from src.database.connection import get_db_conn

logger = get_logger(__name__)

class ExecutionPipeline:
    """
    전략 신호를 수신하여 리스크 검증, 슬리피지 보정, 포지션 사이징, 알림까지의 전 과정을 총괄하는 파이프라인입니다.
    (Deep Module: 인터페이스는 단순하지만 내부에서 복잡한 실행 로직을 처리)
    """
    def __init__(self, portfolio_manager: PortfolioManager):
        self.portfolio_manager = portfolio_manager
        self.broadcast_callback: Optional[Callable] = None

    def set_broadcast_callback(self, callback: Callable):
        self.broadcast_callback = callback

    def calculate_position_size(self, portfolio, signal, price: float, size_ratio: Optional[float] = None) -> Tuple[float, float]:
        """포지션의 수량과 예정 진입 가치를 정밀 계산합니다."""
        action = signal.action
        
        if action == 'BUY':
            # size_ratio가 직접 지정되었으면 이를 우선 사용
            if size_ratio is not None:
                ratio = size_ratio
            else:
                context = getattr(signal, 'context', {}) or {}
                ratio = context.get('weight', context.get('ratio', 0.1))
            
            # 비율 범위 제한 (0.0 < ratio <= 1.0)
            if not (0.0 < ratio <= 1.0):
                ratio = 0.1
                
            ex_key = (getattr(signal, 'exchange', None) or portfolio.exchange_id or 'upbit').lower()
            available_cash = portfolio.exchange_cash.get(ex_key, portfolio.cash) if getattr(portfolio, 'exchange_cash', None) else portfolio.cash
            
            target_value = available_cash * ratio
            quantity = target_value / price
            return quantity, target_value
            
        elif action == 'SELL':
            ex_key = (getattr(signal, 'exchange', None) or portfolio.exchange_id or 'upbit').lower()
            pos = portfolio.positions.get((ex_key, signal.symbol))
            if not pos or pos.quantity <= 0:
                return 0.0, 0.0
            
            # 보유 수량 전량 매도
            quantity = pos.quantity
            target_value = quantity * price
            return quantity, target_value
            
        return 0.0, 0.0

    def check_risk_limits(self, portfolio, signal, price: float, qty: float, target_value: float, risk_limits_enabled: bool = True) -> Tuple[bool, str]:
        """포지션 진입 전에 리스크 한도 필터를 실행합니다."""
        if not risk_limits_enabled:
            return True, ""

        action = signal.action
        if action != 'BUY':
            return True, ""

        if qty <= 0 or target_value <= 0:
            return False, "유효하지 않은 주문 수량"

        ex_key = (getattr(signal, 'exchange', None) or portfolio.exchange_id or 'upbit').lower()
        available_cash = portfolio.exchange_cash.get(ex_key, portfolio.cash) if getattr(portfolio, 'exchange_cash', None) else portfolio.cash

        # 1. 사용 가능 현금 검사
        exchange_config = self.portfolio_manager.exchange_configs.get(portfolio.exchange_id, {})
        fee_rate = exchange_config.get('fee_rate', 0.0005)
        total_cost = target_value * (1 + fee_rate)

        if total_cost > available_cash:
            return False, f"잔고 부족 (소요 현금: {total_cost:,.0f} > 보유 현금: {available_cash:,.0f})"

        # 2. 단일 종목 투자 한도 검사 (최대 30%)
        # 자산 평가를 위한 종목별 현재가 사전 구성 (기존 포지션 평균 단가 기반)
        current_prices = {pos.symbol: pos.avg_price for pos in portfolio.positions.values()}
        current_prices[signal.symbol] = price  # 진입할 종목 현재가 주입
        
        total_portfolio_value = portfolio.get_total_value(current_prices)
        if total_portfolio_value <= 0:
            total_portfolio_value = portfolio.initial_cash

        ex_key = (getattr(signal, 'exchange', None) or portfolio.exchange_id or 'upbit').lower()
        pos_key = (ex_key, signal.symbol)
        existing_qty = portfolio.positions[pos_key].quantity if pos_key in portfolio.positions else 0
        predicted_asset_value = (existing_qty * price) + target_value
        
        weight = predicted_asset_value / total_portfolio_value
        max_weight_limit = 0.30  # 단일 종목 최대 한도 30%

        if weight > max_weight_limit:
            return False, f"단일 종목 투자 한도(30%) 초과 (예정 비중: {weight * 100:.1f}%)"

        return True, ""

    def apply_slippage(self, signal, price: float, slippage_rate: float = 0.001) -> float:
        """가상 체결 시 시뮬레이션 현실성을 위한 슬리피지를 적용합니다."""
        action = signal.action
        if slippage_rate <= 0:
            return price
            
        if action == 'BUY':
            # 매수 시에는 시세보다 비싸게 체결
            return price * (1 + slippage_rate)
        elif action == 'SELL':
            # 매도 시에는 시세보다 저렴하게 체결
            return price * (1 - slippage_rate)
            
        return price

    async def process_signal(self, signal: Any, price: float, orderbook: Optional[Dict] = None, portfolio_id: Optional[str] = None, risk_limits_enabled: bool = True, slippage_rate: float = 0.001, size_ratio: Optional[float] = None) -> Optional[Dict]:
        """
        신호를 수신하여 실행 파이프라인의 오케스트레이션 과정을 가동합니다.
        """
        symbol = signal.symbol
        action = signal.action
        exchange = getattr(signal, 'exchange', 'upbit')
        
        logger.info(f"Processing {action} signal for {symbol} (Strategy: {getattr(signal, 'strategy_id', 'unknown')})")

        # 1. 신호 액션 검증
        if action not in ['BUY', 'SELL']:
            logger.warning(f"Invalid action {action} for {symbol}")
            return None

        # 2. 거래소 포트폴리오 안전 라우팅
        if portfolio_id is None or portfolio_id in ['default', 'stock_default', 'bithumb_default']:
            portfolio = self.portfolio_manager.get_active_simulation_portfolio()
            if not portfolio:
                logger.warning("활성화된 실시간 모의투자 세션이 없어 주문 신호가 무시(Skip)되었습니다.")
                await self._broadcast_skip(signal, "활성 모의투자 세션 없음")
                return None
        else:
            portfolio = self.portfolio_manager.portfolios.get(portfolio_id)
            if not portfolio:
                logger.error(f"Portfolio {portfolio_id} not found.")
                return None

        # 3. 정밀 포지션 사이징 계산
        qty, target_value = self.calculate_position_size(portfolio, signal, price, size_ratio=size_ratio)
        if qty <= 0.0:
            # 매도할 포지션이 없거나 수량이 0인 경우 통과
            if action == 'SELL':
                logger.debug(f"SELL signal ignored: No existing position for {symbol}")
            return None

        # 4. 리스크 한도(Risk Limits) 필터 실행
        passed, skip_reason = self.check_risk_limits(portfolio, signal, price, qty, target_value, risk_limits_enabled=risk_limits_enabled)
        if not passed:
            logger.warning(f"Trade signal SKIPPED for {symbol}: {skip_reason}")
            await self._broadcast_skip(signal, skip_reason)
            return None

        # 5. 현실적인 슬리피지 보정 적용
        execution_price = self.apply_slippage(signal, price, slippage_rate=slippage_rate)

        # 6. 정제된 가격과 수량으로 최종 매매 주문 지시
        try:
            result = await self.portfolio_manager.execute_pipeline_order(
                portfolio_id=portfolio.id,
                signal=signal,
                quantity=qty,
                execution_price=execution_price,
                orderbook_data=orderbook
            )
            
            if not result:
                logger.warning(f"Execution failed or skipped inside PortfolioManager for {symbol}")
                return None

            # 7. 성공 시 알림 생성 및 처리
            await self._handle_notifications(result, signal)
            return result
            
        except Exception as e:
            logger.error(f"Execution Error for {symbol}: {str(e)}")
            return None

    async def _broadcast_skip(self, signal: Any, reason: str):
        """리스크 및 규칙 위반으로 거래가 취소(Skip)되었음을 UI에 공유합니다."""
        symbol = signal.symbol
        exchange = getattr(signal, 'exchange', 'upbit')
        
        msg = f"⚠️ [매매보류] {symbol} 주문 보류 ({reason})"
        alert = {
            "type": "alert",
            "alert_type": "skip",
            "exchange": exchange,
            "code": symbol,
            "price": 0.0,
            "msg": msg,
            "timestamp": int(time.time() * 1000)
        }

        # 1. DB 알림 저장
        await self._save_alert_to_db(alert)

        # 2. 웹소켓 브로드캐스트
        if self.broadcast_callback:
            await self.broadcast_callback(alert)

    async def _handle_notifications(self, result: Dict, signal: Any):
        """거래 발생 알림을 생성하고 저장/전송합니다."""
        symbol = result['symbol']
        exchange = result['exchange']
        action = result['side']
        price = result['price']
        reason = getattr(signal, 'reason', '')
        
        msg = f"🤖 [전략매매] {action} 체결: {symbol} @ {price:,.2f} ({reason})"
        
        alert = {
            "type": "alert",
            "alert_type": "trade",
            "exchange": exchange,
            "code": symbol,
            "price": price,
            "msg": msg,
            "timestamp": int(time.time() * 1000)
        }

        # 1. DB 알림 저장
        await self._save_alert_to_db(alert)

        # 2. 외부 브로드캐스트 (WebSocket 등)
        if self.broadcast_callback:
            await self.broadcast_callback(alert)

    async def _save_alert_to_db(self, alert: Dict):
        """알림을 데이터베이스에 영구 저장합니다."""
        try:
            async with get_db_conn() as db:
                await db.execute(
                    "INSERT INTO alerts (exchange, symbol, price, msg, timestamp) VALUES (?, ?, ?, ?, ?)",
                    (alert['exchange'], alert['code'], alert['price'], alert['msg'], alert['timestamp'])
                )
                await db.commit()
        except Exception as e:
            logger.error(f"Alert Save Error: {e}")
