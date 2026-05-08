from typing import Dict, Optional
from src.engine.strategy import BaseStrategy, StrategyResult, StrategyRegistry
from src.engine.candles import Candle

@StrategyRegistry.register
class SequentialStrategy(BaseStrategy):
    """
    두 개의 다른 전략을 순차적으로 조합하는 마스터 전략입니다.
    1차 전략의 신호가 발생한 후, 지정된 캔들 갯수 이내에 2차 전략의 동일한 방향(BUY/SELL) 신호가 발생하면 최종 신호를 냅니다.
    """
    def __init__(self, first_strategy_id: str = "rsi_strategy", second_strategy_id: str = "macd_strategy", wait_candles: int = 3):
        self.first_strategy_id = first_strategy_id.lower()
        self.second_strategy_id = second_strategy_id.lower()
        self.wait_candles = wait_candles
        
        # 내부 하위 전략 인스턴스 (기본값으로 초기화)
        self.first_strategy: Optional[BaseStrategy] = StrategyRegistry.create_strategy(self.first_strategy_id)
        self.second_strategy: Optional[BaseStrategy] = StrategyRegistry.create_strategy(self.second_strategy_id)
        
        # 상태 머신 제어 변수
        self.countdown = 0
        self.waiting_for_second = False
        self.expected_action = None # "BUY" or "SELL"

    @classmethod
    def get_metadata(cls) -> Dict:
        metadata = super().get_metadata()
        metadata["name"] = "순차 복합 전략 (Combo)"
        metadata["params"] = {
            "first_strategy_id": {"type": "str", "default": "rsi_strategy", "description": "1차 조건 전략 ID"},
            "second_strategy_id": {"type": "str", "default": "macd_strategy", "description": "2차 조건 전략 ID"},
            "wait_candles": {"type": "int", "default": 3, "description": "1차 조건 달성 후 대기할 최대 캔들 수"}
        }
        return metadata

    def update_params(self, params: Dict):
        super().update_params(params)
        # ID 파라미터가 동적으로 변경될 경우 내부 하위 전략을 다시 로드합니다.
        if "first_strategy_id" in params or "second_strategy_id" in params:
            self.first_strategy = StrategyRegistry.create_strategy(self.first_strategy_id.lower())
            self.second_strategy = StrategyRegistry.create_strategy(self.second_strategy_id.lower())
            self.countdown = 0
            self.waiting_for_second = False
            self.expected_action = None

    def on_candle(self, candle: Candle) -> StrategyResult:
        if not self.first_strategy or not self.second_strategy:
            return StrategyResult("HOLD", reason="하위 전략이 제대로 로드되지 않았습니다.")

        # 두 하위 전략 모두에게 실시간 캔들을 전달하여 내부 지표(RSI, MACD 등)를 업데이트하도록 합니다.
        res1 = self.first_strategy.on_candle(candle)
        res2 = self.second_strategy.on_candle(candle)

        # --- 상태 머신 평가 로직 ---
        if not self.waiting_for_second:
            # 1. 평상시: 1차 전략의 신호를 감시합니다.
            if res1 and res1.action in ["BUY", "SELL"]:
                self.waiting_for_second = True
                self.expected_action = res1.action
                self.countdown = self.wait_candles
                # 아직 최종 신호는 아닙니다. 내부 로깅용 HOLD 반환.
                return StrategyResult("HOLD", reason=f"[Combo] 1차 조건({self.first_strategy_id} {res1.action}) 달성. {self.countdown}캔들 동안 2차 대기 중...")
        else:
            # 2. 2차 신호 대기 중: 카운트다운을 소모하며 2차 전략의 신호를 기다립니다.
            self.countdown -= 1
            
            # 2차 전략이 1차 전략과 '같은 방향'의 신호를 내야 최종 통과!
            if res2 and res2.action == self.expected_action:
                final_action = self.expected_action
                # 조건 달성 후 다음 사이클을 위해 상태 리셋
                self.waiting_for_second = False
                self.countdown = 0
                self.expected_action = None
                return StrategyResult(final_action, price=candle.close, reason=f"{self.first_strategy_id} ➔ {self.second_strategy_id} 콤보 달성!")
            
            # 기다렸지만 아무 일도 일어나지 않고 카운트다운이 끝났을 경우
            if self.countdown <= 0:
                self.waiting_for_second = False
                self.expected_action = None
                return StrategyResult("HOLD", reason="[Combo] 2차 조건 대기 시간 초과. 상태 리셋됨.")
            else:
                return StrategyResult("HOLD", reason=f"[Combo] 2차 조건 대기 중... (남은 캔들: {self.countdown})")

        return StrategyResult("HOLD")
