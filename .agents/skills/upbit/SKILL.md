---
name: upbit
description: >
  업비트 거래소 연동 스킬입니다. 로컬 가상환경 CLI를 활용하여 계좌 잔고를 조회하고,
  마켓 심볼을 수집하며, 지정가 매수/매도 주문을 안전하게 실행합니다.
  사용자가 업비트 자산 조회나 매매 명령을 요청하거나 /upbit 명령을 실행할 때 사용됩니다.
---

# 🪙 Upbit Agent Skill (가상환경 격리형 공식 CLI)

## 📌 개요
본 스킬은 에이전트(Antigravity)가 프로젝트 로컬 파이썬 가상환경에 봉인 설치된 `upbit` CLI 실행 파일을 활용하여, 업비트 거래소의 실시간 자산 상태를 조회하고 모의/실제 매매 주문을 완벽하고 무결하게 제어할 수 있도록 돕는 에이전트 전용 공식 스킬 패키지입니다.

---

## 🛠️ 사용 가능한 명령 도구 목록
에이전트는 프로젝트 루트 경로에서 `./venv/bin/upbit` 바이너리를 셸 명령어로 직접 구동하여 작업을 수행합니다.

### 1. 계좌 자산 및 잔고 조회 (Account Balance)
* **명령**: `./venv/bin/upbit account balance`
* **설명**: 사용자의 보유 계좌 내 현금(KRW) 잔액과 각 가상자산 보유량, 매수평균가를 출력합니다.

### 2. 마켓 목록 조회 (Market All)
* **명령**: `./venv/bin/upbit market all`
* **설명**: 업비트에 상장된 전체 마켓 코드(예: `KRW-BTC`) 정보를 수집합니다.

### 3. 지정가 매수 주문 (Limit Buy Order)
* **명령**: `./venv/bin/upbit order buy <symbol> <price> <qty>`
* **예시**: `./venv/bin/upbit order buy KRW-BTC 50000000 0.001`
* **설명**: 지정한 종목에 대해 입력된 가격과 수량으로 안전하게 매수 주문을 집행합니다.

### 4. 지정가 매도 주문 (Limit Sell Order)
* **명령**: `./venv/bin/upbit order sell <symbol> <price> <qty>`
* **예시**: `./venv/bin/upbit order sell KRW-BTC 60000000 0.001`
* **설명**: 보유한 자산에 대해 매도 주문을 즉각 생성합니다.

### 5. 주문 취소 (Order Cancel)
* **명령**: `./venv/bin/upbit order cancel <order_uuid>`
* **설명**: 미체결된 특정 주문을 UUID를 통해 즉시 취소합니다.

---

## 🔐 보안 및 인증
- 본 스킬은 프로젝트 루트의 `.env` 파일에 기록된 `UPBIT_ACCESS_KEY` 및 `UPBIT_SECRET_KEY`를 파이썬의 표준 `urllib` 엔진이 자동으로 참조하여 서명(JWT Token)을 생성해 통과합니다.
- 키 노출을 막기 위해 소스코드 내부에 절대 API 키를 하드코딩해서는 안 됩니다.
