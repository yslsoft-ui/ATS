---
name: kis-preflight
description: KIS API 관련 작업(수정·디버깅·신규 기능) 시작 전 필수 선행 조회 절차. 공식 매뉴얼 확인 → 코드 교차 검증 → 작업 범위 확정 순서로 진행한다.
---

# Workflow: kis-preflight

KIS(한국투자증권) API 관련 작업을 시작하기 전에 반드시 이 workflow를 실행한다.

## Step 1. API 목록 확인

`docs/manual/kis/kis_api_list.md`를 열어 작업 대상 TR 코드와 엔드포인트를 찾는다.

확인 항목:
- TR 코드 (예: `H0STCNT0`, `FHKST03010200`)
- REST / WebSocket 구분
- 실서버 / 모의투자 엔드포인트 URL

## Step 2. 스펙 상세 확인

`docs/manual/kis/kis_domestic_stock.md`에서 해당 API의 세부 스펙을 확인한다.

확인 항목:
- 요청 파라미터 이름·타입·필수 여부
- 응답 필드 **순서** (인덱스 기반 파싱이므로 순서가 핵심)
- 부호 처리 방식 (예: `data_parts[3]` = 부호, `data_parts[4]` = 절대값)
- 메시지 포맷 구분자 (예: `^` 구분자)

## Step 3. 현재 코드 교차 검증

매뉴얼 스펙과 실제 구현 파일을 비교하여 불일치 항목을 목록으로 작성한다.

주요 검증 파일:
- `src/engine/collector_kis.py` — WebSocket 수신·파싱 로직
- `src/engine/market/kis_adapter.py` — DTO 매핑
- `src/engine/market/dto.py` — 필드 정의

## Step 4. 작업 범위 확정

Step 1~3 결과를 바탕으로 수정이 필요한 파일과 라인 범위를 명확히 정의한 후 작업을 시작한다.

> ⚠️ Step 1~3 완료 전까지 KIS 관련 파일을 수정하지 않는다.
