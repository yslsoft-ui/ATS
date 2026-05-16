# Upbit Real-time Dashboard

Upbit 거래소의 실시간 체결 데이터를 수집하여 시각화하고, 급등 탐지 및 자동 매매 시뮬레이션을 수행하는 시스템입니다.

## Language

**Candle**:
특정 시간 범위(Interval) 동안의 가격 변동(시가, 고가, 저가, 종가)과 거래량을 요약한 데이터 단위.
_Avoid_: 봉, 차트 데이터

**Interval**:
캔들이 생성되는 시간적 간격 (예: 1s, 1m, 5m).
_Avoid_: 주기, 타임프레임

**Tick**:
거래소에서 발생하는 최소 단위의 개별 체결 데이터.
_Avoid_: Trade, 체결 건

**Spike**:
가격이나 거래량이 단기간에 임계치 이상으로 상승하는 현상.
_Avoid_: 급등락, 펌핑

**Alert**:
**Spike** 포착 또는 특정 지표 조건 충족 시 생성되어 사용자에게 전달되는 정보 단위.
_Avoid_: 알림 메시지, 노티

**Strategy**:
시장 상황을 분석하여 매수/매도 신호를 생성하는 규칙 모음.
_Avoid_: 매매 로직, 알고리즘

**Backtest**:
과거 데이터를 기반으로 **Strategy**의 성과를 측정하는 실험.
_Avoid_: 과거 검증, 수익률 테스트

**Trade Simulation**:
실제 자산을 사용하지 않고 가상 자산으로 거래를 수행하는 모든 행위. 과거 데이터 테스트(**Backtest**)와 실시간 가상 매매를 모두 포함함.
_Avoid_: 모의 투자, 페이퍼 트레이딩

**Order Matching**:
호가창 데이터를 기반으로 주문의 체결 여부와 실제 체결 가격(슬리피지 포함)을 결정하는 프로세스.
_Avoid_: 주문 처리, 체결 확인

**Portfolio**:
**Trade Simulation** 중에 관리되는 가상 자산(현금 및 보유 종목)의 상태.
_Avoid_: 잔고, 지갑, 계좌

**TradeEngine**:
종목별로 독립적인 **Candle** 생성, 지표 계산, **Strategy** 실행을 총괄하는 핵심 엔진 유닛.
_Avoid_: 매매 엔진, 메인 루프

**Warm-up**:
실시간 데이터 처리 전, 최근의 **Tick** 데이터를 엔진에 주입하여 지표와 **Strategy** 상태를 최신화하는 과정.
_Avoid_: 초기화, 데이터 로딩, 사전 학습

**Doji (도지)**:
시가와 종가가 동일한 **Candle**. 시스템은 이전 **Candle**의 종가와 비교하여 추세 색상을 결정함.
_Avoid_: 십자봉, 무변동 봉

**PortfolioManager**:
여러 개의 **Portfolio**를 관리하고, **TradeEngine**의 신호를 받아 **OrderExecutor**를 통해 주문을 처리하는 관리 모듈.
_Avoid_: 자산 관리자, 매매 관리기

**OrderExecutor**:
주문을 실제로 집행하는 추상 레이어. 가상 체결(**VirtualExecutor**)과 실제 API 체결을 동일한 인터페이스로 제공함.
_Avoid_: 주문기, 체결 처리기

## Relationships

- 하나의 **Interval** 설정에 따라 여러 개의 **Candle**이 연속적으로 생성됨
- **Candle**은 기술 지표(Indicator) 계산의 기초 데이터가 됨
- 수많은 **Tick**이 모여 하나의 **Candle**을 구성함
- **TradeEngine**은 각 종목의 **Tick**을 수신하여 내부의 **Candle** 상태를 관리함
- 실시간 수집 전, **TradeEngine**은 반드시 **Warm-up** 과정을 거쳐 지표를 최신화해야 함
- **Doji**가 발생하면 시스템은 현재 가격을 이전 **Candle**의 종가와 비교하여 **Trade Simulation**의 색상 로직에 반영함
- **Spike Detector**는 **Tick** 스트림을 분석하여 **Spike**를 포착함
- **Spike**가 발생하면 시스템은 **Alert**를 생성하고 저장함
- **Trade Simulation**은 하나 이상의 **Strategy**를 실행하여 매매 신호를 발생시킴
- **Backtest**는 과거의 **Candle** 데이터를 입력값으로 사용하는 **Trade Simulation**의 한 형태임
- **Order Matching** 엔진은 **Trade Simulation** 과정에서 발생한 주문의 실제 체결가를 결정함
- **Portfolio** 상태는 **Order Matching** 결과에 따라 업데이트됨
- **PortfolioManager**는 **TradeEngine**이 생성한 신호를 받아 적절한 **Portfolio**에 배분함

## Flagged ambiguities

- "Trade"는 업비트 API에서 **Tick**을 의미하지만, 시스템 내에서는 **Trade Simulation**의 실행 단위(거래)와 혼동될 수 있으므로 개별 데이터는 **Tick**으로 통일합니다.
- "Simulation"은 과거 데이터 검증(**Backtest**)과 실시간 가상 매매를 모두 포함하는 포괄적인 용어로 정의합니다.
- **TradeEngine**은 **Tick**을 받아 **Candle**을 만들고 **Strategy**를 실행하는 모든 연산을 수행하는 단일 책임 단위를 의미합니다.
- **PortfolioManager**는 실제 주문 집행과 자산 관리를 분리하여 엔진의 독립성을 보장합니다.
