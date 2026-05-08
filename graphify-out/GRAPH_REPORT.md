# Graph Report - TEST  (2026-05-07)

## Corpus Check
- 36 files · ~24,177 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 242 nodes · 418 edges · 33 communities detected
- Extraction: 60% EXTRACTED · 40% INFERRED · 0% AMBIGUOUS · INFERRED: 167 edges (avg confidence: 0.6)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- [[_COMMUNITY_Community 0|Community 0]]
- [[_COMMUNITY_Community 1|Community 1]]
- [[_COMMUNITY_Community 2|Community 2]]
- [[_COMMUNITY_Community 3|Community 3]]
- [[_COMMUNITY_Community 4|Community 4]]
- [[_COMMUNITY_Community 5|Community 5]]
- [[_COMMUNITY_Community 6|Community 6]]
- [[_COMMUNITY_Community 7|Community 7]]
- [[_COMMUNITY_Community 8|Community 8]]
- [[_COMMUNITY_Community 9|Community 9]]
- [[_COMMUNITY_Community 10|Community 10]]
- [[_COMMUNITY_Community 11|Community 11]]
- [[_COMMUNITY_Community 12|Community 12]]
- [[_COMMUNITY_Community 22|Community 22]]
- [[_COMMUNITY_Community 24|Community 24]]
- [[_COMMUNITY_Community 25|Community 25]]
- [[_COMMUNITY_Community 26|Community 26]]
- [[_COMMUNITY_Community 27|Community 27]]
- [[_COMMUNITY_Community 28|Community 28]]
- [[_COMMUNITY_Community 29|Community 29]]
- [[_COMMUNITY_Community 30|Community 30]]
- [[_COMMUNITY_Community 31|Community 31]]
- [[_COMMUNITY_Community 32|Community 32]]
- [[_COMMUNITY_Community 33|Community 33]]
- [[_COMMUNITY_Community 34|Community 34]]
- [[_COMMUNITY_Community 35|Community 35]]
- [[_COMMUNITY_Community 36|Community 36]]
- [[_COMMUNITY_Community 37|Community 37]]
- [[_COMMUNITY_Community 38|Community 38]]
- [[_COMMUNITY_Community 39|Community 39]]
- [[_COMMUNITY_Community 40|Community 40]]
- [[_COMMUNITY_Community 41|Community 41]]
- [[_COMMUNITY_Community 42|Community 42]]

## God Nodes (most connected - your core abstractions)
1. `CandleGenerator` - 28 edges
2. `IndicatorCalculator` - 28 edges
3. `StrategyRegistry` - 28 edges
4. `Candle` - 19 edges
5. `StrategyResult` - 15 edges
6. `BacktestEngine` - 14 edges
7. `RSIStrategy` - 13 edges
8. `BaseStrategy` - 12 edges
9. `MACDStrategy` - 12 edges
10. `ConnectionManager` - 11 edges

## Surprising Connections (you probably didn't know these)
- `MACDStrategy` --conceptually_related_to--> `Backtest Macd Golden Cross`  [INFERRED]
  src\engine\strategies\macd_strategy.py → backtest_macd_golden_cross.png
- `RSIStrategy` --conceptually_related_to--> `Backtest Rsi 30 70`  [INFERRED]
  src\engine\strategies\rsi_strategy.py → backtest_rsi_30_70.png
- `BacktestEngine` --conceptually_related_to--> `Backtest Macd Golden Cross`  [INFERRED]
  src\engine\backtest.py → backtest_macd_golden_cross.png
- `BacktestEngine` --conceptually_related_to--> `Backtest Rsi 30 70`  [INFERRED]
  src\engine\backtest.py → backtest_rsi_30_70.png
- `get_candles()` --calls--> `calculate_all_indicators()`  [INFERRED]
  src\server\main.py → src\engine\indicators.py

## Communities

### Community 0 - "Community 0"
Cohesion: 0.12
Nodes (21): BaseStrategy, Candle, 새로운 틱 가격을 업데이트하고 현재 계산된 지표를 반환합니다.                  :param price: 현재 체결가, MACDStrategy, MACD 골든크로스(매수) 및 데드크로스(매도) 신호를 생성합니다., 특정 전략의 파라미터를 업데이트합니다., update_strategy_params(), RSI 지표를 기반으로 과매도(Buy) 및 과매수(Sell) 신호를 생성합니다. (+13 more)

### Community 1 - "Community 1"
Cohesion: 0.1
Nodes (19): cleanup_data(), clear_alerts(), CollectorManager, ConnectionManager, db_writer_loop(), get_alerts(), get_trades(), WebSocket 연결 및 종목별 구독을 관리합니다. (+11 more)

### Community 2 - "Community 2"
Cohesion: 0.12
Nodes (25): addAlertToTable(), calculateIndicators(), clearAlertHistory(), connectWS(), drillDown(), exitExplorerMode(), init(), initLWChart() (+17 more)

### Community 3 - "Community 3"
Cohesion: 0.11
Nodes (17): BacktestEngine, Backtest Macd Golden Cross, Backtest Rsi 30 70, BaseModel, BacktestRequest, get_recent_trades(), get_status(), 지정된 파라미터로 백테스트 엔진을 실행합니다. (+9 more)

### Community 4 - "Community 4"
Cohesion: 0.11
Nodes (10): calculate_all_indicators(), IndicatorCalculator, 틱(Tick) 단위로 유입되는 실시간 데이터를 효율적으로 처리하기 위해     슬라이딩 윈도우(Sliding Window) 방식으로 지표를 계산, disable_strategy(), get_market(), get_symbols(), 전체 KRW 마켓 종목 정보(한글명, 현재가, 변동률, 거래대금)를 반환합니다., 수집 가능한 전체 KRW 종목 목록을 반환합니다. (+2 more)

### Community 5 - "Community 5"
Cohesion: 0.17
Nodes (8): 틱 데이터를 기반으로 멀티 타임프레임 백테스트를 수행합니다., CandleGenerator, 틱 데이터를 실시간으로 수집하여 여러 타임프레임의 캔들을 생성합니다., 새로운 틱 데이터를 처리하고, 완성된(Closed) 캔들이 있다면 반환합니다.                  :param side: 'BID', get_candles(), 틱 데이터를 기반으로 캔들(OHLC) 데이터를 생성하고 기술 지표를 추가하여 반환합니다., SpikeDetector, TestCandleGenerator

### Community 6 - "Community 6"
Cohesion: 0.16
Nodes (8): load_dynamic_strategies(), 지정된 디렉토리 내의 모든 .py 파일을 찾아 전략 클래스로 로드합니다., 메모리 레지스트리에서 전략을 제거합니다.     (파일은 삭제하지 않고 관리 목록에서만 제외), unload_strategy(), 문법 오류가 있는 파일을 로드할 때 시스템이 중단되지 않는지 확인합니다., 전략 해제 시 레지스트리 제거 및 파일 삭제가 이루어지는지 확인합니다., 동일한 클래스명을 가진 전략이 여러 파일에 있을 때의 처리를 확인합니다., TestStrategyLoaderTDD

### Community 7 - "Community 7"
Cohesion: 0.21
Nodes (8): 해당 종목을 구독 중인 클라이언트에게만 전송합니다., 종목 구독 여부와 상관없이 모든 연결된 클라이언트에게 전송합니다., UI 확인용 테스트 알림을 강제로 발생시킵니다., 엔진에서 실시간 전략으로 발생한 모의 체결을 브로드캐스트합니다., save_alert(), simulate_trade_from_engine(), test_alert(), create_strategy()

### Community 8 - "Community 8"
Cohesion: 0.22
Nodes (5): ABC, list_strategies(), 사용 가능한 모든 전략 목록과 메타데이터를 반환합니다., get_all_metadata(), get_metadata()

### Community 9 - "Community 9"
Cohesion: 0.4
Nodes (4): main(), run_and_print(), plot_backtest_result(), 백테스트 결과를 차트로 시각화하여 파일로 저장합니다.

### Community 10 - "Community 10"
Cohesion: 0.4
Nodes (1): React

### Community 11 - "Community 11"
Cohesion: 0.5
Nodes (1): DBWriter

### Community 12 - "Community 12"
Cohesion: 1.0
Nodes (1): Vite

### Community 22 - "Community 22"
Cohesion: 1.0
Nodes (1): 캔들 리스트를 받아 모든 기술 지표가 포함된 DataFrame을 반환합니다.

### Community 24 - "Community 24"
Cohesion: 1.0
Nodes (1): Antigravity

### Community 25 - "Community 25"
Cohesion: 1.0
Nodes (1): Architecture

### Community 26 - "Community 26"
Cohesion: 1.0
Nodes (1): Backtest Engine Design

### Community 27 - "Community 27"
Cohesion: 1.0
Nodes (1): Collector Design

### Community 28 - "Community 28"
Cohesion: 1.0
Nodes (1): Context

### Community 29 - "Community 29"
Cohesion: 1.0
Nodes (1): Design

### Community 30 - "Community 30"
Cohesion: 1.0
Nodes (1): Ui Design

### Community 31 - "Community 31"
Cohesion: 1.0
Nodes (1): Domain

### Community 32 - "Community 32"
Cohesion: 1.0
Nodes (1): Issue Tracker

### Community 33 - "Community 33"
Cohesion: 1.0
Nodes (1): Triage Labels

### Community 34 - "Community 34"
Cohesion: 1.0
Nodes (1): Index

### Community 35 - "Community 35"
Cohesion: 1.0
Nodes (1): Readme

### Community 36 - "Community 36"
Cohesion: 1.0
Nodes (1): Prd

### Community 37 - "Community 37"
Cohesion: 1.0
Nodes (1): 01 Strategy Registry Metadata

### Community 38 - "Community 38"
Cohesion: 1.0
Nodes (1): 02 Parameter Update Validation

### Community 39 - "Community 39"
Cohesion: 1.0
Nodes (1): 03 Management Ui

### Community 40 - "Community 40"
Cohesion: 1.0
Nodes (1): Favicon

### Community 41 - "Community 41"
Cohesion: 1.0
Nodes (1): Icons

### Community 42 - "Community 42"
Cohesion: 1.0
Nodes (1): Hero

## Knowledge Gaps
- **34 isolated node(s):** `틱 데이터를 실시간으로 수집하여 여러 타임프레임의 캔들을 생성합니다.`, `새로운 틱 데이터를 처리하고, 완성된(Closed) 캔들이 있다면 반환합니다.                  :param side: 'BID'`, `틱(Tick) 단위로 유입되는 실시간 데이터를 효율적으로 처리하기 위해     슬라이딩 윈도우(Sliding Window) 방식으로 지표를 계산`, `새로운 틱 가격을 업데이트하고 현재 계산된 지표를 반환합니다.                  :param price: 현재 체결가`, `캔들 리스트를 받아 모든 기술 지표가 포함된 DataFrame을 반환합니다.` (+29 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **Thin community `Community 10`** (5 nodes): `App()`, `CandleChart()`, `App.jsx`, `main.jsx`, `React`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 11`** (5 nodes): `DBWriter`, `.flush()`, `.__init__()`, `.run()`, `db_writer.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 12`** (2 nodes): `vite.config.js`, `Vite`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 22`** (1 nodes): `캔들 리스트를 받아 모든 기술 지표가 포함된 DataFrame을 반환합니다.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 24`** (1 nodes): `Antigravity`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 25`** (1 nodes): `Architecture`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 26`** (1 nodes): `Backtest Engine Design`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 27`** (1 nodes): `Collector Design`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 28`** (1 nodes): `Context`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 29`** (1 nodes): `Design`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 30`** (1 nodes): `Ui Design`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 31`** (1 nodes): `Domain`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 32`** (1 nodes): `Issue Tracker`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 33`** (1 nodes): `Triage Labels`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 34`** (1 nodes): `Index`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 35`** (1 nodes): `Readme`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 36`** (1 nodes): `Prd`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 37`** (1 nodes): `01 Strategy Registry Metadata`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 38`** (1 nodes): `02 Parameter Update Validation`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 39`** (1 nodes): `03 Management Ui`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 40`** (1 nodes): `Favicon`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 41`** (1 nodes): `Icons`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 42`** (1 nodes): `Hero`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `StrategyRegistry` connect `Community 0` to `Community 1`, `Community 4`, `Community 5`, `Community 6`, `Community 7`, `Community 8`?**
  _High betweenness centrality (0.101) - this node is a cross-community bridge._
- **Why does `CandleGenerator` connect `Community 5` to `Community 0`, `Community 1`, `Community 3`, `Community 4`, `Community 7`, `Community 8`?**
  _High betweenness centrality (0.092) - this node is a cross-community bridge._
- **Why does `BacktestEngine` connect `Community 3` to `Community 8`, `Community 9`, `Community 5`?**
  _High betweenness centrality (0.071) - this node is a cross-community bridge._
- **Are the 23 inferred relationships involving `CandleGenerator` (e.g. with `BacktestEngine` and `틱 데이터를 기반으로 멀티 타임프레임 백테스트를 수행합니다.`) actually correct?**
  _`CandleGenerator` has 23 INFERRED edges - model-reasoned connections that need verification._
- **Are the 24 inferred relationships involving `IndicatorCalculator` (e.g. with `MACDStrategy` and `MACD 골든크로스(매수) 및 데드크로스(매도) 신호를 생성합니다.`) actually correct?**
  _`IndicatorCalculator` has 24 INFERRED edges - model-reasoned connections that need verification._
- **Are the 26 inferred relationships involving `StrategyRegistry` (e.g. with `지정된 디렉토리 내의 모든 .py 파일을 찾아 전략 클래스로 로드합니다.` and `Candle`) actually correct?**
  _`StrategyRegistry` has 26 INFERRED edges - model-reasoned connections that need verification._
- **Are the 17 inferred relationships involving `Candle` (e.g. with `StrategyResult` and `BaseStrategy`) actually correct?**
  _`Candle` has 17 INFERRED edges - model-reasoned connections that need verification._