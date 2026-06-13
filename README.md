# Multi-Market Real-time Trading System (ATS)

이 프로젝트는 국내 주식(한국투자증권 API) 및 가상자산(Upbit API)의 실시간 체결 데이터를 수집하고, 이를 가변 시간 프레임의 OHLCV 캔들로 가공하여 기술 지표(SMA, RSI, Bollinger Bands, MACD) 연산 및 자동매매 시뮬레이션(Backtest 및 실시간 Paper Trading)을 수행하는 통합 트레이딩 플랫폼입니다.

---

## 1. 주요 특징 (Key Features)

- **다중 시장(Multi-Market) 지원**: Upbit(가상자산)과 KIS(국내주식) 수집 데몬의 다중 세션 관리.
- **실시간 데이터 스트리밍**: ZeroMQ IPC 및 FastAPI WebSocket을 결합하여 지연 시간을 최소화한 실시간 가격 데이터 전송.
- **가변 타임프레임 캔들 생성**: 초 단위(1S, 3S, 5S, 10S, 30S) 및 분 단위(60S) 틱 데이터 정규화 및 캔들 자동 취합.
- **수집기 데몬 실시간 모니터링**: 수집 대기열(RCV/DB/Candle)의 사용률 수준(NORMAL/WARNING/CRITICAL) 진단, 데몬 메모리(RSS) 및 버전 정합성 감시, PID 및 기동시각 교차 검증 기반 비동기 시작/중지/자가재기동 제어가 완결된 전용 탭 UI(`🛰️ 수집기 데몬`) 지원.
- **모의투자 시뮬레이션 엔진**: 호가창 데이터 기반 슬리피지가 반영된 가상 체결 엔진(Virtual Order Executor).
- **지표 오버레이 차트**: Vanilla JS 및 Lightweight Charts를 사용하여 프레임워크 없는 초경량 차트 및 자산 비중 원형 그래프 렌더링.

---

## 2. 퀵 스타트 (Quick Start)

### 2.1. 개발 환경 설정
본 프로젝트는 Python 3.10+ 기반으로 작동하며, 의존성 패키지와 가상환경을 사용합니다.

```bash
# 1. 가상환경 생성 및 활성화
python3 -m venv venv
source venv/bin/activate

# 2. 필수 의존성 패키지 설치
pip install -r requirements.txt
```

### 2.2. 실행 구성
실시간 수집과 시뮬레이션을 동시 서빙하기 위해 통합 FastAPI 웹서버를 작동합니다.

```bash
# 3. 데이터베이스 초기화 및 uvicorn 웹 서버 실행
./run.sh
```
서버 기동 완료 후 브라우저에서 `http://localhost:8000`으로 접속하여 대시보드 및 백테스트 환경을 제어할 수 있습니다.

---

## 3. 프로젝트 전체 문서 지도 (Documentation Map)

프로젝트 구조와 아키텍처 흐름을 한눈에 파악할 수 있도록 도메인 용어집, 설계 명세서 및 연동 가이드를 정리한 인덱스 테이블입니다. 각 문서는 통합 관리를 위해 `docs/` 디렉토리에 분류 및 저장되어 있습니다.

| 문서명 | 파일 위치 | 주요 내용 및 역할 |
| :--- | :--- | :--- |
| **도메인 용어집 (Domain Glossary)** | [CONTEXT.md](file:///home/simon/ATS/CONTEXT.md) | 프로젝트 내 주요 용어 정의 및 핵심 비즈니스 개념 관계도 |
| **시스템 아키텍처 명세** | [docs/architecture.md](file:///home/simon/ATS/docs/architecture.md) | ZMQ IPC, 웹소켓 브로드캐스트 거시적(Macro)/미시적(Micro) 다이어그램 |
| **데이터베이스 명세** | [docs/database.md](file:///home/simon/ATS/docs/database.md) | SQLite 3 테이블 스키마, 기본값, 외래키 제약조건, 복합 인덱스 및 Compact 정책 |
| **API 및 웹소켓 프로토콜 명세** | [docs/api.md](file:///home/simon/ATS/docs/api.md) | 백엔드 FastAPI REST API 엔드포인트 및 실시간 구독 웹소켓 JSON 포맷 |
| **데몬 시스템 구성 명세** | [docs/daemons.md](file:///home/simon/ATS/docs/daemons.md) | 수집기 및 전략 엔진 데몬의 구동 흐름, ZMQ IPC 제어 프로토콜 및 Graceful Shutdown |
| **프론트엔드 아키텍처 명세** | [docs/frontend.md](file:///home/simon/ATS/docs/frontend.md) | Vanilla JS 기반 라우팅(router), 상태(store), 실시간 Lightweight Charts 차트 구조 |
| **백테스트 엔진 설계서** | [docs/backtest-engine-design.md](file:///home/simon/ATS/docs/backtest-engine-design.md) | 역사적 데이터를 활용한 과거 수익률 테스트 및 리플레이 엔진 연산 로직 |
| **수집기 데몬 설계서** | [docs/collector-design.md](file:///home/simon/ATS/docs/collector-design.md) | 다중 WebSocket 세션 유지보수 및 50건 배치 DB 커밋 쓰기 로직 |
| **UI/UX 디자인 가이드** | [docs/ui-design.md](file:///home/simon/ATS/docs/ui-design.md) | 전문가용 터미널 다크 테마 색상 체계, 차트 위젯 표준 디자인 시스템 |
| **한국투자증권(KIS) 연동 규격** | [docs/manual/kis/kis_api_list.md](file:///home/simon/ATS/docs/manual/kis/kis_api_list.md) | 국내외 주식, 선물옵션, 채권 주문 및 실시간 웹소켓 TR_ID 명세 |
| **아키텍처 결정 기록 (ADR)** | [docs/adr/](file:///home/simon/ATS/docs/adr/) | 포트폴리오 매니저 분리 및 실시간 국소 복기·카운터팩츄얼 대조 기반 챔피언 전략 순환 매매 비전 선언(ADR 0008), 클린업 데몬 동시성 제어 및 ACK 프로토콜(ADR 0009) 등 주요 결정 이력 |
| **에이전트 지침서** | [AGENTS.md](file:///home/simon/ATS/AGENTS.md) | 개발 AI 에이전트의 작업 준수 가이드 및 문서화 동기화 필수 규정 |

> [!IMPORTANT]
> **문서화 동기화 프로토콜(AGENTS.md)**에 따라, DB 스키마 수정/API 엔드포인트 변경/폴더 구조 변경 시에는 연관 문서 및 이 루트 `README.md` 파일도 반드시 동기화하여 수정해야 합니다.
