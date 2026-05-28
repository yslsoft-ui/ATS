# 데이터 콜렉터 상세 설계 (COLLECTOR_DESIGN.md)

이 문서는 Upbit WebSocket을 통해 실시간 체결 데이터를 수집하고 이를 SQLite 데이터베이스에 저장하기 위한 상세 설계를 다룹니다.

## 1. 데이터베이스 스키마 (Database Schema)

효율적인 틱 데이터 저장 및 조회를 위해 다음과 같은 스키마를 사용합니다. (`src/database/schema.py`)

### 1.1. `trades` 테이블

실시간 체결 정보를 저장하는 메인 테이블입니다.

| 컬럼명 | 데이터 타입 | 설명 |
| :--- | :--- | :--- |
| `id` | INTEGER PRIMARY KEY | 내부 식별자 (Auto Increment) |
| `exchange` | TEXT | 거래소 명칭 (예: UPBIT, BINANCE) |
| `symbol` | TEXT | 종목 코드 (예: KRW-BTC, BTCUSDT) |
| `trade_price` | REAL | 체결 가격 |
| `trade_volume` | REAL | 체결 수량 |
| `ask_bid` | TEXT | 매수/매도 구분 (BID/ASK) |
| `trade_timestamp` | INTEGER | 체결 시각 (Unix Timestamp, ms 단위) |
| `sequential_id` | INTEGER | 각 거래소의 고유 체결 번호 |
| `created_at` | DATETIME | DB 저장 시각 |

- **인덱스 설계**:
  - `CREATE INDEX idx_exch_sym_time ON trades (exchange, symbol, trade_timestamp);`
  - 사유: 거래소별/종목별 시간 범위 조회를 위한 인덱스 최적화.

> ⚠️ **참고**: `src/server/main.py`의 간이 CREATE문은 `exchange`, `sequential_id`, `created_at` 컬럼이 누락된 축소 스키마를 사용합니다. 정식 스키마 초기화 시 `schema.py`를 사용하세요.

### 1.2. `orderbooks` 테이블

호가창(Orderbook) 스냅샷 데이터를 저장하는 테이블입니다. 백테스트 시 동적 슬리피지 계산에 사용됩니다.

| 컬럼명 | 데이터 타입 | 설명 |
| :--- | :--- | :--- |
| `id` | INTEGER PRIMARY KEY | 내부 식별자 |
| `exchange` | TEXT | 거래소 명칭 |
| `symbol` | TEXT | 종목 코드 |
| `timestamp` | INTEGER | 호가 생성 시각 (ms) |
| `bids` | TEXT | 매수 호가 및 잔량 데이터 (JSON 구조: `[{"price": 100, "size": 1.5}, ...]`) |
| `asks` | TEXT | 매도 호가 및 잔량 데이터 (JSON 구조: `[{"price": 101, "size": 2.0}, ...]`) |
| `created_at` | DATETIME | DB 저장 시각 |

- **인덱스 설계**:
  - `CREATE INDEX idx_ob_exch_sym_time ON orderbooks (exchange, symbol, timestamp);`

## 2. 수집기 아키텍처 (Collector Architecture)

현재 수집기는 **두 가지 구현체**가 존재합니다.

### 2.1. 통합 서버 내장 수집기 — `src/server/main.py: CollectorManager` (메인)

서버 프로세스 내에서 `asyncio.Task`로 동작하며, 웹 UI를 통해 원격 제어합니다.

- **WebSocket 라이브러리**: `aiohttp`
- **수집 대상**: KRW-BTC, KRW-ETH, KRW-XRP, KRW-SOL, KRW-DOGE (5개 종목)
- **데이터 유형**: `trade` (체결) 데이터만 구독
- **처리 흐름**:
  1. WebSocket 수신 → 즉시 `ConnectionManager.broadcast()`로 브라우저에 PUSH.
  2. 동시에 DB INSERT 실행 → **50건마다 배치 커밋**.
- **제어 API**:
  - `POST /collector/start`: 수집 시작
  - `POST /collector/stop`: 수집 중단 (task cancel)
  - `GET /collector/status`: 실행 상태 조회
- **재연결**: 에러 발생 시 5초 후 재연결 (고정 간격).

### 2.2. 독립 수집기 — `src/collector/upbit_ws.py: UpbitCollector` (대체)

별도 프로세스로 실행 가능한 Queue 기반 수집기입니다.

- **WebSocket 라이브러리**: `websockets`
- **데이터 유형**: `trade` + `orderbook` 모두 구독
- **처리 흐름**: 듀얼 트랙(Dual-Track) 파이프라인 구조
  1. **Dispatcher**: WebSocket 수신 데이터를 `asyncio.Queue`에 삽입.
  2. **DB Writer** (`src/database/db_writer.py`): Queue에서 데이터를 꺼내 배치 처리.
     - **Batch Size**: 100건
     - **Flush Interval**: 1초 (타임아웃 시 잔여 버퍼 플러시)
     - trade와 orderbook 버퍼를 분리 관리.
     - `executemany()`로 벌크 INSERT.

### 2.3. 데이터 관리 API — `src/server/main.py`

- `POST /data/cleanup?date=YYYY-MM-DD`: 지정된 날짜 이전의 trades 데이터를 영구 삭제.

### 2.4. 재연결 및 안정성 전략

- **Ping/Pong**: Upbit 서버와의 세션 유지를 위해 주기적인 Ping-Pong 체크.
- **재연결**: 연결 끊김 발생 시 재연결 시도.
  - `CollectorManager` (통합 서버): 5초 고정 대기 후 재연결.
  - `UpbitCollector` (독립 수집기): 2초 고정 대기 후 재귀적 재연결.
  - ⚠️ **TODO**: Exponential Backoff 방식으로 개선 필요.
- **중복 방지**: `sequential_id`를 DB에 저장하지만, 중복 체크 로직은 미구현.
  - ⚠️ **TODO**: `sequential_id` 기반 UNIQUE 제약조건 또는 INSERT OR IGNORE 적용 필요.
- **서버 종료 안전성**: `@app.on_event("shutdown")`으로 수집기 태스크 정리.

## 3. 구현 기술 세부 사항

- **Library**: `aiohttp` (통합 서버), `websockets` (독립 수집기), `aiosqlite` (비동기 DB 처리).
- **Batch Size**: 통합 서버 50건, 독립 수집기 100건.
- **WAL Mode**: `schema.py`에서 설정 (`PRAGMA journal_mode=WAL`). 통합 서버의 간이 초기화에서는 미설정.
- **Storage**: `data/backtest.db` (로컬 파일 시스템).

## 4. 검증 계획 (Verification Plan)

- [x] Upbit WebSocket 연결 및 메시지 수신 확인.
- [x] 수신된 데이터가 정의된 스키마에 맞게 파싱되는지 확인.
- [ ] 1분간 수집 후 DB에 저장된 데이터 개수가 실제 수신된 데이터 개수와 일치하는지 검증.
- [ ] 중복 데이터 유입 시 `sequential_id` 필터를 통해 정상적으로 무시되는지 테스트.
- [x] 수집기 시작/중지 API가 정상 동작하는지 웹 UI를 통해 확인.
- [x] 배치 커밋이 서버 안정성에 미치는 영향 확인 (50건 배치 적용 후 안정).
