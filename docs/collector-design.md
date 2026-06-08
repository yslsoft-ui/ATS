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

### 2.4. 비동기 백필 및 벌크 최적화 전략 (Backfill Optimization)

* **비동기 백필 기동:** 수집기가 구동될 때 과거 누락된 1분봉 데이터를 동기화하는 백필 작업이 실시간 수집을 차단하지 않도록 `asyncio.create_task(self.backfill_candles(config))`로 비동기 실행합니다. 이를 통해 구동 즉시 1~2초 내에 실시간 소켓 연결이 맺어지며 데이터 수집이 시작되고, 과거 누락분은 백그라운드에서 병행 수집됩니다.
* **벌크 병합 백필 (Bulk Merged Backfill):** 디스크 DB에 듬성듬성 비어있는 과거 누락 캔들을 채울 때, 개별 틈새(Gap)마다 요청을 쪼개어 API를 날리지 않고, 전체 누락 타임스탬프 중 `[min_missing, max_missing]`의 단일 대형 구간을 계산하여 단 1회의 벌크 API 호출로 데이터를 수집합니다. 수집된 캔들 중 이미 로컬 DB에 존재하는 데이터는 메모리 상에서 중복 필터링(Duplicate Filtering)하여 순수 누락 데이터만 저장함으로써 API 호출 횟수를 최대 90% 이상 획기적으로 절감했습니다.
* **지능적 탐색 범위 축소 (Intelligent Lookback Reduction):** 데몬 재기동 시 고정된 과거 24시간 전체 범위를 맹목적으로 백필하는 낭비를 방지하기 위해, 각 종목별 DB 내 최신 캔들 시각(`MAX(timestamp)`)을 사전에 조회합니다. 이후 백필 조사 구간의 시작점(`max_lookback`)을 `max(기본 24시간 전, DB 최신 캔들 시각)`으로 제한하여 탐색 범위를 지능적으로 축소하고, 불필요한 거래소 API 호출 트래픽 및 DB 쿼리 부하를 줄였습니다.
* **재연결 및 안정성 전략**
  - **Ping/Pong**: Upbit 서버와의 세션 유지를 위해 주기적인 Ping-Pong 체크.
  - **재연결**: 연결 끊김 발생 시 재연결 시도.
    - `CollectorManager` (통합 서버): 5초 고정 대기 후 재연결.
    - `UpbitCollector` (독립 수집기): 2초 고정 대기 후 재귀적 재연결.
    - ⚠️ **TODO**: Exponential Backoff 방식으로 개선 필요.
  - **중복 방지**: `sequential_id`를 DB에 저장하지만, 중복 체크 로직은 미구현.
    - ⚠️ **TODO**: `sequential_id` 기반 UNIQUE 제약조건 또는 INSERT OR IGNORE 적용 필요.
  - **서버 종료 안전성**: `@app.on_event("shutdown")`으로 수집기 태스크 정리.
  
### 2.5. 한국투자증권(KIS) 실시간 타임스탬프 보정
- **타임스탬프 신뢰성 확보**: KIS 실시간 WebSocket 통합 체결가(`H0UNCNT0`) 데이터 처리 시, 로컬 시스템의 수신 시각을 타임스탬프로 지정하는 대신 데이터에 명시된 `STCK_CNTG_HOUR`(주식 체결 시간, `HHMMSS` 포맷)를 파싱합니다.
- **날짜 결합 및 변환**: 파싱된 체결 시간 문자열을 로컬 당일 날짜와 결합한 뒤, 서울 타임존(`Asia/Seoul`) 기준의 정수형 Unix Timestamp(ms)로 정규화하여 전달합니다. 이를 통해 네트워크 지연으로 인해 1분 경계선(xx:00초 전후) 틱이 다른 분봉에 병합되어 가격 및 거래량이 왜곡되는 오동작을 원천적으로 방지합니다.

### 2.6. KIS 통합 호가 및 장운영정보 활용
- **통합 호가 (`H0UNASP0`)**: KRX와 NXT의 복수 시장 환경 대응을 위해, 실시간 주식 호가 수집 시 개별 시장 호가 대신 통합 호가인 `H0UNASP0` 규격을 채택하여 데이터 파이프라인의 명세를 일치시킵니다.
- **장운영정보 (`H0UNMKO0`)**: 시장 전체의 거래정지/서킷브레이커 및 개별 종목의 VI 발동 상황을 실시간으로 감지하기 위해 `H0UNMKO0`를 웹소켓으로 구독합니다. `TRHT_YN` (거래정지 여부), `TR_SUSP_REAS_CNTT` (정지 사유) 필드 등을 분석하여 거래소의 `SUSPENDED` 상태를 명시적으로 파악하고 시스템 주문 제어 및 화면 경고 표시에 연동합니다.

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
