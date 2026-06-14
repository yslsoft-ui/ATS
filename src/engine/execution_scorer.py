from typing import Optional, Tuple, Any
from src.engine.utils.telemetry import get_logger

logger = get_logger(__name__)

class ExecutionScorer:
    """
    ExecutionPipeline에서 분리되어 포지션 사이징, 리스크 필터링, 슬리피지 보정을 전담하는
    상태가 없는(Stateless) 순수 비즈니스 계산 모듈입니다.
    DB, Repository, PortfolioManager, WebSocket에 직접 의존하지 않습니다.
    """

    def calculate_position_size(self, portfolio, signal: Any, price: float, size_ratio: Optional[float] = None) -> Tuple[float, float]:
        """
        포지션의 수량과 예정 진입 가치를 정밀 계산합니다.
        
        Args:
            portfolio: 포트폴리오 인스턴스 (cash, positions, exchange_id 등 보유)
            signal: 신호 객체 (action, symbol, exchange, context 등 보유)
            price: 현재 진입 희망가
            size_ratio: 수동 지정 비중 비율 (기본 None)
            
        Returns:
            Tuple[float, float]: (주문 수량, 예정 진입 가치)
        """
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
                
            ex_id = getattr(signal, 'exchange_id', None)
            if not ex_id:
                raise ValueError("주문 수량 계산 중 신호의 exchange_id가 누락되었습니다.")
            ex_key = ex_id.lower()
            if ex_key not in portfolio.exchange_cash:
                raise KeyError(f"포트폴리오에 '{ex_id}' 거래소 현금이 등록되어 있지 않습니다.")
            available_cash = portfolio.exchange_cash[ex_key]
            
            target_value = available_cash * ratio
            quantity = target_value / price
            return quantity, target_value
            
        elif action == 'SELL':
            ex_id = getattr(signal, 'exchange_id', None)
            if not ex_id:
                raise ValueError("주문 수량 계산 중 신호의 exchange_id가 누락되었습니다.")
            ex_key = ex_id.lower()
            pos = portfolio.positions.get((ex_key, signal.symbol))
            if not pos or pos.quantity <= 0:
                return 0.0, 0.0
            
            # 보유 수량 전량 매도
            quantity = pos.quantity
            target_value = quantity * price
            return quantity, target_value
            
        return 0.0, 0.0

    def check_risk_limits(self, portfolio, signal: Any, price: float, qty: float, target_value: float, fee_rate: float, risk_limits_enabled: bool = True) -> Tuple[bool, str]:
        """
        포지션 진입 전에 리스크 한도 필터를 실행합니다.
        
        Args:
            portfolio: 포트폴리오 인스턴스
            signal: 신호 객체
            price: 현재가
            qty: 계산된 주문 수량
            target_value: 계산된 예정 진입 가치
            fee_rate: 거래소 수수료율 (ExecutionPipeline에서 전달받음)
            risk_limits_enabled: 리스크 한도 필터 적용 여부
            
        Returns:
            Tuple[bool, str]: (통과 여부, 보류 사유)
        """
        if not risk_limits_enabled:
            return True, ""

        action = signal.action
        if action != 'BUY':
            return True, ""

        if qty <= 0 or target_value <= 0:
            return False, "유효하지 않은 주문 수량"

        ex_id = getattr(signal, 'exchange_id', None)
        if not ex_id:
            raise ValueError("리스크 검증 중 신호의 exchange_id가 누락되었습니다.")
        ex_key = ex_id.lower()
        if ex_key not in portfolio.exchange_cash:
            raise KeyError(f"포트폴리오에 '{ex_id}' 거래소 현금이 등록되어 있지 않습니다.")
        available_cash = portfolio.exchange_cash[ex_key]

        # 1. 사용 가능 현금 검사 (수수료 포함)
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

        pos_key = (ex_key, signal.symbol)
        existing_qty = portfolio.positions[pos_key].quantity if pos_key in portfolio.positions else 0
        predicted_asset_value = (existing_qty * price) + target_value
        
        weight = predicted_asset_value / total_portfolio_value
        max_weight_limit = 0.30  # 단일 종목 최대 한도 30%

        if weight > max_weight_limit:
            return False, f"단일 종목 투자 한도(30%) 초과 (예정 비중: {weight * 100:.1f}%)"

        return True, ""

    def apply_slippage(self, signal: Any, price: float, slippage_rate: float = 0.001) -> float:
        """
        가상 체결 시 시뮬레이션 현실성을 위한 슬리피지를 적용합니다.
        
        Args:
            signal: 신호 객체
            price: 현재가
            slippage_rate: 슬리피지 비율
            
        Returns:
            float: 슬리피지가 반영된 실행(체결) 가격
        """
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
