import matplotlib.pyplot as plt
import pandas as pd
from typing import List, Dict, Any
import datetime

def plot_backtest_result(candle_history: List[Any], trades: List[Dict[str, Any]], title: str, output_path: str):
    """
    백테스트 결과를 차트로 시각화하여 파일로 저장합니다.
    """
    if not candle_history:
        print("No candle history to plot.")
        return

    # 데이터 프레임 변환
    df = pd.DataFrame([vars(c) for c in candle_history])
    df['dt'] = pd.to_datetime(df['timestamp'], unit='s')
    
    plt.figure(figsize=(15, 8))
    
    # 1. 가격 차트 (종가 기준)
    plt.plot(df['dt'], df['close'], label='Close Price', color='skyblue', alpha=0.7)
    
    # 2. 매매 타점 표시
    buy_trades = [t for t in trades if t['type'] == 'BUY']
    sell_trades = [t for t in trades if t['type'] == 'SELL']
    
    if buy_trades:
        buy_dt = pd.to_datetime([t['timestamp'] // 1000 for t in buy_trades], unit='s')
        buy_price = [t['price'] for t in buy_trades]
        plt.scatter(buy_dt, buy_price, marker='^', color='red', s=100, label='BUY Signal', zorder=5)
        
    if sell_trades:
        sell_dt = pd.to_datetime([t['timestamp'] // 1000 for t in sell_trades], unit='s')
        sell_price = [t['price'] for t in sell_trades]
        plt.scatter(sell_dt, sell_price, marker='v', color='blue', s=100, label='SELL Signal', zorder=5)

    plt.title(f"Backtest Result: {title}")
    plt.xlabel("Time")
    plt.ylabel("Price (KRW)")
    plt.legend()
    plt.grid(True, linestyle='--', alpha=0.5)
    
    # 이미지 저장
    plt.savefig(output_path)
    plt.close()
    print(f"Chart saved to {output_path}")
