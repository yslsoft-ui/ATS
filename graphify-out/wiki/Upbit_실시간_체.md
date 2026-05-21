# Upbit 실시간 체

> 23 nodes · cohesion 0.50

## Key Concepts

- **UpbitCollector** (20 connections) — `src/engine/collector.py`
- **.data_processor_worker()** (6 connections) — `src/engine/collector.py`
- **.run()** (6 connections) — `src/engine/collector.py`
- **.background_warmup()** (5 connections) — `src/engine/collector.py`
- **collector.py** (3 connections) — `src/engine/collector.py`
- **BaseCollector** (3 connections)
- **.start()** (2 connections) — `src/engine/collector.py`
- **종목별 워밍업을 백그라운드에서 하나씩 천천히 수행합니다.** (2 connections) — `src/engine/collector.py`
- **exchange()** (1 connections) — `src/engine/collector.py`
- **._fetch_symbols()** (1 connections) — `src/engine/collector.py`
- **._get_websocket_url()** (1 connections) — `src/engine/collector.py`
- **._subscribe()** (1 connections) — `src/engine/collector.py`
- **._parse_message()** (1 connections) — `src/engine/collector.py`
- **업비트 API로부터 실시간 체결 데이터를 수집하고 분석 엔진으로 배분합니다.** (1 connections) — `src/engine/collector.py`
- **.__init__()** (1 connections) — `src/engine/collector.py`
- **.stop()** (1 connections) — `src/engine/collector.py`
- **업비트 API로부터 실시간 체결 데이터를 수집하고 분석 엔진으로 배분합니다.** (1 connections) — `src/engine/collector.py`
- **종목별 워밍업을 백그라운드에서 하나씩 천천히 수행합니다.** (1 connections) — `src/engine/collector.py`
- **큐에서 데이터를 꺼내 분석 및 전략 실행을 담당하는 워커** (1 connections) — `src/engine/collector.py`
- **종목별 워밍업을 백그라운드에서 하나씩 천천히 수행합니다.** (1 connections) — `src/engine/collector.py`
- **큐에서 데이터를 꺼내 분석 및 전략 실행을 담당하는 워커** (1 connections) — `src/engine/collector.py`
- **큐에서 데이터를 꺼내 분석 및 전략 실행을 담당하는 워커** (1 connections) — `src/engine/collector.py`
- **# TODO: 전략 설정을 주입받도록 개선 필요** (1 connections) — `src/engine/collector.py`

## Relationships

- [[시스템 메인 및 진입점]] (2 shared connections)
- [[백테스트 및 실시간]] (2 shared connections)
- [[기능 모듈 그룹 32]] (2 shared connections)
- [[포트폴리오 및 자산 관리]] (1 shared connections)
- [[Community 60]] (1 shared connections)
- [[기능 모듈 그룹 29]] (1 shared connections)
- [[KIS 실시간 데이터]] (1 shared connections)

## Source Files

- `src/engine/collector.py`

## Audit Trail

- EXTRACTED: 54 (87%)
- INFERRED: 8 (13%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*