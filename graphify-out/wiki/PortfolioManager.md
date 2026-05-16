# PortfolioManager

> God node · 14 connections · `src/engine/portfolio.py`

**Community:** [[포트폴리오 및 자산 관리]]

## Connections by Relation

### calls
- [[test()]] `INFERRED`
- [[test_portfolio_manager_handle_signal()]] `INFERRED`

### contains
- [[portfolio.py]] `EXTRACTED`

### method
- [[.handle_signal()]] `EXTRACTED`
- [[.load_from_db()]] `EXTRACTED`
- [[.save_to_db()]] `EXTRACTED`
- [[.load_exchange_configs()]] `EXTRACTED`
- [[.liquidate_all()]] `EXTRACTED`
- [[.__init__()]] `EXTRACTED`
- [[.add_portfolio()]] `EXTRACTED`

### rationale_for
- [[여러 포트폴리오를 관리하고 전략 신호를 주문으로 연결합니다.]] `EXTRACTED`

### uses
- [[ConnectionManager]] `INFERRED`
- [[CollectorManager]] `INFERRED`
- [[OrderbookMatchingEngine]] `INFERRED`

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*