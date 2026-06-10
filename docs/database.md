# 데이터베이스 상세 명세 (Database Specification)

이 문서는 통합 실시간 매매 시스템(ATS)의 SQLite 데이터베이스 스키마, 테이블 제약조건, 관계성 및 성능 향상을 위한 인덱스 구조를 정의합니다.

- **데이터베이스 파일 위치**: `data/backtest.db`
- **구현 관리 파일**: [schema.py](file:///home/simon/ATS/src/database/schema.py)

---

## 1. 테이블 정의 (Table Definitions)

### 1.1. exchanges (거래소 마스터)
시스템 내부에서 처리하는 시장/거래소 정보를 저장합니다.

| 컬럼명 | 데이터 타입 | 제약조건 / 기본값 | 설명 |
| :--- | :--- | :--- | :--- |
| **id** (PK) | TEXT | NOT NULL | 거래소 고유 식별자 (`upbit`, `kis`) |
| **name** | TEXT | NOT NULL | 거래소 표시명 (`Upbit`, `KIS`) |
| **fee_rate** | REAL | DEFAULT 0.0005 | 거래소 수수료율 |
| **market_type** | TEXT | DEFAULT 'crypto' | 자산군 분류 (`crypto`, `stock`) |
| **created_at** | DATETIME | DEFAULT CURRENT_TIMESTAMP | 레코드 생성 일시 |
| **updated_at** | DATETIME | DEFAULT CURRENT_TIMESTAMP | 레코드 최종 변경 일시 |

---

### 1.2. trades (실시간 틱 데이터)
거래소로부터 실시간 수신한 개별 체결(Tick) 내역을 기록합니다.

| 컬럼명 | 데이터 타입 | 제약조건 / 기본값 | 설명 |
| :--- | :--- | :--- | :--- |
| **id** (PK) | INTEGER | PRIMARY KEY AUTOINCREMENT | 체결 레코드 순번 |
| **exchange** | TEXT | - | 거래소 ID (`upbit`, `bithumb`, `kis`) |
| **market** | TEXT | - | 세부 시장 (예: `KRW`, `BTC` / `KRX`, `NXT`) |
| **symbol** | TEXT | - | 순수 자산 심볼 (예: `BTC`, `005930`) |
| **trade_price** | REAL | - | 체결 가격 |
| **trade_volume** | REAL | - | 체결 수량 |
| **ask_bid** | TEXT | - | 매수/매도 구분 (`ASK`, `BID`) |
| **trade_timestamp** | INTEGER | - | 거래소 기준 체결 타임스탬프 (ms) |
| **sequential_id** | INTEGER | - | 거래소 제공 순차 ID (동시간 체결 정렬용) |
| **created_at** | DATETIME | DEFAULT CURRENT_TIMESTAMP | 로컬 DB 기록 일시 |

---

### 1.3. portfolios (시뮬레이션 포트폴리오 마스터)
백테스트 및 실시간 거래 시뮬레이션 과정에서 운용되는 포트폴리오의 마스터 정보를 관리합니다.

| 컬럼명 | 데이터 타입 | 제약조건 / 기본값 | 설명 |
| :--- | :--- | :--- | :--- |
| **id** (PK) | TEXT | NOT NULL | 포트폴리오 고유 ID (예: 백테스트 UUID) |
| **name** | TEXT | NOT NULL | 포트폴리오 식별 이름 |
| **exchange_id** | TEXT | DEFAULT 'upbit' | 기본 대상 거래소 ID |
| **type** | TEXT | NOT NULL | 운용 타입 (`backtest`, `paper`, `live`) |
| **initial_cash** | REAL | DEFAULT 1000000 | 최초 운용 가능 현금 자산 |
| **cash** | REAL | DEFAULT 0 | 현재 운용 가능 현금 자산 (통합) |
| **duration** | REAL | DEFAULT 0.0 | 백테스트 소요 시간 등 운용 경과 시간 (초) |
| **strategy_info** | TEXT | DEFAULT '' | 연결된 매매 전략 설정 정보 (JSON 스트링 등) |
| **created_at** | DATETIME | DEFAULT CURRENT_TIMESTAMP | 생성 일시 |
| **updated_at** | DATETIME | DEFAULT CURRENT_TIMESTAMP | 최종 갱신 일시 |

---

### 1.4. portfolio_exchanges (포트폴리오-거래소 맵 및 세부 잔고)
하나의 포트폴리오가 복수의 거래소 자산을 동시에 보유/관리할 수 있도록 보장하는 중간 맵 테이블입니다.

| 컬럼명 | 데이터 타입 | 제약조건 / 기본값 | 설명 |
| :--- | :--- | :--- | :--- |
| **portfolio_id** (PK, FK) | TEXT | REFERENCES portfolios(id) ON UPDATE CASCADE ON DELETE CASCADE | 소속 포트폴리오 ID |
| **exchange_id** (PK) | TEXT | - | 해당 자산이 귀속된 거래소 ID |
| **initial_cash** | REAL | DEFAULT 0.0 | 해당 거래소용 초기 설정 현금 |
| **cash** | REAL | DEFAULT 0.0 | 해당 거래소에서 운용 가능한 현재 현금 |
| **metrics** | TEXT | DEFAULT '{}' | 포트폴리오 성과 지표 (MDD, 승률, 누적수익률 등 JSON 포맷) |
| **created_at** | DATETIME | DEFAULT CURRENT_TIMESTAMP | 생성 일시 |
| **updated_at** | DATETIME | DEFAULT CURRENT_TIMESTAMP | 최종 변경 일시 |

---

### 1.5. positions (보유 자산 포지션)
포트폴리오가 현재 실시간/가상으로 보유 중인 자산 목록을 상세 기록합니다. (국내 주식의 경우 KRX와 NXT의 세부 구분 없이 단일 `symbol` 하에 통합 합산되어 잔고가 관리됩니다.)

| 컬럼명 | 데이터 타입 | 제약조건 / 기본값 | 설명 |
| :--- | :--- | :--- | :--- |
| **portfolio_id** (PK, FK) | TEXT | - | 소속 포트폴리오 ID |
| **exchange** (PK, FK) | TEXT | - | 보유 자산의 거래소 |
| **symbol** (PK) | TEXT | - | 자산 심볼 |
| **quantity** | REAL | DEFAULT 0 | 보유 수량 (실시간 매매 시 소수점 지원) |
| **avg_price** | REAL | DEFAULT 0 | 평균 매수 단가 |
| **updated_at** | DATETIME | DEFAULT CURRENT_TIMESTAMP | 최종 갱신 일시 |

* **외래키 제약조건**:
  - `(portfolio_id, exchange)`는 `portfolio_exchanges(portfolio_id, exchange_id)`를 참조하며, 부모 레코드 수정 시 `ON UPDATE CASCADE`, 삭제 시 `ON DELETE CASCADE` 처리됩니다.

---

### 1.6. orders_history (주문 내역 이력)
시뮬레이션 및 실제 매매 집행 과정에서 발생한 모든 주문 내역을 상세 저장합니다.

| 컬럼명 | 데이터 타입 | 제약조건 / 기본값 | 설명 |
| :--- | :--- | :--- | :--- |
| **id** (PK) | INTEGER | PRIMARY KEY AUTOINCREMENT | 주문 번호 (자동 증가) |
| **portfolio_id** (FK) | TEXT | REFERENCES portfolios(id) ON UPDATE CASCADE ON DELETE CASCADE | 발주한 포트폴리오 ID |
| **exchange** | TEXT | - | 주문 거래소 |
| **market** | TEXT | - | 주문 및 실제 체결된 세부 시장 (예: `KRW` / `KRX`, `NXT`, `SOR`) |
| **strategy_id** | TEXT | - | 발주를 유도한 매매 전략 ID |
| **symbol** | TEXT | - | 주문 대상 자산 심볼 |
| **side** | TEXT | - | 주문 구분 (`BUY`: 매수, `SELL`: 매도) |
| **price** | REAL | - | 주문 체결 단가 |
| **quantity** | REAL | - | 주문 체결 수량 |
| **fee** | REAL | - | 주문 시 차감된 수수료 |
| **timestamp** | INTEGER | - | 체결 시점 타임스탬프 (Unix Time) |
| **reason** | TEXT | - | 주문 트리거 사유 (예: `RSI Under 30`) |
| **context** | TEXT | - | 주문 당시의 상태 맥락 스냅샷 (JSON 스트링) |

---

### 1.7. alerts (급등락 실시간 알림)
실시간 가격 급등락(Spike) 감지 또는 특정 지표 조건 돌파 시 발생한 이벤트를 영속화합니다.

| 컬럼명 | 데이터 타입 | 제약조건 / 기본값 | 설명 |
| :--- | :--- | :--- | :--- |
| **id** (PK) | INTEGER | PRIMARY KEY AUTOINCREMENT | 알림 고유 번호 |
| **exchange** | TEXT | - | 감지 대상 거래소 |
| **symbol** | TEXT | - | 자산 심볼 |
| **price** | REAL | - | 감지 시점의 체결가 |
| **msg** | TEXT | - | 사용자 경고 메시지 내용 (예: `[Spike] BTC 가격 3.5% 급등!`) |
| **timestamp** | INTEGER | - | 감지 시점 타임스탬프 (ms) |

---

### 1.8. candles (OHLCV 캔들스틱 데이터)
틱 데이터를 가변 인터벌 단위로 변환 및 취합한 역사적(Historical) 캔들 차트 정보입니다.

| 컬럼명 | 데이터 타입 | 제약조건 / 기본값 | 설명 |
| :--- | :--- | :--- | :--- |
| **exchange** (PK) | TEXT | - | 대상 거래소 |
| **symbol** (PK) | TEXT | - | 자산 심볼 |
| **interval** (PK) | INTEGER | - | 캔들 주기(초 단위, 예: 1, 3, 5, 10, 60 등) |
| **timestamp** (PK) | INTEGER | - | 캔들 시작 타임스탬프 (ms, 정규화된 시점) |
| **open** | REAL | - | 시가 (Open) |
| **high** | REAL | - | 고가 (High) |
| **low** | REAL | - | 저가 (Low) |
| **close** | REAL | - | 종가 (Close) |
| **volume** | REAL | - | 해당 기간 총 누적 거래량 (Volume) |

---

### 1.9. asset_master (전체 자산 한글명 및 정보 마스터)
전체 거래 대상 자산의 메타데이터와 국가별 한글명을 일괄 캐시/관리합니다.

| 컬럼명 | 데이터 타입 | 제약조건 / 기본값 | 설명 |
| :--- | :--- | :--- | :--- |
| **symbol** (PK) | TEXT | NOT NULL | 자산 심볼 (예: `BTC`, `005930`) |
| **korean_name** | TEXT | NOT NULL | 한글 종목명 (예: `비트코인`, `삼성전자`) |
| **asset_type** | TEXT | NOT NULL | 자산 속성 구분 (`crypto`, `stock`) |
| **created_at** | DATETIME | DEFAULT CURRENT_TIMESTAMP | 생성 일시 |
| **updated_at** | DATETIME | DEFAULT CURRENT_TIMESTAMP | 최종 변경 일시 |

---

### 1.10. exchange_assets (거래소별 취급 자산 관리)
각 거래소에서 실제 거래 가능하거나, 시스템에서 실시간으로 수집/트레이딩할 활성 자산 상태를 연결합니다.

| 컬럼명 | 데이터 타입 | 제약조건 / 기본값 | 설명 |
| :--- | :--- | :--- | :--- |
| **exchange** (PK) | TEXT | - | 거래소 ID |
| **symbol** (PK, FK) | TEXT | REFERENCES asset_master(symbol) ON UPDATE CASCADE | 자산 심볼 |
| **is_active** | INTEGER | DEFAULT 1 | 현재 수집 및 전략 감시 활성화 여부 (0: 비활성, 1: 활성) |
| **is_delisted** | INTEGER | DEFAULT 0 | 상장 폐지 여부 (1: 상장폐지) |
| **created_at** | DATETIME | DEFAULT CURRENT_TIMESTAMP | 등록 일시 |
| **updated_at** | DATETIME | DEFAULT CURRENT_TIMESTAMP | 상태 갱신 일시 |

### 1.11. system_events (시스템 및 데몬 운영 이력)
데몬 및 웹서버의 시작/종료, 거래소 수집기의 기동/중단, 사용자 수동 조작 요청, 거래소 서킷브레이커 발동 및 크래쉬 복구 감지 등 시스템 전반의 운영 상태와 이력을 영속화합니다.

| 컬럼명 | 데이터 타입 | 제약조건 / 기본값 | 설명 |
| :--- | :--- | :--- | :--- |
| **id** (PK) | INTEGER | PRIMARY KEY AUTOINCREMENT | 이벤트 고유 번호 |
| **event_type** | TEXT | NOT NULL | 이벤트 유형분류 (아래 참조) |
| **target** | TEXT | NOT NULL | 이벤트 대상 식별자 (예: `collector_daemon`, `kis` 등) |
| **message** | TEXT | - | 이벤트 상세 로그 메시지 |
| **timestamp** | INTEGER | NOT NULL | 로컬 밀리초 타임스탬프 (ms) |
| **context** | TEXT | - | 이벤트 발생 당시의 맥락 데이터 및 고유 `command_id` 정보 (JSON 스트링) |

#### 주요 이벤트 유형 (event_type) 및 구분 기준:
- **사용자 조작 감사 로그 (User Command Audit Loop)**
  - 각 사용자 조작 요청 시 `_REQUEST` 이벤트 선행 로깅 -> 작업 시도 -> 성공 시 `_SUCCESS`, 실패 시 `_FAILED` 이벤트가 `context`에 동일한 `command_id`(UUID)를 공유하며 한 쌍으로 기록됩니다.
  - `COLLECTOR_START_REQUEST` / `COLLECTOR_START_SUCCESS` / `COLLECTOR_START_FAILED`: 수집기 기동 제어
  - `COLLECTOR_STOP_REQUEST` / `COLLECTOR_STOP_SUCCESS` / `COLLECTOR_STOP_FAILED`: 수집기 중단 제어
  - `DAEMON_RESTART_SIGNAL_REQUEST` / `DAEMON_RESTART_SIGNAL_SUCCESS` / `DAEMON_RESTART_SIGNAL_FAILED`: 수집기/전략 데몬 자가 재기동 신호 송신 제어
  - `STRATEGY_ENABLE_REQUEST` / `STRATEGY_ENABLE_SUCCESS` / `STRATEGY_ENABLE_FAILED`: 특정 전략 활성화 제어
  - `STRATEGY_DISABLE_REQUEST` / `STRATEGY_DISABLE_SUCCESS` / `STRATEGY_DISABLE_FAILED`: 특정 전략 비활성화 제어
  - `STRATEGY_UPDATE_PARAMS_REQUEST` / `STRATEGY_UPDATE_PARAMS_SUCCESS` / `STRATEGY_UPDATE_PARAMS_FAILED`: 전략 설정 파라미터 업데이트 제어
  - `STRATEGY_SESSION_START_REQUEST` / `STRATEGY_SESSION_START_SUCCESS` / `STRATEGY_SESSION_START_FAILED`: 모의투자 세션 시작 제어
  - `STRATEGY_SESSION_END_REQUEST` / `STRATEGY_SESSION_END_SUCCESS` / `STRATEGY_SESSION_END_FAILED`: 모의투자 세션 종료 제어
  - `STRATEGY_SESSION_PANIC_REQUEST` / `STRATEGY_SESSION_PANIC_SUCCESS` / `STRATEGY_SESSION_PANIC_FAILED`: 포지션 긴급 전량 매도 및 비상 정지 제어
- **데몬 자동/완료 상태**
  - `DAEMON_START`: 데몬 프로세스 기동 완료 (프로그램 수정/배포 재시작 포함)
  - `DAEMON_STOP`: 데몬 프로세스의 안전 종료 완료 (Graceful Shutdown)
  - `COLLECTOR_START`: 거래소별 수집 모듈 실시간 기동 시작
  - `COLLECTOR_STOP`: 거래소별 수집 모듈 기동 중단
- **크래쉬 감지**
  - `DAEMON_CRASHED`: 데몬 재기동 시점에 이전 실행의 정상 종료 이력(`DAEMON_STOP`)이 존재하지 않아 비정상 종료(크래쉬)가 발생했음을 자동 감지하고 보완 등록한 이력
- **시장/거래소 상태**
  - `EXCHANGE_SUSPENDED`: 거래소 서킷브레이크/거래정지 상태 감지
  - `EXCHANGE_RESUMED`: 거래정지 상태 해제 및 정상 수집 복구
  - `EXCHANGE_ERROR`: 수집/통신 중 발생한 치명적 API 오류

---


## 2. 데이터베이스 인덱스 (Database Indexes)

데이터 로딩 성능 및 백테스트 조회 최적화를 위해 다음과 같은 복합/단일 인덱스를 운용합니다.

1. **`idx_trades_exch_sym_time`**
   - 대상 테이블: `trades`
   - 인덱스 구성 컬럼: `(exchange, symbol, trade_timestamp DESC)`
   - 목적: 특정 종목의 최근 체결 틱을 백테스트 엔진이나 캔들 복원기에서 시간 내림차순으로 매우 빠르게 조회하기 위함. 특히 1초봉 등 초 단위 저분봉 데이터를 백엔드에서 실시간 온디맨드 즉석 조립(Aggregation)하여 제공할 때, 대량의 틱 데이터를 30분 단위(13ms 수준)로 초고속 조회 및 가공하는 데 핵심적인 역할을 수행함.
2. **`idx_candles_exch_sym_time`**
   - 대상 테이블: `candles`
   - 인덱스 구성 컬럼: `(exchange, symbol, interval, timestamp DESC)`
   - 목적: 대시보드 차트 요청 시 최근 N개의 캔들(SMA, RSI 연산용) 데이터를 효율적으로 반환하기 위함.
3. **`idx_orders_history_portfolio_id`**
   - 대상 테이블: `orders_history`
   - 인덱스 구성 컬럼: `(portfolio_id)`
   - 목적: 특정 백테스트 시뮬레이션의 누적 주문 내역을 조회할 때 병목 현상을 방지하기 위함.
4. **`idx_positions_portfolio_id`**
   - 대상 테이블: `positions`
   - 인덱스 구성 컬럼: `(portfolio_id)`
   - 목적: 포트폴리오의 실자산 보유 비중 현황을 조회하기 위함.
5. **`idx_exchange_assets_active`**
   - 대상 테이블: `exchange_assets`
   - 인덱스 구성 컬럼: `(exchange, is_active)`
   - 목적: 데몬 구동 시 활성화된 수집 자산 종목들만 즉시 추출하여 수집 세션에 주입하기 위함.

---

### 1.12. strategy_versions (전략 활성 버전 마스터)
전략별 실시간으로 활성화되어 동작 중인 파라미터 셋과 롤백 소스 이력을 기록합니다.

| 컬럼명 | 데이터 타입 | 제약조건 / 기본값 | 설명 |
| :--- | :--- | :--- | :--- |
| **strategy_id** (PK) | TEXT | NOT NULL | 전략 고유 ID |
| **current_version_id** | INTEGER | NOT NULL | 현재 활성 버전 번호 |
| **current_params** | TEXT | NOT NULL | 현재 적용 중인 파라미터 JSON 문자열 |
| **rollback_source_version** | INTEGER | NULL | 롤백 발생 시, 원인이 되었던 문제 버전 ID |
| **applied_at** | INTEGER | NOT NULL | 파라미터 실제 적용 밀리초 시각 (ms) |
| **updated_at** | DATETIME | DEFAULT CURRENT_TIMESTAMP | 최종 변경 일시 |

---

### 1.13. strategy_parameter_history (전략 파라미터 변경 이력)
전략 파라미터의 변이 이력과 버전 분기 계보(Family Tree)를 역추적하기 위한 기록입니다.

| 컬럼명 | 데이터 타입 | 제약조건 / 기본값 | 설명 |
| :--- | :--- | :--- | :--- |
| **id** (PK) | INTEGER | PRIMARY KEY AUTOINCREMENT | 이력 번호 |
| **strategy_id** | TEXT | NOT NULL | 전략 ID |
| **version_id** | INTEGER | NOT NULL | 순차적으로 증가하는 버전 ID |
| **parent_version_id** | INTEGER | NULL | 부모 버전 ID (파생 계보 추적용) |
| **old_params** | TEXT | NULL | 변경 전 파라미터 JSON 문자열 |
| **new_params** | TEXT | NOT NULL | 변경 후 파라미터 JSON 문자열 |
| **proposal_id** | INTEGER | NULL | 연관된 승인 제안 ID (수동 변경 시 NULL) |
| **is_current** | INTEGER | DEFAULT 0 | 현재 활성 버전인지 여부 (0: 과거, 1: 현재) |
| **changed_by** | TEXT | NOT NULL | 변경 주체 (`USER`, `AUTO`) |
| **change_reason** | TEXT | NOT NULL | 변경 사유 (`MANUAL_UPDATE`, `PROPOSAL_APPLY`, `ROLLBACK`, `STARTUP_RESTORE`) |
| **created_at** | DATETIME | DEFAULT CURRENT_TIMESTAMP | 이력 생성 일시 |

---

### 1.14. strategy_performance_snapshots (전략 성과 스냅샷)
데몬 기동, 파라미터 변경, 롤백 및 주기적 성과 측정 시점의 실전 ROI 및 리스크 지표 스냅샷입니다.

| 컬럼명 | 데이터 타입 | 제약조건 / 기본값 | 설명 |
| :--- | :--- | :--- | :--- |
| **id** (PK) | INTEGER | PRIMARY KEY AUTOINCREMENT | 스냅샷 번호 |
| **strategy_id** | TEXT | NOT NULL | 대상 전략 ID |
| **version_id** | INTEGER | NOT NULL | 성과 측정 대상 전략 버전 |
| **parameter_hash** | TEXT | NOT NULL | 파라미터 JSON 해시값 (SHA-256) |
| **snapshot_type** | TEXT | NOT NULL | 이벤트 유형 (`PERIODIC`, `PARAMETER_CHANGE`, `ROLLBACK`, `STARTUP`) |
| **timestamp** | INTEGER | NOT NULL | 기록 시점 타임스탬프 (ms) |
| **roi** | REAL | - | 누적 ROI (%) |
| **mdd** | REAL | - | 누적 Max Drawdown (%) |
| **profit_factor** | REAL | - | Profit Factor |
| **win_rate** | REAL | - | 승률 (%) |
| **trade_count** | INTEGER | - | 체결 거래 건수 |
| **created_at** | DATETIME | DEFAULT CURRENT_TIMESTAMP | 생성 일시 |

---

### 1.15. market_regime_summaries (시장 상태 요약 피처)
1분 주기로 가공 수집된 시장 지표 피처로, 거시적 시장 특성을 요약해 AI 가설 분석의 기반 데이터로 사용합니다.

| 컬럼명 | 데이터 타입 | 제약조건 / 기본값 | 설명 |
| :--- | :--- | :--- | :--- |
| **id** (PK) | INTEGER | PRIMARY KEY AUTOINCREMENT | 기록 번호 |
| **timestamp** | INTEGER | NOT NULL | 1분 주기 버킷 밀리초 타임스탬프 (ms) |
| **symbol** | TEXT | NOT NULL | 종목 식별 기호 (`exchange:symbol` 형식) |
| **volatility** | REAL | - | 변동성 표준편차 비율 |
| **rsi** | REAL | - | 1분봉 기준 14분 RSI |
| **volume_ratio** | REAL | - | 최근 20분 평균 대비 직전 1분 거래량 비율 |
| **spread** | REAL | - | 1분간 체결 스프레드 비율 |
| **orderbook_imbalance**| REAL | - | 1분간 매수-매도 체결량 불균형 비율 |
| **created_at** | DATETIME | DEFAULT CURRENT_TIMESTAMP | 생성 일시 |

---

### 1.16. strategy_insights (분석 통계 인사이트)
손실 거래 분석을 통해 시장 Regime과 거래 매칭 결과를 종합하여 도출한 AI 인사이트입니다.

| 컬럼명 | 데이터 타입 | 제약조건 / 기본값 | 설명 |
| :--- | :--- | :--- | :--- |
| **id** (PK) | INTEGER | PRIMARY KEY AUTOINCREMENT | 인사이트 번호 |
| **portfolio_id** | TEXT | - | 소속 포트폴리오 ID |
| **strategy_id** | TEXT | - | 대상 전략 ID |
| **category** | TEXT | NOT NULL | 분류 (`STOP_LOSS`, `TRAILING_STOP`, `TIME_LIMIT`, `ENTRY_FILTER`) |
| **fact_summary** | TEXT | NOT NULL | 인사이트 텍스트 요약 |
| **details_json** | TEXT | - | 통계 상세 지표 데이터 (JSON) |
| **created_at** | DATETIME | DEFAULT CURRENT_TIMESTAMP | 생성 일시 |

---

### 1.17. strategy_proposals (전략 파라미터 개선 제안)
통계 분석 및 Shadow Backtest 검증을 거친 후 사용자 승인을 대기하는 파라미터 개선 제안 목록입니다.

| 컬럼명 | 데이터 타입 | 제약조건 / 기본값 | 설명 |
| :--- | :--- | :--- | :--- |
| **id** (PK) | INTEGER | PRIMARY KEY AUTOINCREMENT | 제안 번호 |
| **insight_id** | INTEGER | REFERENCES strategy_insights(id) | 매칭된 원인 인사이트 ID (수동 제안 시 NULL) |
| **proposal_group_id** | TEXT | - | 제안 그룹 식별자 |
| **version** | INTEGER | - | 제안 버전 |
| **portfolio_id** | TEXT | - | 대상 포트폴리오 ID |
| **strategy_id** | TEXT | - | 대상 전략 ID |
| **status** | TEXT | NOT NULL | 제안 상태 (`PENDING`, `APPROVED`, `APPLIED`, `ROLLED_BACK`, `REJECTED`, `DEFERRED`, `PRUNED`) |
| **outcome** | TEXT | NOT NULL | 실전 성패 결과 (`RUNNING`, `ROLLED_BACK`, `COMPLETED`) |
| **original_params** | TEXT | - | 변경 전 파라미터 JSON 문자열 |
| **proposed_params** | TEXT | - | 제안 파라미터 JSON 문자열 |
| **metrics** | TEXT | - | 백테스트 예측 성과 지표 (JSON) |
| **mutation_trace** | TEXT | - | 파라미터 변형 추적 이력 (JSON) |
| **confidence_score**| INTEGER | - | 제안 신뢰도 점수 (0~100) |
| **applied_at** | INTEGER | NULL | 실전 적용 완료 밀리초 시각 (ms) |
| **rolled_back_at** | INTEGER | NULL | 실전 적용 후 롤백 처리 밀리초 시각 (ms) |
| **decision_path_hash** | TEXT | - | 의사결정 해시 (SHA-256) |
| **audit_log_json** | TEXT | - | 채점 상세 정보 및 다양성 규제 로그 (JSON) |
| **counterfactual_roi** | REAL | DEFAULT 0.0 | 반사실적 가상 ROI (%) |
| **counterfactual_mdd** | REAL | DEFAULT 0.0 | 반사실적 가상 MDD (%) |
| **is_counterfactual_tracked** | INTEGER | DEFAULT 0 | 반사실적 가상 성과 추적 상태 (`0: 미대상/안함, 1: 추적중, 2: 만료/완료`) |
| **created_at** | DATETIME | DEFAULT CURRENT_TIMESTAMP | 제안 생성 일시 |
| **updated_at** | DATETIME | DEFAULT CURRENT_TIMESTAMP | 제안 최종 갱신 일시 |

---

### 1.18. proposal_evaluations (제안 사후 성과 평가)
승인/제안된 전략 또는 Shadow 후보 전략에 대한 다양한 가상/실제 Horizon(시간 기준 10m, 30m, 2h / 주식 세션 기준 등)별 사후 누적 성과와 Virtual Rollback 여부를 추적하고 예측 리스크 점수의 오차를 분석합니다. 1:N Horizon 관계를 위해 `(proposal_id, horizon_name)` 복합 유니크 제약을 적용합니다.

| 컬럼명 | 데이터 타입 | 제약조건 / 기본값 | 설명 |
| :--- | :--- | :--- | :--- |
| **id** (PK) | INTEGER | PRIMARY KEY AUTOINCREMENT | 평가 번호 |
| **proposal_id** | INTEGER | REFERENCES strategy_proposals(id) | 평가 대상 제안 ID |
| **horizon_name** | TEXT | NOT NULL | Horizon 식별 이름 (예: `10m`, `30m`, `market_close` 등) |
| **candidate_roi** | REAL | - | 가상/실제 후보 전략 누적 ROI (소수점 ratio) |
| **champion_roi** | REAL | - | 가상/실제 챔피언 전략 누적 ROI (소수점 ratio) |
| **roi_gap** | REAL | - | ROI 편차 (`candidate_roi - champion_roi`) |
| **candidate_mdd** | REAL | - | 가상/실제 후보 전략 누적 MDD (소수점 ratio) |
| **champion_mdd** | REAL | - | 가상/실제 챔피언 전략 누적 MDD (소수점 ratio) |
| **virtual_rollback**| INTEGER | DEFAULT 0 | 가상 롤백 트리거 여부 (0: 유지, 1: 가상롤백발생) |
| **actual_label** | TEXT | - | 가상 롤백 기반 이진 분류 정답 레이블 (`GOOD`, `BAD`) |
| **actual_label_source**| TEXT | - | 레이블 결정 상세 원인 및 임계치 정보 |
| **due_at** | INTEGER | NOT NULL | 평가 만기 타임스탬프 (Unix epoch, 초 단위) |
| **evaluated_at** | INTEGER | - | 실제 평가가 완료된 타임스탬프 |
| **evaluation_status**| TEXT | DEFAULT 'PENDING' | 평가 FSM 진행 상태 (`PENDING`, `EVALUATING`, `COMPLETED`, `SKIPPED`, `ERROR`) |
| **horizon_type** | TEXT | - | Horizon 유형 구분 (`elapsed`, `elapsed_in_session`, `calendar_session`) |
| **horizon_value** | INTEGER | - | Horizon 세부 파라미터 값 (초 단위 등) |
| **policy_version** | TEXT | - | 평가 당시 적용된 EvaluationPolicyRouter 버전 |
| **scorer_version** | TEXT | - | 평가 당시 적용된 GIRSScorer 모델 버전 |
| **predicted_risk_score**| REAL | - | 제안 생성 당시 예측된 Shadow Risk Score |
| **locked_at** | INTEGER | DEFAULT NULL | 분산 평가 루프 원자적 선점용 락 타임스탬프 |
| **retry_count** | INTEGER | DEFAULT 0 | 실패 및 락 타임아웃 복구 재시도 횟수 |
| **last_error** | TEXT | - | 마지막 평가 실패 예외 및 스택트레이스 기록 |
| **created_at** | DATETIME | DEFAULT CURRENT_TIMESTAMP | 평가 생성 일시 |

---

### 1.19. girs_shadow_metrics [NEW]
GIRS Shadow Operation 구동 및 모니터링 시 매 루프마다 수집되는 실시간 피처 스냅샷, 리스크 점수, 데이터 신선도, 거래소 시장 특성을 취합 기록합니다.

| 컬럼명 | 데이터 타입 | 제약조건 / 기본값 | 설명 |
| :--- | :--- | :--- | :--- |
| **id** (PK) | INTEGER | PRIMARY KEY AUTOINCREMENT | 기록 일련번호 |
| **timestamp** | REAL | NOT NULL | 기록 시점 타임스탬프 (Unix epoch, 초 단위) |
| **proposal_id** | TEXT | - | 대상 승격 제안 ID (없을 시 NULL) |
| **strategy_id** | TEXT | - | 대상 전략 ID |
| **model_risk_score** | REAL | - | GIRS GNN 모델 리스크 점수 |
| **fallback_risk_score** | REAL | - | 룰 기반 폴백 리스크 점수 |
| **final_promotion_score** | REAL | - | 최종 승격 심사 점수 (1 - final_risk_score) |
| **shadow_risk_score** | REAL | - | 섀도 운용 리스크 점수 |
| **replay_drift** | REAL | - | 리플레이 시뮬레이션 편차 (drift) 값 |
| **correction_active** | INTEGER | DEFAULT 0 | 드리프트 보정 활성화 여부 (0: 비활성, 1: 활성) |
| **operation_mode** | TEXT | - | 시스템 운영 모드 (`shadow`, `live` 등) |
| **model_version** | TEXT | - | 판정 시점의 GIRS 모델 버전 정보 |
| **scaler_version** | TEXT | - | 판정 시점의 GIRS 스케일러 버전 정보 |
| **strategy_version_id** | INTEGER | - | 판정 시점의 활성 전략 버전 번호 |
| **simulation_session_id** | TEXT | - | 모의투자 세션 ID |
| **decision_type** | TEXT | - | 판정 의사결정 타입 (예: `SHADOW`, `LIVE`) |
| **blocked_reason** | TEXT | - | 섀도 모드로 인한 승격 차단 사유 설명 |
| **trade_age_ms** | INTEGER | - | 시세 수신 시연 연령 (ms) |
| **orderbook_age_ms**| INTEGER | - | 호가 수신 시연 연령 (ms) |
| **indicator_age_ms**| INTEGER | - | 지표 계산 시연 연령 (ms) |
| **is_fresh** | INTEGER | DEFAULT 1 | 데이터 신선도 충족 여부 (0: 만료/stale, 1: 신선) |
| **stale_reason** | TEXT | - | 데이터 만료 상세 원인 설명 |
| **snapshot_version**| TEXT | - | 피처 스냅샷 DTO 스키마 버전 |
| **snapshot_hash** | TEXT | - | 피처 구조체 직렬화 SHA-256 해시값 (이중 해싱 1) |
| **feature_vector_hash**| TEXT | - | 실 수치 벡터 직렬화 SHA-256 해시값 (이중 해싱 2) |
| **orderbook_available**| INTEGER | DEFAULT 0 | 호가 데이터 수집 및 가용 상태 여부 |
| **market_type** | TEXT | - | 자산군 분류 (`crypto`, `stock`) |
| **session_state** | TEXT | - | 세션 운영 레짐 (`regular_trading`, `24h` 등) |
| **volatility_regime**| TEXT | - | 변동성 상태 분류 (`low`, `high` 등) |
| **liquidity_regime**| TEXT | - | 유동성 상태 분류 (`low`, `high` 등) |
| **exchange** | TEXT | - | 거래소 코드 (`upbit`, `kis` 등) |

---

### 1.20. universe_guard_state [NEW]
종목별 실시간 유니버스 가드(Cooldown, Quota, Limit 등)의 현재 차단 상태 및 누적 차단 카운트를 관리하는 상태 저장소입니다.

| 컬럼명 | 데이터 타입 | 제약조건 / 기본값 | 설명 |
| :--- | :--- | :--- | :--- |
| **symbol** (PK) | TEXT | PRIMARY KEY | 대상 종목 심볼 (예: `BTC`) |
| **status** | TEXT | - | 현재 가드 감시 상태 (`WATCHED`, `CANDIDATE` 등) |
| **blocked_reason** | TEXT | - | 현재 차단 사유 (`COOLDOWN`, `LIMIT`, `QUOTA` 등) |
| **blocked_count** | INTEGER | DEFAULT 0 | 동일 차단 사유 발생 횟수 누적 카운트 |
| **last_blocked_at** | REAL | - | 마지막 차단 발생 타임스탬프 (Unix epoch, 초 단위) |
| **last_event_logged_reason** | TEXT | - | `system_events` 감사 로그에 마지막으로 기록된 차단 사유 |

---

## 2. 데이터베이스 인덱스 (Database Indexes)

데이터 로딩 성능 및 백테스트 조회 최적화를 위해 다음과 같은 복합/단일 인덱스를 운용합니다.

1. **`idx_trades_exch_sym_time`**
   - 대상 테이블: `trades`
   - 인덱스 구성 컬럼: `(exchange, symbol, trade_timestamp DESC)`
   - 목적: 특정 종목의 최근 체결 틱을 백테스트 엔진이나 캔들 복원기에서 시간 내림차순으로 매우 빠르게 조회하기 위함. 특히 1초봉 등 초 단위 저분봉 데이터를 백엔드에서 실시간 온디맨드 즉석 조립(Aggregation)하여 제공할 때, 대량의 틱 데이터를 30분 단위(13ms 수준)로 초고속 조회 및 가공하는 데 핵심적인 역할을 수행함.
2. **`idx_candles_exch_sym_time`**
   - 대상 테이블: `candles`
   - 인덱스 구성 컬럼: `(exchange, symbol, interval, timestamp DESC)`
   - 목적: 대시보드 차트 요청 시 최근 N개의 캔들(SMA, RSI 연산용) 데이터를 효율적으로 반환하기 위함.
3. **`idx_orders_history_portfolio_id`**
   - 대상 테이블: `orders_history`
   - 인덱스 구성 컬럼: `(portfolio_id)`
   - 목적: 특정 백테스트 시뮬레이션의 누적 주문 내역을 조회할 때 병목 현상을 방지하기 위함.
4. **`idx_positions_portfolio_id`**
   - 대상 테이블: `positions`
   - 인덱스 구성 컬럼: `(portfolio_id)`
   - 목적: 포트폴리오의 실자산 보유 비중 현황을 조회하기 위함.
5. **`idx_exchange_assets_active`**
   - 대상 테이블: `exchange_assets`
   - 인덱스 구성 컬럼: `(exchange, is_active)`
   - 목적: 데몬 구동 시 활성화된 수집 자산 종목들만 즉시 추출하여 수집 세션에 주입하기 위함.
6. **`idx_strategy_param_hist`**
   - 대상 테이블: `strategy_parameter_history`
   - 인덱스 구성 컬럼: `(strategy_id, version_id)`
   - 목적: 특정 전략의 버전별 파라미터 변경 내역 및 상세 파라미터를 초고속 검색하기 위함.
7. **`idx_strategy_perf_snap`**
   - 대상 테이블: `strategy_performance_snapshots`
   - 인덱스 구성 컬럼: `(strategy_id, version_id)`
   - 목적: 특정 전략 및 버전별 이벤트 성과 지표(ROI/MDD) 변화를 효율적으로 조회하기 위함.
8. **`idx_market_regime_sum`**
   - 대상 테이블: `market_regime_summaries`
   - 인덱스 구성 컬럼: `(symbol, timestamp DESC)`
   - 목적: 가설 분석기 기동 시, 특정 종목의 매칭 시점 인근 시장 상태 요약을 신속히 연동하기 위함.
9. **`idx_strategy_prop_group`**
   - 대상 테이블: `strategy_proposals`
   - 인덱스 구성 컬럼: `(proposal_group_id)`
   - 목적: 하나의 제안 묶음 그룹 단위로 제안 데이터를 조회하고 표시하기 위함.
10. **`idx_prop_eval_status_due`**
    - 대상 테이블: `proposal_evaluations`
    - 인덱스 구성 컬럼: `(evaluation_status, due_at)`
    - 목적: 사후 성과 평가 루프에서 만기 경과 대상(PENDING 상태 및 due_at 만료)을 빠르게 스캔하여 평가하기 위함.
11. **`idx_prop_eval_id_horizon`**
    - 대상 테이블: `proposal_evaluations`
    - 인덱스 구성 컬럼: `(proposal_id, horizon_name)`
    - 목적: 특정 제안에 대한 다중 Horizon 평가 결과 대조 조회 및 리포팅 성능을 가속화하기 위함.
12. **`idx_girs_shadow_metrics_time`**
    - 대상 테이블: `girs_shadow_metrics`
    - 인덱스 구성 컬럼: `(timestamp DESC)`
    - 목적: 실시간 섀도 지표 분석 및 리포트 작성을 위한 최근 판정 데이터의 조회 성능을 향상시키기 위함.
13. **`idx_universe_guard_state_status`**
    - 대상 테이블: `universe_guard_state`
    - 인덱스 구성 컬럼: `(status)`
    - 목적: 특정 가드 감시 상태에 해당하는 종목들의 차단 현황을 빠르게 스캔하기 위함.

---

## 3. SQLite 용량 최적화 및 Compact 정책 (Space Reclamation & Compaction Policy)

SQLite는 대량의 `DELETE` 쿼리를 수행해도 파일 크기가 즉각적으로 줄어들지 않고, 변경 이력이 WAL 파일에 잔류하거나 빈 공간(Free List)으로 데이터베이스 내부에 남게 됩니다. 본 시스템의 대용량 틱/분봉 데이터 정리 후의 용량 최적화를 위해 아래의 정책을 적용합니다.

### 3.1. SQLite의 공간 회수(Space Reclamation) 원리
- **Free List 메커니즘**: `DELETE`된 페이지들은 SQLite 내부에서 '재사용 가능한 빈 페이지'로 표시됩니다. 이후 신규 데이터가 유입될 때 파일 크기를 늘리지 않고 이 공간을 먼저 채우게 되지만, OS 상에서 보이는 데이터베이스 파일(`.db`)의 크기는 작아지지 않습니다.
- **WAL (Write-Ahead Log) 모드**: 데이터 변경은 원본 DB가 아닌 `.db-wal` 파일에 임시 저장된 후, Checkpoint 이벤트가 발생해야 비로소 원본 DB로 이전됩니다.

### 3.2. WAL Checkpoint 정책
- **정의**: `.db-wal` 파일에 누적된 변경 로그를 원본 데이터베이스 파일로 병합하여 기록을 반영하고, WAL 파일 크기를 제로화(Truncate)하거나 최소화하는 제어 기법입니다.
- **실행 명령**: `PRAGMA wal_checkpoint(TRUNCATE);`
- **주요 트리거 시점**:
  1. **데몬 정상 종료 시**: `ats` 및 `ats_rehearsal` 세션이 종료(stop)되는 시점에 자동으로 Truncate 체크포인트를 명시적으로 실행하여 데이터 안전성을 확보하고 임시 파일을 비웁니다.
  2. **수동/스케줄 점검 시**: 장기 Soak Test 리포트 작성 등 점검 전후 시점에 수동으로 수행합니다.

### 3.3. DB 파일 압축 (VACUUM) 및 락 경합 완화 정책
데이터베이스 파일을 실제로 OS 디스크로 반환하기 위해서는 물리적인 조밀화(Compaction)가 필요합니다. 하지만 실시간 거래 엔진이 구동되는 동안에는 락(Lock) 경합 및 시스템 성능 저하를 방지하기 위해 다음과 같이 정책을 이원화합니다.

#### 1) 실시간 서비스 가동 중: VACUUM 전면 금지
- **이유**: `VACUUM` 명령은 전체 데이터베이스의 복사본을 임시 생성하여 페이지를 재배치하는 무거운 작업입니다. 실행 중 **전체 데이터베이스가 배타적 락(Exclusive Lock)에 고정**되므로, 실시간 수집기의 틱 수집이나 전략 엔진의 주문 시도가 완전히 차단(Timeout)되는 장애를 유발합니다. 또한 일시적으로 **원본의 2배에 달하는 디스크 공간**이 필요합니다.
- **대체 방안 (Incremental Auto-Vacuum)**:
  - 데이터베이스 생성 시점에 `PRAGMA auto_vacuum = INCREMENTAL;` 설정을 활성화하는 것을 권장합니다.
  - 대량의 `DELETE` 수행 이후, `PRAGMA incremental_vacuum(N);` (예: N=1000, 1000페이지씩 정리)을 청크 단위로 나누어 실행하면 실시간 트랜잭션을 방해하지 않고 백그라운드에서 점진적으로 여유 공간을 OS에 반환할 수 있습니다.

#### 2) 정기 점검 시간 (오프라인): 수동 Full VACUUM
- **대상 시점**: 주말 및 트레이딩 서비스 정지(점검) 시간
- **방법**: 데몬을 모두 종료한 오프라인 상태에서 아래 명령어를 단독으로 실행하여 단편화를 제거하고 물리 파일 용량을 최소화합니다.
  ```sql
  PRAGMA wal_checkpoint(TRUNCATE);
  VACUUM;
  ```

