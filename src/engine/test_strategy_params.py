import pytest
from src.engine.strategies.rsi_strategy import RSIStrategy
from src.engine.candles import Candle
from src.engine.strategy_host import StrategyHost
from src.engine.market_data_context import MarketDataContext

@pytest.mark.asyncio
async def test_rsi_parameter_reflection():
    """RSI 전략의 파라미터 변경이 신호 발생에 정확히 반영되는지 테스트합니다."""
    
    # 1. 초기화: 상한선 70, 하한선 30
    strategy = RSIStrategy("rsi_test", params={"rsi_window": 14, "buy_threshold": 30.0, "sell_threshold": 70.0})
    host = StrategyHost(strategy, "upbit", "KRW-BTC", 60)
    context = MarketDataContext("upbit", "KRW-BTC", 60)

    # 파라미터 업데이트: 임계값 조정
    strategy.update_params({"buy_threshold": 60.0})
    assert strategy.buy_threshold == 60.0
    
    # 캔들 데이터 채우기 (RSI 계산을 위해 가격이 천천히 상승하는 구조 주입)
    for i in range(25):
        price = 50000 + i
        context.add_candle(Candle(
            exchange="upbit", symbol="KRW-BTC", interval=60, timestamp=i*60, 
            open=price, high=price, low=price, close=price, volume=1.0, is_closed=True
        ))
        await host.execute(context)
        
    # 다시 초기화해서 정밀 테스트 (도달 불가능한 임계값으로 설정)
    strategy = RSIStrategy("rsi_test2", params={"rsi_window": 14, "buy_threshold": -1.0, "sell_threshold": 70.0})
    host = StrategyHost(strategy, "upbit", "KRW-BTC", 60)
    context = MarketDataContext("upbit", "KRW-BTC", 60)
    
    # 가격 하락 시뮬레이션으로 RSI를 낮춤
    for i in range(25):
        price = 100 - i
        context.add_candle(Candle(
            exchange="upbit", symbol="KRW-BTC", interval=60, timestamp=i*60, 
            open=price, high=price, low=price, close=price, volume=1.0, is_closed=True
        ))
        res = await host.execute(context)
        
    # 임계값이 -1이므로 RSI가 아무리 낮아도 신호가 없어야(None) 함
    assert strategy.buy_threshold == -1.0
    assert res is None
    
    # 파라미터 업데이트: 임계값을 80으로 대폭 상향
    strategy.update_params({"buy_threshold": 80.0})
    assert strategy.buy_threshold == 80.0
    
    # 다음 캔들에서 BUY 신호가 발생하는지 검증
    price = 50
    context.add_candle(Candle(
        exchange="upbit", symbol="KRW-BTC", interval=60, timestamp=1000, 
        open=price, high=price, low=price, close=price, volume=1.0, is_closed=True
    ))
    res = await host.execute(context)
    
    # 파라미터가 80으로 상향되었고 가격이 하락했으므로 과매도 BUY 신호 발생 검증
    assert res is not None
    assert res.action == "BUY"
