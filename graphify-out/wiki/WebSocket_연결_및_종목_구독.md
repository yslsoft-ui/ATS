# WebSocket 연결 및 종목 구독

> 22 nodes · cohesion 0.50

## Key Concepts

- **ConnectionManager** (22 connections) — `src/server/main.py`
- **.broadcast()** (8 connections) — `src/server/main.py`
- **.subscribe()** (6 connections) — `src/server/main.py`
- **websocket_endpoint()** (4 connections) — `src/server/main.py`
- **.connect()** (2 connections) — `src/server/main.py`
- **.disconnect()** (2 connections) — `src/server/main.py`
- **클라이언트가 특정 시장의 종목을 구독합니다.** (2 connections) — `src/server/main.py`
- **WebSocket 연결 및 종목별 구독을 관리합니다.** (2 connections) — `src/server/main.py`
- **.__init__()** (1 connections) — `src/server/main.py`
- **WebSocket 연결 및 종목별 구독을 관리합니다.** (1 connections) — `src/server/main.py`
- **클라이언트가 특정 시장의 종목을 구독합니다.** (1 connections) — `src/server/main.py`
- **해당 종목을 구독 중인 클라이언트에게만 전송합니다.** (1 connections) — `src/server/main.py`
- **WebSocket 연결 및 종목별 구독을 관리합니다.** (1 connections) — `src/server/main.py`
- **해당 종목을 구독 중인 클라이언트에게만 전송합니다.** (1 connections) — `src/server/main.py`
- **클라이언트가 특정 시장의 종목을 구독합니다.** (1 connections) — `src/server/main.py`
- **해당 종목을 구독 중인 클라이언트에게만 전송합니다.** (1 connections) — `src/server/main.py`
- **WebSocket 연결 및 종목별 구독을 관리합니다.** (1 connections) — `src/server/main.py`
- **클라이언트가 특정 시장의 종목을 구독합니다.** (1 connections) — `src/server/main.py`
- **해당 종목을 구독 중인 클라이언트에게만 전송합니다.** (1 connections) — `src/server/main.py`
- **WebSocket 연결 및 종목별 구독을 관리합니다.** (1 connections) — `src/server/main.py`
- **해당 종목을 구독 중인 클라이언트에게만 전송합니다.** (1 connections) — `src/server/main.py`
- **WebSocket 연결 및 종목별 구독을 관리합니다.** (1 connections) — `src/server/main.py`

## Relationships

- [[기능 모듈 그룹 41]] (2 shared connections)
- [[포트폴리오 및 자산 관리]] (2 shared connections)
- [[기능 모듈 그룹 32]] (2 shared connections)
- [[UI 테스트 알림]] (2 shared connections)
- [[시장 데이터 수집 및 가공]] (1 shared connections)
- [[매매 전략 알고리즘]] (1 shared connections)
- [[백테스트 및 실시간]] (1 shared connections)
- [[Community 60]] (1 shared connections)
- [[시스템 메인 및 진입점]] (1 shared connections)
- [[Community 42]] (1 shared connections)

## Source Files

- `src/server/main.py`

## Audit Trail

- EXTRACTED: 53 (85%)
- INFERRED: 9 (15%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*