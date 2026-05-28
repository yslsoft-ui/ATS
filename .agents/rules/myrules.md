---
trigger: always_on
description: Behavioral guidelines for the Antigravity assistant to ensure high-quality, simple, and surgical code changes.
---


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

## 8. 한글 사용
- 모든 사고 과정(Thinking), Artifact, 답변, 설명 및 코드 내 주석은 **한국어**를 기본으로 사용한다.