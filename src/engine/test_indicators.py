import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from src.engine.indicators import IndicatorCalculator

def run_test():
    # 검증을 위해 윈도우 사이즈를 작게(5) 설정
    calc = IndicatorCalculator(window_size=5)
    
    # 가상의 가격 틱 스트림
    prices = [100, 102, 101, 105, 104, 108]
    
    print("=== 실시간 지표 계산 테스트 (Window Size: 5) ===")
    for i, p in enumerate(prices):
        res = calc.update(p)
        print(f"Tick {i+1} (가격: {p}): {res}")

if __name__ == "__main__":
    run_test()
