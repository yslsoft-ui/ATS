# 백테스트 엔진 상세 설계 (BACKTEST_ENGINE_DESIGN.md)

이 문서는 실제 시장 환경과 최대한 유사한 결과를 도출하기 위한 백테스트 엔진의 정밀 로직을 정의합니다.

## 1. 체결 엔진 로직 (Matching Engine Logic)

백테스트의 신뢰도를 결정하는 가장 중요한 요소입니다.

### 1.1. 슬리피지 (Slippage) 모델링

실제 시장에서는 주문 시점과 체결 시점의 가격 차이뿐만 아니라, 주문 수량에 따른 시장 충격(Market Impact)이 발생합니다. 이를 정확히 시뮬레이션하기 위해 호가창 데이터를 활용합니다.

1. **호가창 기반 동적 모델 (Orderbook-based Slippage)** — `src/engine/matching.py`:
   - `OrderbookMatchingEngine` 클래스로 구현 완료.
   - 호가창(Orderbook) 스냅샷 데이터를 바탕으로, 실제 존재하는 호가와 잔량을 소진(Consume)하며 체결가를 산출합니다.
   - **로직**: 주문 수량 `Q`에 대해 최우선 매도/매수 호가부터 잔량을 차감. 한 호가의 잔량이 부족하면 다음 호가로 넘어가며 **가중 평균 체결가(VWAP)**를 계산.
   - 미체결 잔량(unfilled quantity)도 반환하여 부분 체결 상황을 처리.
   - *장점*: 대량 주문 시 발생하는 현실적인 시장 충격을 정확하게 반영할 수 있습니다.

2. **고정 비율 모델 (Simple Slippage)** - 폴백(Fallback) 용도:
   - 호가창 데이터가 누락되었거나 연산 속도를 극대화해야 할 때 사용하는 기본 모델.
   - **시장가 매수**: `체결가 = 현재가 * (1 + 고정 Slippage)`
   - **시장가 매도**: `체결가 = 현재가 * (1 - 고정 Slippage)`
   - *Slippage 설정값 추천: 0.1% ~ 0.2%*
   - ⚠️ **현재 상태**: 미구현. `backtest.py`에서 `cash / price` 직접 계산 중.

### 1.2. 수수료 (Fee) 계산

거래소 수수료는 원금에서 차감하거나 별도로 합산해야 합니다.

- **매수 시**: `필요 현금 = (체결가 * 수량) * (1 + 수수료율)`
- **매도 시**: `수령 현금 = (체결가 * 수량) * (1 - 수수료율)`
- *Upbit 기본값: 0.05% (0.0005)*
- **구현 상태**: `matching.py`에 `fee_rate=0.0005`로 구현 완료. 단, `backtest.py`의 리플레이 루프에서 `matching_engine`을 **아직 연동하지 않음** (직접 `cash / price` 계산).

## 2. 지표 연산 로직 (Indicator Calculation)

틱 데이터 기반이므로 연산 효율성이 중요합니다. `src/engine/indicators.py`에 구현.

### 2.1. 실시간 윈도우 업데이트 (Sliding Window) — `IndicatorCalculator.update()`

- `collections.deque`를 사용하여 지표를 효율적으로 업데이트합니다.
- 매 틱 호출 시 다음 지표를 반환:
  - **SMA** (window_size 기반)
  - **RSI** (상승/하락 평균)
  - **Bollinger Bands** (20, 2σ)
  - **MACD** (12, 26, 9) — EMA 기반, signal line 포함

### 2.2. 배치 지표 계산 — `IndicatorCalculator.calculate_all_indicators()`

- 캔들 리스트를 pandas DataFrame으로 변환 후 rolling window로 일괄 계산.
- `/candles` API에서 과거 데이터 조회 시 사용.
- SMA(20), Bollinger Bands(20, 2σ), RSI(14) 계산.

### 2.3. 프론트엔드 실시간 지표 — `app.js: calculateIndicators()`

- 브라우저에서 틱 수신 시마다 JavaScript로 SMA(20), BB(20, 2σ), RSI(14) 계산.
- 서버 부하 없이 클라이언트 측에서 즉시 지표 업데이트.

## 3. 전략 엔진 (Strategy Engine) — `src/engine/strategy.py`

### 3.1. 전략 인터페이스

- `BaseStrategy` (ABC): `on_candle(candle) → StrategyResult` 추상 메서드 정의.
- `StrategyResult`: `action` ("BUY", "SELL", "HOLD"), `price`, `reason` 포함.

### 3.2. 구현된 전략

1. **RSI 역추세 전략 (`RSIStrategy`)**:
   - RSI < `buy_threshold` (기본 30) → 매수 신호.
   - RSI > `sell_threshold` (기본 70) → 매도 신호.
   - 포지션 상태(`in_position`)로 중복 진입 방지.

2. **MACD 골든크로스 전략 (`MACDStrategy`)**:
   - MACD Histogram이 음수→양수 전환 시 매수 (Golden Cross).
   - MACD Histogram이 양수→음수 전환 시 매도 (Dead Cross).

## 4. 포트폴리오 관리 (Portfolio Management)

### 4.1. 자산 평가 (Equity Evaluation)

- `Total Equity = 보유 현금 + (보유 자산 수량 * 현재 틱 가격)`
- 백테스트 종료 시 최종 자산 가치 및 ROI 산출.

### 4.2. 리스크 관리 (Risk Management)

- **Stop Loss (손절)**: 현재 틱 가격이 진입가 대비 설정된 손실 제한선(예: -3%)에 도달하면 다음 틱에서 즉시 시장가 매도 이벤트 발생.
- **Take Profit (익절)**: 목표 수익권(예: +5%) 도달 시 동일하게 처리.
- ⚠️ **현재 상태**: 미구현.

## 5. 성과 지표 산출 공식 (Performance Metrics)

백테스트 종료 후 다음 지표들을 산출합니다.

1. **총 수익률 (Total Return)** ✅: `(최종 자산 / 초기 자산 - 1) * 100`
2. **최대 낙폭 (MDD, Max Drawdown)** ⚠️ 미구현:
   - 고점 대비 가장 많이 하락한 지점.
   - `Drawdown = (최고점 - 현재점) / 최고점`
   - `MDD = Max(Drawdown)`
3. **승률 (Win Rate)** ⚠️ 미구현: `익절 거래 횟수 / 총 거래 횟수`
4. **손익비 (Profit Factor)** ⚠️ 미구현: `총 이익 합계 / 총 손실 합계`

## 6. 데이터 리플레이 루프 (Replay Loop) — `src/engine/backtest.py`

```python
for tick in tick_data_source:
    # 1. 틱 데이터를 캔들 생성기에 주입
    closed_candles = candle_gen.process_tick(symbol, price, volume, timestamp)
    
    # 2. 완성된 캔들이 있으면 전략 실행
    for candle in closed_candles:
        result = strategy.on_candle(candle)
        
        if result.action == "BUY" and cash > 0:
            position = cash / price  # TODO: matching_engine 연동
            cash = 0
        elif result.action == "SELL" and position > 0:
            cash = position * price  # TODO: matching_engine 연동
            position = 0
```

## 7. 시각화 (Visualization) — `src/utils/visualizer.py`

- `run_backtest_sample.py`에서 호출.
- 백테스트 결과(캔들 차트 + 매수/매도 포인트)를 PNG 이미지로 저장.
- 출력 예시: `backtest_rsi_30_70.png`, `backtest_macd_golden_cross.png`
