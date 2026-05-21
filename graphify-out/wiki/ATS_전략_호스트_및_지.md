# ATS 전략 호스트 및 지

> 21 nodes · cohesion 0.50

## Key Concepts

- **StrategyHost** (13 connections) — `src/engine/strategy_host.py`
- **StrategyContext** (11 connections) — `src/engine/strategy_host.py`
- **TradeSignal** (10 connections) — `src/engine/strategy.py`
- **.on_candle()** (7 connections) — `src/engine/strategy_host.py`
- **._update_indicators()** (5 connections) — `src/engine/strategy_host.py`
- **strategy_host.py** (4 connections) — `src/engine/strategy_host.py`
- **.__init__()** (3 connections) — `src/engine/trade_engine.py`
- **.__init__()** (1 connections) — `src/engine/strategy_host.py`
- **last_candle()** (1 connections) — `src/engine/strategy_host.py`
- **current_price()** (1 connections) — `src/engine/strategy_host.py`
- **.__init__()** (1 connections) — `src/engine/strategy_host.py`
- **새로운 캔들이 들어왔을 때 지표를 업데이트하고 전략을 실행합니다.** (1 connections) — `src/engine/strategy_host.py`
- **전략에서 선언한 지표들을 계산합니다.         IndicatorCalculator 인스턴스를 유지하여 증분 계산을 수행합니다.** (1 connections) — `src/engine/strategy_host.py`
- **.__init__()** (1 connections) — `src/engine/strategy.py`
- **전략이 판단을 내리는 데 필요한 모든 데이터를 제공하는 컨텍스트 객체입니다.** (1 connections) — `src/engine/strategy_host.py`
- **전략을 감싸서(Wrapping) 데이터 공급 및 지표 계산을 관리하는 호스트입니다.** (1 connections) — `src/engine/strategy_host.py`
- **새로운 캔들이 들어왔을 때 지표를 업데이트하고 전략을 실행합니다.** (1 connections) — `src/engine/strategy_host.py`
- **전략이 판단을 내리는 데 필요한 모든 데이터를 제공하는 컨텍스트 객체입니다.** (1 connections) — `src/engine/strategy_host.py`
- **전략을 감싸서(Wrapping) 데이터 공급 및 지표 계산을 관리하는 호스트입니다.** (1 connections) — `src/engine/strategy_host.py`
- **새로운 캔들이 들어왔을 때 지표를 업데이트하고 전략을 실행합니다.** (1 connections) — `src/engine/strategy_host.py`
- **전략에서 선언한 지표들을 계산합니다.         IndicatorCalculator 인스턴스를 유지하여 증분 계산을 수행합니다.** (1 connections) — `src/engine/strategy_host.py`

## Relationships

- [[포트폴리오 및 자산 관리]] (8 shared connections)
- [[기능 모듈 그룹 32]] (3 shared connections)
- [[백테스트 및 실시간]] (3 shared connections)
- [[시장 데이터 수집 및 가공]] (2 shared connections)
- [[Community 42]] (1 shared connections)
- [[매매 전략 알고리즘]] (1 shared connections)
- [[시스템 메인 및 진입점]] (1 shared connections)

## Source Files

- `src/engine/strategy.py`
- `src/engine/strategy_host.py`
- `src/engine/trade_engine.py`

## Audit Trail

- EXTRACTED: 42 (63%)
- INFERRED: 25 (37%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*