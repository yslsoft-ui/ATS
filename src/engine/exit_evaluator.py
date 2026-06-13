import time
from typing import Dict, Tuple, Optional
from src.engine.portfolio import Position
from src.engine.utils.telemetry import get_logger

logger = get_logger(__name__)

class CommonExitEvaluator:
    """
    엔진 레이어에서 공통으로 처리되는 3대 청산 조건(손절, 트레일링 스탑, 시간 제한 탈출) 및 매매비용을 반영한 본전이동(Breakeven Stop)을 평가합니다.
    """
    def __init__(self, config: Dict):
        self.config = config
        self._stop_loss_pct = None
        self._trailing_stop_pct = None
        self._time_limit_seconds = None

    @property
    def exit_rules(self) -> Dict:
        return self.config.get('system', {}).get('exit_rules', {})

    @property
    def stop_loss_pct(self) -> float:
        if self._stop_loss_pct is not None:
            return self._stop_loss_pct
        return float(self.exit_rules.get('stop_loss_pct', 0.0))

    @stop_loss_pct.setter
    def stop_loss_pct(self, value: float):
        self._stop_loss_pct = value

    @property
    def trailing_stop_pct(self) -> float:
        if self._trailing_stop_pct is not None:
            return self._trailing_stop_pct
        return float(self.exit_rules.get('trailing_stop_pct', 0.0))

    @trailing_stop_pct.setter
    def trailing_stop_pct(self, value: float):
        self._trailing_stop_pct = value

    @property
    def time_limit_seconds(self) -> float:
        if self._time_limit_seconds is not None:
            return self._time_limit_seconds
        return float(self.exit_rules.get('time_limit_seconds', 0.0))

    @time_limit_seconds.setter
    def time_limit_seconds(self, value: float):
        self._time_limit_seconds = value

    @property
    def stop_loss_basis(self) -> str:
        return self.exit_rules.get('stop_loss_basis', 'net_pnl')

    @property
    def breakeven_activation_pct(self) -> Optional[float]:
        breakeven_act = self.exit_rules.get('breakeven_activation_pct', None)
        return float(breakeven_act) if breakeven_act is not None else None

    @property
    def execution_costs(self) -> Dict:
        return self.config.get('system', {}).get('execution_cost', {})

    def get_cost_parameters(self, exchange: str) -> Dict:
        """
        거래소별 비용 모델 파라미터를 반환합니다.
        지정되지 않은 거래소인 경우 default 설정을 적용합니다.
        """
        exchange_key = exchange.lower() if exchange else "default"
        costs = self.execution_costs.get(exchange_key)
        if not costs:
            costs = self.execution_costs.get("default", {
                "buy_fee_pct": 0.0,
                "sell_fee_pct": 0.0,
                "sell_tax_pct": 0.0,
                "slippage_pct": 0.0,
                "safety_buffer_pct": 0.0
            })
        return costs

    def calculate_costs(self, exchange: str, avg_price: float, current_price: float) -> Dict:
        """
        비용 관련 상세 지표들을 엄밀하게 계산하여 반환합니다.
        """
        costs = self.get_cost_parameters(exchange)
        buy_fee_pct = float(costs.get("buy_fee_pct", 0.0))
        sell_fee_pct = float(costs.get("sell_fee_pct", 0.0))
        sell_tax_pct = float(costs.get("sell_tax_pct", 0.0))
        slippage_pct = float(costs.get("slippage_pct", 0.0))
        safety_buffer_pct = float(costs.get("safety_buffer_pct", 0.0))
        
        # 1. 진입 비용 포함 가격 (slippage_pct는 매수/매도 각각 1회 적용)
        entry_cost_price = avg_price * (1.0 + (buy_fee_pct + slippage_pct) / 100.0)
        
        # 2. 청산 후 순수령 가격
        exit_net_price = current_price * (1.0 - (sell_fee_pct + sell_tax_pct + slippage_pct) / 100.0)
        
        # 3. 순손익률 (%)
        if entry_cost_price > 0:
            net_pnl_pct = (exit_net_price - entry_cost_price) / entry_cost_price * 100.0
        else:
            net_pnl_pct = 0.0
            
        # 4. 왕복 비용 비율 (%)
        round_trip_cost_pct = buy_fee_pct + sell_fee_pct + sell_tax_pct + (slippage_pct * 2.0)
        
        # 5. 본전이동 최소 현재가 (순수령액 >= 진입비용 * (1 + safety_buffer))
        denom = 1.0 - (sell_fee_pct + sell_tax_pct + slippage_pct) / 100.0
        denom = max(denom, 1e-9)
        breakeven_price_with_cost = (entry_cost_price * (1.0 + safety_buffer_pct / 100.0)) / denom
        
        return {
            "buy_fee_pct": buy_fee_pct,
            "sell_fee_pct": sell_fee_pct,
            "sell_tax_pct": sell_tax_pct,
            "slippage_pct": slippage_pct,
            "safety_buffer_pct": safety_buffer_pct,
            "entry_cost_price": entry_cost_price,
            "exit_net_price": exit_net_price,
            "net_pnl_pct": net_pnl_pct,
            "round_trip_cost_pct": round_trip_cost_pct,
            "breakeven_price_with_cost": breakeven_price_with_cost
        }

    def evaluate(self, pos: Position, current_price: float, current_time: Optional[float] = None) -> Tuple[bool, str]:
        """
        포지션과 현재 가격을 기준으로 청산 여부를 판정합니다.
        
        Args:
            pos: 평가할 포지션 객체
            current_price: 현재 틱 가격
            current_time: 현재 시각 (테스트용으로 수동 입력 가능, 기본값은 time.time())
            
        Returns:
            Tuple[bool, str]: (청산 여부, 청산 사유)
        """
        if pos.quantity <= 0:
            return False, ""
            
        now = current_time if current_time is not None else time.time()
        
        # 비용 및 가격 계산 모델 호출
        cost_info = self.calculate_costs(pos.exchange, pos.avg_price, current_price)
        
        buy_fee_pct = cost_info["buy_fee_pct"]
        sell_fee_pct = cost_info["sell_fee_pct"]
        sell_tax_pct = cost_info["sell_tax_pct"]
        slippage_pct = cost_info["slippage_pct"]
        
        entry_cost_price = cost_info["entry_cost_price"]
        exit_net_price = cost_info["exit_net_price"]
        net_pnl_pct = cost_info["net_pnl_pct"]
        round_trip_cost_pct = cost_info["round_trip_cost_pct"]
        breakeven_price_with_cost = cost_info["breakeven_price_with_cost"]
        
        # 최고가(peak_price) 기준의 가상 순수령액 및 순손익률 계산
        peak_exit_net_price = pos.peak_price * (1.0 - (sell_fee_pct + sell_tax_pct + slippage_pct) / 100.0)
        if entry_cost_price > 0:
            peak_net_pnl_pct = (peak_exit_net_price - entry_cost_price) / entry_cost_price * 100.0
        else:
            peak_net_pnl_pct = 0.0
            
        # 1. Stop Loss 방어선 계산
        stop_loss_floor = 0.0
        if self.stop_loss_pct > 0 and pos.avg_price > 0:
            if self.stop_loss_basis == "price":
                stop_loss_floor = pos.avg_price * (1.0 - self.stop_loss_pct / 100.0)
            else:  # net_pnl
                denom = 1.0 - (sell_fee_pct + sell_tax_pct + slippage_pct) / 100.0
                denom = max(denom, 1e-9)
                stop_loss_floor = entry_cost_price * (1.0 - self.stop_loss_pct / 100.0) / denom
                
        # 2. Breakeven Stop 방어선 계산
        breakeven_floor = 0.0
        if self.breakeven_activation_pct is not None and pos.avg_price > 0:
            if peak_net_pnl_pct >= self.breakeven_activation_pct:
                breakeven_floor = breakeven_price_with_cost
                
        # 3. Trailing Stop 방어선 계산
        trailing_floor = 0.0
        if self.trailing_stop_pct > 0 and pos.peak_price > 0:
            trailing_floor = pos.peak_price * (1.0 - self.trailing_stop_pct / 100.0)
            
        # 4. 청산 방어선 우선순위 선택: 후보군 중 가장 높은 방어선을 채택
        selected_exit_floor = max(stop_loss_floor, breakeven_floor, trailing_floor)
        
        triggered = False
        reason = ""
        
        if selected_exit_floor > 0.0 and current_price <= selected_exit_floor:
            triggered = True
            # 가장 높은 방어선의 사유를 선택
            if selected_exit_floor == stop_loss_floor:
                reason = "STOP_LOSS"
            elif selected_exit_floor == breakeven_floor:
                reason = "BREAKEVEN_STOP"
            elif selected_exit_floor == trailing_floor:
                reason = "TRAILING_STOP"
                
        # 5. 시간 제한 탈출 (가격 청산 조건이 없을 때 별도로 평가)
        if not triggered:
            if self.time_limit_seconds > 0 and pos.entry_time > 0:
                elapsed_time = now - pos.entry_time
                if elapsed_time >= self.time_limit_seconds:
                    triggered = True
                    reason = "TIME_LIMIT"
                    
        # 6. 모든 공통 청산 로그 필드 추가
        if triggered:
            logger.info(
                f"[Common Exit Triggered] "
                f"exchange={pos.exchange}, avg_price={pos.avg_price}, current_price={current_price}, peak_price={pos.peak_price}, "
                f"entry_cost_price={entry_cost_price:.2f}, exit_net_price={exit_net_price:.2f}, "
                f"breakeven_price_with_cost={breakeven_price_with_cost:.2f}, "
                f"net_pnl_pct={net_pnl_pct:.4f}%, peak_net_pnl_pct={peak_net_pnl_pct:.4f}%, "
                f"round_trip_cost_pct={round_trip_cost_pct:.4f}%, selected_exit_floor={selected_exit_floor:.2f}, "
                f"reason={reason}"
            )
            
        return triggered, reason
