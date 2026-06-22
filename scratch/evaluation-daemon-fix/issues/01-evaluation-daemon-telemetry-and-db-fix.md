Status: 에이전트준비

# 이슈: 평가 데몬 모니터링 텔레메트리 에러 및 DB 데이터 미노출 복구

## 1. 요구사항 및 문제 현상
- 데몬 모니터링 "평가 데몬" 탭에서 데이터가 Stale 상태로 유지되며 수치가 갱신되지 않음.
- ZMQ 리스너 내부 로컬 `import time` 선언에 의한 `UnboundLocalError` 발생.
- KIS 실거래 동기화 중 `datetime.datetime.timedelta` 에 의한 `AttributeError` 발생.
- `proposal_evaluations` 테이블이 데이터가 0건으로 비어 있고 외래키가 존재하지 않는 구 테이블(`strategy_proposals_old`)을 가리키는 설계적 결함 확인.

## 2. 작업 내역 및 해결 방식
- [x] **ZMQ 텔레메트리 에러 수정**: `src/server/main.py` 함수 스코프 내 로컬 `import time` 구문 제거 및 전역 모듈 호출로 수정 완료.
- [x] **KIS 주문 동기화 에러 수정**: `src/server/routers/portfolio.py`의 `datetime.datetime.timedelta` -> `timedelta` 호출 방식 교정 완료.
- [x] **DB 스키마 정정 및 데이터 백필**: 
  - `data/backtest.db.bak` 백업본 생성 완료.
  - 마이그레이션 스크립트(`scratch/migrate_evaluations.py`)를 개발 및 실행하여, `proposal_evaluations` 외래키 참조를 `strategy_proposals(id)`로 정정 및 재생성 완료.
  - 기존 7개 제안들에 매칭되는 21개 PENDING 평가 데이터를 삽입하여 즉각적인 사후 평가(`COMPLETED`) 실행 검증 완료.
- [x] **서버/데몬 프로세스 재기동 및 로그 검증**:
  - uvicorn 및 4대 데몬 재기동 (`./run.sh restart --reload`) 완료.
  - `logs/ats.log` 모니터링 결과 에러 로그가 소멸하고 안정적으로 데이터 백필 및 전략 엔진 워밍업이 돌아가는 것을 최종 확인.

## 3. Comments
- **에이전트 조치 내용 (2026-06-23)**:
  - ZMQ 리스너 및 KIS 주문 동기화 중 발생하던 치명적 오류들을 발견하여 해결했습니다.
  - 추가적으로 evaluations 테이블의 데이터 부재 현상 및 외래키 정합성 문제를 스키마 교정 및 백필 마이그레이션 스크립트 실행으로 완벽히 해결했습니다.
  - 최종 21건의 데이터가 모두 정상 갱신되어 데몬 모니터링 메뉴 복구 작업을 마쳤습니다.
