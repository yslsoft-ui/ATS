from abc import ABC, abstractmethod
from typing import Dict, Optional, List, Any
from enum import Enum
from .candles import Candle

class StrategyType(Enum):
    ENTRY = "ENTRY"  # 매수 전용
    EXIT = "EXIT"    # 매도 전용
    BOTH = "BOTH"    # 매수/매도 공용

class TradeSignal:
    def __init__(self, symbol: str, action: str, price: float, reason: str, interval: int, strategy_id: str = "", context: Dict = None):
        self.symbol = symbol
        self.action = action
        self.price = price
        self.reason = reason
        self.interval = interval
        self.strategy_id = strategy_id
        self.context = context or {}

class StrategyResult:
    def __init__(self, action: str, price: Optional[float] = None, reason: str = "", context: Dict = None):
        self.action = action  # "BUY", "SELL", "HOLD"
        self.price = price
        self.reason = reason
        self.context = context or {}

class BaseStrategy(ABC):
    # 기본값은 ENTRY(매수전용)로 설정하여 기존 전략과의 하위 호환성 유지
    type = StrategyType.ENTRY
    default_params = {} # 각 전략 클래스에서 정의할 기본 파라미터

    def __init__(self, strategy_id: str, params: Dict[str, Any] = None):
        self.id = strategy_id
        self.params = params or {}
        # params 내용을 객체 속성으로 매핑 (편의용)
        for key, val in self.params.items():
            setattr(self, key, val)
        self.candles = []
        self.required_indicators = []  # 호스트가 미리 계산해야 할 지표 목록

    @abstractmethod
    def on_candle(self, candle: Candle) -> Optional[str]:
        """기존 인터페이스 (하위 호환용)"""
        pass

    def on_update(self, context: Any) -> Optional[StrategyResult]:
        """새로운 인터페이스: StrategyContext를 받아 결정을 내립니다."""
        return None

    @classmethod
    def get_metadata(cls) -> Dict:
        """전략의 이름, 설명, 타입, 파라미터 정보를 반환합니다."""
        return {
            "id": cls.__name__.lower(),
            "name": cls.__name__,
            "type": cls.type.value,
            "description": cls.__doc__.strip() if cls.__doc__ else "",
            "params": cls.default_params
        }

    def update_params(self, params: Dict):
        """전략의 파라미터를 동적으로 업데이트합니다."""
        for key, value in params.items():
            if hasattr(self, key):
                setattr(self, key, value)

class StrategyRegistry:
    """전략 클래스들을 관리하고 인스턴스화하는 레지스트리입니다."""
    _strategies: Dict[str, type] = {}

    @classmethod
    def register(cls, strategy_cls):
        cls._strategies[strategy_cls.__name__.lower()] = strategy_cls
        return strategy_cls

    @classmethod
    def get_all_metadata(cls) -> List[Dict]:
        return [s.get_metadata() for s in cls._strategies.values()]

    @classmethod
    def create_strategy(cls, strategy_id: str, params: Dict = None) -> Optional[BaseStrategy]:
        strategy_id = strategy_id.lower()
        strategy_cls = cls._strategies.get(strategy_id)
        if strategy_cls:
            return strategy_cls(**(params or {}))
        return None
