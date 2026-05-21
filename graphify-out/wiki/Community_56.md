# Community 56

> 10 nodes · cohesion 0.50

## Key Concepts

- **init_db()** (9 connections) — `src/database/schema.py`
- **test()** (4 connections) — `scratch/test_fee_system.py`
- **schema.py** (3 connections) — `src/database/schema.py`
- **migrate_data()** (3 connections) — `src/database/schema.py`
- **setup_test_db()** (3 connections) — `src/engine/test_execution_pipeline.py`
- **setup_test_db()** (3 connections) — `src/engine/test_portfolio.py`
- **ensure_column()** (2 connections) — `src/database/schema.py`
- **test_fee_system.py** (1 connections) — `scratch/test_fee_system.py`
- **테스트 구동 전 임시 DB를 셋업하고, 테스트 완료 후 디스크에서 완전히 삭제합니다.** (1 connections) — `src/engine/test_execution_pipeline.py`
- **테스트 세션 시작 전 격리된 테스트용 DB를 초기화하고, 완료 후 말끔히 삭제합니다.** (1 connections) — `src/engine/test_portfolio.py`

## Relationships

- [[매매 전략 알고리즘]] (2 shared connections)
- [[시장 데이터 수집 및 가공]] (2 shared connections)
- [[ATS 텔레]] (2 shared connections)
- [[Community 58]] (1 shared connections)
- [[기능 모듈 그룹 41]] (1 shared connections)

## Source Files

- `scratch/test_fee_system.py`
- `src/database/schema.py`
- `src/engine/test_execution_pipeline.py`
- `src/engine/test_portfolio.py`

## Audit Trail

- EXTRACTED: 18 (60%)
- INFERRED: 12 (40%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*