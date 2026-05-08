import asyncio
import os
from src.engine.backtest import BacktestEngine
from src.engine.strategy import RSIStrategy, MACDStrategy
from src.utils.visualizer import plot_backtest_result

async def run_and_print(engine, symbol, initial_cash, strategy, name):
    print(f"\n--- Running {name} Backtest ---")
    result = await engine.run(symbol=symbol, initial_cash=initial_cash, strategy=strategy, interval=60)
    
    if result["status"] == "success":
        summary = result["summary"]
        print(f"Final Value:  {summary['final_value']:,} KRW")
        print(f"ROI:          {summary['roi']}%")
        print(f"Total Trades: {summary['trade_count']}")
        
        # 차트 생성 및 저장 (파일명에서 특수문자 제거)
        safe_name = name.lower().replace(' ', '_').replace('/', '_').replace('(', '').replace(')', '')
        output_filename = f"backtest_{safe_name}.png"
        plot_backtest_result(
            summary['candle_history'], 
            summary['trades'], 
            name, 
            output_filename
        )
        
        if summary['trade_count'] > 0:
            last_trade = summary['trades'][-1]
            print(f"Last Trade:   [{last_trade['type']}] @ {last_trade['price']:,} ({last_trade['reason']})")
    else:
        print(f"Backtest failed: {result.get('message')}")

async def main():
    db_path = os.path.join(os.getcwd(), 'data', 'backtest.db')
    if not os.path.exists(db_path):
        print(f"Error: Database not found at {db_path}")
        return

    engine = BacktestEngine(db_path)
    symbol = "KRW-BTC"
    initial_cash = 1000000

    # 1. RSI 전략 테스트
    await run_and_print(engine, symbol, initial_cash, RSIStrategy(), "RSI (30/70)")

    # 2. MACD 전략 테스트
    await run_and_print(engine, symbol, initial_cash, MACDStrategy(), "MACD Golden Cross")

if __name__ == "__main__":
    asyncio.run(main())
