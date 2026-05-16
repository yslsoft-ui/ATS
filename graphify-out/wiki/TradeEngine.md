# TradeEngine

> God node · 16 connections · `src/engine/trade_engine.py`

**Community:** [[매매 전략 알고리즘]]

## Connections by Relation

### calls
- [[.run()]] `INFERRED`
- [[.run()]] `EXTRACTED`

### contains
- [[trade_engine.py]] `EXTRACTED`

### method
- [[.warm_up()]] `EXTRACTED`
- [[.process_tick()]] `EXTRACTED`
- [[.__init__()]] `EXTRACTED`
- [[.update_strategy_params()]] `EXTRACTED`

### rationale_for
- [[종목별로 캔들 생성과 전략 실행을 통합 관리하는 엔진입니다.]] `EXTRACTED`

### uses
- [[StrategyResult]] `INFERRED`
- [[Candle]] `INFERRED`
- [[BaseStrategy]] `INFERRED`
- [[ConnectionManager]] `INFERRED`
- [[CollectorManager]] `INFERRED`
- [[CandleGenerator]] `INFERRED`
- [[BacktestEngine]] `INFERRED`
- [[StrategyType]] `INFERRED`

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*