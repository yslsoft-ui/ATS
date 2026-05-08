import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from src.engine.matching import OrderbookMatchingEngine

def run_test():
    # 검증 편의를 위해 수수료를 0.1% (0.001)로 설정
    engine = OrderbookMatchingEngine(fee_rate=0.001)
    
    # 가상의 호가창 (Orderbook) 데이터
    asks = [
        {"price": 1000, "size": 1.0}, # 가장 싼 매도호가
        {"price": 1010, "size": 2.0},
        {"price": 1020, "size": 5.0}
    ]
    
    bids = [
        {"price": 990, "size": 1.0},  # 가장 비싼 매수호가
        {"price": 980, "size": 2.0},
        {"price": 970, "size": 5.0}
    ]
    
    print("=== Test 1: 시장가 매수 (Market BUY) ===")
    print("목표: 2.0개 매수")
    # 기대결과: 1000원짜리 1.0개 + 1010원짜리 1.0개 소진
    # 총 비용: 1000 + 1010 = 2010원. 평균 체결가(VWAP): 1005원.
    # 최종 현금지출(수수료 0.1%): -2010 * 1.001 = -2012.01원
    vwap, cash_flow, remain = engine.execute_market_order('BUY', 2.0, asks, bids)
    print(f"가중 평균 체결가: {vwap}")
    print(f"발생 현금 흐름: {cash_flow}")
    print(f"미체결 잔량: {remain}\n")

    print("=== Test 2: 시장가 매도 (Market SELL) ===")
    print("목표: 2.0개 매도")
    # 기대결과: 990원짜리 1.0개 + 980원짜리 1.0개 소진
    # 총 수익: 990 + 980 = 1970원. 평균 체결가(VWAP): 985원.
    # 최종 현금수입(수수료 0.1% 차감): 1970 * 0.999 = 1968.03원
    vwap, cash_flow, remain = engine.execute_market_order('SELL', 2.0, asks, bids)
    print(f"가중 평균 체결가: {vwap}")
    print(f"발생 현금 흐름: {cash_flow}")
    print(f"미체결 잔량: {remain}\n")

    print("=== Test 3: 호가창 잔량 부족 (Slippage Extreme) ===")
    print("목표: 10.0개 대량 매수 (호가창 총합은 8.0개)")
    vwap, cash_flow, remain = engine.execute_market_order('BUY', 10.0, asks, bids)
    print(f"가중 평균 체결가: {vwap}")
    print(f"미체결 잔량: {remain} (체결 불가 수량)")

if __name__ == "__main__":
    run_test()
