# 프론트엔드 아키텍처 명세 (Frontend Architecture)

이 문서는 통합 트레이딩 시스템(ATS)의 프론트엔드 모듈 구조 및 뷰-데이터 흐름을 기술합니다. 본 프론트엔드는 React 등 중량 프레임워크를 사용하지 않고 웹 표준 API와 Vanilla JS로 구현되어 가볍고 빠른 실시간 데이터 렌더링을 보장합니다.

- **디렉토리 위치**: [frontend](file:///home/simon/ATS/frontend)

---

## 1. 아키텍처 개요 (Overview)

본 시스템은 단일 페이지 어플리케이션(SPA) 구조로, **상태(Store)**와 **네트워크 통신(Client/Stream)**, **렌더러(View Components)**가 느슨하게 결합되어 데이터 변화에 따라 화면이 갱신됩니다.

```
       [ FastAPI Server (/ws, /api) ]
                 │          ▲
      Websocket  │          │ REST API
      (stream.js)▼          │ (client.js)
            ┌───────────────┴───────────────┐
            │           store.js            │  ◄── (전역 상태 관리 저장소)
            └───────────────┬───────────────┘
                            │ 상태 전파 (State Propagation)
     ┌──────────────────────┼──────────────────────┐
     ▼                      ▼                      ▼
  chart.js            portfolio-view.js       ranking.js / notifications.js ...
(Lightweight Charts)  (자산 분배 원형그래프)       (기타 대시보드 위젯)
```

---

## 2. 핵심 프론트엔드 모듈

### 2.1. 진입점 및 제어 (Entrypoint & Routing)
- **[index.html](file:///home/simon/ATS/frontend/index.html)**: 대시보드(모의투자/실거래) UI 레이아웃, 세션 선택 드롭다운, 실시간 차트 및 테이블 영역 정의.
- **[app.js](file:///home/simon/ATS/frontend/app.js)**: 애플리케이션 메인 초기화 루프를 수행하고 이벤트를 연동합니다.
- **[router.js](file:///home/simon/ATS/frontend/router.js)**: `ViewRouter` 클래스를 구동하여 해시 라우팅(`#dashboard`, `#settings` 등)에 의거해 알맞은 화면 섹션을 동적으로 온/오프시킵니다.

### 2.2. 데이터 레이어 (Data & State Management)
- **[store.js](file:///home/simon/ATS/frontend/store.js)**: 전역 애플리케이션의 메모리 상태 관리기입니다.
  - 관리 데이터: 활성 포트폴리오 목록, 캔들 데이터, 체결 데이터, 알림 경고 목록 등.
  - 타 모듈에서 데이터 변경 시 콜백을 실행할 수 있도록 옵저버 패턴을 일부 차용합니다.
- **[client.js](file:///home/simon/ATS/frontend/client.js)**: 백엔드 HTTP REST API와의 비동기 통신(`fetch`)을 래핑한 모듈입니다.
- **[stream.js](file:///home/simon/ATS/frontend/stream.js)**: 백엔드 `/ws` 엔드포인트와 WebSocket을 개설 및 복구 관리하며, 체결 틱(`tick`), 캔들 업데이트(`candle`) 및 시스템 경보 데이터를 실시간 수신하여 `store.js` 및 컴포넌트로 전달합니다.

### 2.3. 컴포넌트 & 뷰 레이어 (Views & Visualization)
- **[chart.js](file:///home/simon/ATS/frontend/chart.js)**: Lightweight Charts를 사용하여 Candlestick 차트를 그리며, SMA/EMA/볼린저 밴드 오버레이 및 거래량, MACD, RSI, ATR 보조 지표를 각각 독립적인 Y축 가격 스케일 및 정교한 수직 마진 분할(메인 50%, 거래량 15%, MACD 15%, ATR 10%, RSI 10%)을 통해 겹침 없이 고속 렌더링합니다.
- **[overview.js](file:///home/simon/ATS/frontend/overview.js)**: 실시간 운용 대시보드(Overview) 뷰 렌더러로, `simulation` 및 `live` 뷰 각각에 대해 6대 성과 메트릭 카드(ROI, 원금, 총 자산, 현금, 종목 평가액, 누적 수수료) 및 거래소별 자산 배분 비중 바를 독립적으로 렌더링합니다. 또한 각 거래소 블록 클릭 시 대시보드 하단에 상세 테이블 영역을 동적으로 활성화하고, `portfolio-view.js`의 테이블 렌더링 로직을 재사용하여 상세 종목 현황 및 종목별 거래내역을 연동 렌더링합니다. (기존 하단에 존재하던 보유 포지션 및 피드 패널은 대시보드 뷰 간략화 및 자산 요약 중심 렌더링을 위해 삭제되었습니다.)
- **[portfolio.js](file:///home/simon/ATS/frontend/portfolio.js)**: 실계좌 자산 관리, 수동 주문(주문 모달, 호가창), 미체결 및 예약 주문 관리를 관장하는 모듈입니다.
  - 주요 기능: 거래소별 실자산 조회, 거래 이력 모달 제어, 호가창(Orderbook) 실시간 렌더링 및 스크롤 센터링, 실계좌 주문 전송 및 제어, 주문 결과 피드백 제공.
  - 미체결 및 예약 내역 제어: `loadOutstandingOrders()`를 통해 각 거래소별 미체결 및 KIS 예약 주문을 조회하여 테이블에 실시간 렌더링하며, `cancelOutstandingOrder()`를 통해 사용자가 확인을 거친 후 일반 미체결 또는 예약 주문을 원격 취소하고 로컬 DB와 잔고를 최신화하도록 구현되었습니다.
- **[portfolio-view.js](file:///home/simon/ATS/frontend/portfolio-view.js)**: 포트폴리오의 실물 보유 현황 및 가상 투자 운용 상태를 표와 폼으로 렌더링합니다.
- **[portfolio-chart.js](file:///home/simon/ATS/frontend/portfolio-chart.js)**: 포트폴리오 자산 비중 현황을 직관적인 원형 차트(Pie Chart)로 표현하며, 한글 종목명 매핑을 적용해 시인성을 보장합니다.
- **[portfolio-adapter.js](file:///home/simon/ATS/frontend/portfolio-adapter.js)**: 백엔드 포지션 데이터(`avg_price`, `quantity`, `symbol`)를 프론트엔드 차트 및 UI 규격에 맞게 계산 및 가공해주는 변환기 모듈입니다.
- **[settings.js](file:///home/simon/ATS/frontend/settings.js)**: 실시간 수집기(Collector) 기동/중지 스위치 제어 및 DB 디스크 정리 관리 페이지입니다. **[NEW]** 실시간 팝업 알림의 전역 및 4대 채널(시스템 에러, 전략매매 체결, 매매 보류, 자산 변동) 개별 차단 체크박스 UI 제어와 백엔드 DB 영속성 바인딩 기능이 탑재되어 있습니다.
- **[daemon-monitoring.js](file:///home/simon/ATS/frontend/daemon-monitoring.js)**: [NEW] 통합 데몬 상태 모니터링 뷰의 최상위 조율 컨트롤러(`DaemonMonitoringView`)입니다. 공통 헤더 UI(PID, 기동 시각, 하트비트, CPU/메모리 리소스, 상태 뱃지, 데몬 재기동) 제어를 통합 관리하며 각 데몬(수집기, 전략, 평가, 클린업)의 탭 전환 및 서브 인스턴스 생애주기를 관장합니다.
- **[collector.js](file:///home/simon/ATS/frontend/collector.js)**: [NEW] 수집 데몬 프로세스의 실시간 리소스(메모리, 큐 사용률)와 거래소별 틱 수신 정보를 시각화하는 모듈로, 통합 데몬 모니터링 내 수집 데몬 탭 전용 화면 렌더링 및 개별 비동기 제어(거래소별 온/오프, ZMQ ACK 처리 등)를 담당합니다.
- **[strategy-daemon.js](file:///home/simon/ATS/frontend/strategy-daemon.js)**: [NEW] 전략 실행 데몬 프로세스의 텔레메트리 렌더링 및 개별 제어를 담당하는 하위 모듈입니다.
- **[evaluation-daemon.js](file:///home/simon/ATS/frontend/evaluation-daemon.js)**: [NEW] 평가 데몬 프로세스의 텔레메트리 렌더링 및 개별 제어를 담당하는 하위 모듈입니다.
- **[cleanup.js](file:///home/simon/ATS/frontend/cleanup.js)**: [NEW] 시장 데이터 정리 데몬의 라이프사이클 제어와 4개 카드 통합 실시간 텔레메트리 렌더링, 수동 정리 날짜 피커 변경 시 틱(Trades) 예상 삭제량 실시간 자동 쿼리, command_id 기반 비동기 대기 및 타임아웃, 중복 실행 방지(Mutex), 감사 이력 타임라인을 렌더링하는 클린업 탭 전용 모듈입니다.
- **[ranking.js](file:///home/simon/ATS/frontend/ranking.js)**: 수집 중인 실시간 종목들의 상승/하락률 및 거래대금 기준 랭킹 대시보드 뷰입니다.
- **[restored-view.js](file:///home/simon/ATS/frontend/restored-view.js)**: 캔들 데이터와 체결 틱 데이터의 정합성을 대조하여 불일치(누락) 캔들을 식별하고 수동/자동 복원 요청을 관리하는 복원 캔들 제어 뷰입니다. **[NEW]** 누락 캔들과 고스트 캔들(실제 틱이 없으나 DB에는 존재하는 오류 분봉) 탭 전환 기능을 탑재하고, 고스트 캔들 탭에서는 테이블 내 개별 '🗑️ 삭제' 버튼 연동을 통해 DB 데이터를 즉시 영구 클린업할 수 있도록 구현되어 있습니다.
- **[system-events.js](file:///home/simon/ATS/frontend/system-events.js)**: [NEW] 시스템 감사 로그 통합 조회 페이지입니다. `system_events` 테이블의 모든 감사 로그를 조회하고, 실시간 검색(키워드 필터링) 및 동적 이벤트 타입 필터를 지원하는 전용 감사 로그 뷰 모듈입니다.
- **[notifications.js](file:///home/simon/ATS/frontend/notifications.js)**: 실시간 매매 및 시스템 알림(수동 제어, 에러, 종목 동기화 등) 푸시 팝업 표시 및 상단 고정형 상장/상폐 예정 이벤트 배너 노출을 관장하는 모듈입니다. **[NEW]** store.js의 개별 채널 제어 변수(isTradeAlertEnabled, isSystemAlertEnabled 등)를 기반으로 팝업 알림 생성을 선택적으로 필터링(차단)하는 가드 조건이 적용되어 있습니다.
  * **통합 알림 수신 규격화**: 백엔드로부터 전달되는 모든 알림 이벤트(`skip`, `error`, `system`, `trade`, `asset`)를 단일화된 `"notification"` 페이로드(`type: "notification"`) 규격으로 수신하여 일관되게 토스트 팝업을 조립합니다.
  * **지능형 타겟 라우팅**: 알림 페이로드의 `target` 필드 정보를 파싱(예: `symbol:KRW-BTC`, `exchange:upbit` 등)하여 사용자가 알림 팝업을 클릭할 때 해당 거래소/종목의 대시보드 화면으로 즉시 포커스 및 이동(라우팅)하는 기능이 지원됩니다.
  * `checkUpcomingAssetEvents()`를 수행하여 미처리된 예정 일정이 존재하는 경우 대시보드 상단에 닫기 버튼이 포함된 영속적 배너를 생성합니다.
- **[market.js](file:///home/simon/ATS/frontend/market.js)**: 마켓(Market) 관리 모듈로, 거래소별 탭(Upbit, Bithumb, KIS)에 맞춰 실시간 시세 및 24h 변동 지표를 테이블 형태로 렌더링합니다. 전역 정렬 기준 필드(`state.marketSortKey`, `state.marketSortOrder`)를 활용해 클라이언트 사이드 실시간 정렬(3단계 순환 토글)을 수행하며, KIS 탭의 미수집 종목은 항상 하단에 고정하는 지능형 정렬이 적용되어 있습니다.
- **[kis-detail.js](file:///home/simon/ATS/frontend/kis-detail.js)**: KIS 및 가상자산 종목 상세 정보를 조회하고 실시간으로 렌더링하는 모듈입니다. 독립 라우트 뷰가 아닌 `monitoring-view` 내부의 '종목 상세정보' 탭 콘텐츠 영역으로 이식되었으며, 주식 종목일 경우 Nextrade 연동 여부 및 기업 상세 제원을 렌더링하고 가상자산 종목일 경우 전역 시세 캐시와 실시간 웹소켓 체결 틱을 재사용하여 3개 카드 구조(수집 상태 정보, 시세 요약 정보, 틱 기반 누적 체결 강도)의 상세 정보를 실시간 렌더링합니다.

---

## 3. 실시간 UI 갱신 시퀀스 (Real-time Flow)

1. **소켓 수신**: `stream.js`가 WebSocket을 통해 신규 `tick` 패킷을 받음.
2. **상태 업데이트**: 수신한 데이터를 `store.js` 내의 특정 배열에 누적(Push) 및 캐시 데이터 갱신.
3. **그래프 리트레이싱**: `chart.js`는 데이터 누적 이벤트를 받아 Lightweight Charts의 `setData()` 및 `update()` 메서드를 사용해 브라우저 렌더링 부하를 최소화하며 차트를 동적으로 갱신합니다.
4. **대시보드 실시간 자산 동기화**: `overview.js`는 실시간 시세 틱(`tick`)이 오면 `cachedPortfolio.positions`에 시세를 동기화하고, 포지션 목록의 현재가/수익률을 즉각 리렌더링할 뿐만 아니라 대시보드의 총 평가 자산 및 거래소별 자산 비중 바(상세 메트릭인 총 평가, ROI, 현금, 평가액 포함)를 실시간으로 재계산하여 화면을 갱신합니다. 특히, 사용자가 자산 비중 바을 관찰(마우스 호버) 중일 때는 툴팁이 깨지지 않도록 DOM 갱신을 일시적으로 지연(skip)시키고, 마우스가 이탈하는 즉시 최신 데이터로 업데이트를 반영하는 지연 렌더링 방식이 적용되어 있습니다.
5. **인터벌 전환**: 상단 시간 주기(Interval) 선택 시, 백엔드로부터 새로운 주기의 역사적 캔들 셋을 `client.js`로 호출하여 스토어를 전면 교체한 후 차트를 다시 로딩합니다.
6. **세션 드롭다운 및 포트폴리오 양방향 동기화**: 대시보드 상단의 세션 선택 드롭다운은 모의투자(`#overview-simulation-session-select`) 및 실거래(`#overview-live-session-select`)로 이중화되어 각각 `state.currentSimPortfolioId`와 `state.currentLivePortfolioId` 변경에 관여합니다. 현재 활성화된 화면과 일치하는 뷰의 세션 ID가 변경되면 `state.currentPortfolioId`에 동기화되어 `loadPortfolio()`가 트리거됩니다. 반대로 포트폴리오 뷰 이력을 클릭해도 대시보드의 드롭다운 선택값이 즉각적으로 일치됩니다.
7. **컴팩트 자산 비중 시각화**: 자산 비중 바의 낭비 공간을 최소화하기 위해 범주(Legend) 텍스트 영역을 생략하였으며, '기타' 병합 처리 없이 보유한 전 종목 자산 세그먼트를 100% 스택 바에 표현하고 마우스 호버 시에만 커스텀 CSS 툴팁으로 상세 정보를 제공합니다.
8. **상장 및 상장폐지 예정 이벤트 배너 갱신**: 대시보드 페이지 로드 시 또는 백소켓을 통한 실시간 알림(`toast_alert` 중 예정 등록 관련) 수신 시 `checkUpcomingAssetEvents()`가 실행되어 DB 예정 목록을 비동기 조회한 후 대시보드 상단에 고정 안내 배너를 동적으로 노출합니다. 사용자가 배너의 닫기 단추를 클릭하면 해당 상태가 백엔드 DB(`system_settings` 테이블)에 비동기 저장되어, 기기나 브라우저를 변경하더라도 다시 노출되지 않고 닫기 상태가 영구 유지됩니다.
9. **가상자산 상세 탭 실시간 갱신**: 사용자가 '상세정보' 탭을 열고 있을 때 실시간 체결 틱(`tick`)이 들어오면 `KisDetailView.updateCryptoDetailRealtime(tick)`이 작동하여 현재가, 전일 대비 변동, 누적 매수/매도 거래량 및 틱 기반 실시간 체결 강도(Volume Power)를 1초 미만 주기로 즉시 동적 갱신합니다.


---

## 4. 지연 로딩 및 롤링 윈도우 전략 (Lazy Loading & Rolling Window)

1. **지연 로딩 (Lazy Loading / 무한 스크롤):**
   - 1초봉 등 초 단위 봉의 경우 거래소 API 한계로 과거 백필이 불가능합니다.
   - 따라서 프론트엔드 차트의 왼쪽(과거) 끝단(`logicalRange.from < 10`) 도달 시 이벤트를 트리거하여 백엔드 API에 과거 30분 단위 범위의 체결 틱 데이터를 요청합니다.
   - 백엔드는 DB `trades` 테이블에서 해당 시간 구간을 쿼리(인덱스 스캔)해 즉석에서 초 단위 봉으로 조립하여 프론트엔드로 리턴하고, 프론트엔드는 이 데이터를 차트 맨 앞에 자연스럽게 머지(Merge)합니다.
2. **실시간 롤링 윈도우 보존:**
   - 캔들이 마감될 때 메모리 누수 방지를 위해 기본 500건으로 캔들 개수를 슬라이싱(`slice(-500)`)하여 롤 윈도우를 유지합니다.
   - 단, 사용자가 과거 데이터를 당겨와 탐색하는 중(AutoScroll OFF / Explorer Mode ON)에는 실시간 틱이 유입되더라도 슬라이싱을 우회하여 과거 데이터 소실을 방지합니다.
   - 사용자가 다시 "실시간 복귀" 버튼을 누르거나 마우스 우클릭으로 실시간 모드로 복귀할 때만 캔들 배열을 다시 500개로 축소 정제하여 성능과 탐색 편의성을 모두 달성합니다.

---

## 5. 데이터 렌더링 시 주의사항 (Precautions)

1. **자산 평가 시세 폴백 (`??` 연산자 필수 사용)**:
   - 백엔드에서 시세 조회가 불가능한(상장폐지/거래중단 등) 종목은 현재가가 `0.0`원으로 전송될 수 있습니다.
   - 이때 자바스크립트의 `||` 연산자를 사용하면 `current_price || avg_price` 과정에서 `0.0`원이 Falsy로 오인되어 과거 높은 평단가(`avg_price`)로 복귀해 자산 가치가 과도하게 평가되는 치명적인 오류가 발생할 수 있습니다. 
   - 따라서 반드시 널 병합 연산자 `??`를 사용하여 `0.0`원이 평가 및 비중 연산에 그대로 적용되도록 보장합니다.

2. **원화(KRW) 현금 절사**:
   - 수수료나 분할 거래 등으로 인해 발생하는 미세한 소수점 단위의 원화(KRW) 잔고 찌꺼기가 화면에 노출되는 것을 방지하기 위해, 원화 잔고에 한해 원 단위 이하 절사(정수화)를 수행하여 화면 시인성을 확보합니다.

3. **포트폴리오 날짜 범위 포맷팅**:
   - 모의투자 포트폴리오의 날짜 표시는 시작일시와 종료일시 모두 `YYYY-MM-DD HH:mm:ss` 형식으로 포맷팅합니다.
   - 포트폴리오의 진행 여부는 `ended_at` 속성의 존재 여부로 판단하며, 진행 중인 포트폴리오는 `시작일시 ~`, 종료된 포트폴리오는 `시작일시 ~ 종료일시` 형태로 날짜 범위를 출력합니다.

---

## 6. 의사결정 콘솔 뷰 (Decision Console View)

### 6.1. 개요

`strategy-view` 섹션은 단순 나열식 목록이 아닌, **전략의 전체 생애주기를 드릴다운으로 추적할 수 있는 입체적 의사결정 콘솔**로 재설계되었습니다.

**레이아웃**: 3단 분할 + 전체화면 Tracer 모달 하이브리드 구조

```
┌──────────────────────────────────────────────────────────────┐
│  [상단 요약 바] — 운영모드 · 활성전략 · 대기제안 · GIRS안정성   │
├─────────────┬─────────────────────────┬──────────────────────┤
│ 좌측 Tree   │   중앙 Workspace          │  우측 Tracer 요약    │
│ (전략/제안  │  (전략 상세 / 제안 목록) │  (GIRS · 가드 · 이력) │
│  카테고리)  │                          │  [전체화면 확장 ↗]   │
└─────────────┴─────────────────────────┴──────────────────────┘
                              ↓ 전체화면 확장 클릭
┌──────────────────────────────────────────────────────────────┐
│  Decision Intelligence Tracer (10개 탭 전체화면 모달)          │
│  FSM | GIRS | Feature | CF | Queue | Diff | Orders | Log |   │
│  Events | [Shadow] 재평가                                     │
└──────────────────────────────────────────────────────────────┘
```

### 6.2. 핵심 모듈: [strategy.js](file:///home/simon/ATS/frontend/strategy.js)

- **`loadStrategies()`**: 의사결정 콘솔 초기 진입 시 summary API 및 트리 데이터를 로드합니다.
- **`selectTreeLeaf(type, id)`**: 좌측 트리 노드 클릭 핸들러. `type`이 `strategy`이면 전략 상세 워크스페이스를, `proposal-group`이면 제안 목록을 중앙에 렌더링합니다.
- **`selectStrategy(strategyId)`**: 레거시/E2E 호환용 래퍼 (내부적으로 `selectTreeLeaf` 호출).
- **`loadStrategyWorkspace(strategyId)`**: `/api/decision-console/strategies/{id}/trace` 호출 후 4대 일치성 진단판, 파라미터 Diff, 성과 타임라인을 중앙 패널에 렌더링합니다.
- **`loadProposalListWorkspace(groupKey)`**: 제안 목록을 상태 필터와 함께 테이블로 렌더링하고 행 클릭 시 우측 Tracer 패널을 갱신합니다.
- **`loadTracerPanel(proposalId)`**: `/api/decision-console/proposals/{id}/trace` 호출 후 우측 요약 패널(GIRS 점수, 가드 목록, 이벤트 이력)을 업데이트합니다.
- **`openFullTracerModal()`** / **`closeFullTracerModal()`**: 전체화면 모달 열기/닫기.
- **`switchTracerTab(tabId)`**: 10개 Tracer 탭 전환 및 각 탭별 렌더러 호출.
- **`requestReevaluation()`**: `POST /api/decision-console/proposals/{id}/reevaluate` 호출 후 3초 폴링으로 Job 상태(QUEUED→RUNNING→COMPLETED)를 추적하여 UI에 실시간 반영합니다.
- **`initDecisionConsole()`**: 모달 확장 버튼, 탭 전환, 재평가 버튼 이벤트를 일괄 바인딩합니다.

### 6.3. APIClient 확장 메서드 ([client.js](file:///home/simon/ATS/frontend/client.js))

| 메서드 | 호출 엔드포인트 |
|---|---|
| `fetchKisSymbolDetail(symbol)` | `GET /market/symbols/kis/detail?symbol={symbol}` |
| `fetchDecisionConsoleSummary()` | `GET /api/decision-console/summary` |
| `fetchDecisionConsoleStrategies()` | `GET /api/decision-console/strategies` |
| `fetchDecisionConsoleStrategyTrace(id)` | `GET /api/decision-console/strategies/{id}/trace` |
| `fetchDecisionConsoleProposals(params)` | `GET /api/decision-console/proposals` |
| `fetchDecisionConsoleProposalTrace(id)` | `GET /api/decision-console/proposals/{id}/trace` |
| `requestProposalReevaluation(id)` | `POST /api/decision-console/proposals/{id}/reevaluate` |
| `fetchReevaluationJobs(id)` | `GET /api/decision-console/proposals/{id}/reevaluation-jobs` |
| `fetchDecisionConsoleEvents(params)` | `GET /api/decision-console/events` |
| `fetchDecisionConsoleRaw(type, id)` | `GET /api/decision-console/raw/{type}/{id}` |
| `fetchSystemSetting(key)` | `GET /api/system/settings/{key}` |
| `saveSystemSetting(key, value)` | `POST /api/system/settings/{key}` |

### 6.4. 라우팅 등록

`strategy.js` 최하단에서 `DOMContentLoaded` 이벤트 이후 `ViewRouter.registerRoute('strategy-view', ...)` 를 호출하여 전략 탭 진입 시 `initDecisionConsole()` → `loadStrategies()` 순으로 초기화합니다.

> **중요**: `registerRoute` 호출은 반드시 `DOMContentLoaded` 이후에 실행해야 합니다 (`router.js` 로딩 Race Condition 방지).

---

## 7. 통합 데몬 상태 모니터링 뷰 (Integrated Daemon Status Monitoring View)

### 7.1. 개요
`daemon-monitoring-view` 섹션은 시스템을 안정적으로 지탱하는 4대 백엔드 데몬 프로세스(데이터 수집기, 전략 실행기, 평가 엔진, 클린업 도구)의 건강 상태(Health Status)를 단일 뷰 및 공통 UI 레이아웃으로 통합하고, 개별 탭 전환을 통해 상세 리소스 모니터링 및 개별 비동기 제어를 제공하는 관리 콘솔입니다.

### 7.2. 탭 구성 및 상세 모듈
- **수집 데몬 탭 ([collector.js](file:///home/simon/ATS/frontend/collector.js))**: 수집 대기열(RCV/DB/Candle) 큐 사용률 텔레메트리, 거래소별 수집 카드 동적 시각화, 글로벌 수집 설정 모니터링, 거래소 제어 ZMQ ACK 처리를 수행합니다.
- **전략 데몬 탭 ([strategy-daemon.js](file:///home/simon/ATS/frontend/strategy-daemon.js))**: 전략 실행 데몬의 활성 전략 현황, 기동 파라미터, 그리고 세부 진단 데이터를 모니터링합니다.
- **평가 데몬 탭 ([evaluation-daemon.js](file:///home/simon/ATS/frontend/evaluation-daemon.js))**: 실시간 평가 파이프라인의 작업 처리율, 누적 매수/매도 평가 연산 카운트 및 에러 카드를 제공합니다.
- **클린업 데몬 탭 ([cleanup.js](file:///home/simon/ATS/frontend/cleanup.js))**: 데이터베이스 최적화 관리 뷰로, 틱 예상 삭제량 실시간 사전 쿼리, command_id 기반 비동기 대기 및 타임아웃, 중복 실행 방지(Mutex), 그리고 최근 10건의 감사 이력 타임라인을 렌더링합니다.

### 7.3. 공통 UI 조율 및 캐시 제어 메커니즘
1. **공통 헤더 구조 단일화**:
   - 4개 데몬 화면의 낭비되는 상단 공간을 제거하고, 공통 메트릭(PID, 기동 시각, 하트비트, CPU 사용량, 메모리 RSS, 활성 상태 뱃지, Stale 지연 경고)을 하나의 고정 헤더 UI(`DaemonMonitoringView.updateSharedHeader`)로 통일하여 시각적 직관성을 향상했습니다.
2. **캐시 기반 격리 및 렌더링 조율 (Race Condition 방지)**:
   - 각 데몬이 백그라운드 탭으로 전환되어 화면에 표시되지 않는 상태에서도 웹소켓(ZMQ) 실시간 틱은 지속적으로 수신됩니다.
   - 이때 백그라운드 데몬의 상태 변화가 현재 표시 중인 활성 탭 데몬의 상단 공통 헤더 정보를 덮어쓰거나 오염시키는 것을 방지하기 위해, `DaemonMonitoringView`는 `daemonDataCache` 오브젝트에 4개 데몬의 정보를 각각 격리 캐싱합니다.
   - `updateSharedHeader`가 호출되면 유입된 데이터는 해당 데몬의 캐시 슬롯에만 반영되며, **오직 업데이트된 데몬이 현재 활성화된 탭과 일치할 때에만** 화면 헤더 영역을 갱신 렌더링합니다.
3. **통합 재기동 제어**:
   - 공통 헤더 영역의 "데몬 재기동" 버튼 클릭 시, 현재 활성화된(선택된) 탭을 감지하여 각 서브 뷰의 재기동 핸들러(`restartCollectorDaemon`, `restartStrategyDaemon`, `restartEvaluationDaemon`, `restartCleanupDaemon`)를 분기 실행합니다.
   - 비동기 재기동 과정에서 이전 PID/기동시각과의 대조 검증을 수행하여 물리적 재생성을 확인한 후 UI를 정상 복구합니다.

