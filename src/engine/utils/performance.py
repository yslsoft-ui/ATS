import time
from typing import List, Dict, Any

def calculate_performance_metrics(history: List[Dict[str, Any]], initial_cash: float, current_cash: float, positions: Dict[Any, Any], current_prices: Dict[str, float]) -> Dict[str, Any]:
    """
    거래 내역(history)과 포트폴리오 상태를 분석하여 ROI, MDD, Win Rate, Profit Factor, Trade Count를 계산합니다.
    """
    # 1. ROI 계산
    # 현재 포지션 가치 평가
    position_value = 0.0
    for pos_key, pos in positions.items():
        qty = getattr(pos, 'quantity', 0.0) if not isinstance(pos, dict) else pos.get('quantity', 0.0)
        avg_price = getattr(pos, 'avg_price', 0.0) if not isinstance(pos, dict) else pos.get('avg_price', 0.0)
        symbol = getattr(pos, 'symbol', '') if not isinstance(pos, dict) else pos.get('symbol', '')
        
        if qty > 0:
            price = current_prices.get(symbol, avg_price)
            position_value += qty * price
            
    total_value = current_cash + position_value
    roi = ((total_value - initial_cash) / initial_cash * 100) if initial_cash > 0 else 0.0

    # 2. MDD 계산 (Max Drawdown)
    # 거래 이력 순서대로 자산 변화를 추적하여 MDD 산출
    equity_curve = [initial_cash]
    temp_cash = initial_cash
    temp_positions = {}  # (exchange, symbol) -> (quantity, avg_price)
    
    # 시간 순 정렬
    sorted_history = sorted(history, key=lambda x: x.get('timestamp', 0))
    
    for tx in sorted_history:
        ex = (tx.get('exchange_id') or tx.get('exchange') or '').lower()
        sym = tx.get('symbol', '')
        side = tx.get('side', '')
        price = tx.get('price', 0.0)
        qty = tx.get('quantity', 0.0)
        fee = tx.get('fee', 0.0)
        
        pos_key = (ex, sym)
        if pos_key not in temp_positions:
            temp_positions[pos_key] = [0.0, 0.0]  # quantity, avg_price
            
        p_qty, p_avg = temp_positions[pos_key]
        
        if side == 'BUY':
            total_cost = (p_avg * p_qty) + (price * qty)
            p_qty += qty
            if p_qty > 0:
                p_avg = total_cost / p_qty
            temp_cash -= (price * qty) + fee
        else:
            p_qty -= qty
            temp_cash += (price * qty) - fee
            if p_qty <= 0:
                p_qty = 0.0
                p_avg = 0.0
                
        temp_positions[pos_key] = [p_qty, p_avg]
        
        # 현재 시점의 포지션 평가액 계산 (tx 시점의 가격은 tx.price로 근사)
        p_val = 0.0
        for pk, (q, avg) in temp_positions.items():
            if q > 0:
                p_val += q * avg  # 과거 시점의 정확한 종가를 알 수 없으므로 평균단가로 평가
        
        equity_curve.append(temp_cash + p_val)
        
    # 최고점 대비 하락 비율 계산
    max_drawdown = 0.0
    peak = initial_cash
    for val in equity_curve:
        if val > peak:
            peak = val
        if peak > 0:
            drawdown = (peak - val) / peak * 100
            if drawdown > max_drawdown:
                max_drawdown = drawdown

    # 3. Win Rate & Profit Factor 계산
    # 각 SELL 거래에 대해 실현 손익을 계산하여 승률 및 프로핏 팩터 산출
    realized_profits = []
    realized_losses = []
    
    # 간이 손익 매칭 (SELL 발생 시, 해당 포지션의 평단가와 비교)
    temp_positions = {}  # (exchange, symbol) -> (quantity, avg_price)
    for tx in sorted_history:
        ex = (tx.get('exchange_id') or tx.get('exchange') or '').lower()
        sym = tx.get('symbol', '')
        side = tx.get('side', '')
        price = tx.get('price', 0.0)
        qty = tx.get('quantity', 0.0)
        fee = tx.get('fee', 0.0)
        
        pos_key = (ex, sym)
        if pos_key not in temp_positions:
            temp_positions[pos_key] = [0.0, 0.0]
            
        p_qty, p_avg = temp_positions[pos_key]
        
        if side == 'BUY':
            total_cost = (p_avg * p_qty) + (price * qty)
            p_qty += qty
            if p_qty > 0:
                p_avg = total_cost / p_qty
            temp_positions[pos_key] = [p_qty, p_avg]
        else:
            # SELL 거래일 경우 평단가 대비 손익 계산
            # 실현 수익 = (매도가 - 평단가) * 수량 - 수수료
            profit = (price - p_avg) * qty - fee
            if profit > 0:
                realized_profits.append(profit)
            else:
                realized_losses.append(abs(profit))
                
            p_qty -= qty
            if p_qty <= 0:
                p_qty = 0.0
                p_avg = 0.0
            temp_positions[pos_key] = [p_qty, p_avg]
            
    total_closed_trades = len(realized_profits) + len(realized_losses)
    win_rate = (len(realized_profits) / total_closed_trades * 100) if total_closed_trades > 0 else 0.0
    
    sum_profits = sum(realized_profits)
    sum_losses = sum(realized_losses)
    
    if sum_losses == 0.0:
        profit_factor = 999.0 if sum_profits > 0 else 1.0  # 손실이 없고 이익만 있으면 999
    else:
        profit_factor = sum_profits / sum_losses
        
    return {
        "roi": round(roi, 2),
        "mdd": round(max_drawdown, 2),
        "win_rate": round(win_rate, 2),
        "profit_factor": round(profit_factor, 2),
        "trade_count": len(history)
    }
