from pydantic import BaseModel

class MarketTickerDTO(BaseModel):
    """
    거래소별 실시간 시세 요약 정보 데이터 전송 객체 (Typed DTO)
    """
    exchange: str
    market: str
    korean_name: str
    trade_price: float = 0.0
    signed_change_rate: float = 0.0
    change_price: float = 0.0
    acc_trade_price_24h: float = 0.0
    high_price: float = 0.0
    low_price: float = 0.0
