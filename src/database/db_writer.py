import asyncio
import json
from src.database.connection import get_db_conn

class DBWriter:
    def __init__(self, queue: asyncio.Queue, db_path: str = None, batch_size=100, flush_interval=1.0):
        self.queue = queue
        self.db_path = db_path
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        self.trade_buffer = []
        self.orderbook_buffer = []

    async def run(self):
        print(f"DBWriter started.")
        async with get_db_conn() as db:
            while True:
                try:
                    # 지정된 시간동안 큐에서 데이터 가져오기 시도 (타임아웃 시 플러시)
                    item = await asyncio.wait_for(self.queue.get(), timeout=self.flush_interval)
                    
                    data_type = item.get('type')
                    if data_type == 'trade':
                        self.trade_buffer.append((
                            'UPBIT',
                            item.get('code'),
                            item.get('trade_price'),
                            item.get('trade_volume'),
                            item.get('ask_bid'),
                            item.get('trade_timestamp'),
                            item.get('sequential_id')
                        ))
                    elif data_type == 'orderbook':
                        # orderbook_units를 bids, asks 배열로 분리하여 JSON 직렬화
                        units = item.get('orderbook_units', [])
                        bids = [{"price": u['bid_price'], "size": u['bid_size']} for u in units]
                        asks = [{"price": u['ask_price'], "size": u['ask_size']} for u in units]
                        
                        self.orderbook_buffer.append((
                            'UPBIT',
                            item.get('code'),
                            item.get('timestamp'),
                            json.dumps(bids),
                            json.dumps(asks)
                        ))
                    
                    self.queue.task_done()

                    # 버퍼가 차면 플러시 (Bulk Insert)
                    if len(self.trade_buffer) >= self.batch_size or len(self.orderbook_buffer) >= self.batch_size:
                        await self.flush(db)
                        
                except asyncio.TimeoutError:
                    # 타임아웃 발생 시 버퍼에 데이터가 있다면 무조건 플러시
                    if self.trade_buffer or self.orderbook_buffer:
                        await self.flush(db)
                except asyncio.CancelledError:
                    # 프로세스 종료 요청 시 남은 데이터 저장 후 종료
                    await self.flush(db)
                    print("DBWriter task cancelled. Flushed remaining data.")
                    break
                except Exception as e:
                    print(f"DBWriter Error: {e}")

    async def flush(self, db):
        try:
            if self.trade_buffer:
                await db.executemany('''
                    INSERT INTO trades (exchange, symbol, trade_price, trade_volume, ask_bid, trade_timestamp, sequential_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', self.trade_buffer)
                self.trade_buffer.clear()
            
            if self.orderbook_buffer:
                await db.executemany('''
                    INSERT INTO orderbooks (exchange, symbol, timestamp, bids, asks)
                    VALUES (?, ?, ?, ?, ?)
                ''', self.orderbook_buffer)
                self.orderbook_buffer.clear()
            
            await db.commit()
        except Exception as e:
            print(f"Flush Error: {e}")
