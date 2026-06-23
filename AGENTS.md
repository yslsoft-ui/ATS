# AGENTS.md

이 파일은 이 리포지토리에서 작동하는 AI 에이전트(Antigravity, Claude 등)에 대한 설정입니다.

## Agent skills

### Issue tracker

이슈는 `scratch/<feature>/issue.md` 로컬 마크다운 파일로 관리합니다. See `docs/agents/issue-tracker.md`.

### Triage labels

라벨: `검토필요`, `정보필요`, `에이전트준비`, `사람필요`, `처리안함`. See `docs/agents/triage-labels.md`.

### Domain docs

단일 컨텍스트(Single-context) 구조 — 루트 `CONTEXT.md` + `docs/adr/`. See `docs/agents/domain.md`.

## KIS API 필수 선행 조회 규칙 (Mandatory Reference Lookup)

**한국투자증권(KIS) API와 관련된 모든 작업(코드 수정, 디버깅, 신규 기능 추가)을 시작하기 전에 반드시 아래 공식 매뉴얼 파일을 먼저 확인해야 합니다.**

### 강제 Workflow 실행

| 상황 | 실행할 Workflow |
|---|---|
| 코드 수정·신규 기능 추가 전 | `/kis-preflight` — 매뉴얼 확인 → 코드 교차 검증 |
| 오류·경고·데이터 이상 발생 시 | `/kis-debug` — 로그 분석 → 가설 검증 |

> **에이전트는 KIS 관련 파일을 수정하기 전에 반드시 `/kis-preflight` workflow를 실행해야 한다.**
> **KIS 관련 오류가 발생했을 때는 반드시 `/kis-debug` workflow를 실행해야 한다.**

### 공식 참조 문서

| 파일 | 용도 |
|---|---|
| `docs/manual/kis/kis_api_list.md` | KIS OpenAPI 전체 목록 — TR 코드, 엔드포인트, REST/WebSocket 구분 |
| `docs/manual/kis/kis_domestic_stock.md` | 국내주식 상세 API 스펙 — 요청/응답 필드, 메시지 포맷, 필드 인덱스 |

### 위반 금지 사항

- `/kis-preflight` 실행 없이 KIS 관련 파일을 **직접 수정하지 않는다**.
- 메시지 포맷(`H0STCNT0` 등), 필드 인덱스(`data_parts[N]`), 구독 포맷 등은 **매뉴얼에서 확인한 후** 코드에 반영한다.
- 공식 문서 없이 KIS API 응답 구조를 **추측하지 않는다**.

> **이유**: KIS API는 필드 순서, 부호 처리, 세션 유지 방식 등이 타 거래소와 상이하며, 사전 확인 없이 수정 시 데이터 오염·연결 오류를 유발할 수 있음.

---

## Documentation Synchronization Protocol

- **원칙**: 시스템 설계나 코드 구조(예: DB 스키마, API 엔드포인트, 프론트엔드 모듈 및 라우팅 구조) 변경을 수반하는 작업을 수행하는 경우, 관련 문서(`docs/database.md`, `docs/api.md`, `docs/frontend.md`, `docs/architecture.md` 등)를 반드시 동기화하여 수정합니다.
- **루트 README.md 동기화**: 신규 문서가 추가되거나 기존 문서의 구조/위치가 변경될 경우, 루트 폴더의 [README.md](file:///home/simon/ATS/README.md)의 문서 맵(Index)도 즉각적으로 업데이트해야 합니다.
- **작업 완료 정의(DOD)**: 에이전트는 코드 수정 후 관련 문서들의 정합성 갱신을 완료하기 전까지는 작업을 완료할 수 없습니다.

## Fail-Fast 구현 및 불필요한 폴백(Fallback) 지양 규칙

- **원칙**: 설정 파일 파싱, API 매개변수 유효성 검사 등에서 오류가 발생한 경우, 조용히 기본값으로 대체(Silent Fallback)하는 로직을 절대 지양하고 즉시 명시적인 오류(`ValueError`, `TypeError` 등)를 던져 시스템을 정지시키거나 에러를 전파하는 **Fail-Fast 방식**으로 구현합니다.
- **임의 폴백 구현 금지 (Fail-Fast, Fail-Stop)**: 프로젝트 전반에 걸쳐 사용자의 명시적인 폴백 요청이 있지 않은 이상, **임의로 폴백 코드(예: 기본값으로 대체, 오류 시 디폴트 파라미터 적용 등)를 구현하는 것을 엄격히 금지**합니다. 파라미터 유효성 검증 실패, 설정 누락, API 실패 등 비정상 상황이 감지되면 즉시 에러를 전파하고 시스템을 정지시켜 개발자가 즉각 인지할 수 있도록(Fail-Fast, Fail-Stop) 개발해야 합니다.
- **이유**: 잘못된 설정이나 인수가 조용히 기본값으로 덮어써지면 오동작을 개발자가 인지하기 어렵고, 실거래 시스템 특성상 자금 유실이나 엉뚱한 거래 체결 등 심각한 사고로 이어질 수 있기 때문입니다.

## 5. Environment & Tools

**모든 터미널 명령어는 반드시 가상환경(Venv)에서 실행한다.**

- Python 관련 도구(pip, pytest, graphify 등) 사용 시 반드시 `./venv/bin/` 경로의 실행 파일을 사용하거나 `source venv/bin/activate`를 선행한다.
- 일반 환경에서 명령어를 실행하여 발생하는 'command not found' 또는 패키지 누락 오류를 방지하기 위해 실행 전 환경을 항상 확인한다.

## 6. Global Skills & Persistence

**에이전트는 시작 시 사용자 홈 디렉토리의 글로벌 스킬을 확인한다.**

- 프로젝트 로컬 스킬 외에도 `~/.agents/skills/` 경로에 설치된 글로벌 에이전트 스킬들이 있는지 확인하고, 해당 스킬들의 지침을 우선적으로 따른다.
- 새로운 세션이 시작될 때 이 경로를 탐색하여 사용 가능한 도구(예: graphify 등)를 즉시 파악한다.

## 7. 절대 승인전에 작업을 개시하지 않는다.
- 명확환 승인과 작업개시 요청에 의해서만 코드를 수정한다

## 8. 언어 설정 및 한글 사용 규칙
- 사용자와의 최종 답변, 설명, 생성하는 사고 과정(Thinking), Artifact, 마크다운 파일, 그리고 코드 내 주석은 무조건 **한국어**를 기본으로 작성하여 검토 편의성을 보장한다.


