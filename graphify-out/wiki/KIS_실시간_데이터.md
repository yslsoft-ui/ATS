# KIS 실시간 데이터

> 35 nodes · cohesion 0.50

## Key Concepts

- **KisCollector** (29 connections) — `src/engine/collector_kis.py`
- **._ranking_loop()** (6 connections) — `src/engine/collector_kis.py`
- **.stop()** (5 connections) — `src/engine/collector_kis.py`
- **.fetch_market_rank()** (5 connections) — `src/engine/collector_kis.py`
- **.run()** (5 connections) — `src/engine/collector_kis.py`
- **._handle_message()** (5 connections) — `src/engine/collector_kis.py`
- **.start()** (4 connections) — `src/engine/collector_kis.py`
- **collector_kis.py** (3 connections) — `src/engine/collector_kis.py`
- **market_hours.py** (3 connections) — `src/engine/utils/market_hours.py`
- **MarketHours** (3 connections) — `src/engine/utils/market_hours.py`
- **._is_market_open()** (3 connections) — `src/engine/collector_kis.py`
- **._prepare_connection()** (2 connections) — `src/engine/collector_kis.py`
- **._start_additional_tasks()** (2 connections) — `src/engine/collector_kis.py`
- **._handle_connection_error()** (2 connections) — `src/engine/collector_kis.py`
- **KIS 웹소켓 메시지 파싱 및 큐 전달** (2 connections) — `src/engine/collector_kis.py`
- **.__init__()** (1 connections) — `src/engine/collector_kis.py`
- **exchange()** (1 connections) — `src/engine/collector_kis.py`
- **._fetch_symbols()** (1 connections) — `src/engine/collector_kis.py`
- **._get_websocket_url()** (1 connections) — `src/engine/collector_kis.py`
- **._subscribe()** (1 connections) — `src/engine/collector_kis.py`
- **._parse_message()** (1 connections) — `src/engine/collector_kis.py`
- **._pre_connect_check()** (1 connections) — `src/engine/collector_kis.py`
- **한국투자증권(KIS) API로부터 국내 주식 실시간 체결 데이터를 수집합니다.** (1 connections) — `src/engine/collector_kis.py`
- **is_krx_open()** (1 connections) — `src/engine/utils/market_hours.py`
- **time_until_open()** (1 connections) — `src/engine/utils/market_hours.py`
- *... and 10 more nodes in this community*

## Relationships

- [[시스템 메인 및 진입점]] (2 shared connections)
- [[백테스트 및 실시간]] (2 shared connections)
- [[Upbit 실시간 체]] (1 shared connections)
- [[포트폴리오 및 자산 관리]] (1 shared connections)
- [[기능 모듈 그룹 32]] (1 shared connections)
- [[Community 60]] (1 shared connections)
- [[Community 58]] (1 shared connections)

## Source Files

- `src/engine/collector_kis.py`
- `src/engine/utils/market_hours.py`

## Audit Trail

- EXTRACTED: 89 (90%)
- INFERRED: 10 (10%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*