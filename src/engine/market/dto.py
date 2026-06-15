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
    is_collected: bool = True  # KIS 동적 수집 여부 체크용 (업비트/빗썸은 상시 수집이므로 True 기본값)
