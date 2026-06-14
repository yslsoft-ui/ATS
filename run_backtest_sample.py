import asyncio
import os
from src.engine.backtest import BacktestEngine
from src.utils.visualizer import plot_backtest_result
from src.engine.strategies.rsi_strategy import RSIStrategy
from src.engine.strategies.macd_strategy import MACDStrategy
from src.engine.strategies.momentum_spike_strategy import MomentumSpikeStrategy

async def run_and_print(engine, exchange_id, symbol, start_date, end_date, initial_cash, strategy_configs, name):
    print(f"\n--- Running {name} Backtest ---")
    result = await engine.run(
        exchange_id=exchange_id,
        symbol=symbol,
        start_date=start_date,
        end_date=end_date,
        initial_cash=initial_cash,
        strategy_configs=strategy_configs,
        risk_limits_enabled=False
    )
    
    if result["status"] == "success":
        summary = result["summary"]
        print(f"Final Value:  {summary['final_value']:,} KRW")
        print(f"ROI:          {summary['roi']}%")
        print(f"Total Trades: {summary['trade_count']}")
        
        # 차트 생성 및 저장
        safe_name = name.lower().replace(' ', '_').replace('/', '_')
        output_filename = f"backtest_{safe_name}.png"
        
        # visualizer expects list of candle objects/dicts and trade dicts
        # we convert list of dicts to visualizer expected structure
        class SimpleCandle:
            def __init__(self, d):
                self.timestamp = d["time"]
                self.open = d["open"]
                self.high = d["high"]
                self.low = d["low"]
                self.close = d["close"]
                self.volume = d["volume"]
        
        candle_objs = [SimpleCandle(c) for c in summary['candle_history']]
        
        plot_backtest_result(
            candle_objs, 
            summary['trades'], 
            name, 
            output_filename
        )
        
        if summary['trade_count'] > 0:
            last_trade = summary['trades'][-1]
            print(f"Last Trade:   [{last_trade['side']}] @ {last_trade['price']:,} ({last_trade['reason']})")
    else:
        print(f"Backtest failed: {result.get('message')}")

async def main():
    db_path = os.path.join(os.getcwd(), 'data', 'backtest.db')
    if not os.path.exists(db_path):
        print(f"Error: Database not found at {db_path}")
        return

    engine = BacktestEngine(db_path)
    exchange_id = "upbit"
    symbol = "BTC"
    initial_cash = 10000000.0  # 10,000,000 KRW

    # DB에 적재된 실제 최근 시간대를 조회해 동적으로 설정
    # max_timestamp = 1781413655852
    # 최근 2시간 범위의 데이터를 지정하여 백테스트를 수행합니다.
    end_date = 1781413655852
    start_date = end_date - 2 * 3600 * 1000

    # 1. RSI 전략 테스트
    strategy_configs_rsi = {
        "RSIStrategy": {
            "enabled": True,
            "params": {"rsi_window": 14, "buy_threshold": 30.0, "sell_threshold": 70.0, "interval": 60}
        }
    }
    await run_and_print(engine, exchange_id, symbol, start_date, end_date, initial_cash, strategy_configs_rsi, "RSI (30/70)")

    # 2. MACD 전략 테스트
    strategy_configs_macd = {
        "MACDStrategy": {
            "enabled": True,
            "params": {"fast_period": 12, "slow_period": 26, "signal_period": 9, "interval": 60}
        }
    }
    await run_and_print(engine, exchange_id, symbol, start_date, end_date, initial_cash, strategy_configs_macd, "MACD Golden Cross")

    # 3. Momentum Spike 전략 테스트 (10초 인터벌)
    strategy_configs_momentum = {
        "MomentumSpikeStrategy": {
            "enabled": True,
            "params": {"lookback_periods": 20, "vol_multiplier": 3.0, "freq_multiplier": 2.0, "interval": 10}
        }
    }
    await run_and_print(engine, exchange_id, symbol, start_date, end_date, initial_cash, strategy_configs_momentum, "Momentum Spike")

if __name__ == "__main__":
    asyncio.run(main())
