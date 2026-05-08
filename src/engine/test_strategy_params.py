import unittest
from src.engine.strategy import RSIStrategy
from src.engine.candles import Candle

class TestStrategyParams(unittest.TestCase):
    def test_rsi_parameter_reflection(self):
        """RSI 전략의 파라미터 변경이 신호 발생에 정확히 반영되는지 테스트합니다."""
        
        # 1. 초기화: 상한선 70, 하한선 30
        strategy = RSIStrategy(rsi_window=14, buy_threshold=30.0, sell_threshold=70.0)
        
        # 가상의 캔들 생성 (종가 50,000)
        # IndicatorCalculator에 충분한 데이터를 주입하여 RSI를 특정 값으로 유도하기는 복잡하므로,
        # 내부 계산기의 결과값을 모킹하거나 강제로 임계값을 조정하여 테스트합니다.
        
        # 테스트를 위해 임계값을 극단적으로 조정
        strategy.update_params({"buy_threshold": 60.0})
        self.assertEqual(strategy.buy_threshold, 60.0)
        
        # 2. 로직 반영 확인
        # RSI 계산기에 값을 하나 넣어 RSI를 발생시킴
        # (실제 RSI 수치를 제어하기 위해 여러 번 업데이트)
        for i in range(20):
            price = 50000 + i
            strategy.on_candle(Candle(
                symbol="KRW-BTC", interval=60, timestamp=i*60, 
                open=price, high=price, low=price, close=price, volume=1.0
            ))
            
        # 현재 RSI가 50 부근이라고 가정할 때 (가격이 완만하게 상승 중)
        # buy_threshold가 30일 때는 BUY가 안 떠야 하지만, 60으로 높이면 BUY가 떠야 함.
        
        # 다시 초기화해서 정밀 테스트
        strategy = RSIStrategy(buy_threshold=-1.0) # 도달 불가능한 낮은 임계값
        
        # 가격 하락 시뮬레이션으로 RSI를 낮춤
        for i in range(20):
            price = 100 - i
            res = strategy.on_candle(Candle(
                symbol="KRW-BTC", interval=60, timestamp=i*60, 
                open=price, high=price, low=price, close=price, volume=1.0
            ))
            
        # 임계값이 -1이므로 RSI가 아무리 낮아도 HOLD여야 함
        self.assertEqual(strategy.buy_threshold, -1.0)
        
        # 파라미터 업데이트: 임계값을 80으로 대폭 상향
        strategy.update_params({"buy_threshold": 80.0})
        self.assertEqual(strategy.buy_threshold, 80.0)
        
        # 이제 다음 캔들에서 바로 BUY 신호가 발생해야 함 (RSI는 이미 80보다 낮을 것이므로)
        price = 50
        # 디버깅을 위해 RSI 값 직접 확인
        indicators = strategy.calculator.update(price)
        rsi = indicators.get('rsi')
        print(f"DEBUG: Current RSI = {rsi}, Buy Threshold = {strategy.buy_threshold}")

        res = strategy.on_candle(Candle(
            symbol="KRW-BTC", interval=60, timestamp=1000, 
            open=price, high=price, low=price, close=price, volume=1.0
        ))
        self.assertEqual(res.action, "BUY", f"파라미터가 80으로 변경되었으므로 BUY 신호가 발생해야 합니다. (Reason: {res.reason}, RSI: {rsi})")

if __name__ == '__main__':
    unittest.main()
