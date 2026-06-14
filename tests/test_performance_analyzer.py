# -*- coding: utf-8 -*-

import pytest
from src.engine.portfolio import Portfolio, Position
from src.engine.performance_analyzer import PerformanceAnalyzer


def test_performance_analyzer_empty_portfolio():
    # 빈 포트폴리오 테스트
    portfolio = Portfolio(
        portfolio_id="test_empty",
        name="Empty Test Portfolio",
        portfolio_type="simulation"
    )
    portfolio.exchange_cash = {"upbit": 10000000.0}
    portfolio.exchange_initial_cash = {"upbit": 10000000.0}

    trades = []
    current_prices = {}

    report = PerformanceAnalyzer.calculate_report(
        portfolio=portfolio,
        trades=trades,
        current_prices=current_prices
    )

    assert report["status"] == "success"
    assert report["portfolio_id"] == "test_empty"
    assert report["initial_cash"] == 10000000.0
    assert report["cash"] == 10000000.0
    assert report["total_value"] == 10000000.0
    assert report["roi"] == 0.0

    # DTO 구조에 주요 키가 모두 있는지 확인
    assert "summary" in report
    assert "positions" in report
    assert "results" in report
    assert "history" in report
    assert "exchanges" in report
    assert "exchange_initial_cash" in report

    summary = report["summary"]
    assert summary["initial_cash"] == 10000000.0
    assert summary["final_value"] == 10000000.0
    assert summary["profit"] == 0.0
    assert summary["roi"] == 0.0
    assert summary["fee"] == 0.0
    assert summary["trade_count"] == 0


def test_performance_analyzer_single_exchange_with_trades():
    portfolio = Portfolio(
        portfolio_id="test_single",
        name="Single Exchange Test Portfolio",
        portfolio_type="simulation"
    )
    portfolio.exchange_cash = {"upbit": 9000000.0}
    portfolio.exchange_initial_cash = {"upbit": 10000000.0}
    portfolio.positions[("upbit", "BTC")] = Position(
        exchange_id="upbit",
        symbol="BTC",
        quantity=0.02,
        avg_price=50000000.0,
        updated_at=1000.0
    )

    # 거래 이력 (시간 순서대로 전달됨)
    trades = [
        {
            "exchange": "upbit",
            "symbol": "BTC",
            "side": "BUY",
            "price": 50000000.0,
            "quantity": 0.02,
            "fee": 500.0,
            "timestamp": 100,
            "reason": "buy signal"
        }
    ]

    current_prices = {"BTC": 55000000.0}  # 평가 가격 상승

    report = PerformanceAnalyzer.calculate_report(
        portfolio=portfolio,
        trades=trades,
        current_prices=current_prices
    )

    # 1. 수수료 및 거래 수 집계 확인
    assert report["summary"]["fee"] == 500.0
    assert report["summary"]["trade_count"] == 1

    # 실제 수치가 공식에 부합하는지 정밀 확인
    assert report["summary"]["profit"] == 99500.0
    assert report["total_value"] == 10099500.0
    assert report["roi"] == 1.0  # report["roi"]는 total_roi = (total_profit / total_initial * 100) = 99500 / 10,000,000 * 100 = 0.995 => round(..., 2) 적용되어 1.0
    assert report["summary"]["roi"] == 1.0


def test_performance_analyzer_multi_exchange_isolation():
    portfolio = Portfolio(
        portfolio_id="test_multi",
        name="Multi Exchange Test Portfolio",
        portfolio_type="simulation",
        strategy_info='{"initial_cash": {"upbit": 10000000.0, "kis": 10000000.0}}'
    )
    portfolio.exchange_cash = {"upbit": 9000000.0, "kis": 8000000.0}
    portfolio.exchange_initial_cash = {"upbit": 10000000.0, "kis": 10000000.0}

    portfolio.positions[("upbit", "BTC")] = Position(
        exchange_id="upbit",
        symbol="BTC",
        quantity=0.02,
        avg_price=50000000.0,
        updated_at=1000.0
    )
    portfolio.positions[("kis", "005930")] = Position(
        exchange_id="kis",
        symbol="005930",
        quantity=20.0,
        avg_price=100000.0,
        updated_at=1000.0
    )

    trades = [
        {
            "exchange": "upbit",
            "symbol": "BTC",
            "side": "BUY",
            "price": 50000000.0,
            "quantity": 0.02,
            "fee": 500.0,
            "timestamp": 100
        },
        {
            "exchange": "kis",
            "symbol": "005930",
            "side": "BUY",
            "price": 100000.0,
            "quantity": 20.0,
            "fee": 1000.0,
            "timestamp": 200
        }
    ]

    current_prices = {
        "BTC": 60000000.0,   # upbit 평가액 상승
        "005930": 90000.0    # kis 평가액 하락
    }

    report = PerformanceAnalyzer.calculate_report(
        portfolio=portfolio,
        trades=trades,
        current_prices=current_prices
    )

    # 1. 거래소별 자금 요약 검증
    # upbit: cash = 9,000,000, valuation = 0.02 * 60,000,000 = 1,200,000 => total = 10,200,000
    # upbit_profit = 0 + 1,200,000 - 1,000,000 - 500 = 199,500
    # kis: cash = 8,000,000, valuation = 20 * 90,000 = 1,800,000 => total = 9,800,000
    # kis_profit = 0 + 1,800,000 - 2,000,000 - 1000 = -201,000
    exchanges = {ex["exchange_id"].lower(): ex for ex in report["exchanges"]}
    
    assert exchanges["upbit"]["cash"] == 9000000.0
    assert exchanges["upbit"]["total_value"] == 10200000.0
    assert exchanges["upbit"]["profit"] == 199500.0

    assert exchanges["kis"]["cash"] == 8000000.0
    assert exchanges["kis"]["total_value"] == 9800000.0
    assert exchanges["kis"]["profit"] == -201000.0

    # 2. 종합 요약 검증
    # total_profit = 199,500 + (-201,000) = -1,500
    # total_initial = 20,000,000
    # roi = -1500 / 20,000,000 * 100 = -0.0075 => round(..., 2) = -0.01
    assert report["summary"]["profit"] == -1500.0
    assert report["roi"] == -0.01

