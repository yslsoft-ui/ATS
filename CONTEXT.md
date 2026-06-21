# Multi-Market Real-time Trading System

다양한 시장(가상자산, 국내 주식 등)의 실시간 체결 데이터를 수집하여 시각화하고, 지표 분석 및 자동 매매 시뮬레이션을 수행하는 통합 시스템입니다.

## Language

**Market**:
특정 자산이 거래 및 결제되는 세부 하부 시장 또는 결제 수단 (예: 가상자산의 `KRW`, `BTC`, `USDT` 마켓 / KIS 주식의 `KRX`, `NXT` 및 자동 라우팅을 위한 `SOR` 시장).
_Avoid_: 섹터, 테마

**Exchange (ID)**:
시스템 내부에서 물리적 거래소 서비스 또는 브로커를 식별하는 고유 ID (예: `upbit`, `bithumb`, `kis`).

**AssetSymbol**:
거래소 내의 순수 종목 코드 (예: `BTC`, `ETH`, `005930`). 더 이상 접두어를 포함하지 않음.

**Composite Key**:
`(Exchange, AssetSymbol)` 쌍으로 시스템 내 모든 자산을 고유하게 식별.

**Candle**:
특정 시간 범위(Interval) 동안의 가격 변동(시가, 고가, 저가, 종가)과 거래량을 요약한 데이터 단위.
_Avoid_: 봉, 차트 데이터

**Interval**:
캔들이 생성되는 시간적 간격 (예: 1s, 1m, 5m).
_Avoid_: 주기, 타임프레임

**Tick**:
거래소에서 발생하는 최소 단위의 개별 체결 데이터.
_Avoid_: Trade, 체결 건

**Spike**:
가격이나 거래량이 단기간에 임계치 이상으로 상승하는 현상.
_Avoid_: 급등락, 펌핑

**Alert**:
**Spike** 포착 또는 특정 지표 조건 충족 시 생성되어 사용자에게 전달되는 정보 단위.
_Avoid_: 알림 메시지, 노티

**Strategy**:
시장 상황을 분석하여 매수/매도 신호를 생성하는 규칙 모음 (예: RSI 평균 회귀, 단기상승흐름 모멘텀).
_Avoid_: 매매 로직, 알고리즘

**Backtest**:
과거 데이터를 기반으로 **Strategy**의 성과를 측정하는 실험.
_Avoid_: 과거 검증, 수익률 테스트

**Trade Simulation**:
실제 자산을 사용하지 않고 가상 자산으로 거래를 수행하는 모든 행위. 과거 데이터 테스트(**Backtest**)와 실시간 가상 매매를 모두 포함함.
_Avoid_: 모의 투자, 페이퍼 트레이딩

**Order Matching**:
호가창 데이터를 기반으로 주문의 체결 여부와 실제 체결 가격(슬리피지 포함)을 결정하는 프로세스.
_Avoid_: 주문 처리, 체결 확인

**Portfolio**:
**Trade Simulation** 중에 관리되는 가상 자산(현금 및 보유 종목)의 상태.
_Avoid_: 잔고, 지갑, 계좌

**TradeEngine**:
종목별로 독립적인 **Candle** 생성, 지표 계산, **Strategy** 실행을 총괄하는 핵심 엔진 유닛.
_Avoid_: 매매 엔진, 메인 루프

**Warm-up**:
실시간 데이터 처리 전, 최근의 **Tick** 데이터를 엔진에 주입하여 지표와 **Strategy** 상태를 최신화하는 과정.
_Avoid_: 초기화, 데이터 로딩, 사전 학습

**Doji (도지)**:
시가와 종가가 동일한 **Candle**. 시스템은 이전 **Candle**의 종가와 비교하여 추세 색상을 결정함.
_Avoid_: 십자봉, 무변동 봉

**PortfolioManager**:
여러 개의 **Portfolio**를 관리하고, **TradeEngine**의 신호를 받아 **OrderExecutor**를 통해 주문을 처리하는 관리 모듈.
_Avoid_: 자산 관리자, 매매 관리기

**OrderExecutor**:
주문을 실제로 집행하는 추상 레이어. 가상 체결(**VirtualOrderExecutorAdapter**)과 실제 API 체결을 동일한 인터페이스로 제공함.
_Avoid_: 주문기, 체결 처리기

**Collector**:
특정 거래소(Market)의 실시간 **Tick** 데이터를 WebSocket으로 수집하고, 내부 **TradeEngine**으로 배분하는 모듈.
_Avoid_: 수집기 (혼재 방지를 위해 영문 용어 통일)

**MarketDataContext**:
특정 자산(`Composite Key`) 및 인터벌(`Interval`) 단위로 시세(캔들) 상태를 유지하고, 기술 지표를 동적으로 연산 및 캐싱하여 다수의 전략 실행 컨텍스트(`StrategyContext`)에 공유하는 중앙 데이터 콘텍스트 관리자.

**Integrated Candle (통합 캔들)**:
단일 자산(예: `005930`)에 대해 복수의 실시간 거래 시장(예: KRX와 NXT)에서 발생하는 체결 데이터(Tick)를 시간순으로 융합(Merge)하여 생성한 단일 캔들 스트림.
_Avoid_: 분리 캔들, 개별 차트

**Smart Order Routing (SOR / 최선집행주문)**:
복수의 시장(예: KRX, NXT) 중 주문 시점에 가격 및 수량 조건이 가장 유리한 시장으로 주문을 자동 송신 및 처리해주는 KIS의 지능형 주문 집행 방식.

**Market Division (시장구분)**:
주문 발주 또는 실시간 틱 수신 시, 해당 거래가 실제로 발생한 세부 시장(KRX, NXT)을 구별하는 정보 필드.

**Suspended (시장정지 / 거래정지)**:
서킷브레이커, 변동성완화장치(VI) 발동, 또는 기타 거래소 제약 요인에 의해 시장 전체 혹은 개별 종목의 거래가 일시 중단된 상태. 시스템은 이 상태 동안 주문 집행을 보류하고 내부 미체결 주문을 일괄 정리합니다.

**SystemEvent (시스템 이벤트 / 운영 이력)**:
데몬 및 웹서버의 시작/종료, 거래소 수집기의 기동/중단, 거래소 서킷브레이커 발동 및 복구와 같은 시스템 전반의 운영 상태 변화를 기록한 이력 정보 단위.
_Avoid_: 시스템 로그, 상태 변경 이력

**DaemonSupervisor (데몬 생명주기 관리자)**:
데몬의 기동, 자원 정리, 시그널 수신, 상태 알림 전송 및 자가 재기동 등 공통 생명주기를 감시하고 제어하는 중앙 관리 유닛.
_Avoid_: 데몬 메니저, 라이프사이클 엔진

**Shadow Strategy League (그림자 전략 리그)**:
실시간 시세 스트림 또는 과거 데이터를 기반으로 동일 전략에 다양한 파라미터를 적용한 복수의 그림자 전략 후보군을 동시에 모의 실행하여 성과를 비교 및 모니터링하는 시스템.
_Avoid_: 가상 리그, 모의 테스트 세트

**Shadow Backtest (그림자 백테스트)**:
분석기(Analyzer)에 의해 도출된 후보 파라미터들에 대해 다중 검증 구간(Multi-Window Validation)을 거쳐 수익성과 안정성을 사전에 검증하는 시뮬레이션 프로세스.

**Strategy Proposal (전략 파라미터 제안)**:
Shadow Backtest 검증을 완벽히 통과하여 사용자에게 승인/보류/기각 여부를 묻기 위해 발행된 파라미터 조합 및 기대 지표(ROI, MDD, 승률, 신뢰도 점수 등) 세트.

**Multi-Window Validation (다중 검증 구간)**:
과최적화(Overfitting) 방지를 위해 최근 1일(단기), 7일(중기), 30일(장기) 등 복수의 기간 동안의 틱/캔들 데이터를 활용하여 파라미터의 성능 개선 여부를 종합적으로 교차 검증하는 방식.

**Confidence Score (신뢰도 점수)**:
후보 파라미터의 검증 기간별 수익성 유지도, 거래 횟수, Profit Factor 향상폭 등을 고려하여 도출된 해당 제안의 안전성 및 신뢰도 수치(0~100점).

**League Score (리그 스코어)**:
그림자 리그(SHADOW)에서 챔피언과 챌린저의 실시간 성능을 객관적으로 비교 평가하기 위해 ROI, 승률(WinRate), Profit Factor(수익 팩터), MDD 등을 가중 결합한 종합 성과 지표.

**One Parameter Mutation (단일 파라미터 변이)**:
조합 폭발(Combinatorial Explosion)을 방지하고 정확한 인과관계를 식별하기 위해, 가설 제안 시 한 번에 단 하나의 전략 설정값만 수정하여 테스트하는 최적화 기법.

**Insight (분석 인사이트)**:
Analyzer가 거래 이력 및 시세 변화를 통계적으로 추적하여 도출해 낸 시장 관찰 사실(예: "손절 후 30초 내 반등 확률 43%").

**GNN Proposal Pruner (GNN 제안 프루너)**:
전략 변이 DAG, Canonical Policy Embedding, 그리고 Regime Continuous Embedding을 Heterogeneous Graph Attention Network(GAT) 구조로 학습시켜 신규 전략 제안의 롤백(Rollback) 확률을 예측하고 선제 차단(Auto-pruning)하는 의사결정 모델 레이어. 이 레이어는 안정성 및 자원 격리를 위해 학습(PyTorch/PyG 백그라운드 배치)과 추론(데몬 내 ONNX Runtime 단독 구동)이 완전히 분리됩니다. 특히 런타임 추론 시점에는 그래프 구조를 직접 연산하지 않고, 그래프의 위상 구조(Structural Fingerprints/node degree 등)와 요약된 고정 차원의 정책/국면 임베딩만을 입력받는 **GNN Distilled MLP Head** 구조로 구동됩니다.

**Promotion Queue (승격 대기 큐)**:
GNN Pruner와 룰 필터를 통과한 전략 진화 후보군 생태계로, 단순 가중합(Weighted Sum) 방식의 정렬을 배제하고 다목적 파레토 프런트(Multi-objective Pareto Front: ROI 최대화, MDD 최소화, Stability 최대화) 기반으로 실시간 최적 대안을 랭킹하는 상태 보존형 랭킹 엔진. 1단계 하드 필터(Hard Safety Filter: GNN 신뢰도, MDD 한도, 국면 적합성 등)와 2단계 파레토 랭킹의 2-Layer로 구성되며, `Promotion Event Log`를 통해 상태를 영구 이력 관리합니다.

**Soft Pruning (소프트 프루닝)**:
확률 필터에 의해 차단 판정을 받은 전략 제안을 물리적으로 즉각 삭제하지 않고, `PRUNED` 또는 `ARCHIVED` 레이블을 부여한 상태로 그래프 내에 유지하여 학습 데이터의 다양성을 보존하고 향후 GNN 학습의 음성(Negative) 피드백 자료로 보존하는 차단 기법.

**Graph-informed Risk Scorer (GIRS / 그래프 인지 리스크 스코어러)**:
오프라인에서 전략 변이 DAG 정보 및 시장 국면을 그래프 어텐션 네트워크(GAT)를 통해 특징 압축한 뒤, 실시간 런타임에는 그래프 연산을 배제하고 오직 고정 차원의 요약 피처만을 입력받아 Single-head MLP 구조의 ONNX 모델로 10ms 이하 초고속 추론하는 리스크 평가 레이어. 오프라인 학습 시에는 동일 source_strategy_id 계보 내 인접 proposal에 국한하여 temporal loss를 적용하는 필수 Loss를 활용해 `model_risk_score` (Sigmoid 적용, 높을수록 위험) 단일 출력 헤드만 학습 및 탑재하며, Validation ECE/Brier score 미달 시에는 확률이 아닌 랭킹용 risk index로 활용이 제한됩니다. 시스템 계산 유도로 `uncertainty_score = (-p log_e(p) - (1-p) log_e(1-p)) / log_e(2)` (p는 model_risk_score, [0, 1] 범위로 정규화) Entropy 식을 반영하여 `confidence_score = 1 - uncertainty_score` (UI 표시: 모델 판단 확신도, 안전도가 아님에 유의)를 산출하고, 파생 지표 순환 의존성 차단을 위해 **직전 확정 window frozen/replay rank 변동량** 기반으로 `rank_stability` (EMA 적용)를 구하며, 분모 Zero-division 방지($safe\_baseline = \max(baseline, eps)$, $eps \approx 1e-9$) 및 모든 stability 성분을 `[0.0, 1.0]` 범위로 클립한 가중합 `stability_score = clip(0.6 * min(rank_stability, market, system) + 0.4 * mean(rank_stability, market, system), 0.0, 1.0)` 수식을 적용한 4중 지표 뷰를 가동하고, 모든 점수 성분(`model_risk_score`, `fallback_risk_score`, `girs_promotion_score`, `fallback_promotion_score`, `final_promotion_score`)의 [0, 1] 범위 제한과 Fallback 단조성을 강제하는 **Score Scale Golden Test**로 정합성을 보증하며, 런타임의 피처 정합성 오류 수준에 따라 Staged Progressive Degradation 가드를 수행합니다. 특히 `stability_score <= 0.2` 이거나 원본 데이터 품질 검사 실패(`data_quality_blocked = True`) 상황에서는 일반 리스크 계산 프로세스를 전면 생략하고 모든 promotion_score 및 confidence_score를 0.0으로 강제 반환하여 자동 승격을 원천 차단합니다.

**Promotion Event Log (승격 이벤트 로그)**:
Promotion Queue의 상태 변화와 승격 결정을 누적 기록하는 append-only 형태의 영속화 로그. 8가지 이벤트(`PROPOSAL_ENTERED`, `PRUNER_ACCEPTED`, `PRUNER_REJECTED`, `QUEUE_INSERTED`, `RANK_UPDATED`, `CHAMPION_CHANGED`, `PROMOTION_APPROVED`, `PROMOTION_EXECUTED`)를 가지며, 큐의 전체 랭킹 상태를 리플레이하여 복구할 수 있도록 `state = reduce(events)` 구조의 멱등성을 보장합니다.

**Champion (챔피언 전략)**:
현재 실전 투입되어 실제 자산(또는 메인 모의 포트폴리오)을 굴리고 있는 주력 활성(ACTIVE) 전략.

**Challenger (챌린저 전략)**:
그림자 리그(SHADOW)에서 실시간으로 테스트를 받으며 챔피언 전략의 성능과 경쟁하는 후보 전략.

**Active Promotion (실전 승격)**:
챌린저 전략이 그림자 리그(SHADOW) 관찰 기간 동안 실시간 데이터 비교를 통해 챔피언 전략을 압도하여 승격 후보(CANDIDATE) 상태로 전환된 후, 사용자의 최종 승인에 의해 ACTIVE 상태로 교체 및 기용되는 프로세스.

**Candidate State (승격 후보 상태)**:
그림자 리그(SHADOW) 내의 특정 챌린저가 1차 하드 필터 및 2차 리그 스코어 비교를 모두 통과하여 기존 챔피언을 대체할 준비를 마친 대기 상태.

**Market Regime Summary (시장 상태 요약)**:
일정 주기(예: 1분) 단위로 거래소 및 종목별 시장 상태 피처(변동성, 호가 불균형, 거래량 비율, 스프레드 등)를 집계하여 기록한 시세 피처 요약 레코드.
_Avoid_: 시장 상태 스냅샷

**Champion Cooldown (챔피언 교체 쿨다운)**:
잦은 실전 전략 교체로 인한 계좌 불안정성을 방지하기 위해, 신임 챔피언 전략으로 가동 전환된 이후 **최소 7일 경과 AND 최소 100건 거래 완료** 조건이 동시에 충족될 때까지는 타 챌린저에 의한 강제 강등을 유예하는 안전장치. 이 장치는 평가 잠금이 아닌 정책 동결 윈도우(Policy Freeze Window)로 작동하여, 쿨다운 중에도 큐 내부의 후보 전략 재정렬 및 비교 평가는 Lock되지 않고 실시간으로 계속해서 활발히 이루어집니다.

**Champion Registry (챔피언 계보 레지스트리)**:
역대 실전 Champion 전략들의 등극 시점, 은퇴 시점, 운용 성과(ROI, MDD 등) 및 은퇴 사유를 누적하여 기록 및 관리하는 전략 추적 레지스트리.

**Rollback (원클릭 복구)**:
전략 파라미터 변경 후 성과 악화 시, `strategy_parameter_history` 상의 **사용자가 명시한 특정 버전 설정값으로 즉시 복구**하여 자산을 보호하는 복구 조치.

**Performance Snapshot (전략 성과 스냅샷)**:
전략 파라미터 변경(PARAMETER_CHANGE), 원클릭 복구(ROLLBACK), 데몬 기동(STARTUP), 혹은 정기적 주기(PERIODIC) 시점에 실제 포트폴리오의 실전 성과(ROI, MDD, PF 등) 상태를 특정 전략 버전(`version_id`)과 매핑해 스냅샷으로 영구 보관하는 성과 레코드.

**Strategy Version (전략 현재 버전)**:
각 전략별로 현재 실전 투입(ACTIVE)되어 가동 중인 최신 파라미터 설정, 버전 식별자, 적용 시작 시점(`applied_at`), 롤백 복구 시 원인 버전 ID(`rollback_source_version`), 그리고 어떤 버전에서 파생되었는지 추적하기 위한 부모 버전 식별자(`parent_version_id`) 레코드.

**Strategy Proposal (전략 파라미터 제안)**:
Shadow Backtest 검증을 완벽히 통과하여 사용자에게 승인/보류/기각 여부를 묻기 위해 발행된 파라미터 조합 및 기대 지표 세트. 실전 적용 시점의 상태(`status` - `'APPLIED'`, `'PRUNED'`, `'DEFERRED'`) 및 적용 후의 실전 적용 상태(`outcome` - `'RUNNING'`, `'ROLLED_BACK'`, `'COMPLETED'`)를 함께 추적합니다.

**Proposal Evaluation (제안 사후 평가)**:
승인되어 실전 적용(APPLIED)된 제안의 백테스트 예상 성과 지표와 실전 적용 후의 실제 성과 지표 간의 괴리를 추적 및 비교 분석하는 프로세스.

**Version Intelligence Layer (버전 지능화 계층)**:
AI 제안의 다요소 스코어링, 시장 국면 반영 가중치 적용, 롤백 예방을 위한 파라미터 정규화 거리 계산, 60점 미만 제안 자동 Pruning, 그리고 80점 이상 제안의 하이브리드 이벤트 적용 스케줄링을 통합 제어하는 의사결정 자동화 레이어.

**Auto-pruning (자동 폐기)**:
백테스트 결과 신뢰도 점수가 60점 미만이거나 불리한 성과 지표를 가진 제안을 생성 단계에서 즉시 `PRUNED` 혹은 `DEFERRED` 상태로 격리하여 대시보드 부하를 줄이는 자동 필터링 기능.

**Rollback Safety Lock (롤백 안전 잠금)**:
사용자가 특정 전략에 대해 수동 롤백을 실행했을 때, 해당 전략의 자동 파라미터 적용(AUTO)에 의한 예기치 못한 재변경을 막기 위해 `ENABLE_AUTO_PROPOSAL` 상태를 즉시 False로 고정 및 전역 차단하는 비상 잠금 안전장치.

**Parameter-weighted Normalized Distance (파라미터 가중 정규화 거리)**:
서로 다른 단위와 범주를 지닌 파라미터 간의 물리적 유사도를 가중치와 baseline 대비 비율로 산출하여, 최근 롤백을 유발한 잘못된 파라미터와 후보 파라미터가 다시 근접하는 것을 감지하고 패널티를 주는 유사도 척도.

**DatasetExporter (데이터셋 엑스포터)**:
시스템 내의 DAG 변이 그래프, 전략 제안(Strategy Proposal) 이력, 실거래 성과 등을 머신러닝 학습이 가능한 포맷의 물리적 파일(JSONL 및 메타데이터)로 안전하게 영구 적재하고 동기화하는 데이터 레이어 모듈.
_Avoid_: 데이터 추출기, 데이터 덤퍼

**FeatureBuilder (피처 빌더)**:
물리적으로 저장된 Raw JSONL 파일로부터 부모 노드 정보(Parent State), N-hop 변이 체인 컨텍스트, 이전 ROI 트렌드(Previous ROI trend) 등의 파생 피처(Derived features)를 런타임에 동적으로 연산하여 가공하는 온디맨드 피처 파이프라인이자, 실시간 거래 시점의 틱 요약, 캔들 지표 추출, Freshness TTL 검증 및 결정성(Deterministic) 이중 sha256 해시 연산을 거쳐 FeatureSnapshot DTO를 조립 및 검증하는 런타임 피처 처리 모듈.

**DatasetLoader (데이터셋 로더)**:
FeatureBuilder를 활용하여 가공된 파생 피처들을 머신러닝 모델의 학습 성격(Tabular, Sequence, Graph)에 최적화된 런타임 뷰(Model View) 객체로 변환해주는 데이터 로더 모듈.

**3-Tier Outcome (3계층 결과 모델)**:
머신러닝 데이터셋의 레이블 오염(Label Leakage) 및 Causal Confusion을 막기 위해 노드의 성과를 실제 거래 성과(OBSERVED), 가상 추적 시뮬레이션 성과(ESTIMATED), 미관측 성과(MASKED)로 엄격히 구분하여 정의한 결과 및 레이블 체계.

**Event Buffer (이벤트 버퍼)**:
디스크 I/O 병목 및 정합성 붕괴를 방지하기 위해 발생한 변이 이벤트를 메모리에 임시 보관한 뒤, 주기적인 배치(Periodic Batch) 시점에 멱등성(Idempotent) 있게 그래프를 재구성하는 중간 버퍼.

**Graph-informed Risk Scorer (GIRS / 그래프 인지 리스크 스코어러)**:
오프라인에서 전략 변이 DAG 정보 및 시장 국면을 그래프 어텐션 네트워크(GAT)를 통해 특징 압축한 뒤, 실시간 런타임에는 그래프 연산을 배제하고 오직 고정 차원의 요약 피처만을 입력받아 Single-head MLP 구조 of ONNX 모델로 10ms 이하 초고속 추론하는 리스크 평가 레이어. 오프라인 학습 시에는 동일 source_strategy_id 계보 내 인접 샘플에 국한하여 temporal loss를 적용하는 Loss를 활용하고, 모델 출력(`model_risk_score` index)과 시스템 계산 유도(`confidence_score = 1 - uncertainty_score` (UI 표시: 모델 판단 확신도, 안전도가 아님에 유의), `stability_score = clip(0.6 * min(rank_stability, stability_market, stability_system) + 0.4 * mean(rank_stability, stability_market, stability_system), 0.0, 1.0)`으로 격리 유도하되 `rank_stability`는 순환 의존성 차단을 위해 직전 확정 window frozen/replay rank 변동량을 활용하며, 각 stability 성분은 $eps \approx 1e-9$로 분모 Zero-division을 방어하며 `[0.0, 1.0]` 범위로 클립)를 분리 조율한 4중 지표 뷰를 가동하며, 모든 점수 성분(`model_risk_score`, `fallback_risk_score`, `girs_promotion_score`, `fallback_promotion_score`, `final_promotion_score`)의 [0, 1] 범위 제한과 Fallback 단조성을 강제하는 **Score Scale Golden Test**로 정합성을 보증하며, 런타임의 피처 정합성 오류 수준에 따라 가용성을 유지하기 위해 Staged Progressive Degradation 가드를 수행합니다. 특히 `stability_score <= 0.2` 이거나 원본 데이터 품질 검사 실패(`data_quality_blocked = True`) 상황에서는 일반 리스크 계산 프로세스를 전면 생략하고 모든 promotion_score 및 confidence_score를 0.0으로 강제 반환하여 자동 승격을 원천 차단합니다. Validation ECE/Brier score 미달 시에는 model_risk_score 확률 사용을 제한하고 risk index로만 간주합니다. GNN 학습을 위한 `rollback_risk = 1` 정답 라벨은 적용 후 T시간 ROI 언더퍼폼, MDD 위반, 사용자 수동 롤백 및 shadow league 강등에 의거해 수립하며, 피처 생성 단계에서는 **Causal Data Leakage Guard**를 적용하여 미래 데이터의 누수를 원천 차단합니다.

**Canonical Policy Embedding (표준 정책 임베딩)**:
상이한 매개변수 공간을 가진 다양한 전략들을 고정된 차원의 공동 잠재 정책 공간(Shared Latent Policy Space)으로 MLP 투사(Projection)하여 GIRS가 전략 일반성을 오프라인에서 학습할 수 있도록 인코딩하는 압축 정책 표현.

**Regime Continuous Embedding (연속 국면 임베딩)**:
시장 피처 데이터를 오토인코더(Autoencoder)를 통해 다차원의 연속 실수 공간상에 매핑하여 시장 상태를 압축한 정보 벡터로, GIRS 학습 및 추론 시의 주변부 맥락 노드 피처로 활용됩니다.

**Heterogeneous Strategy Graph (이종 전략 그래프)**:
전략 제안 노드, 부모 전략 노드, 시장 국면 노드, 실행 결과 노드 등 이종의 노 타입 및 다중 관계 메타엣지(유전 상속, 동료 비교, 시장 환경 등)로 구성된 전략 진화 및 성과 관계 구조의 오프라인 그래프 표현.

**Promotion Queue (승격 대기 큐)**:
GIRS의 필터링을 통과한 전략 진화 후보군 생태계로, 단순 가중합(Weighted Sum) 방식의 정렬을 배제하고 다목적 파레토 프런트(Multi-objective Pareto Front: ROI 최대화, MDD 최소화, Stability 최대화) 기반으로 실시간 최적 대안을 랭킹하는 상태 보존형 랭킹 엔진. 리플레이 보정 및 단계별 전이 안전성을 보장하기 위해 Candidate $\rightarrow$ Scored $\rightarrow$ Ranked $\rightarrow$ Promotion(Pending, Locked, Rejected, Executed, Expired 내부 상태로 세분화하며, PromotionLocked timeout 시 PromotionRejected(reason="LOCK_TIMEOUT")로 단일 경로 강제 복귀 전이, Rejected에서 쿨다운 게이트($T_{\text{cooldown}}$) 통과 및 만료 전 회복 시 ScoredQueue로 단일 경로 결정론적 복귀, rejected_max_age 및 proposal_ttl 초과 시 Expired 영구 격리)으로 상태 전이하는 유기적인 **유한 상태 머신 (FSM)** 구조로 설계되며, Fast Path와 비동기 랭킹 정정을 위한 Lazy Replay Correction Layer로 구성됩니다.

**Staged Progressive Degradation (단계적 점진 격하)**:
피처 정합성을 Noise Drift와 Regime Shift로 분리 감지하며, Regime Shift 시에는 **ML 국면과 룰 기반 감지기(Rule-based regime detector)의 2단 이중 가드 및 Rule Override Priority**를 적용합니다. 룰 기반 국면 검출이 확고할 시(`rule_regime_confident` $\rightarrow$ True) 룰 국면을 최종 채택(absolute override)하고, 모호할 경우 두 국면을 가중 결합합니다. 최종 Level 3 Fallback은 치명적인 데이터 붕괴 시(NaN/inf 검출, 차원+스키마 불일치, 또는 동적 rolling std 기반 threshold 임계치를 초과하는 feature_mean_shift 발생 시) ML을 무력화하고 4요소(volatility, drawdown, regime, spread+volume+depth 분해 liquidity) Rule-based Scorer로 대체하여 가용성을 보장합니다.

**Candidate Proposal (후보 제안 객체)**:
`ShadowBacktest` 검증 직후와 `GIRS` 추론 및 `Promotion Queue` 등록 단계 간의 결합을 분리하기 위해, 백테스트 성과(`backtest_result`), 피처(`features`), GIRS 예측 점수(`gnn_score`)를 바인딩하는 중간 메모리 레코드 객체. feature drift 및 버전 미스매치 예방을 위해 `schema_version`, `feature_hash`, `regime_tag`, `generated_at`, `source_strategy_id` 스키마 필드를 의무적으로 가집니다.

**Promotion Event Log (승격 이벤트 로그)**:
Promotion Queue의 상태 변화와 승격 결정을 누적 기록하는 append-only 형태의 영속화 로그. event_id(UUID) 및 sequence_no 컬럼을 탑재하고 (proposal_id, sequence_no) 또는 event_id에 Unique Constraint를 적용해 중복 처리를 차단하며, 큐의 전체 랭킹 상태를 리플레이하여 복구할 수 있도록 `state = reduce(events)` 구조의 멱등성을 보장합니다.

**Materialized View (구현된 상태 뷰 캐시)**:
영구 불변의 Raw Event Log를 매 갱신 시점이나 메모리 구동 시점에 빠른 속도로 캐싱하여 큐의 최신 정렬 상태를 표현하는 인메모리 재빌드 가능(Rebuildable) 캐시 뷰.

**Feature Contract (피처 계약)**:
런타임 피처의 무한 팽창 및 버전 미스매치를 방지하기 위해, 피처의 엄밀한 스키마 규격(`FeatureSchema`)을 강제하고 런타임 추론 진입 전에 입력 벡터의 정합성을 최종 통제하는 검증 레이어. 실시간 패스의 지연 시간 급증과 과민 반응을 막기 위해 **2-Stage Feature Contract Validation** 검증 체계를 수행합니다. 실시간 Step 1 (Cheap Check, 즉시 Fallback)은 NaN/inf 검출, shape mismatch, dtype mismatch 뿐만 아니라, 비정상 범위 이탈을 막는 range check (min/max clamp) 및 필수 피처군(price, liquidity, regime 파생 피처군)의 연속 N회 정체를 감지하는 stale count 기반 zero-variance 검출을 신속히 처리하며, 비동기 Step 2 (Slow Check, Degrade 감쇄)는 동적 rolling 임계치 기반인 $\text{feature\_mean\_shift} > \text{threshold}$ (여기서 $\text{threshold} = k * \text{rolling\_std}(\text{feature\_vector}) \quad (k \approx 2.5 \sim 3.5)$) 및 rolling std drift를 비동기 연산하여 가중 격하에 반영합니다.

**Regime-aware Pareto Clustering (국면 인지 파레토 클러스터링)**:
Pareto 랭킹 연산의 병목 및 진동을 차단하기 위해, 국면 확률 할당(Soft Assignment), 가중 멤버십, 시간 윈도우 스무딩(Pareto Smoothing)의 3단계 안정화 필터링을 적용하고, 연산 지연 최소화를 위해 상태 변경이 있는 클러스터만 부분 갱신하는 **증분식 랭킹 업데이트(Incremental Ranking Update)** 기반의 랭킹 기법.

**Snapshot-based Inference Contract (스냅샷 기반 추론 계약)**:
비동기 피처 수집과 동기 GIRS 추론 간의 경쟁 상태 및 미세 시간차(Micro-lag)를 차단하기 위해, 추론 시점의 피처를 버전 락(`feature_schema_version` 및 `regime_version` 일치 의무화)이 구성되고 시장 변동성 및 시스템 지연 시간에 연동되는 **`Adaptive TTL` (적응형 유효기간)** 가드가 만료되지 않은 불변 `FeatureSnapshot`으로 추론을 격리 보장하는 안전 협약.

**Lazy Replay Correction (비동기 리플레이 정정)**:
실시간 랭킹 Fast Path의 지연 시간을 최소화하기 위해 동기 검증을 수행하지 않고, 백그라운드 데몬 루프에서 **주기적 배치 윈도우(e.g., 10s)** 단위로 `full_event_rebuild()`를 수행하며, 미세 랭킹 흔들림(Oscillation) 방지를 위해 이중 임계치 Hysteresis Rule ($T_{\text{low}}=0.1, T_{\text{high}}=0.3$)에 입각해 `Materialized View` 상태를 최종 보정하는 리플레이 안전 장치. 이때 Rank Drift 계산이 후보 수 $N$에 흔들리지 않도록 정규화하고 누락 예외를 줄이기 위해, 대상을 Fast와 Replay의 합집합($candidates = union(FastRank, ReplayRank)$, $N=len(candidates)$)으로 고정하고 누락 후보는 $missing\_rank = N+1$로 가드 처리하며, 개별 순위 차이를 분모 $N$으로 나누어 정규화하고 [0, 1] 범위로 clip한 **Normalised Weighted Rank Drift** 오차 수식($\text{drift} = \sum weight_i * \text{rank\_diff\_i} / \sum weight_i$, $\text{rank\_diff\_i} = \text{clip}(|RankFast - RankReplay| / \max(1, N), 0.0, 1.0)$, 가중치는 $\min(RankFast, RankReplay)$ 기준 적용)을 사용하되, $N=0$ 또는 가중치합 0일 시 $drift=0.0$ 및 `action = NOOP`으로 예외를 방어(empty guard)합니다. 의사결정 최종 결합은 **Single Decision Authority Rule** (Replay 부재 시 Sigmoid 스무딩 가중합 $\alpha = \text{sigmoid}(10 * (\text{stability\_score} - 0.5))$ 기반의 $\text{final\_promotion\_score} = \alpha * girs\_promotion\_score + (1 - \alpha) * fallback\_promotion\_score$ 충돌 해소 적용, 모든 점수는 `1 - risk` 변환된 promotion_score로 통일)에 입각하여 처리합니다.

## Relationships

- 하나의 **Interval** 설정에 따라 여러 개의 **Candle**이 연속적으로 생성됨
- **Candle**은 기술 지표(Indicator) 계산의 기초 데이터가 됨
- 수많은 **Tick**이 모여 하나의 **Candle**을 구성함
- **TradeEngine**은 각 종목의 **Tick**을 수신하여 내부의 **Candle** 상태를 관리함
- 실시간 수집 전, **TradeEngine**은 반드시 **Warm-up** 과정을 거쳐 지표를 최신화해야 함
- **Doji**가 발생하면 시스템은 현재 가격을 이전 **Candle**의 종가와 비교하여 **Trade Simulation**의 색상 로직에 반영함
- **Spike Detector**는 **Tick** 스트림을 분석하여 **Spike**를 포착함
- **Spike**가 발생하면 시스템은 **Alert**를 생성하고 저장함
- **Trade Simulation**은 하나 이상의 **Strategy**를 실행하여 매매 신호를 발생시킴
- **Backtest**는 과거의 **Candle** 데이터를 입력값으로 사용하는 **Trade Simulation**의 형태임

### 3. Logging & Telemetry
- **Standard**: Always use `src.engine.utils.telemetry.get_logger`.
- **Avoid**: Never use standard `logging.getLogger` or `print()` for system logs.
- **Leverage**: `logger.warning` and `logger.error` automatically broadcast alerts to the UI.

### 4. KIS (한국투자증권) API 연동 규격 원칙
- **KIS (한국투자증권) 연동 및 다중 자산군 개발 시**: 에이전트는 사용자의 추가적인 요청 유무에 관계없이 아래의 **5대 분할 정제 마스터 매뉴얼**을 상시 우선 참조하여 설계의 무결성을 완벽하게 보증해야 합니다.
  - **전체 API 인덱스 목차 (최우선 색인원)**: [kis_api_list.md](file:///home/simon/ATS/docs/%20manual/kis/kis_api_list.md) 문서를 참조하여 전체 KIS API의 ID, 호출 URL, TR_ID, 설명 등의 일람 목차를 초고속 탐색 및 대조합니다.
  - **국내주식 (주문/잔고/실시간 체결·호가)**: [kis_domestic_stock.md](file:///home/simon/ATS/docs/%20manual/kis/kis_domestic_stock.md) 문서를 참조하여 국내주식 현금주문, 정정취소, 잔고조회, 실시간 WebSocket 체결/호가 프레임 레이아웃과 TR_ID를 확인합니다.
  - **해외주식 (주문/잔고/실시간 시세)**: [kis_overseas_stock.md](file:///home/simon/ATS/docs/%20manual/kis/kis_overseas_stock.md) 문서를 참조하여 미국/아시아 해외주식 주문, 예약주문, 실시간 체결 통보 사양을 확인합니다.
  - **선물옵션 및 파생상품**: [kis_derivative.md](file:///home/simon/ATS/docs/%20manual/kis/kis_derivative.md) 문서를 참조하여 선물옵션 주문, 잔고정산, 야간 체결/호가 웹소켓 수신 규격을 분석합니다.
  - **채권/ELW/기타자산**: [kis_bond_etc.md](file:///home/simon/ATS/docs/%20manual/kis/kis_bond_etc.md) 문서를 참조하여 장내/일반채권 주문, 잔고조회 및 ELW 실시간 체결 명세를 확인합니다.

- **Portfolio** 상태는 **Order Matching** 결과에 따라 업데이트됨
- **PortfolioManager**는 **TradeEngine**이 생성한 신호를 받아 적절한 **Portfolio**에 배분함

**PerformanceAnalyzer (성과 분석기)**: 포트폴리오의 실시간/정적 거래 내역 및 잔고를 입력받아 ROI, MDD, 누적 수수료, 거래소별 자산 정렬, 그리고 종목별 손익 통계를 산출하는 무상태형(Stateless) 성과 분석 모듈.

**ExecutionScorer (주문 실행 스코어러)**: DB, Repository, PortfolioManager 등 외부 상태에 의존하지 않고, 신호와 포트폴리오 상태만을 이용해 포지션 수량, 리스크 한도, 슬리피지 보정 가격을 연산하는 무상태형(Stateless) 계산 모듈.

**ExecutionPipeline (주문 실행 파이프라인)**: 매매 신호(BUY/SELL)가 감지되었을 때, ExecutionScorer를 통해 자산 단가 계산, 리스크 검증, 슬리피지 보정을 실행하고, 포트폴리오 매니저에 주문 지시를 보낸 뒤 DB 적재 및 알림 전송 등의 실행 과정을 조율하는 오케스트레이션 모듈.

**ParameterEvaluator (파라미터 평가기)**: DB 및 시뮬레이터 의존성 없이, 가상의 파라미터 셋과 지표 데이터를 기반으로 파라미터 간 정규화 유사 거리, 국면별 적합성 가중치, 다요소 신뢰도 점수(Confidence Score)를 수학적으로 연산하는 무상태형(Stateless) 계산 모듈.

**Local Replay (국소적 진입/탈출 타점 복기)**: 매수/매도 주문이 체결된 시점의 전후(예: ±5분) 미세 호가창 및 틱 데이터를 리플레이하여 최적의 진입/탈출 가격 대비 실행 효율성 점수를 산출 및 시각화하는 기능으로, 시스템 최종 비전(ADR 0008)의 핵심 구성요소입니다.

**Counterfactual Simulation (동일 기간 대조 전략 시뮬레이션)**: 실제 자산 매매가 가동된 기간과 동일한 시점에 가상의 다른 전략이나 매개변수 조합을 대입하여 수익률 곡선의 변화를 비교하고, 챔피언 전략을 선별하기 위한 실시간 대조 시뮬레이션으로, 시스템 최종 비전(ADR 0008)의 핵심 구성요소입니다.

**Auto-strategy Selection (자동 모의투자 전략 매핑)**:
모의투자(실계좌) 포트폴리오 구동 시작 시 사용자가 적용할 전략을 별도로 선택하지 않았을 경우, DB 내에 존재하는 최신 챔피언 전략(`strategy_versions`)을 탐색하여 자동으로 기용하는 시스템 흐름. 만약 DB 내에 유효한 챔피언 전략 정보가 전무하다면, 사전에 약속된 기본 템플릿(Fallback Strategy)을 조립하여 모의투자를 가동합니다.

**Exchange-specific Strategy Overrides (거래소별 전략 동적 오버라이드 및 병합)**:
각 거래소(`Exchange`)의 규격과 자산 특성(예: 수수료율, 거래단위, 특정 interval 제약 등)에 맞추어 맞춤형 전략 운용이 가능하도록, 설정 파일(`settings.yaml`)의 `overrides.[exchange_id]` 영역에 명시된 전략 활성화 여부(`enabled`) 및 파라미터를 `reload_trade_engines` 시점에 실시간 포트폴리오에 동적으로 병합 및 오버라이드하여 적용하는 기능.

**Market Data Cleanup Daemon (시장 데이터 정리 데몬)**:
데이터베이스의 비대화를 방지하기 위해 틱(Tick) 및 분봉(Candle) 데이터의 생명주기를 감시하고, 설정된 보존 기간(TTL)을 초과한 오래된 데이터를 백그라운드에서 주기적으로 영구 삭제 및 다운샘플링하는 상시 실행 프로세스.

**Active Asset (보유 자산)**: 현재 계좌에 잔고가 존재하는 실제 보유 자산. 평가액이 많은 순으로 기본 정렬되며, 자산비중 게이지를 통해 포트폴리오 내 분산 비율을 시각화합니다.

**Liquidated Asset (처분 완료 자산)**: 과거에 거래 이력이 존재하지만 현재는 잔고가 0인 자산. 실현손익이 많은 순으로 기본 정렬되며, 실현수익률 게이지를 통해 매매 성과를 시각화합니다.

**Realized P&L (실현손익)**: 실제 거래 및 현재 평가액을 종합하여 산출된 투자 성과 금액. 보유 자산은 `총 매도액 + 평가금액 - 총 매수액 - 총 수수료 - 총 거래세`로 계산하며, 처분 완료 자산은 `총 매도액 - 총 매수액 - 총 수수료 - 총 거래세`로 산출합니다.

**Realized ROI (실현수익률)**: 투입된 전체 비용(총 매수액 + 총 수수료 + 총 거래세) 대비 실현손익의 비율.

**Planned Asset Event (상장/상장폐지 예정 이벤트)**: 거래소의 공지사항 감지 또는 API 조회를 통해 사전에 인지하여 특정 장래 시점에 상장 또는 상장폐지가 실행되도록 등록한 예약 정보 레코드.

**Auto-universe Synchronization (자동 수집 종목 동기화)**: 수동 자산 동기화 요청 없이, 백그라운드 데몬이 주기적으로 거래소 데이터(공지사항 및 마켓 리스트)를 폴링하여 신규 상장/상장폐지 대상을 감지 및 감시 유니버스에 자동으로 반영하는 프로세스.

## Flagged ambiguities

- "Trade"는 업비트 API에서 **Tick**을 의미하지만, 시스템 내에서는 **Trade Simulation**의 실행 단위(거래)와 혼동될 수 있으므로 개별 데이터는 **Tick**으로 통일합니다.
- "Simulation"은 과거 데이터 검증(**Backtest**)과 실시간 가상 매매를 모두 포함하는 포괄적인 용어로 정의합니다.
- **TradeEngine**은 **Tick**을 받아 **Candle**을 만들고 **Strategy**를 실행하는 모든 연산을 수행하는 단일 책임 단위를 의미합니다.
- **PortfolioManager**는 실제 주문 집행과 자산 관리를 분리하여 엔진의 독립성을 보장합니다.
