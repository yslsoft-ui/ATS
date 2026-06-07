import asyncio
from typing import List, Dict, Any, Optional
from src.database.connection import get_db_conn
from src.database.retry import with_db_retry
from src.engine.utils.telemetry import get_logger


logger = get_logger(__name__)

class DatabaseWriter:
    """
    틱(Trades)과 캔들(Candles) 데이터를 비동기 큐에서 대기 수집하여 
    SQLite DB에 초고속 벌크 플러시(Bulk Flush) 및 트랜잭션 커밋을 전담하는 
    단일 책임의 데이터베이스 영속화 엔진(Deep Module)입니다.
    """
    def __init__(self, db_path: str = "database.db"):
        self.db_path = db_path
        self.db_queue = asyncio.Queue()
        self.candle_queue = asyncio.Queue()
        self.is_running = False
        self._db_task: Optional[asyncio.Task] = None
        self._candle_task: Optional[asyncio.Task] = None

    async def start(self):
        """비동기 DB/Candle 영속화 플러시 워커 태스크를 기동합니다."""
        if self.is_running:
            return
        
        self.is_running = True
        self._db_task = asyncio.create_task(self._db_writer_loop())
        self._candle_task = asyncio.create_task(self._candle_writer_loop())
        logger.info("[DatabaseWriter] 비동기 데이터베이스 영속화 엔진 가동 시작")

    async def stop(self):
        """데이터베이스 엔진을 안전하게 중단하고, 큐에 남아 있는 모든 잔여 틱과 캔들을 완전히 플러시합니다."""
        if not self.is_running:
            return
        
        self.is_running = False
        logger.info("[DatabaseWriter] 데이터베이스 영속화 엔진 중단 요청 감지. 안전 플러시 개시...")

        # 1. 실행 중인 비동기 루프 취소
        if self._db_task:
            self._db_task.cancel()
            try:
                await self._db_task
            except asyncio.CancelledError:
                pass

        if self._candle_task:
            self._candle_task.cancel()
            try:
                await self._candle_task
            except asyncio.CancelledError:
                pass

        # 2. 🚨 우아한 최종 강제 플러시 (Graceful Shutdown Flush) 수행
        await self._force_flush_remaining()
        logger.info("[DatabaseWriter] 데이터베이스 영속화 엔진 안전 중단 완료")

    def enqueue_tick(self, tick_data: Dict[str, Any]):
        """실시간 수신 틱 데이터를 비동기 DB 대기 큐에 안전하게 투입합니다."""
        self.db_queue.put_nowait(tick_data)

    def enqueue_candle(self, candle_data: Any):
        """전략 엔진에서 완성된 캔들 데이터를 비동기 DB 대기 큐에 안전하게 투입합니다."""
        self.candle_queue.put_nowait(candle_data)

    async def _db_writer_loop(self):
        """틱 데이터를 DB에 배치 저장합니다."""
        while self.is_running:
            try:
                async with get_db_conn(self.db_path) as db:
                    while self.is_running:
                        buffer = []
                        try:
                            # 최대 500개까지 1.0초의 타임아웃을 걸고 신속 모음
                            while len(buffer) < 500:
                                item = await asyncio.wait_for(self.db_queue.get(), timeout=1.0)
                                buffer.append((
                                    item.get('exchange', 'upbit'),
                                    item.get('market', 'KRW'),
                                    item['code'],
                                    item['trade_price'],
                                    item['trade_volume'],
                                    item['ask_bid'],
                                    item['trade_timestamp'],
                                    item.get('sequential_id')
                                ))
                                self.db_queue.task_done()
                        except asyncio.TimeoutError:
                            pass

                        if buffer:
                            await self._write_ticks_to_db(db, buffer)
                            await asyncio.sleep(0.01)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[DatabaseWriter] DB 틱 라이터 루프 예외 발생: {e}")
                await asyncio.sleep(1)

    async def _candle_writer_loop(self):
        """캔들 데이터를 DB에 벌크로 저장하고 중단 시 최종 커밋을 완수합니다."""
        buffer = []
        logger.info("[DatabaseWriter] _candle_writer_loop 시작됨")
        
        while self.is_running:
            try:
                logger.info("[DatabaseWriter] get_db_conn 획득 시도 중...")
                async with get_db_conn(self.db_path) as db:
                    logger.info("[DatabaseWriter] get_db_conn 획득 성공! 내부 루프 진입")
                    while self.is_running:
                        buffer.clear()
                        # 1. 첫 번째 캔들이 큐에 진입할 때까지 무한 대기
                        logger.info(f"[DatabaseWriter] 큐 대기 시작... qsize={self.candle_queue.qsize()}")
                        candle = await self.candle_queue.get()
                        logger.info(f"[DatabaseWriter] 큐에서 캔들 1개 획득! symbol={candle.symbol}, ts={candle.timestamp}")
                        buffer.append(candle)
                        self.candle_queue.task_done()
                        
                        # 2. 추가 캔들이 연속 유입되면 최대 0.5초 또는 500개까지 흡수
                        try:
                            while len(buffer) < 500:
                                next_candle = await asyncio.wait_for(self.candle_queue.get(), timeout=0.5)
                                buffer.append(next_candle)
                                self.candle_queue.task_done()
                        except asyncio.TimeoutError:
                            pass
                        
                        # 3. 모인 캔들들을 단 하나의 벌크 트랜잭션으로 커밋
                        if buffer:
                            logger.info(f"[DatabaseWriter] {len(buffer)}개 캔들 DB 플러시 시도")
                            await self._flush_candles_to_db(db, buffer)
                            logger.info(f"[DatabaseWriter] {len(buffer)}개 캔들 DB 플러시 완료")
                            await asyncio.sleep(0.01)
                            
            except asyncio.CancelledError:
                logger.info("[DatabaseWriter] _candle_writer_loop CancelledError 수신")
                break
    @with_db_retry()
    async def _write_ticks_to_db(self, db, buffer):
        """틱 리스트를 실제 DB에 벌크 삽입하고 커밋합니다 (재시도 지원)."""
        await db.executemany(
            "INSERT INTO trades (exchange, market, symbol, trade_price, trade_volume, ask_bid, trade_timestamp, sequential_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", 
            buffer
        )
        await db.commit()

    @with_db_retry()
    async def _flush_candles_to_db(self, db_conn, candles_to_write):
        """실제 데이터베이스 접속 세션을 통해 캔들 리스트를 벌크 삽입합니다."""
        if not candles_to_write:
            return
        query = """
        INSERT OR REPLACE INTO candles 
        (exchange, symbol, interval, timestamp, open, high, low, close, volume) 
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = [
            (c.exchange, c.symbol, c.interval, c.timestamp, c.open, c.high, c.low, c.close, c.volume)
            for c in candles_to_write
        ]
        await db_conn.executemany(query, params)
        await db_conn.commit()
        logger.debug(f"[DatabaseWriter] 캔들 {len(candles_to_write)}개 벌크 플러시 완료")

    @with_db_retry()
    async def _force_flush_remaining(self):
        """종료 시점에 큐에 적체된 잔여 틱 및 캔들을 강제로 털어서 커밋합니다."""
        try:
            async with get_db_conn(self.db_path) as db:
                # 1. 잔여 틱 강제 털기
                ticks_to_write = []
                while not self.db_queue.empty():
                    item = self.db_queue.get_nowait()
                    ticks_to_write.append((
                        item.get('exchange', 'upbit'),
                        item.get('market', 'KRW'),
                        item['code'],
                        item['trade_price'],
                        item['trade_volume'],
                        item['ask_bid'],
                        item['trade_timestamp'],
                        item.get('sequential_id')
                    ))
                    self.db_queue.task_done()

                if ticks_to_write:
                    await db.executemany(
                        "INSERT INTO trades (exchange, market, symbol, trade_price, trade_volume, ask_bid, trade_timestamp, sequential_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        ticks_to_write
                    )
                    logger.info(f"[DatabaseWriter] 종료 가드: 잔여 틱 {len(ticks_to_write)}개 최종 영속화 완수")

                # 2. 잔여 캔들 강제 털기
                candles_to_write = []
                while not self.candle_queue.empty():
                    candle = self.candle_queue.get_nowait()
                    candles_to_write.append(candle)
                    self.candle_queue.task_done()

                if candles_to_write:
                    await self._flush_candles_to_db(db, candles_to_write)
                    logger.info(f"[DatabaseWriter] 종료 가드: 잔여 캔들 {len(candles_to_write)}개 최종 영속화 완수")

                await db.commit()
        except Exception as e:
            logger.error(f"[DatabaseWriter] 종료 가드 최종 영속화 실패: {e}")

