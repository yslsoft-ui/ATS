# ATS 텔레

> 23 nodes · cohesion 0.50

## Key Concepts

- **get_db_conn()** (42 connections) — `src/database/connection.py`
- **telemetry.py** (7 connections) — `src/server/routers/telemetry.py`
- **cleanup_data()** (5 connections) — `src/server/routers/telemetry.py`
- **test_alert()** (4 connections) — `src/server/routers/telemetry.py`
- **test_strategy_status()** (4 connections) — `src/server/routers/telemetry.py`
- **cleanup_data_preview()** (4 connections) — `src/server/routers/telemetry.py`
- **get_alerts()** (2 connections) — `src/server/routers/telemetry.py`
- **clear_alerts()** (2 connections) — `src/server/routers/telemetry.py`
- **get_queue_status()** (2 connections) — `src/server/routers/telemetry.py`
- **connection.py** (1 connections) — `src/database/connection.py`
- **최적화된 설정을 적용한 SQLite 연결을 제공하는 컨텍스트 매니저입니다.     - WAL 모드: 읽기/쓰기 동시성 향상     - Synch** (1 connections) — `src/database/connection.py`
- **각 작업 큐의 현재 적체량 및 누적 처리량을 반환합니다.** (1 connections) — `src/server/routers/telemetry.py`
- **UI 확인용 테스트 알림을 강제로 발생시킵니다.** (1 connections) — `src/server/routers/telemetry.py`
- **UI 확인용 테스트 전략 상태 메시지를 강제로 발생시킵니다.** (1 connections) — `src/server/routers/telemetry.py`
- **지정된 날짜 이전의 삭제 대상 데이터(체결 및 캔들) 건수를 미리 조회합니다.** (1 connections) — `src/server/routers/telemetry.py`
- **지정된 날짜 이전의 체결 데이터 및 캔들 데이터를 영구 삭제합니다.** (1 connections) — `src/server/routers/telemetry.py`
- **UI 확인용 테스트 알림을 강제로 발생시킵니다.** (1 connections) — `src/server/routers/telemetry.py`
- **UI 확인용 테스트 전략 상태 메시지를 강제로 발생시킵니다.** (1 connections) — `src/server/routers/telemetry.py`
- **지정된 날짜 이전의 삭제 대상 데이터(체결 및 캔들) 건수를 미리 조회합니다.** (1 connections) — `src/server/routers/telemetry.py`
- **지정된 날짜 이전의 체결 데이터 및 캔들 데이터를 영구 삭제합니다.** (1 connections) — `src/server/routers/telemetry.py`
- **UI 확인용 테스트 알림을 강제로 발생시킵니다.** (1 connections) — `src/server/routers/telemetry.py`
- **UI 확인용 테스트 전략 상태 메시지를 강제로 발생시킵니다.** (1 connections) — `src/server/routers/telemetry.py`
- **지정된 날짜 이전의 데이터를 삭제합니다.** (1 connections) — `src/server/routers/telemetry.py`

## Relationships

- [[실시간 데이터 DB]] (5 shared connections)
- [[기능 모듈 그룹 41]] (5 shared connections)
- [[시스템 메인 및 진입점]] (3 shared connections)
- [[주문 실행 및 포]] (3 shared connections)
- [[기능 모듈 그룹 26]] (3 shared connections)
- [[Community 56]] (2 shared connections)
- [[Community 43]] (2 shared connections)
- [[매매 전략 알고리즘]] (2 shared connections)
- [[기능 모듈 그룹 30]] (2 shared connections)
- [[백테스트 및 실시간]] (2 shared connections)
- [[데이터베이스 및 영속성 레이어]] (2 shared connections)
- [[Community 55]] (1 shared connections)

## Source Files

- `src/database/connection.py`
- `src/server/routers/telemetry.py`

## Audit Trail

- EXTRACTED: 42 (49%)
- INFERRED: 44 (51%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*