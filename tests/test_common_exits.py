import pytest
import time
from typing import Dict
from src.engine.portfolio import Position, Portfolio
from src.engine.exit_evaluator import CommonExitEvaluator
from src.engine.trade_engine import TradeEngine
from src.engine.strategy import BaseStrategy, StrategyResult, StrategyType

class MockStrategy(BaseStrategy):
    def __init__(self):
        super().__init__("MockStrategy", {"interval": 60})
        self.type = StrategyType.BOTH
        
    def on_update(self, context) -> StrategyResult:
        return StrategyResult(action="HOLD")

class MockPortfolioManager:
    def __init__(self):
        self.portfolios = {}
        self.repository = MockRepository()

    def add_portfolio(self, portfolio):
        self.portfolios[portfolio.id] = portfolio

class MockRepository:
    def __init__(self):
        self.saved_portfolios = []

    async def save_portfolio(self, portfolio):
        self.saved_portfolios.append(portfolio)

def test_exit_evaluator_stop_loss():
    # 손절 2% 설정
    config = {
        "system": {
            "exit_rules": {
                "stop_loss_pct": 2.0,
                "trailing_stop_pct": 0.0,
                "time_limit_seconds": 0
            }
        }
    }
    evaluator = CommonExitEvaluator(config)
    
    # 평단가 10,000원인 포지션
    pos = Position(exchange="upbit", symbol="BTC", quantity=1.0, avg_price=10000.0)
    
    # 9,900원 (1% 하락) -> 청산 미발생
    triggered, reason = evaluator.evaluate(pos, 9900.0)
    assert not triggered
    
    # 9,800원 (2% 하락) -> 청산 발생
    triggered, reason = evaluator.evaluate(pos, 9800.0)
    assert triggered
    assert reason == "STOP_LOSS"
    
    # 9,700원 (3% 하락) -> 청산 발생
    triggered, reason = evaluator.evaluate(pos, 9700.0)
    assert triggered
    assert reason == "STOP_LOSS"

def test_exit_evaluator_trailing_stop():
    # 트레일링 스탑 1.5% 설정
    config = {
        "system": {
            "exit_rules": {
                "stop_loss_pct": 0.0,
                "trailing_stop_pct": 1.5,
                "time_limit_seconds": 0
            }
        }
    }
    evaluator = CommonExitEvaluator(config)
    
    # 최고가 10,000원인 포지션
    pos = Position(exchange="upbit", symbol="BTC", quantity=1.0, avg_price=10000.0, peak_price=10000.0)
    
    # 9,900원 (1% 하락) -> 청산 미발생
    triggered, reason = evaluator.evaluate(pos, 9900.0)
    assert not triggered
    
    # 9,840원 (1.6% 하락) -> 청산 발생
    triggered, reason = evaluator.evaluate(pos, 9840.0)
    assert triggered
    assert reason == "TRAILING_STOP"

def test_exit_evaluator_time_limit():
    # 시간 제한 10초 설정
    config = {
        "system": {
            "exit_rules": {
                "stop_loss_pct": 0.0,
                "trailing_stop_pct": 0.0,
                "time_limit_seconds": 10.0
            }
        }
    }
    evaluator = CommonExitEvaluator(config)
    
    entry_time = time.time()
    pos = Position(exchange="upbit", symbol="BTC", quantity=1.0, avg_price=10000.0, entry_time=entry_time)
    
    # 5초 경과 -> 청산 미발생
    triggered, reason = evaluator.evaluate(pos, 10000.0, current_time=entry_time + 5.0)
    assert not triggered
    
    # 10초 경과 -> 청산 발생
    triggered, reason = evaluator.evaluate(pos, 10000.0, current_time=entry_time + 10.0)
    assert triggered
    assert reason == "TIME_LIMIT"

@pytest.mark.asyncio
async def test_trade_engine_tick_exit():
    strategy = MockStrategy()
    engine = TradeEngine(exchange="upbit", symbol="BTC", strategies=[strategy])
    
    # exit_rules 임의 오버라이드
    engine.exit_evaluator.stop_loss_pct = 2.0
    engine.exit_evaluator.trailing_stop_pct = 1.5
    engine.exit_evaluator.time_limit_seconds = 10.0
    
    pm = MockPortfolioManager()
    portfolio = Portfolio("port_sim", "Simulated Portfolio", initial_cash=1000000.0, exchange_id="upbit")
    
    # 평단가 10,000원에 매수 진입 상태 시뮬레이션
    portfolio.update_position("upbit", "BTC", "BUY", 10000.0, 1.0, fee=0.0)
    pm.add_portfolio(portfolio)
    
    # 1. 틱가 10,500원 (상승) -> peak_price 가 10,500원으로 자동 갱신되는지 검증
    tick1 = {
        "trade_price": 10500.0,
        "trade_volume": 0.1,
        "ask_bid": "BID",
        "trade_timestamp": int(time.time() * 1000)
    }
    signals, _ = await engine.process_tick(tick1, pm)
    assert len(signals) == 0
    assert portfolio.positions[("upbit", "BTC")].peak_price == 10500.0
    assert len(pm.repository.saved_portfolios) > 0 # DB에 갱신 내역 저장되었는지
    
    # 2. 틱가 10,340원 (최고가 10,500원 대비 1.5% 초과 하락: 10,500 * 0.985 = 10,342.5) -> 트레일링 스탑 발동
    tick2 = {
        "trade_price": 10340.0,
        "trade_volume": 0.1,
        "ask_bid": "ASK",
        "trade_timestamp": int(time.time() * 1000)
    }
    signals, _ = await engine.process_tick(tick2, pm)
    assert len(signals) == 1
    assert signals[0].action == "SELL"
    assert "Common Exit: TRAILING_STOP" in signals[0].reason

def test_breakeven_price_with_cost_minimal():
    # 1. breakeven_price_with_cost가 exit_net_price >= entry_cost_price 조건을 만족하는 최소 가격인지 검증
    config = {
        "system": {
            "exit_rules": {
                "stop_loss_pct": 0.0,
                "trailing_stop_pct": 0.0,
                "time_limit_seconds": 0,
                "breakeven_activation_pct": 0.0
            },
            "execution_cost": {
                "upbit": {
                    "buy_fee_pct": 0.05,
                    "sell_fee_pct": 0.05,
                    "sell_tax_pct": 0.0,
                    "slippage_pct": 0.05,
                    "safety_buffer_pct": 0.0  # 안전 마진 없음
                }
            }
        }
    }
    evaluator = CommonExitEvaluator(config)
    
    avg_price = 10000.0
    costs_at_avg = evaluator.calculate_costs("upbit", avg_price, avg_price)
    entry_cost_price = costs_at_avg["entry_cost_price"]
    breakeven_price = costs_at_avg["breakeven_price_with_cost"]
    
    # breakeven_price에서의 exit_net_price 계산
    costs_at_be = evaluator.calculate_costs("upbit", avg_price, breakeven_price)
    exit_net_at_be = costs_at_be["exit_net_price"]
    
    # exit_net_price >= entry_cost_price 여야 함 (실수 오차 허용)
    assert exit_net_at_be >= entry_cost_price - 1e-6
    
    # breakeven_price보다 0.01원이라도 낮으면 exit_net_price < entry_cost_price 여야 함
    costs_at_lower = evaluator.calculate_costs("upbit", avg_price, breakeven_price - 0.01)
    exit_net_lower = costs_at_lower["exit_net_price"]
    assert exit_net_lower < entry_cost_price

def test_slippage_pct_reflection():
    # 2. slippage_pct가 매수/매도 양쪽에 각각 반영되는지 검증
    config = {
        "system": {
            "exit_rules": {},
            "execution_cost": {
                "upbit": {
                    "buy_fee_pct": 0.05,
                    "sell_fee_pct": 0.05,
                    "sell_tax_pct": 0.0,
                    "slippage_pct": 0.05,
                    "safety_buffer_pct": 0.0
                }
            }
        }
    }
    evaluator = CommonExitEvaluator(config)
    
    avg_price = 10000.0
    current_price = 10000.0
    costs = evaluator.calculate_costs("upbit", avg_price, current_price)
    
    # 매수 시: buy_fee(0.05%) + slippage(0.05%) = 0.1% 가산
    expected_entry_cost = 10000.0 * 1.0010
    # 매도 시: sell_fee(0.05%) + sell_tax(0.0%) + slippage(0.05%) = 0.1% 차감
    expected_exit_net = 10000.0 * 0.9990
    
    assert abs(costs["entry_cost_price"] - expected_entry_cost) < 1e-6
    assert abs(costs["exit_net_price"] - expected_exit_net) < 1e-6
    assert costs["round_trip_cost_pct"] == 0.20  # 0.05 + 0.05 + 0.0 + (0.05 * 2)

def test_stop_loss_basis_net_pnl():
    # 3. stop_loss_basis="net_pnl"일 때 순손익률 기준으로 손절되는지 검증
    config = {
        "system": {
            "exit_rules": {
                "stop_loss_pct": 2.0,
                "stop_loss_basis": "net_pnl",
                "trailing_stop_pct": 0.0,
                "time_limit_seconds": 0
            },
            "execution_cost": {
                "upbit": {
                    "buy_fee_pct": 0.05,
                    "sell_fee_pct": 0.05,
                    "sell_tax_pct": 0.0,
                    "slippage_pct": 0.05,
                    "safety_buffer_pct": 0.0
                }
            }
        }
    }
    evaluator = CommonExitEvaluator(config)
    
    # 평단가 10,000원 -> 진입 원가: 10010.0원. 2% 손절 한도는 순수령액 기준 10010.0 * 0.98 = 9809.8원.
    # exit_net_price = current_price * 0.999 이므로, 9809.8 / 0.999 = 9819.62원이 손절 경계 가격.
    pos = Position(exchange="upbit", symbol="BTC", quantity=1.0, avg_price=10000.0)
    
    # 9820.0원 -> exit_net = 9810.18 > 9809.8 (순손익률 약 -1.996%) -> 청산 미발생
    triggered, reason = evaluator.evaluate(pos, 9820.0)
    assert not triggered
    
    # 9819.0원 -> exit_net = 9809.181 < 9809.8 (순손익률 약 -2.006%) -> 청산 발생
    triggered, reason = evaluator.evaluate(pos, 9819.0)
    assert triggered
    assert reason == "STOP_LOSS"

def test_stop_loss_basis_price():
    # 4. stop_loss_basis="price"일 때 기존 가격 기준 손절과 동일하게 동작하는지 검증
    config = {
        "system": {
            "exit_rules": {
                "stop_loss_pct": 2.0,
                "stop_loss_basis": "price",
                "trailing_stop_pct": 0.0,
                "time_limit_seconds": 0
            },
            "execution_cost": {
                "upbit": {
                    "buy_fee_pct": 0.05,
                    "sell_fee_pct": 0.05,
                    "sell_tax_pct": 0.0,
                    "slippage_pct": 0.05,
                    "safety_buffer_pct": 0.0
                }
            }
        }
    }
    evaluator = CommonExitEvaluator(config)
    
    # 평단가 10,000원 -> 단순 가격 기준 2% 손절 가격은 9800.0원. 수수료 설정 무관.
    pos = Position(exchange="upbit", symbol="BTC", quantity=1.0, avg_price=10000.0)
    
    # 9801.0원 -> 청산 미발생
    triggered, reason = evaluator.evaluate(pos, 9801.0)
    assert not triggered
    
    # 9800.0원 -> 청산 발생
    triggered, reason = evaluator.evaluate(pos, 9800.0)
    assert triggered
    assert reason == "STOP_LOSS"

def test_multiple_floors_priority():
    # 5. 가격 기반 청산 조건이 여러 개 동시에 충족될 때 가장 높은 방어선의 reason이 선택되는지 검증
    # 세 조건: 손절 5%, 트레일링 스탑 2%, 본전이동 활성화 0.5% (안전마진 0.0)
    config = {
        "system": {
            "exit_rules": {
                "stop_loss_pct": 5.0,
                "stop_loss_basis": "net_pnl",
                "trailing_stop_pct": 2.0,
                "breakeven_activation_pct": 0.5,
                "time_limit_seconds": 0
            },
            "execution_cost": {
                "upbit": {
                    "buy_fee_pct": 0.05,
                    "sell_fee_pct": 0.05,
                    "sell_tax_pct": 0.0,
                    "slippage_pct": 0.05,
                    "safety_buffer_pct": 0.0
                }
            }
        }
    }
    evaluator = CommonExitEvaluator(config)
    
    # avg_price = 10000.0
    # entry_cost_price = 10010.0
    # breakeven_price_with_cost = 10010.0 / 0.999 = 10020.02
    
    # 시나리오 A: 최고가(peak_price)가 10,500원까지 올랐던 상황.
    # - stop_loss_floor = 10010 * 0.95 / 0.999 = 9519.01
    # - breakeven_floor = 10020.02 (peak_net_pnl_pct = 4.79% >= 0.5% 이므로 활성화)
    # - trailing_floor = 10500 * 0.98 = 10290.0
    # 가장 높은 방어선은 trailing_floor(10290.0)
    # current_price = 10000.0 으로 급락하면 세 방어선 모두의 아래에 도달함.
    # 청산 사유는 가장 높은 방어선이었던 TRAILING_STOP이어야 함.
    pos_a = Position(exchange="upbit", symbol="BTC", quantity=1.0, avg_price=10000.0, peak_price=10500.0)
    triggered, reason = evaluator.evaluate(pos_a, 10000.0)
    assert triggered
    assert reason == "TRAILING_STOP"
    
    # 시나리오 B: 최고가(peak_price)가 10,100원이었고, 트레일링 스탑 비율을 10.0%로 매우 크게 늘림.
    # - stop_loss_floor = 9519.01
    # - breakeven_floor = 10020.02 (peak_net_pnl_pct = 0.79% >= 0.5% 이므로 활성화)
    # - trailing_floor = 10100 * 0.90 = 9090.0
    # 가장 높은 방어선은 breakeven_floor(10020.02)
    # current_price = 10000.0 으로 하락하면 breakeven_floor 및 trailing_floor 이하가 됨.
    # 청산 사유는 가장 높은 방어선인 BREAKEVEN_STOP이어야 함.
    config_b = {
        "system": {
            "exit_rules": {
                "stop_loss_pct": 5.0,
                "stop_loss_basis": "net_pnl",
                "trailing_stop_pct": 10.0,
                "breakeven_activation_pct": 0.5,
                "time_limit_seconds": 0
            },
            "execution_cost": config["system"]["execution_cost"]
        }
    }
    evaluator_b = CommonExitEvaluator(config_b)
    pos_b = Position(exchange="upbit", symbol="BTC", quantity=1.0, avg_price=10000.0, peak_price=10100.0)
    triggered, reason = evaluator_b.evaluate(pos_b, 10000.0)
    assert triggered
    assert reason == "BREAKEVEN_STOP"

def test_db_reload_breakeven_recalculation():
    # 6. DB 재로드 후 peak_price 기준으로 Breakeven 활성화 여부가 동일하게 재계산되는지 검증
    # 별도 컬럼 없이 avg_price와 peak_price만으로 breakeven_floor 활성화가 잘 복원되는지 검증
    config = {
        "system": {
            "exit_rules": {
                "stop_loss_pct": 0.0,
                "trailing_stop_pct": 0.0,
                "breakeven_activation_pct": 0.5,
                "time_limit_seconds": 0
            },
            "execution_cost": {
                "upbit": {
                    "buy_fee_pct": 0.05,
                    "sell_fee_pct": 0.05,
                    "sell_tax_pct": 0.0,
                    "slippage_pct": 0.05,
                    "safety_buffer_pct": 0.0
                }
            }
        }
    }
    evaluator = CommonExitEvaluator(config)
    
    # DB에서 갓 로드된 것으로 가정된 Position 객체 (활성화 상태 플래그 등은 전혀 없음)
    # avg_price = 10000.0 -> entry_cost = 10010.0
    # peak_price = 10100.0 -> peak_exit_net = 10100 * 0.999 = 10089.9 -> peak_net_pnl = 0.798% >= 0.5% (활성화 만족)
    pos_reloaded = Position(exchange="upbit", symbol="BTC", quantity=1.0, avg_price=10000.0, peak_price=10100.0)
    
    # breakeven_floor = 10010.0 / 0.999 = 10020.02
    # current_price = 10020.0 -> breakeven_floor 이하이므로 즉시 본전이동 청산 발동
    triggered, reason = evaluator.evaluate(pos_reloaded, 10020.0)
    assert triggered
    assert reason == "BREAKEVEN_STOP"
    
    # peak_price가 낮아서 breakeven 조건 미달인 포지션이 로드된 경우
    # peak_price = 10050.0 -> peak_exit_net = 10039.95 -> peak_net_pnl = 0.299% < 0.5% (활성화 불만족)
    pos_not_activated = Position(exchange="upbit", symbol="BTC", quantity=1.0, avg_price=10000.0, peak_price=10050.0)
    
    # 10020.0원으로 하락해도 본전이동 비활성화 상태이므로 청산 미발생
    triggered, reason = evaluator.evaluate(pos_not_activated, 10020.0)
    assert not triggered
