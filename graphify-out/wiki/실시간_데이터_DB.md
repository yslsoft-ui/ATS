# 실시간 데이터 DB

> 28 nodes · cohesion 0.50

## Key Concepts

- **DatabaseWriter** (15 connections) — `src/database/writer.py`
- **test_database_writer_flow()** (7 connections) — `src/database/test_db_writer.py`
- **._candle_writer_loop()** (5 connections) — `src/database/writer.py`
- **._force_flush_remaining()** (5 connections) — `src/database/writer.py`
- **test_db_writer.py** (4 connections) — `src/database/test_db_writer.py`
- **MockCandle** (4 connections) — `src/database/test_db_writer.py`
- **setup_test_db()** (4 connections) — `src/database/test_db_writer.py`
- **.start()** (4 connections) — `src/database/writer.py`
- **._flush_candles_to_db()** (4 connections) — `src/database/writer.py`
- **teardown_test_db()** (3 connections) — `src/database/test_db_writer.py`
- **.stop()** (3 connections) — `src/database/writer.py`
- **._db_writer_loop()** (3 connections) — `src/database/writer.py`
- **.enqueue_tick()** (2 connections) — `src/database/writer.py`
- **.enqueue_candle()** (2 connections) — `src/database/writer.py`
- **.__init__()** (1 connections) — `src/database/test_db_writer.py`
- **테스트용 임시 SQLite 테이블 구조를 세팅합니다.** (1 connections) — `src/database/test_db_writer.py`
- **테스트 완료 후 임시 파일을 청소합니다.** (1 connections) — `src/database/test_db_writer.py`
- **DatabaseWriter의 비동기 라이프사이클과 최종 플러시 기능의 무결성을 검증합니다.** (1 connections) — `src/database/test_db_writer.py`
- **writer.py** (1 connections) — `src/database/writer.py`
- **.__init__()** (1 connections) — `src/database/writer.py`
- **틱(Trades)과 캔들(Candles) 데이터를 비동기 큐에서 대기 수집하여      SQLite DB에 초고속 벌크 플러시(Bulk Flus** (1 connections) — `src/database/writer.py`
- **비동기 DB/Candle 영속화 플러시 워커 태스크를 기동합니다.** (1 connections) — `src/database/writer.py`
- **데이터베이스 엔진을 안전하게 중단하고, 큐에 남아 있는 모든 잔여 틱과 캔들을 완전히 플러시합니다.** (1 connections) — `src/database/writer.py`
- **실시간 수신 틱 데이터를 비동기 DB 대기 큐에 안전하게 투입합니다.** (1 connections) — `src/database/writer.py`
- **전략 엔진에서 완성된 캔들 데이터를 비동기 DB 대기 큐에 안전하게 투입합니다.** (1 connections) — `src/database/writer.py`
- *... and 3 more nodes in this community*

## Relationships

- [[ATS 텔레]] (5 shared connections)
- [[Community 60]] (1 shared connections)
- [[매매 전략 알고리즘]] (1 shared connections)

## Source Files

- `src/database/test_db_writer.py`
- `src/database/writer.py`

## Audit Trail

- EXTRACTED: 68 (86%)
- INFERRED: 11 (14%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*