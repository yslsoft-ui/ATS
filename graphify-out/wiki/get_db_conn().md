# get_db_conn()

> God node · 21 connections · `src/database/connection.py`

**Community:** [[시스템 메인 및 진입점]]

## Connections by Relation

### calls
- [[.handle_signal()]] `INFERRED`
- [[.load_from_db()]] `INFERRED`
- [[.warm_up()]] `INFERRED`
- [[save_alert()]] `INFERRED`
- [[panic_sell()]] `INFERRED`
- [[init_db()]] `INFERRED`
- [[.save_to_db()]] `INFERRED`
- [[.load_exchange_configs()]] `INFERRED`
- [[.run()]] `INFERRED`
- [[db_writer_loop()]] `INFERRED`
- [[.run()]] `INFERRED`
- [[get_recent_trades()]] `INFERRED`
- [[get_candles()]] `INFERRED`
- [[candle_writer_loop()]] `INFERRED`
- [[get_candles()]] `INFERRED`
- [[cleanup_data()]] `INFERRED`
- [[get_alerts()]] `INFERRED`
- [[clear_alerts()]] `INFERRED`
- [[get_trades()]] `INFERRED`

### contains
- [[connection.py]] `EXTRACTED`

### rationale_for
- [[최적화된 설정을 적용한 SQLite 연결을 제공하는 컨텍스트 매니저입니다.     - WAL 모드: 읽기/쓰기 동시성 향상     - Synch]] `EXTRACTED`

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*