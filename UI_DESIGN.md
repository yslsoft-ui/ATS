# 트레이딩 시스템 UI/UX 설계 (UI_DESIGN.md)

이 문서는 사용자가 시스템을 제어하고 데이터를 모니터링하기 위한 웹 인터페이스 대시보드 설계를 다룹니다.

## 1. 디자인 컨셉 (Design Concept)

- **테마**: 다크 모드 (Dark Mode) 기반의 전문적인 핀테크 대시보드.
- **색상** (DESIGN.md 기준):
  - 배경: `#0E1117` (Deep Dark) / 카드·사이드바: `#262730` (Medium Dark)
  - 상승(Bull): `#FF4B4B` (Vibrant Red) / 하락(Bear): `#0072FF` (Vibrant Blue)
  - 텍스트: `#FAFAFA` (Primary), `#AFAFAF` (Secondary)
  - 액센트: `#FFA500` (Orange)
- **특징**: 실시간 데이터의 흐름을 방해하지 않는 반응형 레이아웃.

## 2. 기술 스택 (Frontend Tech Stack)

| 구분 | 기술 | 사유 |
| :--- | :--- | :--- |
| **마크업** | Vanilla HTML | 프레임워크 의존 없이 경량 구현 |
| **스타일** | Vanilla CSS (CSS Custom Properties) | 디자인 토큰 기반 일관성 유지 |
| **로직** | Vanilla JavaScript | 별도 빌드 과정 없이 즉시 실행 |
| **차트** | Plotly.js (CDN) | 캔들스틱, 다중 Y축, 실시간 업데이트 지원 |
| **호스팅** | FastAPI `StaticFiles` | `frontend/` 디렉토리를 `/`로 서빙 |

## 3. 전체 레이아웃 구조 (Layout) — `frontend/index.html`

### 3.1. 사이드바 (Sidebar) — 260px 고정 너비

- **로고**: 🚀 UPBIT TERMINAL (accent color)
- **내비게이션 메뉴** (구현 완료):
  - 📊 모니터링 (실시간 차트 & 체결 내역)
  - ⚙️ 설정 (수집기 관리, DB 관리)
- **미구현 메뉴** (향후 확장):
  - 🌐 마켓 정보 (거래소별 코인 현황)
  - 📈 백테스트 (전략 시뮬레이션 UI)
- **하단 설정 영역** (`sidebar-config`):
  - 모니터링 종목 선택 (select): BTC, ETH, XRP, SOL, DOGE
  - 캔들 간격 선택 (select): 1초, 3초, 5초, 10초, 30초, 1분
  - 지표 설정 체크박스: SMA(20), Bollinger Bands, Volume, RSI(14)
  - 표시 범위 슬라이더 (10~200 캔들)

### 3.2. 메인 콘텐츠 영역 (Main Section)

#### A. 모니터링 뷰 (`monitoring-view`) ✅ 구현 완료

1. **헤더 영역**:
   - 현재 종목 심볼 표시 (h1)
   - WebSocket 연결 상태 배지 (CONNECTED / DISCONNECTED)
   - 현재가 + 24h 변동률 메트릭 (Roboto Mono 고정폭 폰트)

2. **차트 컨테이너** (Plotly.js, 높이 500px):
   - **캔들스틱**: 상승(Red) / 하락(Blue) 색상 구분.
   - **SMA(20)**: Orange 라인 오버레이.
   - **Bollinger Bands**: 반투명 Light Blue 영역.
   - **Volume**: 하단 별도 Y축, 방향별 색상 적용.
   - **RSI(14)**: 하단 별도 Y축 (0~100 범위), Magenta 라인.
   - **동적 레이아웃**: 지표 토글에 따라 차트 도메인 자동 재배치.
   - **시간 고정 윈도우**: 현재 시각 기준 N개 캔들을 고정 표시 (빈 슬롯 포함).
   - **마우스 휠 줌**: 차트 위에서 휠 스크롤로 표시 범위 확대/축소 (10~500).

3. **체결 내역 테이블** (PUSH 방식):
   - 컬럼: 시간, 가격, 수량, 구분(BID/ASK)
   - 최근 10건 유지, 실시간 prepend.
   - 가격 셀에 Bull/Bear 클래스로 색상 적용.

#### B. 설정 뷰 (`settings-view`) ✅ 구현 완료

1. **데이터 수집기 관리 카드**:
   - 실행 상태 표시 (실행 중: 녹색 / 중단됨: 빨간색).
   - 시작/중단 토글 버튼 (API 연동: `/collector/start`, `/collector/stop`).
   - 2초 주기로 상태 자동 갱신.

2. **데이터베이스 관리 카드**:
   - 날짜 선택 (date input).
   - 선택 날짜 이전 데이터 영구 삭제 버튼 (확인 다이얼로그 포함).
   - API 연동: `/data/cleanup?date=YYYY-MM-DD`.

#### C. 마켓 정보 페이지 (Market Overview) ⚠️ 미구현

1. **거래소 탭**: [Upbit] [Bithumb] [Binance] 등 선택 가능한 상단 탭.
2. **마켓 필터**: [KRW] [BTC] [USDT] 등 마켓별 그룹핑 필터.
3. **코인 리스트 테이블**:
   - 컬럼: 코인 아이콘, 한글명, 영문명, 심볼, 현재가(실시간), 24시간 변동률, 거래대금.
   - 검색 기능: 코인명 또는 심볼로 실시간 검색.
4. **통계 요약**: 해당 마켓의 총 코인 개수 및 상승/하락 종목 수 요약.

#### D. 백테스트 페이지 ⚠️ 미구현 (웹 UI)

- 백엔드 API (`/api/backtest/run`)와 CLI 스크립트 (`run_backtest_sample.py`)는 구현 완료.
- 웹 UI에서의 전략 선택, 파라미터 입력, 결과 차트 시각화는 미구현.

## 4. 사용자 시나리오 (User Scenario)

1. **데이터 준비**: 설정 페이지에서 수집기를 시작하면 WebSocket으로 틱 데이터를 수신하여 DB에 저장합니다.
2. **실시간 모니터링**: 모니터링 화면에서 캔들 차트와 기술 지표를 실시간으로 확인합니다.
3. **종목 전환**: 사이드바에서 다른 종목을 선택하면 차트와 체결 내역이 즉시 전환됩니다.
4. **인터벌 변경**: 캔들 간격을 변경하면 서버에서 과거 데이터를 다시 로드합니다.
5. **검증**: 백테스트 탭(미구현)으로 이동하여 수집된 데이터를 바탕으로 전략을 실행합니다.
6. **최적화**: 결과 차트를 보며 파라미터를 수정하고 다시 리플레이하여 최적의 값을 찾습니다.

## 5. 실시간 데이터 흐름 (WebSocket)

```
[Upbit WS] → [CollectorManager] → broadcast() → [Browser WS (/ws)]
                                 → DB INSERT (50건 배치)
                                 
[Browser] → processTick(tick)
          → 캔들 생성/업데이트 (bucket 방식)
          → calculateIndicators() (SMA, BB, RSI)
          → updateMetrics() + updateTable()
          → renderChart() (Plotly.react)
```

## 6. 모바일 및 반응형 대응 (Mobile & Responsive)

- ⚠️ **현재 상태**: 고정 레이아웃 (데스크톱 전용). 반응형 미구현.
- **향후 계획**:
  - CSS 미디어 쿼리로 모바일 기기에서 사이드바 접힘 처리.
  - 작은 화면에서는 틱 리스트를 숨기고 핵심 지표 위주 표시.
  - 텔레그램(Telegram) 봇 연동으로 모바일 알림 지원.
