import logging
import time
from typing import Optional, Callable, Dict, Any
from src.engine.portfolio import PortfolioManager
from src.database.connection import get_db_conn

logger = logging.getLogger(__name__)

class ExecutionPipeline:
    """
    전략 신호를 수신하여 검증, 실행, 알림까지의 전 과정을 총괄하는 파이프라인입니다.
    (Deep Module: 인터페이스는 단순하지만 내부에서 복잡한 실행 로직을 처리)
    """
    def __init__(self, portfolio_manager: PortfolioManager):
        self.portfolio_manager = portfolio_manager
        self.broadcast_callback: Optional[Callable] = None

    def set_broadcast_callback(self, callback: Callable):
        self.broadcast_callback = callback

    async def process_signal(self, signal: Any, price: float, orderbook: Optional[Dict] = None):
        """
        신호를 수신하여 실행 파이프라인을 가동합니다.
        """
        symbol = signal.symbol
        action = signal.action
        
        logger.info(f"Pipeline: Processing {action} signal for {symbol} (Strategy: {getattr(signal, 'strategy_id', 'unknown')})")

        # 1. 신호 유효성 검증
        if action not in ['BUY', 'SELL']:
            logger.warning(f"Pipeline: Invalid action {action} for {symbol}")
            return

        # 2. 주문 실행 (PortfolioManager)
        # TODO: 리스크 관리 모듈(RiskManager)이 있다면 여기서 체크 로직 수행 가능
        try:
            result = await self.portfolio_manager.handle_signal(
                portfolio_id="default",
                signal=signal,
                trade_price=price,
                orderbook_data=orderbook
            )
            
            if not result:
                logger.warning(f"Pipeline: Execution failed or skipped for {symbol}")
                return

            # 3. 알림 생성 및 처리
            await self._handle_notifications(result, signal)
            
        except Exception as e:
            logger.error(f"Pipeline: Execution Error for {symbol}: {str(e)}")

    async def _handle_notifications(self, result: Dict, signal: Any):
        """거래 발생 알림을 생성하고 저장/전송합니다."""
        symbol = result['symbol']
        action = result['side']
        price = result['price']
        reason = getattr(signal, 'reason', '')
        
        msg = f"🤖 [전략매매] {action} 체결: {symbol} @ {price:,.2f} ({reason})"
        
        alert = {
            "type": "alert",
            "alert_type": "trade",
            "code": symbol,
            "price": price,
            "msg": msg,
            "timestamp": int(time.time() * 1000)
        }

        # 1. DB 알림 저장
        await self._save_alert_to_db(alert)

        # 2. 외부 브로드캐스트 (WebSocket 등)
        if self.broadcast_callback:
            await self.broadcast_callback(alert)

    async def _save_alert_to_db(self, alert: Dict):
        """알림을 데이터베이스에 영구 저장합니다."""
        try:
            async with get_db_conn() as db:
                await db.execute(
                    "INSERT INTO alerts (symbol, price, msg, timestamp) VALUES (?, ?, ?, ?)",
                    (alert['code'], alert['price'], alert['msg'], alert['timestamp'])
                )
                await db.commit()
        except Exception as e:
            logger.error(f"Pipeline: Alert Save Error: {e}")
