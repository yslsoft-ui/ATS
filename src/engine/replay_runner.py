import asyncio
from typing import Dict, List, Any, Optional

class BacktestPortfolioManagerProxy:
    """
    StrategyHost가 포트폴리오 요약을 조회할 때 
    특정 백테스트 임시 포트폴리오 ID를 바라보도록 우회해 주는 프록시 객체입니다.
    """
    def __init__(self, manager: Any, portfolio_id: str):
        self.manager = manager
        self.portfolio_id = portfolio_id
        
    def get_portfolio_summary(self, symbol: str, portfolio_id: Optional[str] = None, exchange_id: Optional[str] = None) -> Dict[str, Any]:
        target_id = portfolio_id if portfolio_id is not None else self.portfolio_id
        return self.manager.get_portfolio_summary(symbol, target_id, exchange_id)

class TickReplayRunner:
    """
    DB, 설정 파일, 혹은 환경 변수에 의존하지 않는 무상태형(Stateless) 틱 데이터 리플레이 루프 실행기입니다.
    단일 및 다중 종목 백테스트 리플레이 루프를 통일된 구조로 수행합니다.
    """
    def __init__(
        self,
        portfolio_id: str,
        execution_pipeline: Any,
        size_ratio: float,
        risk_limits_enabled: bool = True,
        slippage_rate: float = 0.001
    ):
        self.portfolio_id = portfolio_id
        self.execution_pipeline = execution_pipeline
        self.size_ratio = size_ratio
        self.risk_limits_enabled = risk_limits_enabled
        self.slippage_rate = slippage_rate

    async def run(
        self,
        ticks: List[Dict[str, Any]],
        engines: Dict[str, Any],
        proxy_manager: BacktestPortfolioManagerProxy
    ) -> Dict[str, Any]:
        """
        시간 순서대로 융합/정렬된 틱 데이터 스트림을 리플레이하며,
        전략 및 거래 매칭 엔진 상태를 업데이트하고 가상 체결 파이프라인을 호출합니다.

        Args:
            ticks: 정형화된 틱 데이터 리스트. 각 틱은 다음 필드를 필수 포함해야 합니다:
                   {
                       "exchange_id": str,
                       "symbol": str,
                       "trade_price": float,
                       "trade_volume": float,
                       "ask_bid": str,
                       "trade_timestamp": int
                   }
            engines: { f"{exchange_id}_{symbol}": TradeEngine } 형태의 활성 매칭 엔진 매핑
            proxy_manager: 포트폴리오 요약 조회용 프록시 매니저 객체

        Returns:
            Dict[str, Any]:
                {
                    "candle_histories": { f"{exchange_id}_{symbol}": List[Dict[str, Any]] },
                    "last_prices": { (exchange_id.lower(), symbol): float }
                }
        """
        candle_histories: Dict[str, List[Dict[str, Any]]] = {}
        last_prices: Dict[tuple[str, str], float] = {}

        for tick in ticks:
            ex = tick["exchange_id"]
            sym = tick["symbol"]
            price = tick["trade_price"]
            
            # 최종 체결가 갱신
            last_prices[(ex.lower(), sym)] = price
            
            key = f"{ex}_{sym}"
            if key not in candle_histories:
                candle_histories[key] = []

            # 해당 종목용 TradeEngine 검색
            engine = engines.get(key)
            if not engine:
                continue

            # 1. TradeEngine에 틱 입력 주입 및 전략 신호, 닫힌 캔들 획득
            signals, closed_candles = await engine.process_tick(tick, proxy_manager)

            # 2. 닫힌 캔들 히스토리 수집
            for c in closed_candles:
                candle_histories[key].append({
                    "time": c.timestamp,
                    "open": c.open,
                    "high": c.high,
                    "low": c.low,
                    "close": c.close,
                    "volume": c.volume
                })

            # 3. 발생한 전략 신호를 가상 체결 파이프라인으로 전송
            for sig in signals:
                await self.execution_pipeline.process_signal(
                    signal=sig,
                    price=price,
                    portfolio_id=self.portfolio_id,
                    risk_limits_enabled=self.risk_limits_enabled,
                    slippage_rate=self.slippage_rate,
                    size_ratio=self.size_ratio
                )

        return {
            "candle_histories": candle_histories,
            "last_prices": last_prices
        }
