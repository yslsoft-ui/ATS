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
  chart.js            portfolio-view.js       ranking.js / alerts.js ...
(Lightweight Charts)  (자산 분배 원형그래프)       (기타 대시보드 위젯)
```

---

## 2. 핵심 프론트엔드 모듈

### 2.1. 진입점 및 제어 (Entrypoint & Routing)
- **[index.html](file:///home/simon/ATS/frontend/index.html)**: 대시보드 UI 레이아웃, 실시간 차트 및 테이블 영역 정의.
- **[app.js](file:///home/simon/ATS/frontend/app.js)**: 애플리케이션 메인 초기화 루프를 수행하고 이벤트를 연동합니다.
- **[router.js](file:///home/simon/ATS/frontend/router.js)**: `ViewRouter` 클래스를 구동하여 해시 라우팅(`#dashboard`, `#backtest`, `#settings` 등)에 의거해 알맞은 화면 섹션을 동적으로 온/오프시킵니다.

### 2.2. 데이터 레이어 (Data & State Management)
- **[store.js](file:///home/simon/ATS/frontend/store.js)**: 전역 애플리케이션의 메모리 상태 관리기입니다.
  - 관리 데이터: 활성 포트폴리오 목록, 캔들 데이터, 체결 데이터, 알림 경고 목록 등.
  - 타 모듈에서 데이터 변경 시 콜백을 실행할 수 있도록 옵저버 패턴을 일부 차용합니다.
- **[client.js](file:///home/simon/ATS/frontend/client.js)**: 백엔드 HTTP REST API와의 비동기 통신(`fetch`)을 래핑한 모듈입니다.
- **[stream.js](file:///home/simon/ATS/frontend/stream.js)**: 백엔드 `/ws` 엔드포인트와 WebSocket을 개설 및 복구 관리하며, 체결 틱(`tick`), 캔들 업데이트(`candle`) 및 시스템 경보 데이터를 실시간 수신하여 `store.js` 및 컴포넌트로 전달합니다.

### 2.3. 컴포넌트 & 뷰 레이어 (Views & Visualization)
- **[chart.js](file:///home/simon/ATS/frontend/chart.js)**: Lightweight Charts를 사용하여 Candlestick 차트를 그리며, SMA/볼린저 밴드 오버레이 및 RSI 보조 지표를 별도 서브 차트에 고속 렌더링합니다.
- **[portfolio-view.js](file:///home/simon/ATS/frontend/portfolio-view.js)**: 포트폴리오의 실물 보유 현황 및 가상 투자 운용 상태를 표와 폼으로 렌더링합니다.
- **[portfolio-chart.js](file:///home/simon/ATS/frontend/portfolio-chart.js)**: 포트폴리오 자산 비중 현황을 직관적인 원형 차트(Pie Chart)로 표현하며, 한글 종목명 매핑을 적용해 시인성을 보장합니다.
- **[portfolio-adapter.js](file:///home/simon/ATS/frontend/portfolio-adapter.js)**: 백엔드 포지션 데이터(`avg_price`, `quantity`, `symbol`)를 프론트엔드 차트 및 UI 규격에 맞게 계산 및 가공해주는 변환기 모듈입니다.
- **[backtest.js](file:///home/simon/ATS/frontend/backtest.js)**: 백테스트 설정 값 전송, 백테스트 진행 상태 표시 및 결과 성과 리포트 출력 폼을 관리합니다.
- **[settings.js](file:///home/simon/ATS/frontend/settings.js)**: 실시간 수집기(Collector) 기동/중지 스위치 제어 및 DB 디스크 정리 관리 페이지입니다.
- **[ranking.js](file:///home/simon/ATS/frontend/ranking.js)**: 수집 중인 실시간 종목들의 상승/하락률 및 거래대금 기준 랭킹 대시보드 뷰입니다.

---

## 3. 실시간 UI 갱신 시퀀스 (Real-time Flow)

1. **소켓 수신**: `stream.js`가 WebSocket을 통해 신규 `tick` 패킷을 받음.
2. **상태 업데이트**: 수신한 데이터를 `store.js` 내의 특정 배열에 누적(Push) 및 캐시 데이터 갱신.
3. **그래프 리트레이싱**: `chart.js`는 데이터 누적 이벤트를 받아 Lightweight Charts의 `setData()` 및 `update()` 메서드를 사용해 브라우저 렌더링 부하를 최소화하며 차트를 동적으로 갱신합니다.
4. **인터벌 전환**: 상단 시간 주기(Interval) 선택 시, 백엔드로부터 새로운 주기의 역사적 캔들 셋을 `client.js`로 호출하여 스토어를 전면 교체한 후 차트를 다시 로딩합니다.

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
