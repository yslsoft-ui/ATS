import datetime
from src.engine.utils.telemetry import get_logger
from zoneinfo import ZoneInfo

logger = get_logger(__name__)

class MarketHours:
    """
    국내외 시장별 운영 시간을 관리하는 유틸리티입니다.
    Python 3.9+ 표준 라이브러리인 zoneinfo를 사용하여 시간대를 처리합니다.
    """
    KST = ZoneInfo('Asia/Seoul')

    @classmethod
    def is_krx_open(cls, dt: datetime.datetime = None, start_time_str: str = "08:30", end_time_str: str = "18:10") -> bool:
        """
        국내 주식(KRX) 장 운영 시간인지 확인합니다.
        평일 지정 시간대 (기본값 08:30 ~ 18:10)
        """
        if dt is None:
            dt = datetime.datetime.now(cls.KST)
        elif dt.tzinfo is None:
            # 타임존 정보가 없는 naive datetime인 경우 KST로 간주하여 처리
            dt = dt.replace(tzinfo=cls.KST)
        else:
            dt = dt.astimezone(cls.KST)

        # 1. 주말 체크
        if dt.weekday() >= 5:
            return False

        # 2. 시간 체크
        current_time = dt.time()
        
        try:
            sh, sm = map(int, start_time_str.split(':'))
            start_time = datetime.time(sh, sm)
        except Exception:
            start_time = datetime.time(8, 30)

        try:
            eh, em = map(int, end_time_str.split(':'))
            end_time = datetime.time(eh, em)
        except Exception:
            end_time = datetime.time(18, 10)

        if not (start_time <= current_time <= end_time):
            return False

        return True

    @classmethod
    def time_until_open(cls, exchange: str = 'kis', start_time_str: str = "08:30") -> float:
        """다음 장 개장까지 남은 시간(초)을 반환합니다."""
        now = datetime.datetime.now(cls.KST)
        
        if exchange == 'kis':
            try:
                sh, sm = map(int, start_time_str.split(':'))
            except Exception:
                sh, sm = 8, 30
            # 다음 평일 sh:sm 계산
            target = now.replace(hour=sh, minute=sm, second=0, microsecond=0)
            if now >= target or now.weekday() >= 5:
                # 오늘 이미 지났거나 주말이면 다음날로
                days_ahead = 1
                if now.weekday() == 4: days_ahead = 3 # 금요일 -> 월요일
                elif now.weekday() == 5: days_ahead = 2 # 토요일 -> 월요일
                target += datetime.timedelta(days=days_ahead)
            
            return (target - now).total_seconds()
        
        return 0.0
