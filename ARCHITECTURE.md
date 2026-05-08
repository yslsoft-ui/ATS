# Upbit 실시간 체결 데이터 기반 백테스트 시스템 아키텍처

이 문서는 Upbit WebSocket 체결 정보를 활용하여 데이터를 수집하고, 이를 바탕으로 웹 기반 백테스트 환경을 구축하기 위한 설계 지침입니다.

## 1. 시스템 개요 (System Overview)

본 시스템은 실시간 시장 데이터를 수집하여 저장하고, 사용자가 웹 인터페이스를 통해 다양한 매매 전략을 테스트하고 결과를 시각화할 수 있도록 지원합니다.

## 2. 주요 컴포넌트 (Core Components)

### 2.1. 데이터 콜렉터 및 거래소 어댑터 (Collector & Adapters)

- **거래소 어댑터 (Exchange Adapters)**: Upbit, Bithumb, Binance 등 각 거래소의 서로 다른 API 규격을 통일된 내부 데이터 형식으로 변환합니다.
- **데이터 콜렉터 (Data Collector)**: 설정된 여러 거래소의 어댑터를 동시에 관리하며 실시간 데이터를 수신합니다.
- **기능**:
  - 다중 WebSocket 세션 유지 및 관리.
  - 거래소별 데이터 규격 표준화 (Normalize).
  - 영속성 저장소에 거래소 정보를 포함하여 저장.

### 2.2. 시뮬레이션 및 리플레이 엔진 (Simulation & Replay Engine)

- **역할**: 저장된 과거 데이터를 실시간 상황처럼 재현(Replay)하여 로직을 검증합니다.
- **핵심 원칙 (Logic Consistency)**:
  - 실시간 판단 로직과 재연산 로직을 **동일한 인터페이스(Logic Handler)**로 공유합니다.
  - **가변 타임프레임 지원**: 1초, 3초, 5초, 10초, 30초, 60초 등 사용자가 선택한 간격에 따라 틱 데이터를 OHLC 캔들로 동시 변환하여 제공합니다.
  - 데이터 소스가 WebSocket이든 DB이든 관계없이 로직은 동일하게 동작하여 백테스트와 실전의 괴리를 최소화합니다.
- **기능**:
  - 과거 틱 데이터의 순차적 리플레이.
  - 전략 매개변수 최적화 및 결과 리포트 생성.
- **구현된 전략**: RSI 역추세 전략, MACD 골든크로스/데드크로스 전략.

### 2.3. 웹 백엔드 (Web Backend - FastAPI)

- **역할**: 프론트엔드와 시스템 내부 로직 간의 인터페이스 역할을 수행합니다.
- **서버 구조 (이중 진입점)**:
  - `src/server/main.py` (**통합 서버, 메인**): FastAPI API + 프론트엔드 정적 파일 호스팅 + WebSocket 실시간 브로드캐스트를 동시 담당. `python -m uvicorn src.server.main:app --reload`로 실행.
  - `src/api/main.py` (REST API 전용): CORS 설정 포함, 백테스트 엔드포인트 등 API 전용 서버. 별도 실행 가능.
- **기능**:
  - 실시간 WebSocket 브로드캐스트 (`/ws` 엔드포인트).
  - 수집기(Collector) 원격 시작/중지 API (`/collector/start`, `/collector/stop`).
  - 캔들 데이터 + 기술 지표 조회 API (`/candles`).
  - 체결 내역 조회 API (`/trades`).
  - DB 데이터 정리 API (`/data/cleanup`).
  - 백테스트 실행 API (`/api/backtest/run`).
  - **안정화**: 서버 종료 시 `@app.on_event("shutdown")`으로 수집기 태스크를 정리하여 유령 프로세스 방지.
  - **성능 최적화**: DB 커밋을 50건 단위 배치 처리하여 초당 수백 건 유입 시에도 서버 안정성 확보.

### 2.4. 웹 프론트엔드 (Web Frontend - Vanilla HTML/JS/CSS)

- **역할**: 사용자에게 직관적인 제어 및 시각화 화면을 제공합니다.
- **기술 구현**: React 프레임워크 없이 순수 HTML/JS/CSS로 구현하며, FastAPI의 `StaticFiles`를 통해 서빙됩니다.
- **기능**:
  - 실시간 캔들스틱 차트 (Plotly.js) 및 기술 지표(SMA, BB, RSI) 오버레이.
  - 다중 인터벌 선택 (1S, 3S, 5S, 10S, 30S, 60S).
  - 다중 종목 전환 (BTC, ETH, XRP, SOL, DOGE).
  - 체결 내역 실시간 스트리밍 테이블.
  - 설정 페이지: 수집기 제어, DB 관리.
  - 마우스 휠 줌(표시 범위 조절).

## 3. 데이터 흐름 (Data Flow)

1. **수집**: `Upbit WebSocket` → `CollectorManager (aiohttp)` → `Database (SQLite)` + `WebSocket Broadcast`
2. **실시간 모니터링**: `Browser WebSocket` ← `Server Broadcast` → `Plotly.js 렌더링`
3. **분석**: `Web Frontend` → `Backend API` → `Backtest Engine` (DB 데이터 로드)
4. **표시**: `Backtest Engine` → `Backend API` → `Web Frontend (Visualization)`

## 4. 프로젝트 디렉토리 구조

```
TEST/
├── src/
│   ├── server/main.py         # 통합 서버 (메인 진입점)
│   ├── api/main.py            # REST API 전용 서버
│   ├── collector/upbit_ws.py  # 독립 수집기 (Queue 기반)
│   ├── database/
│   │   ├── schema.py          # DB 스키마 초기화 (trades + orderbooks)
│   │   └── db_writer.py       # 배치 기반 DB Writer
│   ├── engine/
│   │   ├── candles.py         # 캔들 생성기 (CandleGenerator)
│   │   ├── indicators.py      # 기술 지표 계산기 (SMA, RSI, BB, MACD)
│   │   ├── matching.py        # 호가창 기반 체결 엔진 (VWAP)
│   │   ├── strategy.py        # 전략 (RSI, MACD)
│   │   └── backtest.py        # 백테스트 엔진
│   └── utils/
│       └── visualizer.py      # 백테스트 결과 차트 시각화
├── frontend/
│   ├── index.html             # 메인 HTML
│   ├── app.js                 # 프론트엔드 로직
│   └── style.css              # 스타일시트
├── data/
│   └── backtest.db            # SQLite 데이터베이스
├── run_backtest_sample.py     # 백테스트 샘플 실행 스크립트
├── ARCHITECTURE.md            # (본 문서)
├── BACKTEST_ENGINE_DESIGN.md
├── COLLECTOR_DESIGN.md
├── UI_DESIGN.md
└── DESIGN.md                  # 디자인 가이드라인 (색상, 폰트 등)
```

## 5. 추천 기술 스택 (Tech Stack)

| 구분 | 기술 | 사유 |
| :--- | :--- | :--- |
| **언어** | Python 3.10+ | 데이터 분석 및 비동기 소켓 처리 최적화 |
| **백엔드 프레임워크** | FastAPI | 빠르고 현대적인 비동기 API 서버 구축 |
| **프론트엔드** | Vanilla HTML/JS/CSS + Plotly.js | 프레임워크 의존 없이 경량 실시간 차트 구현 |
| **데이터베이스** | SQLite + aiosqlite | 로컬 환경에서의 빠른 개발 및 가벼운 데이터 저장 |
| **비동기 라이브러리** | `aiohttp` (통합 서버), `websockets` (독립 수집기) | Upbit API와의 효율적인 통신 |
| **데이터 분석** | pandas, numpy | 기술 지표 계산 및 백테스트 데이터 처리 |

## 6. 단계별 구현 로드맵 (Roadmap)

- **1단계** ✅: Upbit WebSocket 수집기 구현 및 DB 저장 로직 검증.
- **2단계** ✅: 기본 백테스트 엔진(틱 데이터 처리) 핵심 로직 개발.
- **3단계** ✅: FastAPI를 통한 기본 API 서버 및 실시간 데이터 연동.
- **4단계** ✅: Plotly.js 기반 실시간 대시보드 및 기술 지표 차트 구현.
  - **차트 위젯**: 실시간 가격 차트 및 이동평균선(SMA), 볼린저 밴드(BB), RSI 등 기술적 지표 표시.
    - **인터벌 셀렉터**: 1S, 3S, 5S, 10S, 30S, 60S 등 캔들 시간 단위 즉시 전환 기능.
  - **설정 페이지**: 수집기 시작/중지, DB 데이터 정리 UI.
- **5단계**: 전략 최적화 및 실시간 시뮬레이션(Paper Trading) 기능 확장.
  - 성과 지표 고도화 (MDD, 승률, 손익비).
  - 마켓 정보 페이지 및 백테스트 UI 구현.

## 7. 실행 방법

```bash
# 통합 서버 실행 (프론트엔드 + API + WebSocket)
python -m uvicorn src.server.main:app --reload

# 접속
http://localhost:8000
```
