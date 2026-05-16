import asyncio
import aiosqlite
import os
from contextlib import asynccontextmanager

# 프로젝트 루트 기준의 DB 경로 설정
DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'data', 'backtest.db')

# 전역 DB 접속 제한 세마포어 (최대 10개 동시 접속)
db_semaphore = asyncio.Semaphore(10)

@asynccontextmanager
async def get_db_conn(db_path: str = DB_PATH):
    """
    최적화된 설정을 적용한 SQLite 연결을 제공하는 컨텍스트 매니저입니다.
    - WAL 모드: 읽기/쓰기 동시성 향상
    - Synchronous=NORMAL: 쓰기 속도 향상
    - Semaphore: 동시 접속 제어
    - Busy Timeout: 30초 대기
    """
    async with db_semaphore:
        async with aiosqlite.connect(db_path, timeout=30) as db:
            # 성능 최적화 PRAGMA 설정
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute("PRAGMA synchronous=NORMAL")
            await db.execute("PRAGMA cache_size=-64000") # 약 64MB 캐시 사용
            await db.execute("PRAGMA temp_store=MEMORY")
            await db.execute("PRAGMA busy_timeout=30000")
            
            db.row_factory = aiosqlite.Row
            yield db
            # commit은 명시적으로 호출해야 함 (데이터 안전을 위해)
