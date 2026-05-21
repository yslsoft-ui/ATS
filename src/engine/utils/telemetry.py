import logging
import os
import sys
import asyncio
from logging.handlers import TimedRotatingFileHandler
from typing import Optional, Callable, Any

# ANSI 색상 코드 정의
class LogColors:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    GRAY = "\033[90m"

class ColorFormatter(logging.Formatter):
    """콘솔용 컬러 포매터"""
    COLORS = {
        logging.DEBUG: LogColors.GRAY,
        logging.INFO: LogColors.CYAN,
        logging.WARNING: LogColors.YELLOW,
        logging.ERROR: LogColors.RED,
        logging.CRITICAL: LogColors.BOLD + LogColors.RED,
    }

    def format(self, record):
        color = self.COLORS.get(record.levelno, LogColors.RESET)
        format_orig = self._style._fmt
        # 파일명과 라인번호 강조
        record.name = f"{LogColors.BOLD}{record.name}{LogColors.RESET}"
        self._style._fmt = f"{LogColors.GRAY}%(asctime)s{LogColors.RESET} [{color}%(levelname)-8s{LogColors.RESET}] %(name)s: %(message)s"
        result = super().format(record)
        self._style._fmt = format_orig
        return result

class UIBroadcastHandler(logging.Handler):
    """특정 레벨 이상의 로그를 UI로 자동 브로드캐스트하는 핸들러"""
    def __init__(self, broadcast_callback: Optional[Callable] = None):
        super().__init__()
        self.broadcast_callback = broadcast_callback
        # WARNING 이상만 UI로 전송
        self.setLevel(logging.WARNING)

    def emit(self, record):
        if not self.broadcast_callback:
            return
            
        try:
            msg = self.format(record)
            alert_type = "info"
            if record.levelno >= logging.ERROR:
                alert_type = "error"
            elif record.levelno >= logging.WARNING:
                alert_type = "warning"

            # UI 규격에 맞춘 알림 데이터 생성
            alert_data = {
                "type": "alert",
                "alert_type": "system",
                "level": record.levelname,
                "msg": f"[{record.name}] {record.getMessage()}",
                "timestamp": record.created * 1000
            }
            
            # 비동기 콜백 호출
            if asyncio.iscoroutinefunction(self.broadcast_callback):
                asyncio.create_task(self.broadcast_callback(alert_data))
        except Exception:
            self.handleError(record)

# 전역 설정 상태
_is_initialized = False
_ui_handler: Optional[UIBroadcastHandler] = None

def setup_logging(
    level: int = logging.INFO, 
    log_dir: str = "logs", 
    log_file: str = "ats.log",
    broadcast_callback: Optional[Callable] = None
):
    """시스템 전체 로깅 설정을 초기화합니다."""
    global _is_initialized, _ui_handler
    if _is_initialized:
        return
        
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, log_file)

    # 루트 로거 대신 'src' 로거를 대상으로 하여 우리 코드에만 적용
    target_logger = logging.getLogger('src')
    target_logger.setLevel(level)
    # 외부로 전파하지 않아 루트 로거(Uvicorn 등)와 섞이지 않게 함
    target_logger.propagate = False 
    
    # 1. 콘솔 핸들러 (컬러 포맷)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(ColorFormatter())
    target_logger.addHandler(console_handler)
    
    # 2. 파일 핸들러 (일일 순환)
    file_handler = TimedRotatingFileHandler(
        log_path, when="midnight", interval=1, backupCount=30, encoding="utf-8"
    )
    file_formatter = logging.Formatter(
        "%(asctime)s [%(levelname)-8s] %(name)s (%(filename)s:%(lineno)d): %(message)s"
    )
    file_handler.setFormatter(file_formatter)
    target_logger.addHandler(file_handler)

    # 3. UI 브로드캐스트 핸들러
    _ui_handler = UIBroadcastHandler(broadcast_callback)
    _ui_handler.setFormatter(logging.Formatter("%(message)s"))
    target_logger.addHandler(_ui_handler)

    # 외부 라이브러리 로그 통합 로직 삭제

    _is_initialized = True
    target_logger.info(f"Telemetry system initialized for 'src' package. Log file: {log_path}")

def update_broadcast_callback(callback: Callable):
    """런타임에 UI 콜백을 업데이트합니다 (WebSocket 연결 후)."""
    global _ui_handler
    if _ui_handler:
        _ui_handler.broadcast_callback = callback

def get_logger(name: str) -> logging.Logger:
    """모듈별 로거를 반환합니다."""
    return logging.getLogger(name)
