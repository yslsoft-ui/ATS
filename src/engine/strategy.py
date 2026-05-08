from abc import ABC, abstractmethod
from typing import Dict, Optional, List
from .candles import Candle

class StrategyResult:
    def __init__(self, action: str, price: Optional[float] = None, reason: str = ""):
        self.action = action  # "BUY", "SELL", "HOLD"
        self.price = price
        self.reason = reason

class BaseStrategy(ABC):
    @abstractmethod
    def on_candle(self, candle: Candle) -> StrategyResult:
        pass

    @classmethod
    def get_metadata(cls) -> Dict:
        """전략의 이름, 설명, 파라미터 정보를 반환합니다."""
        return {
            "id": cls.__name__.lower(),
            "name": cls.__name__,
            "description": cls.__doc__.strip() if cls.__doc__ else "",
            "params": {}
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
