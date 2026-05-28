# API 상세 명세 (Web API & WebSocket Protocol)

이 문서는 통합 트레이딩 시스템(ATS)의 백엔드(FastAPI)가 클라이언트(프론트엔드)에 노출하는 HTTP REST API 명세 및 실시간 통신을 위한 WebSocket 프로토콜 스펙을 기술합니다.

- **기본 포트**: `http://localhost:8000`
- **구현 라우터 디렉토리**: [routers](file:///home/simon/ATS/src/server/routers)

---

## 1. REST API 명세 (HTTP REST Endpoints)

### 1.1. 수집기 제어 (Data Collector)
실시간 시세 수집기(Collector) 데몬들의 상태를 모니터링하고 원격 제어합니다.

- **`GET /collector/status`**
  - **설명**: 수집기들의 실시간 구동 여부와 오류 상태를 조회합니다.
  - **응답 (JSON)**:
    ```json
    {
      "upbit": { "is_running": true, "error": null },
      "kis": { "is_running": false, "error": "Unauthorized API Key" }
    }
    ```

- **`POST /collector/start/{exchange}`**
  - **설명**: 특정 거래소(`upbit`, `kis` 등)의 수집기 세션을 시작합니다.
  - **응답 (JSON)**: `{"status": "ok", "message": "upbit collector start command sent."}`

- **`POST /collector/stop/{exchange}`**
  - **설명**: 특정 거래소의 수집기 세션을 중단합니다.
  - **응답 (JSON)**: `{"status": "ok", "message": "upbit collector stop command sent."}`

- **`POST /collector/start`**
  - **설명**: 전체 거래소의 수집기 세션을 일괄 시작합니다.

- **`POST /collector/stop`**
  - **설명**: 전체 거래소의 수집기 세션을 일괄 중단합니다.

---

### 1.2. 마켓 데이터 조회 (Market & Symbols)
자산 종목 조회, 실시간/역사적 캔들 정보 및 KIS 순위 데이터를 제공합니다.

- **`GET /market`**
  - **설명**: 감시 대상 마켓의 전체 종목 실시간 가격 변동 현황(한글명, 현재가, 변동률, 변동액, 거래대금)을 조회합니다.
  - **응답 (JSON)**:
    ```json
    {
      "upbit:BTC": { "price": 98550000.0, "change_rate": 0.05, "volume_power": 112.5, "korean_name": "비트코인" }
    }
    ```

- **`GET /symbols`**
  - **설명**: 가용한 전체 종목 목록(설정 내 고정 종목 및 DB 수집 이력이 존재해 활성화된 종목)을 반환합니다.
  - **응답 (JSON)**:
    ```json
    [
      { "exchange": "upbit", "symbol": "BTC", "name": "비트코인" }
    ]
    ```

- **`GET /candles?exchange={exchange_id}&symbol={symbol}&interval={seconds}&limit={count}&start_ts={ms}&end_ts={ms}`**
  - **설명**: 특정 종목의 최근 OHLCV 캔들스틱 목록을 반환합니다. (지표 계산용 이전 캔들 반환 지원)
  - **응답 (JSON)**:
    ```json
    [
      {
        "timestamp": 1716870000000,
        "open": 98450000.0,
        "high": 98600000.0,
        "low": 98300000.0,
        "close": 98550000.0,
        "volume": 12.34
      }
    ]
    ```

- **`GET /restored-candles?exchange={exchange_id}&symbol={symbol}&limit_minutes={min}`**
  - **설명**: DB 수집 누락 시점에 틱 체결 이력을 기반으로 로컬에서 복구 및 임시 재생성된 캔들 목록을 조회합니다.

- **`GET /market/ranking/types`**
  - **설명**: KIS OpenAPI가 지원하는 22종 순위 분석 항목(시가총액 상위, 배당률 상위, 거래량 급증 등)의 TR_ID와 설명 목록을 반환합니다.

- **`GET /market/ranking/fetch?tr_id={TR_ID}`**
  - **설명**: 지정된 TR_ID에 맞춰 한국투자증권 실시간 순위 조회 API를 호출한 후 가공된 상위 30개 종목을 반환합니다.

- **`POST /market/symbols/kis/toggle`**
  - **설명**: KIS 국내 주식 등 수집 대상 종목을 동적으로 활성화(On)/비활성화(Off) 처리합니다.
  - **요청 Body (JSON)**: `{"symbol": "005930", "is_active": true}`

- **`POST /market/sync-assets`**
  - **설명**: 외부 마스터 파일(예: `stock_master.json`)을 기반으로 데이터베이스 자산 목록 및 한글명 매핑 사전을 동기화합니다.

---

### 1.3. 포트폴리오 & 모의 트레이딩 (Portfolios & Simulation)
가상/실제 자산 포트폴리오를 관리하고 모의 매매 시뮬레이션 상태를 제어합니다.

- **`GET /api/portfolios`**
  - **설명**: 현재 구동되고 있는 활성 시뮬레이션 포트폴리오 정보 목록을 조회합니다.

- **`GET /api/portfolio?portfolio_id={id}`**
  - **설명**: 특정 포트폴리오의 상세 상태, 거래소별 잔고(`portfolio_exchanges`) 및 보유 종목(`positions`) 목록을 반환합니다.

- **`POST /api/portfolio/start`**
  - **설명**: 새로운 실시간 모의투자 시뮬레이션을 생성하고 동작을 시작합니다.
  - **요청 Body (JSON)**:
    ```json
    {
      "name": "BTC RSI 15M 시뮬레이션",
      "exchange_id": "upbit",
      "strategy_id": "rsi_strategy",
      "initial_cash": 5000000,
      "symbols": ["BTC"]
    }
    ```

- **`POST /api/portfolio/{portfolio_id}/end`**
  - **설명**: 실행 중인 특정 모의매매 시뮬레이션을 완전히 종료하고 최종 성과를 기록합니다.

- **`POST /api/portfolio/{portfolio_id}/panic`**
  - **설명**: 비상 긴급 상황 시 포트폴리오가 보유한 모든 포지션을 시장가로 즉시 강제 전량 매도하고 전략을 중지합니다.

- **`GET /trades?portfolio_id={id}&limit={count}`**
  - **설명**: 특정 포트폴리오에서 발주된 주문들의 최근 체결 내역을 조회합니다.

- **`GET /api/exchanges/upbit/assets`**
  - **설명**: 업비트의 실제 계좌 잔고를 API를 통해 직접 조회하고 실시간 평가가치를 반영해 평가액이 높은 자산 순서대로 정렬해 반환합니다.

---

### 1.4. 백테스트 (Backtest Execution)
역사적 과거 틱/캔들 데이터를 기반으로 트레이딩 전략 성과를 검증합니다.

- **`POST /api/backtest/run`**
  - **설명**: 틱 리플레이 기반 백테스트 작업을 요청합니다.
  - **요청 Body (JSON)**:
    ```json
    {
      "exchange": "upbit",
      "symbol": "BTC",
      "start_date": "2026-05-23T00:00:00",
      "end_date": "2026-05-25T00:00:00",
      "initial_cash": 10000000,
      "strategies": {
        "rsi_strategy": { "enabled": true, "params": { "interval": 60, "rsi_period": 14 } }
      },
      "risk_limits_enabled": true,
      "slippage_rate": 0.001
    }
    ```

- **`GET /api/backtest/history`**
  - **설명**: 이전 실행했던 백테스트 최종 성과 리포트 히스토리 목록을 반환합니다.

- **`DELETE /api/backtest/history/{portfolio_id}`**
  - **설명**: 특정 백테스트 세트 또는 마감된 실시간 모의투자 세션의 상세 정보를 DB에서 영구 삭제합니다.

- **`DELETE /api/backtest/history`**
  - **설명**: 누적된 모든 백테스트 및 종료된 실시간 모의투자 이력을 DB 및 메모리에서 일괄 영구 삭제합니다.

- **`GET /api/backtest/default-configs`**
  - **설명**: 백테스트 폼 화면에 표기할 디폴트 매개변수 및 전략 명세 데이터를 반환합니다.

---

### 1.5. 트레이딩 전략 관리 (Strategies)
시스템 내 등록된 매매 전략(Strategy)을 가동 제어합니다.

- **`GET /api/strategies`**
  - **설명**: 시스템에 등록된 전체 전략 스크립트 정보와 활성화 상태를 조회합니다.

- **`PUT /api/strategies/{strategy_id}`**
  - **설명**: 특정 전략의 매개변수 설정값을 수정하거나 업데이트합니다.

- **`DELETE /api/strategies/{strategy_id}`**
  - **설명**: 특정 전략 설정을 삭제합니다.

- **`POST /api/strategies/{strategy_id}/enable`**
  - **설명**: 특정 전략의 작동 여부를 사용/미사용으로 토글 제어합니다.

---

### 1.6. 시스템 텔레메트리 & 데이터 클리닝 (Telemetry & Cleanup)
시스템 진단 및 DB 저장 용량 유지를 위한 부가 기능 API입니다.

- **`GET /alerts?limit={count}`**
  - **설명**: 시스템 전체에서 수집된 최근 급등락 경고 메시지 목록을 조회합니다.

- **`DELETE /api/alerts`**
  - **설명**: DB에 누적된 모든 알림 기록을 일괄 삭제합니다.

- **`GET /api/system/queues`**
  - **설명**: 수집 데몬 큐, DB 저장 대기 큐, 캔들 가공 큐 등에 적체된 미처리 패킷 백로그 건수 및 누적 처리량을 반환합니다.

- **`GET /test-alert?symbol={symbol}`**
  - **설명**: UI 연동 테스트를 위해 특정 심볼의 가상 급등락(Spike) 알림을 강제로 발생시키고 브로드캐스트합니다.

- **`GET /test-status?strategy_id={id}`**
  - **설명**: UI 연동 테스트를 위해 특정 매매 전략의 가상 동작 지표 상태 데이터를 강제 발생 및 전송합니다.

- **`GET /data/cleanup/preview?date={date_iso}`**
  - **설명**: 지정된 날짜(예: `2026-05-01`) 이전에 해당하는 삭제 대상 데이터(체결 틱 및 캔들) 건수를 미리 조회합니다.

- **`POST /data/cleanup?date={date_iso}`**
  - **설명**: 지정된 날짜 이전의 체결 데이터 및 캔들 데이터를 DB에서 완전히 삭제하여 디스크 용량을 최적화합니다.

---

## 2. WebSocket 실시간 스트리밍 프로토콜

클라이언트는 백엔드와 단일 WebSocket 연결(`/ws`)을 수립하여 실시간 시세 데이터와 이벤트 알림을 수신합니다.

- **접속 주소**: `ws://localhost:8000/ws`

### 2.1. 클라이언트 구독 요청 (Subscribe Request)
접속 후 클라이언트는 실시간으로 체결 및 캔들 업데이트를 받길 원하는 종목을 다음과 같이 JSON 문자열 형식으로 요청해야 합니다.

```json
{
  "subscribe": "BTC",
  "exchange": "upbit"
}
```

---

### 2.2. 백엔드 브로드캐스트 이벤트 (Broadcast Events)
구독 완료된 틱 및 내부 상태가 변경될 때 서버는 다음과 같은 유형의 패킷들을 브로드캐스트합니다.

#### A. 실시간 틱 데이터 패킷 (Type: `tick`)
```json
{
  "type": "tick",
  "exchange": "upbit",
  "symbol": "BTC",
  "trade_price": 98550000.0,
  "trade_volume": 0.054,
  "ask_bid": "BID",
  "trade_timestamp": 1716870005120
}
```

#### B. 캔들 최종 업데이트 패킷 (Type: `candle`)
```json
{
  "type": "candle",
  "exchange": "upbit",
  "symbol": "BTC",
  "interval": 60,
  "timestamp": 1716870000000,
  "open": 98450000.0,
  "high": 98600000.0,
  "low": 98300000.0,
  "close": 98550000.0,
  "volume": 3.12,
  "indicators": {
    "sma20": 98230000.0,
    "rsi14": 42.5,
    "bb_upper": 98900000.0,
    "bb_lower": 97560000.0
  }
}
```

#### C. 수집기 및 큐 시스템 상태 변경 경고 패킷 (Type: `collector_status` / `queue_status`)
```json
{
  "type": "collector_status",
  "exchange": "upbit",
  "is_running": true,
  "error": null
}
```
```json
{
  "type": "queue_status",
  "processing": 0,
  "database": 4,
  "candle": 0,
  "total": 4
}
```
