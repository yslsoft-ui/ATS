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
| **exchange** | TEXT | - | 거래소 ID |
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
포트폴리오가 현재 실시간/가상으로 보유 중인 자산 목록을 상세 기록합니다.

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
