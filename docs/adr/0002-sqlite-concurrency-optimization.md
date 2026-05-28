# 0002. SQLite 동시성 및 조회 성능 최적화

* Status: accepted
* Date: 2026-05-15

## Context and Problem Statement

시스템 시작 시 수백 개의 종목에 대해 동시에 **Warm-up** 작업을 수행하면서 SQLite `database is locked` 에러가 빈번하게 발생했습니다. 또한 데이터가 수만 건 이상 쌓이면서 종목별 최신 틱 데이터를 조회하는 속도가 눈에 띄게 저하되었습니다.

## Decision Outcome

SQLite의 병렬 처리 능력을 극대화하고 조회 성능을 보장하기 위해 다음과 같은 최적화 기법을 적용하기로 결정했습니다.

### 세부 내용
1. **WAL (Write-Ahead Logging) 모드 적용**: 읽기와 쓰기 작업이 서로를 차단하지 않도록 설정했습니다.
2. **Busy Timeout (30s) 설정**: 일시적인 잠금 상태 발생 시 즉시 에러를 내지 않고 충분히 대기 후 재시도하도록 설정했습니다.
3. **복합 인덱스 생성**: `trades` 테이블에 `(symbol, trade_timestamp DESC)` 인덱스를 생성하여 종목별 최신 데이터 조회 성능을 O(1)에 가깝게 최적화했습니다.
4. **Semaphore 제어**: 동시 DB 접속 수를 최대 10개로 제한하여 시스템 자원 경합을 방지했습니다.

## Consequences

* **Positive**: `database is locked` 에러가 완전히 해결되었으며, 서버 초기화(Warm-up) 속도가 수십 배 이상 향상되었습니다.
* **Negative**: 인덱스 추가로 인해 저장 공간이 소폭 증가하고 쓰기 성능에 미세한 영향이 있을 수 있으나, 현재의 읽기 위주 패턴에서는 이득이 훨씬 큽니다.
