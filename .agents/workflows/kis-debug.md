---
name: kis-debug
description: KIS WebSocket/REST API 오류 및 데이터 이상 발생 시 체계적인 진단 절차. 증상 파악 → 로그 분석 → 매뉴얼 대조 → 가설 검증 순서로 진행한다.
---

# Workflow: kis-debug

KIS API 관련 버그·경고·데이터 이상이 발생했을 때 이 workflow를 실행한다.

> **알려진 이슈**: KIS 서버는 약 10초마다 아래 JSON으로 세션 유지를 확인한다. 응답하지 않으면 ~110초 후 서버가 연결을 강제 종료한다.
> ```json
> {"header":{"tr_id":"PINGPONG","datetime":"YYYYMMDDHHMMSS"}}
> ```
> 수신 즉시 동일 JSON을 echo 해야 하며, `collector_kis.py`의 `_parse_message`에서 처리한다.

## Step 1. 증상 및 로그 수집

```bash
# 최근 KIS 관련 경고·에러 추출
grep -E "\[KIS\]|\[WARNING\]|\[ERROR\]" logs/ats.log | tail -50
```

확인 항목:
- 오류 메시지 전문
- 발생 시각 및 주기 (간헐적 vs 지속적)
- 직전 정상 동작 시점

## Step 2. 매뉴얼 대조

`/kis-preflight` 와 동일한 방식으로 관련 TR 코드의 스펙을 확인한다.

- `docs/manual/kis/kis_api_list.md` — 해당 API 식별
- `docs/manual/kis/kis_domestic_stock.md` — 필드·포맷 스펙 대조

현재 코드의 파싱 로직(`_parse_message`, `_subscribe` 등)과 매뉴얼의 응답 구조를 1:1 비교한다.

## Step 3. 범위 좁히기

의심 지점을 한 가지로 좁힌다. 가설 형식으로 작성:

> "data_parts[N]의 인덱스가 매뉴얼과 다르게 구현되어 있어 부호가 반전된다"

## Step 4. 최소 재현

의심 코드를 격리하여 로그나 테스트로 가설을 검증한다.

```bash
# 로그 레벨 임시 상향 후 수집기 재시작
grep "H0STCNT0" logs/ats.log | head -20
```

## Step 5. 수정 및 검증

수정 후 동일 증상이 재발하지 않는지 최소 1 사이클(5분 이상) 모니터링한다.

```bash
tail -f logs/ats.log | grep "\[KIS\]"
```

> ⚠️ 가설 없이 코드를 수정하지 않는다. 반드시 Step 3 가설 → Step 4 검증 순서를 지킨다.
