import asyncio
import sqlite3
import random
from functools import wraps
from src.engine.utils.telemetry import get_logger

logger = get_logger(__name__)

def with_db_retry(max_retries: int = 5, initial_delay: float = 0.05, max_delay: float = 0.5):
    """
    SQLite WAL 모드 하에서 다중 프로세스 쓰기 시 'database is locked' (OperationalError)가 발생하면
    지수 백오프(Exponential Backoff) 및 랜덤 지터(Jitter)를 적용하여 재시도하는 비동기 데코레이터입니다.
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            retries = 0
            delay = initial_delay
            while True:
                try:
                    return await func(*args, **kwargs)
                except sqlite3.OperationalError as e:
                    # 'database is locked' 에러 체크
                    if "locked" in str(e).lower() and retries < max_retries:
                        retries += 1
                        # 지터(Jitter)를 적용한 대기 시간 계산
                        sleep_time = delay * (0.5 + random.random())
                        sleep_time = min(sleep_time, max_delay)
                        
                        logger.warning(
                            f"[DB Retry] Database is locked. Retrying {retries}/{max_retries} "
                            f"after {sleep_time:.3f}s in {func.__name__}. Error: {e}"
                        )
                        await asyncio.sleep(sleep_time)
                        delay *= 2
                    else:
                        raise e
        return wrapper
    return decorator
