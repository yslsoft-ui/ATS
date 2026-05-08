from typing import List, Dict, Tuple

class OrderbookMatchingEngine:
    """
    호가창(Orderbook) 데이터를 기반으로 현실적인 슬리피지와 수수료를 반영하여
    가중 평균 체결가(VWAP)를 계산하는 체결 엔진입니다.
    """
    def __init__(self, fee_rate: float = 0.0005):
        # 기본 수수료 0.05% (Upbit 기준)
        self.fee_rate = fee_rate

    def execute_market_order(self, order_type: str, quantity: float, orderbook_asks: List[Dict], orderbook_bids: List[Dict]) -> Tuple[float, float, float]:
        """
        시장가 주문 시뮬레이션을 수행합니다.
        
        :param order_type: 'BUY' (매수) 또는 'SELL' (매도)
        :param quantity: 거래하고자 하는 목표 수량
        :param orderbook_asks: 매도 호가 리스트 [{'price': 100, 'size': 1.0}, ...] (매수 시 사용)
        :param orderbook_bids: 매수 호가 리스트 [{'price': 99, 'size': 1.0}, ...] (매도 시 사용)
        :return: (가중 평균 체결가, 발생한 현금 흐름(수수료 반영), 체결 실패한 미체결 잔량)
        """
        remaining_qty = quantity
        total_cost = 0.0  # (가격 * 수량)의 누적합
        
        if order_type == 'BUY':
            # 매수 시: 매도 호가(Asks)를 가장 싼 가격부터 위로 긁어모음 (오름차순 정렬)
            target_book = sorted(orderbook_asks, key=lambda x: x['price'])
        elif order_type == 'SELL':
            # 매도 시: 매수 호가(Bids)를 가장 비싼 가격부터 아래로 던짐 (내림차순 정렬)
            target_book = sorted(orderbook_bids, key=lambda x: x['price'], reverse=True)
        else:
            raise ValueError("order_type must be 'BUY' or 'SELL'")

        for level in target_book:
            if remaining_qty <= 0:
                break
            
            price = level['price']
            available_size = level['size']
            
            # 현재 호가에서 체결 가능한 실제 수량
            exec_qty = min(remaining_qty, available_size)
            
            total_cost += price * exec_qty
            remaining_qty -= exec_qty

        # 실제 체결된 총 수량
        executed_qty = quantity - remaining_qty
        
        if executed_qty == 0:
            return 0.0, 0.0, quantity

        # 가중 평균 체결가 (VWAP)
        vwap_price = total_cost / executed_qty
        
        # 현금 흐름 및 수수료 계산
        if order_type == 'BUY':
            # 매수: 계좌에서 현금이 빠져나감 (음수 처리, 수수료 더함)
            final_cash_flow = -(total_cost * (1 + self.fee_rate))
        else:
            # 매도: 계좌로 현금이 들어옴 (양수 처리, 수수료 뺌)
            final_cash_flow = total_cost * (1 - self.fee_rate)

        return vwap_price, final_cash_flow, remaining_qty
