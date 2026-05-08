import asyncio
import websockets
import json
import uuid
import sys
import os

# src 경로 인식용 설정
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from src.database.db_writer import DBWriter

class UpbitCollector:
    def __init__(self, queue):
        self.uri = "wss://api.upbit.com/websocket/v1"
        self.queue = queue
        
    async def connect_and_listen(self, symbols):
        async with websockets.connect(self.uri) as websocket:
            print(f"Connected to Upbit WebSocket for {symbols}")
            
            # 구독 요청 (체결 및 호가창)
            subscribe_data = [
                {"ticket": str(uuid.uuid4())},
                {"type": "trade", "codes": symbols},
                {"type": "orderbook", "codes": symbols}
            ]
            await websocket.send(json.dumps(subscribe_data))
            
            try:
                while True:
                    response = await websocket.recv()
                    data = json.loads(response)
                    # 수신된 데이터를 큐에 삽입
                    await self.queue.put(data)

            except websockets.ConnectionClosed:
                print("Connection closed. Attempting to reconnect...")
                await asyncio.sleep(2)
                await self.connect_and_listen(symbols) # 재연결

if __name__ == "__main__":
    shared_queue = asyncio.Queue()
    collector = UpbitCollector(shared_queue)
    
    db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'data', 'backtest.db')
    db_writer = DBWriter(shared_queue, db_path)
    
    async def main_run():
        print("Starting continuous data collection...")
        # 1. 수집기 실행 (Producer)
        ws_task = asyncio.create_task(collector.connect_and_listen(["KRW-BTC"]))
        # 2. DB 저장기 실행 (Consumer)
        db_task = asyncio.create_task(db_writer.run())
        
        # 무한 대기 (Ctrl+C로 종료)
        try:
            await asyncio.gather(ws_task, db_task)
        except asyncio.CancelledError:
            print("Shutting down...")
        
    asyncio.run(main_run())
