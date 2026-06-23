from src.engine.utils.telemetry import get_logger
import time
from typing import Optional, Callable, Dict, Any, Tuple
from src.engine.portfolio import PortfolioManager
from src.database.repository import BaseTradingRepository
from src.engine.execution_scorer import ExecutionScorer

logger = get_logger(__name__)

class ExecutionPipeline:
    """
    전략 신호를 수신하여 리스크 검증, 슬리피지 보정, 포지션 사이징, 알림까지의 전 과정을 총괄하는 파이프라인입니다.
    (Deep Module: 인터페이스는 단순하지만 내부에서 복잡한 실행 로직을 처리)
    """
    def __init__(
        self,
        portfolio_manager: PortfolioManager,
        notification_service: Any,
        repository: Optional[BaseTradingRepository] = None
    ):
        if notification_service is None:
            raise ValueError("ExecutionPipeline: notification_service 의존성이 누락되었습니다. (Fail-Fast)")
            
        self.portfolio_manager = portfolio_manager
        self.repository = repository or portfolio_manager.repository
        self.broadcast_callback: Optional[Callable] = None
        self.scorer = ExecutionScorer()
        self.notification_service = notification_service

    def set_broadcast_callback(self, callback: Callable):
        self.broadcast_callback = callback

    def calculate_position_size(self, portfolio, signal, price: float, size_ratio: Optional[float] = None) -> Tuple[float, float]:
        """포지션의 수량과 예정 진입 가치를 정밀 계산합니다. (ExecutionScorer로 위임)"""
        return self.scorer.calculate_position_size(portfolio, signal, price, size_ratio=size_ratio)

    def check_risk_limits(self, portfolio, signal, price: float, qty: float, target_value: float, risk_limits_enabled: bool = True) -> Tuple[bool, str]:
        """포지션 진입 전에 리스크 한도 필터를 실행합니다. (ExecutionScorer로 위임)"""
        ex_id = getattr(signal, 'exchange_id', None)
        if not ex_id:
            raise ValueError("리스크 검증 중 신호의 exchange_id가 누락되었습니다.")
        exchange_config = self.portfolio_manager.exchange_configs.get(ex_id, {})
        fee_rate = exchange_config.get('fee_rate', 0.0005)
        return self.scorer.check_risk_limits(
            portfolio=portfolio,
            signal=signal,
            price=price,
            qty=qty,
            target_value=target_value,
            fee_rate=fee_rate,
            risk_limits_enabled=risk_limits_enabled
        )

    def apply_slippage(self, signal, price: float, slippage_rate: float = 0.001) -> float:
        """가상 체결 시 시뮬레이션 현실성을 위한 슬리피지를 적용합니다. (ExecutionScorer로 위임)"""
        return self.scorer.apply_slippage(signal, price, slippage_rate=slippage_rate)

    async def process_signal(self, signal: Any, price: float, orderbook: Optional[Dict] = None, portfolio_id: Optional[str] = None, risk_limits_enabled: bool = True, slippage_rate: float = 0.001, size_ratio: Optional[float] = None) -> Optional[Dict]:
        """
        신호를 수신하여 실행 파이프라인의 오케스트레이션 과정을 가동합니다.
        """
        symbol = signal.symbol
        action = signal.action
        
        exchange_id = getattr(signal, 'exchange_id', None)
        if not exchange_id:
            raise ValueError("process_signal: 신호에 exchange_id가 제공되지 않았습니다.")
        
        logger.info(f"Processing {action} signal for {symbol} (Strategy: {getattr(signal, 'strategy_id', 'unknown')}, Exchange: {exchange_id})")

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

        # [Fail-Stop] 포트폴리오 비정상 상태 체크
        if portfolio.status in ("ERROR", "PAUSED"):
            logger.warning(f"[Pipeline] 주문 전송 차단: 포트폴리오 {portfolio.id}가 현재 {portfolio.status} 상태입니다.")
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
        """리스크 및 규칙 위반으로 거래가 취소(Skip)되었음을 알림 서비스를 통해 발행합니다."""
        symbol = signal.symbol
        exchange_id = getattr(signal, 'exchange_id', None)
        if not exchange_id:
            raise ValueError("_broadcast_skip: exchange_id가 누락되었습니다.")

        # 동적 수치가 포함될 수 있는 reason 문자열을 고정된 대표 키값으로 정규화
        skip_reason_code = "risk.unknown_blocked"
        if reason.startswith("잔고 부족"):
            skip_reason_code = "risk.insufficient_balance"
        elif reason.startswith("단일 종목 투자 한도"):
            skip_reason_code = "risk.max_position_blocked"
        elif "daily" in reason.lower() or "일일" in reason:
            skip_reason_code = "risk.daily_loss_blocked"

        portfolio = self.portfolio_manager.get_active_simulation_portfolio()
        portfolio_id = portfolio.id if portfolio else "default"

        await self.notification_service.publish(
            notification_type="skip",
            level="WARNING",
            code=skip_reason_code,
            message=f"⚠️ [매매보류] {symbol} 주문 보류 ({reason})",
            target=f"portfolio:{portfolio_id}/exchange:{exchange_id}/symbol:{symbol}",
            context={"raw_reason": reason}
        )

    async def _handle_notifications(self, result: Dict, signal: Any):
        """거래 발생 알림을 알림 서비스를 통해 발행합니다."""
        symbol = result['symbol']
        exchange_id = result['exchange_id']
        action = result['side']
        price = result['price']
        reason = getattr(signal, 'reason', '')
        
        portfolio_id = result.get('portfolio_id', 'unknown')

        await self.notification_service.publish(
            notification_type="trade",
            level="SUCCESS",
            code=f"trade.{action.lower()}",
            message=f"🤖 [전략매매] {action} 체결: {symbol} @ {price:,.2f} ({reason})",
            target=f"portfolio:{portfolio_id}/exchange:{exchange_id}/symbol:{symbol}",
            context={
                "price": price,
                "quantity": result.get("quantity", 0.0),
                "side": action,
                "strategy_id": getattr(signal, 'strategy_id', 'unknown'),
                "reason": reason
            }
        )

