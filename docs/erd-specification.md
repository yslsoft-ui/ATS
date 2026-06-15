# 데이터베이스 ERD 명세서 (ERD Specification)

이 문서는 통합 실시간 매매 시스템(ATS)의 SQLite 데이터베이스 스키마 간의 Entity-Relationship Diagram(ERD)과 각 테이블 및 관계성에 대한 상세 명세를 다룹니다.

이 문서의 다이어그램은 **Mermaid** 문법으로 작성되었습니다. Mermaid를 지원하는 마크다운 뷰어(예: GitHub, VSCode Mermaid 확장 등)를 통해 시각적으로 조회할 수 있습니다.

---

## 1. 개략적 관계도 (High-Level Entity Relationship Diagram)

시스템의 테이블 간 관계를 거시적으로 나타낸 다이어그램입니다. 포트폴리오를 중심으로 주문/포지션이 묶이고, 거래소 정보와 자산 마스터 정보가 수집용 데이터(`trades`, `candles`, `exchange_assets`)와 연동됩니다.

### 개략적 관계도 내 주요 테이블 한글 설명 및 역할 요약

| 테이블 물리명 | 한글 논리명 | 역할 및 주요 설명 |
| :--- | :--- | :--- |
| `PORTFOLIOS` | 포트폴리오 마스터 | 백테스트 및 실시간 거래 시뮬레이션의 운용 계정 정보 |
| `EXCHANGES` | 거래소 마스터 | 지원 거래소(Upbit, Bithumb, KIS)의 수수료율 및 자산군 메타데이터 |
| `PORTFOLIO_EXCHANGES` | 포트폴리오 잔고 | 포트폴리오별 소속 거래소의 투자 자금, 예수금, 성과 메트릭 정보 |
| `POSITIONS` | 보유 포지션 | 포트폴리오가 현재 보유 중인 개별 자산의 수량, 평균 매수 단가 등 정보 |
| `ORDERS_HISTORY` | 주문 체결 이력 | 거래 집행 과정에서 발생한 모든 매수/매도 주문의 체결 단가, 수량, 수수료, 사유 |
| `ASSET_MASTER` | 자산 정보 마스터 | 전 시장/거래소의 자산 한글명 및 기본 메타데이터 캐시 |
| `EXCHANGE_ASSETS` | 거래소 자산 관리 | 각 거래소에서 실시간 수집 및 매매 감시를 진행할 활성 종목 및 상폐 상태 제어 |
| `TRADES` | 실시간 체결 (Tick) | 거래소 소켓 등으로부터 실시간 수집된 틱 데이터 적재 |
| `CANDLES` | 분봉 캔들 (OHLCV) | 틱 데이터를 특정 주기(초 단위)로 가공한 역사적 캔들스틱 데이터 |
| `ALERTS` | 실시간 알림 | 가격 급등락이나 기술 지표 특정 조건 돌파 시 발생하는 시스템 Alert 메시지 |
| `STRATEGY_INSIGHTS` | 전략 통계 인사이트 | AI 손실 원인 분석을 통해 도출한 통계적 진단 결과 |
| `STRATEGY_PROPOSALS` | 전략 개선 제안 | AI 분석을 바탕으로 파라미터 개선을 제안하고 그 승인/적용 상태 관리 |
| `PROPOSAL_EVALUATIONS` | 제안 사후 평가 | 적용된 제안에 대해 다중 시간축(Horizon) 기준으로 롤백 여부 사후 검증 |
| `PROPOSAL_REEVALUATION_JOBS` | 재평가 작업 큐 | 비동기로 동작하는 사용자의 수동 재평가 요청 작업 대기열 |
| `PROPOSAL_EVALUATION_RUNS` | 재평가 실행 이력 | 수동 재평가 작업 완료에 따라 계산된 리스크 및 승격 심사 점수 이력 |
| `STRATEGY_VERSIONS` | 전략 활성 버전 | 각 매매 전략별 현재 활성화된 파라미터 버전 정보 |
| `STRATEGY_PARAMETER_HISTORY` | 파라미터 변경 이력 | 파라미터 변경(수동 수정, AI 자동 적용, 롤백 등)에 대한 이력 및 계보 추적 |
| `STRATEGY_PERFORMANCE_SNAPSHOTS` | 전략 성과 스냅샷 | 특정 이벤트 발생 시점 기준의 누적 ROI, MDD, 승률 등의 지표 기록 |
| `MARKET_REGIME_SUMMARIES` | 시장 상태 요약 | 1분 단위로 수집된 시장 변동성, RSI, 호가 불균형 등 분석용 피처 데이터 |
| `GIRS_SHADOW_METRICS` | GIRS 섀도 지표 | 실시간 섀도 모니터링 시점의 모델 리스크 점수, 데이터 신선도, 연산 지연 상태 |
| `UNIVERSE_GUARD_STATE` | 유니버스 가드 상태 | 쿨다운/한도 제한 등으로 실시간 매매 대상에서 일시 차단된 종목 감시 상태 |
| `SYSTEM_EVENTS` | 시스템 이벤트 | 데몬 기동/종료, 에러, 사용자 수동 제어 등 시스템 운영 이력 및 감사 로그 |


```mermaid
erDiagram
    PORTFOLIOS_포트폴리오 ||--o{ PORTFOLIO_EXCHANGES_포트폴리오_잔고 : "거래소 잔고 보유"
    PORTFOLIOS_포트폴리오 ||--o{ POSITIONS_보유_포지션 : "보유 포지션 내역"
    PORTFOLIOS_포트폴리오 ||--o{ ORDERS_HISTORY_주문_이력 : "체결 주문 이력"
    PORTFOLIOS_포트폴리오 ||--o{ STRATEGY_INSIGHTS_전략_인사이트 : "AI 손실 분석 생성"
    PORTFOLIOS_포트폴리오 ||--o{ STRATEGY_PROPOSALS_전략_제안 : "개선 제안 소유"

    EXCHANGES_거래소 ||--o{ PORTFOLIO_EXCHANGES_포트폴리오_잔고 : "자산 호스팅"
    EXCHANGES_거래소 ||--o{ TRADES_실시간_체결 : "실시간 틱 수집"
    EXCHANGES_거래소 ||--o{ CANDLES_분봉_캔들 : "OHLCV 캔들 취합"
    EXCHANGES_거래소 ||--o{ EXCHANGE_ASSETS_거래소_자산 : "취급 자산 목록"
    
    PORTFOLIO_EXCHANGES_포트폴리오_잔고 ||--o{ POSITIONS_보유_포지션 : "자산 포지션 잔고 보유"
    
    ASSET_MASTER_자산_마스터 ||--o{ EXCHANGE_ASSETS_거래소_자산 : "거래 자산 메타 매핑"
    
    STRATEGY_INSIGHTS_전략_인사이트 ||--o{ STRATEGY_PROPOSALS_전략_제안 : "개선 파라미터 제안 도출"
    STRATEGY_PROPOSALS_전략_제안 ||--o{ PROPOSAL_EVALUATIONS_제안_사후_평가 : "다중 Horizon 사후 평가"
    STRATEGY_PROPOSALS_전략_제안 ||--o{ PROPOSAL_EVALUATION_RUNS_재평가_실행_이력 : "수동 재평가 결과 누적"
    
    PROPOSAL_REEVALUATION_JOBS_재평가_작업_큐 ||--o{ PROPOSAL_EVALUATION_RUNS_재평가_실행_이력 : "비동기 평가 Job 실행"
```

---

## 2. 테이블별 상세 엔티티 구조 및 한글 설명 (Entity Attributes & Descriptions)

### 2.1. 사용자 및 자산 코어 영역
사용자의 투자 계정(포트폴리오), 연결 거래소 잔고, 보유 중인 자산 포지션 및 거래 집행 이력을 관리하는 핵심 영역입니다.

#### 2.1.1. `portfolios` (시뮬레이션 포트폴리오 마스터 테이블)
백테스트 및 실시간 거래 시뮬레이션 과정에서 운용되는 포트폴리오의 마스터 정보를 관리합니다.

#### 2.1.2. `exchanges` (거래소 마스터 테이블)
시스템 내부에서 처리하는 시장/거래소 정보(수수료율, 자산군 분류)를 저장합니다.

#### 2.1.3. `portfolio_exchanges` (포트폴리오-거래소 맵 및 세부 잔고 테이블)
하나의 포트폴리오가 복수의 거래소 자산을 동시에 보유/관리할 수 있도록 보장하는 중간 매핑 테이블로, 거래소별 운용 가능한 현재 예수금과 성과 지표(MDD, 승률, 누적수익률 등)를 JSON 포맷으로 관리합니다.

#### 2.1.4. `positions` (보유 자산 포지션 테이블)
포트폴리오가 현재 실시간/가상으로 보유 중인 자산 목록(수량, 평균 단가, 진입 당시 도달 최고가 등)을 상세 기록합니다.

#### 2.1.5. `orders_history` (주문 내역 이력 테이블)
가상/실제 매매 집행 과정에서 발생한 모든 주문 내역(매수/매도 구분, 가격, 수량, 수수료, 체결 사유 등)을 이력 관리합니다.

```mermaid
erDiagram
    PORTFOLIOS_포트폴리오 ||--o{ PORTFOLIO_EXCHANGES_포트폴리오_잔고 : "거래소 잔고 보유"
    PORTFOLIOS_포트폴리오 ||--o{ POSITIONS_보유_포지션 : "보유 포지션 내역"
    PORTFOLIOS_포트폴리오 ||--o{ ORDERS_HISTORY_주문_이력 : "체결 주문 이력"
    EXCHANGES_거래소 ||--o{ PORTFOLIO_EXCHANGES_포트폴리오_잔고 : "자산 호스팅"
    PORTFOLIO_EXCHANGES_포트폴리오_잔고 ||--o{ POSITIONS_보유_포지션 : "자산 포지션 잔고 보유"

    PORTFOLIOS_포트폴리오 {
        integer id PK "일련번호 (자동 증가)"
        text name "식별 이름 (중복 허용)"
        text type "운용 타입 (live, simulation, backtest)"
        real duration "운용 경과 시간 (초)"
        text strategy_info "연결 매매 전략 설정 (JSON)"
        datetime ended_at "모의투자 종료 일시"
        datetime created_at "레코드 생성 일시"
        datetime updated_at "최종 수정 일시"
    }

    EXCHANGES_거래소 {
        text id PK "거래소 고유 식별자 (upbit, bithumb, kis)"
        text name "거래소 한글/영문 표시명"
        real fee_rate "기본 적용 수수료율"
        text market_type "자산군 분류 (crypto / stock)"
        datetime created_at "등록 일시"
        datetime updated_at "수정 일시"
    }

    PORTFOLIO_EXCHANGES_포트폴리오_잔고 {
        integer portfolio_id PK, FK "소속 포트폴리오 ID"
        text exchange_id PK "해당 거래소 ID"
        real initial_cash "초기 설정 투자 자금"
        real cash "운용 가능한 현재 예수금"
        text metrics "성과 평가 지표 데이터 (JSON)"
        datetime created_at "등록 일시"
        datetime updated_at "최종 갱신 일시"
    }

    POSITIONS_보유_포지션 {
        integer portfolio_id PK, FK "소속 포트폴리오 ID"
        text exchange_id PK, FK "보유 자산 거래소 ID"
        text symbol PK "자산 심볼 코드 (BTC, 005930 등)"
        real quantity "보유 수량"
        real avg_price "평균 매수 단가"
        real entry_time "최초 진입 시점 타임스탬프"
        real peak_price "최고가 기록 (트레일링 스탑용)"
        datetime updated_at "최종 평가/갱신 일시"
    }

    ORDERS_HISTORY_주문_이력 {
        integer id PK "주문 번호 (자동 증가)"
        integer portfolio_id FK "주문 발주 포트폴리오 ID"
        text exchange_id "주문 거래소 ID"
        text market "체결 세부 시장 (KRW, KRX 등)"
        text strategy_id "주문 트리거 매매 전략 ID"
        text symbol "주문 자산 심볼"
        text side "주문 방향 (BUY: 매수, SELL: 매도)"
        real price "실제 체결 단가"
        real quantity "실제 체결 수량"
        real fee "거래 차감 수수료"
        integer timestamp "체결 시각 타임스탬프 (초)"
        text reason "주문 발주 원인 사유"
        text context "주문 시점의 상태 정보 스냅샷 (JSON)"
    }
```

* **`portfolios` (시뮬레이션 포트폴리오 마스터)**: 백테스트 및 실시간 거래 시뮬레이션 과정에서 운용되는 포트폴리오의 마스터 정보를 관리합니다.
* **`exchanges` (거래소 마스터)**: 시스템 내부에서 처리하는 시장/거래소 정보(수수료율, 자산군 분류)를 저장합니다.
* **`portfolio_exchanges` (포트폴리오-거래소 맵 및 세부 잔고)**: 하나의 포트폴리오가 복수의 거래소 자산을 동시에 보유/관리할 수 있도록 보장하는 중간 매핑 테이블로, 거래소별 운용 가능한 현재 현금과 성과 지표(MDD, 승률, 누적수익률 등)를 JSON 포맷으로 관리합니다.
* **`positions` (보유 자산 포지션)**: 포트폴리오가 현재 실시간/가상으로 보유 중인 자산 목록(수량, 평균 단가, 진입 당시 도달 최고가 등)을 상세 기록합니다.
* **`orders_history` (주문 내역 이력)**: 가상/실제 매매 집행 과정에서 발생한 모든 주문 내역(매수/매도 구분, 가격, 수량, 수수료, 체결 사유 등)을 이력 관리합니다.

---

### 2.2. 시장 시세 및 수집 영역
실시간으로 거래소로부터 수집되는 틱 데이터와 가변 시간 프레임으로 가공되는 캔들 정보, 거래소별 감시 활성 대상 자산군을 제어합니다.

```mermaid
erDiagram
    EXCHANGES_거래소 ||--o{ EXCHANGE_ASSETS_거래소_자산 : "취급 자산 목록"
    EXCHANGES_거래소 ||--o{ TRADES_실시간_체결 : "실시간 틱 수집"
    EXCHANGES_거래소 ||--o{ CANDLES_분봉_캔들 : "OHLCV 캔들 취합"
    EXCHANGES_거래소 ||--o{ ALERTS_실시간_알림 : "급등락 경보 발생"
    ASSET_MASTER_자산_마스터 ||--o{ EXCHANGE_ASSETS_거래소_자산 : "거래 자산 메타 매핑"

    EXCHANGES_거래소 {}
    ASSET_MASTER_자산_마스터 {
        text symbol PK "자산 고유 심볼 코드"
        text korean_name "한글 종목명"
        text asset_type "자산 속성 (crypto / stock)"
        datetime created_at "마스터 등록 일시"
        datetime updated_at "마스터 최종 수정 일시"
    }

    EXCHANGE_ASSETS_거래소_자산 {
        text exchange_id PK "거래소 ID"
        text symbol PK, FK "자산 심볼 코드"
        integer is_active "수집 및 감시 활성 여부 (0/1)"
        integer is_delisted "상장 폐지 여부 (0: 유지, 1: 상폐)"
        datetime created_at "맵 등록 일시"
        datetime updated_at "상태 변경 일시"
    }

    TRADES_실시간_체결 {
        integer id PK "체결 레코드 일련번호"
        text exchange_id "수집 거래소 ID"
        text market "세부 시장 (KRW, KRX 등)"
        text symbol "자산 심볼 코드"
        real trade_price "체결 가격"
        real trade_volume "체결 수량"
        text ask_bid "매수/매도 구분 (ASK / BID)"
        integer trade_timestamp "거래소 체결 시각 타임스탬프 (ms)"
        integer sequential_id "거래소 제공 고유 체결 번호"
        datetime created_at "로컬 DB 적재 일시"
    }

    CANDLES_분봉_캔들 {
        text exchange_id PK "대상 거래소 ID"
        text symbol PK "자산 심볼 코드"
        integer interval PK "캔들 주기 규격 (초 단위)"
        integer timestamp PK "캔들 시작 타임스탬프 (ms)"
        real open "시가 (Open)"
        real high "고가 (High)"
        real low "저가 (Low)"
        real close "종가 (Close)"
        real volume "누적 체결 거래량"
    }

    ALERTS_실시간_알림 {
        integer id PK "알림 고유 일련번호"
        text exchange_id "감지 거래소 ID"
        text symbol "자산 심볼 코드"
        real price "감지 시점 체결가"
        text msg "사용자 경고 메시지 내용"
        integer timestamp "이벤트 발생 시각 타임스탬프 (ms)"
    }
```

#### 2.2.1. `asset_master` (전체 자산 정보 마스터 테이블)
전체 거래 대상 자산의 메타데이터와 국가별 한글명(예: 삼성전자, 비트코인 등)을 일괄 매핑 및 캐시하여 관리합니다.

#### 2.2.2. `exchange_assets` (거래소별 취급 자산 관리 테이블)
각 거래소에서 수집/전략 감시를 수행할 활성 종목 여부(`is_active=1`) 및 상장 폐지 여부(`is_delisted=1`) 상태를 설정합니다.

#### 2.2.3. `trades` (실시간 틱 데이터 테이블)
거래소로부터 실시간 수신한 개별 체결(Tick) 내역을 저장합니다.

#### 2.2.4. `candles` (OHLCV 캔들스틱 데이터 테이블)
틱 데이터를 가변 인터벌(1초, 5초, 1분 등) 단위로 변환 및 취합한 역사적 캔들 정보입니다.

#### 2.2.5. `alerts` (급등락 실시간 알림 테이블)
실시간 가격 급등락(Spike) 감지 또는 특정 기술 지표 조건 돌파 시 발생한 이벤트를 기록합니다.

---

### 2.3. AI 가설 및 제안 사후 평가 영역
AI 모델을 활용해 손실 원인을 분석하고, 최적의 파라미터 개선을 제안하며, 다중 시간축(Horizon) 및 수동 시뮬레이션을 통해 성과 오차와 롤백 여부를 평가 및 추적합니다.

```mermaid
erDiagram
    PORTFOLIOS_포트폴리오 ||--o{ STRATEGY_INSIGHTS_전략_인사이트 : "AI 손실 분석 생성"
    PORTFOLIOS_포트폴리오 ||--o{ STRATEGY_PROPOSALS_전략_제안 : "개선 제안 소유"
    STRATEGY_INSIGHTS_전략_인사이트 ||--o{ STRATEGY_PROPOSALS_전략_제안 : "개선 파라미터 제안 도출"
    STRATEGY_PROPOSALS_전략_제안 ||--o{ PROPOSAL_EVALUATIONS_제안_사후_평가 : "다중 Horizon 사후 평가"
    STRATEGY_PROPOSALS_전략_제안 ||--o{ PROPOSAL_EVALUATION_RUNS_재평가_실행_이력 : "수동 재평가 결과 누적"
    PROPOSAL_REEVALUATION_JOBS_재평가_작업_큐 ||--o{ PROPOSAL_EVALUATION_RUNS_재평가_실행_이력 : "비동기 평가 Job 실행"

    PORTFOLIOS_포트폴리오 {}
    STRATEGY_INSIGHTS_전략_인사이트 {
        integer id PK "인사이트 일련번호"
        integer portfolio_id FK "소속 포트폴리오 ID"
        text strategy_id "분석 대상 전략 ID"
        text category "인사이트 분류 (STOP_LOSS, TIME_LIMIT 등)"
        text fact_summary "텍스트 요약 메시지"
        text details_json "상세 지표 수치 데이터 (JSON)"
        datetime created_at "작성 일시"
    }

    STRATEGY_PROPOSALS_전략_제안 {
        integer id PK "제안 고유 번호"
        integer insight_id FK "원인 제공 인사이트 ID"
        text proposal_group_id "제안 묶음 그룹 ID"
        integer version "제안 버전"
        integer portfolio_id FK "대상 포트폴리오 ID"
        text strategy_id "대상 전략 ID"
        text status "제안 FSM 상태 (PENDING, APPROVED 등)"
        text outcome "실전 적용 성패 상태 (RUNNING, COMPLETED 등)"
        text original_params "변경 전 기존 파라미터 셋 (JSON)"
        text proposed_params "제안 개선 파라미터 셋 (JSON)"
        text metrics "백테스트 예측 성과 지표 (JSON)"
        text mutation_trace "파라미터 변경 이력 추적 (JSON)"
        integer confidence_score "제안 신뢰 점수 (0~100)"
        integer applied_at "실전 적용 완료 시각 (ms)"
        integer rolled_back_at "실전 적용 후 롤백 시각 (ms)"
        text decision_path_hash "의사결정 경로 해시"
        text audit_log_json "채점 및 규제 상세 로그 (JSON)"
        real counterfactual_roi "반사실적 가상 ROI (%)"
        real counterfactual_mdd "반사실적 가상 MDD (%)"
        integer is_counterfactual_tracked "반사실적 가상 성과 추적 여부 (0/1/2)"
        datetime created_at "등록 일시"
        datetime updated_at "수정 일시"
    }

    PROPOSAL_EVALUATIONS_제안_사후_평가 {
        integer id PK "평가 일련번호"
        integer proposal_id FK "평가 대상 제안 ID"
        text horizon_name "Horizon 식별자 (10m, 30m, 1d 등)"
        real candidate_roi "가상/실제 후보(Candidate) 누적 ROI"
        real champion_roi "가상/실제 챔피언(Champion) 누적 ROI"
        real roi_gap "ROI 편차 (candidate - champion)"
        real candidate_mdd "가상/실제 후보 누적 MDD"
        real champion_mdd "가상/실제 챔피언 누적 MDD"
        integer virtual_rollback "가상 롤백 발생 여부 (0/1)"
        text actual_label "사후 분류 정답 레이블 (GOOD / BAD)"
        text actual_label_source "레이블 결정 상세 원인 및 임계치"
        integer due_at "평가 만기 타임스탬프 (Unix epoch 초)"
        integer evaluated_at "실제 평가 완수 타임스탬프"
        text evaluation_status "FSM 상태 (PENDING, COMPLETED 등)"
        text horizon_type "Horizon 유형 (elapsed, calendar_session)"
        integer horizon_value "Horizon 설정값 (초)"
        text policy_version "적용 EvaluationPolicyRouter 버전"
        text scorer_version "적용 GIRSScorer 모델 버전"
        real predicted_risk_score "제안 시점의 예측 섀도 리스크 점수"
        integer locked_at "원자적 선점용 분산 락 시각"
        integer retry_count "평가 실패 재시도 횟수"
        text last_error "최종 에러 로그 메시지"
        datetime created_at "평가 생성 일시"
    }

    PROPOSAL_REEVALUATION_JOBS_재평가_작업_큐 {
        integer job_id PK "수동 재평가 Job ID"
        integer proposal_id "재평가 대상 제안 ID"
        text status "FSM 상태 (QUEUED, RUNNING, COMPLETED 등)"
        integer requested_at "Job 생성 Unix epoch ms"
        integer started_at "처리 시작 타임스탬프"
        integer finished_at "처리 완료/실패 타임스탬프"
        text requested_by "요청 주체 (user, system)"
        text mode "재평가 동작 모드"
        integer input_snapshot_id "사용된 피처 스냅샷 ID"
        text error_message "실패 원인 오류 메시지"
        text worker_id "처리 담당 데몬 ID"
    }

    PROPOSAL_EVALUATION_RUNS_재평가_실행_이력 {
        integer evaluation_run_id PK "수동 재평가 런(Run) 일련번호"
        integer proposal_id "평가 대상 제안 ID"
        integer job_id "연결된 reevaluation Job ID"
        real girs_score "재추론된 GIRS 리스크 점수"
        real promotion_score "최종 승격 심사 점수 (1 - risk)"
        real stability_score "시장/랭킹 등 종합 안정성 점수"
        real rollback_probability "롤백 발생 위험 추정치"
        integer data_quality_blocked "데이터 품질 미달 차단 여부 (0/1)"
        integer counterfactual_result_id "연결된 proposal_evaluations ID"
        text model_version "재추론 사용 GIRS 모델 버전"
        text scorer_version "재추론 사용 Scorer 버전"
        text simulator_version "사용한 시뮬레이터 버전"
        integer created_at "런 생성 Unix epoch ms"
    }
```

#### 2.3.1. `strategy_insights` (분석 통계 인사이트 테이블)
손실 거래 데이터 분석을 바탕으로 하여 어떤 유형(손절매, 타임아웃, 진입 필터 등)의 규칙이 부적합했는지 AI가 추론해 낸 통계 인사이트를 영속화합니다.

#### 2.3.2. `strategy_proposals` (전략 파라미터 개선 제안 테이블)
통계 분석 및 섀도 백테스트 검증을 거친 후 적용을 앞두고 있는 파라미터 개선 제안들의 목록과 가상/실전 성패 결과를 관리합니다.

#### 2.3.3. `proposal_evaluations` (제안 사후 성과 평가 테이블)
승인된 제안이나 후보 전략들에 대해 여러 Horizon 시각 기준(만기 시점 `due_at`)에 맞추어 실제 시장에서 롤백 기준에 부합했는지를 사후 평가 FSM 상태(`PENDING`, `EVALUATING`, `COMPLETED`)를 통해 기록합니다.

#### 2.3.4. `proposal_reevaluation_jobs` (수동 재평가 작업 대기열 테이블)
사용자가 의사결정 콘솔 UI에서 재평가를 요청하면 비동기로 동작하는 백그라운드 Job 큐로, 데몬이 이를 순차 감지하여 처리합니다.

#### 2.3.5. `proposal_evaluation_runs` (수동 재평가 점수 누적 이력 테이블)
수동 재평가 결과 완료 시 생성되는 이력 테이블로, 시간에 따라 GIRS 리스크 점수나 안정성 점수 변화 추이를 추적할 수 있도록 돕습니다.

---

### 2.4. 전략 파라미터 버전 관리 및 시스템 운영 영역
전략 파라미터 롤백 및 변이 히스토리를 추적하고, 시스템 감사 로그 및 리스크 관리 피처들을 취합합니다.

```mermaid
erDiagram
    STRATEGY_VERSIONS_전략_버전 ||--o{ STRATEGY_PARAMETER_HISTORY_파라미터_변경_이력 : "버전별 변경 상세 기록"
    STRATEGY_VERSIONS_전략_버전 ||--o{ STRATEGY_PERFORMANCE_SNAPSHOTS_성과_스냅샷 : "버전별 성과 추적"
    STRATEGY_PROPOSALS_전략_제안 ||--o{ STRATEGY_PARAMETER_HISTORY_파라미터_변경_이력 : "승인 제안 적용"
    STRATEGY_PROPOSALS_전략_제안 ||--o{ GIRS_SHADOW_METRICS_GIRS_섀도_지표 : "제안 시점 리스크 로깅"
    STRATEGY_VERSIONS_전략_버전 ||--o{ GIRS_SHADOW_METRICS_GIRS_섀도_지표 : "활성 버전 리스크 감시"

    STRATEGY_PROPOSALS_전략_제안 {}
    STRATEGY_VERSIONS_전략_버전 {
        text strategy_id PK "전략 고유 ID 식별자"
        integer current_version_id "현재 런타임 활성 버전 번호"
        text current_params "현재 동작 중인 파라미터 JSON 문자열"
        integer rollback_source_version "롤백 유발 대상 원인 버전 ID"
        integer applied_at "파라미터 적용 Unix epoch ms"
        datetime updated_at "최종 변경 일시"
    }

    STRATEGY_PARAMETER_HISTORY_파라미터_변경_이력 {
        integer id PK "이력 고유 일련번호"
        text strategy_id "대상 전략 ID"
        integer version_id "버전 번호"
        integer parent_version_id "부모 버전 번호 (계통 추적)"
        text old_params "변경 전 이전 파라미터 JSON"
        text new_params "변경 후 신규 파라미터 JSON"
        integer proposal_id "연결된 승인 제안 ID"
        integer is_current "현재 적용 버전 여부 (0: 과거, 1: 활성)"
        text changed_by "변경 주체 (USER: 사용자, AUTO: 시스템)"
        text change_reason "상세 변경 사유 구분자"
        datetime created_at "이력 기록 일시"
    }

    STRATEGY_PERFORMANCE_SNAPSHOTS_성과_스냅샷 {
        integer id PK "스냅샷 일련번호"
        text strategy_id "대상 전략 ID"
        integer version_id "성과 측정 대상 전략 버전"
        text parameter_hash "파라미터 JSON 해시값 (SHA-256)"
        text snapshot_type "이벤트 유형 (PERIODIC, ROLLBACK 등)"
        integer timestamp "기록 시점 타임스탬프 (ms)"
        real roi "누적 ROI (%)"
        real mdd "누적 Max Drawdown (%)"
        real profit_factor "수익팩터 (Profit Factor)"
        real win_rate "승률 (%)"
        integer trade_count "총 체결 주문 거래 건수"
        datetime created_at "스냅샷 작성 일시"
    }

    MARKET_REGIME_SUMMARIES_시장_요약 {
        integer id PK "피처 기록 번호"
        integer timestamp "1분 정규화 버킷 타임스탬프 (ms)"
        text symbol "exchange:symbol 결합 종목 코드"
        real volatility "변동성 표준편차 비율"
        real rsi "1분봉 기준 14분 RSI 수치"
        real volume_ratio "20분 평균 대비 직전 1분 거래 비율"
        real spread "1분간 체결 스프레드 괴리율"
        real orderbook_imbalance "1분간 매수/매도 호가 불균형 비율"
        datetime created_at "피처 적재 일시"
    }

    GIRS_SHADOW_METRICS_GIRS_섀도_지표 {
        integer id PK "메트릭 적재 일련번호"
        real timestamp "기록 시각 (Unix epoch 초)"
        text proposal_id "연관 승격 제안 ID"
        text strategy_id "대상 전략 ID"
        real model_risk_score "GIRS 모델 리스크 점수"
        real fallback_risk_score "룰 기반 폴백 리스크 점수"
        real final_promotion_score "최종 승격 심사 점수 (1-risk)"
        real shadow_risk_score "섀도 모드 운용 리스크 점수"
        real replay_drift "리플레이 시뮬레이션 편차"
        integer correction_active "드리프트 보정 활성화 여부 (0/1)"
        text operation_mode "시스템 운영 모드"
        text model_version "판정 시점 GIRS 모델 버전"
        text scaler_version "판정 시점 GIRS 스케일러 버전"
        integer strategy_version_id "판정 시점 활성 전략 버전 번호"
        text simulation_session_id "모의투자 세션 ID"
        text decision_type "의사결정 타입 (SHADOW / LIVE)"
        text blocked_reason "승격 차단 사유 설명"
        integer trade_age_ms "시세 데이터 수신 지연 연령"
        integer orderbook_age_ms "호가 데이터 수신 지연 연령"
        integer indicator_age_ms "기술 지표 연산 지연 연령"
        integer is_fresh "데이터 신선도 조건 충족 여부 (0/1)"
        text stale_reason "데이터 유실/지연 상세 사유"
        text snapshot_version "피처 DTO 스키마 버전"
        text snapshot_hash "피처 DTO 직렬화 SHA-256 해시"
        text feature_vector_hash "실 수치 벡터 직렬화 SHA-256 해시"
        integer orderbook_available "호가 데이터 가용 상태 여부"
        text market_type "자산군 분류 (crypto / stock)"
        text session_state "세션 운영 레짐"
        text volatility_regime "변동성 레짐 레벨"
        text liquidity_regime "유동성 레짐 레벨"
        text exchange_id "거래소 ID"
    }

    UNIVERSE_GUARD_STATE_유니버스_가드_상태 {
        text exchange_id PK "대상 거래소 ID"
        text market_type PK "crypto / stock"
        text symbol PK "대상 종목 심볼 코드"
        text status "가드 감시 상태 (WATCHED 등)"
        text blocked_reason "현재 차단 사유 (COOLDOWN, QUOTA 등)"
        integer blocked_count "동일 사유 차단 누적 횟수"
        real last_blocked_at "마지막 차단 발생 타임스탬프 (초)"
        text last_event_logged_reason "마지막 감사 로그에 기록된 차단 사유"
    }

    SYSTEM_EVENTS_시스템_이벤트 {
        integer id PK "이벤트 번호 (자동 증가)"
        text event_type "이벤트 유형분류 (DAEMON_START, EXCHANGE_SUSPENDED 등)"
        text target "이벤트 대상 식별자"
        text message "이벤트 상세 로그 메시지"
        integer timestamp "로컬 밀리초 타임스탬프 (ms)"
        text context "이벤트 당시 맥락 및 command_id (JSON)"
    }
```

#### 2.4.1. `strategy_versions` (전략 활성 버전 마스터 테이블)
각 매칭 전략별 현재 서비스 상에서 활성화되어 구동 중인 버전 번호와 실제 파라미터 JSON 문자열을 보관합니다.

#### 2.4.2. `strategy_parameter_history` (전략 파라미터 변경 이력 테이블)
사용자의 수동 변경이나 자동 AI 승격 제안 적용, 롤백 등으로 인한 전략 파라미터 변경 이력과 버전 분기 계보(부모 버전 ID)를 계통 관리합니다.

#### 2.4.3. `strategy_performance_snapshots` (전략 성과 스냅샷 테이블)
기동 시점이나 롤백 시점 등 이벤트가 일어난 시점별 누적 ROI, MDD, 승률, 누적 거래 건수 등의 리스크 지표 스냅샷을 관리합니다.

#### 2.4.4. `market_regime_summaries` (시장 상태 요약 피처 테이블)
거시적인 시장 특성을 1분 주기로 요약(RSI, 변동성 표준편차 비율, 호가 불균형 비율 등)하여 가설 수립 및 분석용 피처로 활용합니다.

#### 2.4.5. `girs_shadow_metrics` (GIRS 섀도 지표 테이블)
섀도 모니터링 시 매 루프마다 산출된 모델 리스크 점수, 데이터 가용성 신선도, 리플레이 시뮬레이션 편차(Drift)를 실시간으로 기록합니다.

#### 2.4.6. `universe_guard_state` (유니버스 가드 상태 테이블)
쿨다운이나 쿼터 제한 등으로 인해 실시간 유니버스에서 차단된 상태인지 여부와 누적 차단 횟수를 관리합니다.

#### 2.4.7. `system_events` (시스템 및 데몬 운영 이력 테이블)
사용자 수동 조작 감사 로그(`_REQUEST`, `_SUCCESS`, `_FAILED` 세트), 데몬 프로세스 기동/종료, 수집기 에러 및 크래쉬 감지 등 운영 이력을 영속화합니다.

---

## 3. 핵심 외래키 및 무결성 제약조건 관계성

1. **`portfolios` (포트폴리오 마스터) (1 : N) `portfolio_exchanges` (포트폴리오 세부 잔고)**
   * **설명**: 한 포트폴리오는 여러 거래소의 자산과 잔고를 보유할 수 있습니다.
   * **제약**: `ON UPDATE CASCADE ON DELETE CASCADE` 설정으로 포트폴리오 마스터가 삭제되면 세부 자산 잔고 정보도 자동으로 안전하게 연쇄 삭제됩니다.
2. **`portfolio_exchanges` (포트폴리오 세부 잔고) (1 : N) `positions` (보유 자산 포지션)**
   * **설명**: 특정 거래소에 연결된 세부 포트폴리오 잔고 하에 다중 자산 포지션(코인/주식 종목)이 실시간으로 존재합니다.
   * **제약**: 외래키로 `(portfolio_id, exchange_id)` 복합 외래키 조합을 사용하여 잔고와 보유 종목 간의 결합 정합성을 엄격하게 이중으로 보호합니다.
3. **`strategy_insights` (분석 통계 인사이트) (1 : N) `strategy_proposals` (전략 파라미터 개선 제안)**
   * **설명**: AI가 식별해 낸 특정 거래 손실 분석 인사이트(`insight_id`)를 해소하기 위해 복수의 파라미터 개선안이 도출되어 제안될 수 있습니다.
4. **`strategy_proposals` (전략 파라미터 개선 제안) (1 : N) `proposal_evaluations` (제안 사후 성과 평가)**
   * **설명**: 개선 제안 파라미터의 타당성과 오차를 실증 분석하기 위해, 1개의 제안에 대하여 `10m`, `30m`, `1d` 등 N개의 서로 다른 시간 Horizon 별 평가 FSM이 구성됩니다.
5. **`strategy_proposals` (전략 파라미터 개선 제안) (1 : N) `proposal_evaluation_runs` (수동 재평가 점수 누적 이력)**
   * **설명**: 수동 시뮬레이션 요청에 연관된 Job이 완료될 때마다 append-only 형태로 승격 점수 변화 이력이 이 테이블에 누적됩니다.
6. **`proposal_reevaluation_jobs` (수동 재평가 Job Queue) (1 : N) `proposal_evaluation_runs` (수동 재평가 점수 누적 이력)**
   * **설명**: 사용자가 요청한 각 수동 재평가 작업(`job_id`) 결과가 실제 완료되었을 때의 추론 점수 및 시뮬레이션 결과와 매핑됩니다.
