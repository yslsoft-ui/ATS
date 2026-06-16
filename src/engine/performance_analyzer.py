# -*- coding: utf-8 -*-

import json
from typing import List, Dict, Any, Optional, TYPE_CHECKING
from src.engine.utils.stock_mapper import stock_mapper

if TYPE_CHECKING:
    from src.engine.portfolio import Portfolio


class PerformanceAnalyzer:
    """
    Stateless 포트폴리오 성과 분석 연산기.
    어떠한 외부 DB/API/시세 조회 I/O 없이, 주어진 입력값만을 기반으로 리포트 데이터를 산출합니다.
    """

    @staticmethod
    def calculate_report(
        portfolio: "Portfolio",
        trades: List[Dict[str, Any]],
        current_prices: Dict[str, float]
    ) -> Dict[str, Any]:
        """
        포트폴리오의 실시간/정적 성과 통계 및 요약 보고서 데이터를 빌드합니다.
        기존 PortfolioManager의 get_portfolio_report_data 연산 로직을 그대로 유지하되
        외부 의존성 및 I/O를 배제하여 순수 연산으로 처리합니다.
        """
        total_fee = sum(t.get('fee', 0.0) for t in trades)
        total_tax = sum(t.get('tax', 0.0) for t in trades)
        trade_count = len(trades)

        # 1. 거래소별 자산 집계
        exchanges_summary = []
        exchange_cash_map = {}
        ex_initial_cash_map = {}
        
        meta = {}
        strategy_info_str = getattr(portfolio, 'strategy_info', '')
        if strategy_info_str:
            try:
                meta = json.loads(strategy_info_str)
            except Exception:
                pass
                
        initial_cash_map = meta.get("initial_cash", {}) if isinstance(meta, dict) else {}
        
        # 1.1. 거래소별 초기 자금 복원 (우선순위: portfolio.exchange_initial_cash -> strategy_info -> 동적 분할)
        if hasattr(portfolio, 'exchange_initial_cash') and portfolio.exchange_initial_cash:
            ex_initial_cash_map = {ex.lower(): float(val) for ex, val in portfolio.exchange_initial_cash.items()}
        elif initial_cash_map:
            ex_initial_cash_map = {ex.lower(): float(val) for ex, val in initial_cash_map.items()}
        else:
            ex_set = set((t.get("exchange_id") or t.get("exchange") or "upbit").lower() for t in trades) if trades else set()
            for pos_key in portfolio.positions.keys():
                ex_set.add(pos_key[0].lower())
            if hasattr(portfolio, 'exchange_cash') and portfolio.exchange_cash:
                for ex in portfolio.exchange_cash.keys():
                    ex_set.add(ex.lower())
            if ex_set:
                each_cash = portfolio.initial_cash / len(ex_set)
                ex_initial_cash_map = {ex.lower(): each_cash for ex in ex_set}
            else:
                ex_id = "upbit"
                if hasattr(portfolio, 'exchange_cash') and portfolio.exchange_cash:
                    ex_id = list(portfolio.exchange_cash.keys())[0]
                ex_initial_cash_map = {ex_id.lower(): portfolio.initial_cash}

        # 1.2. 거래소별 현금 복원
        if hasattr(portfolio, 'exchange_cash') and portfolio.exchange_cash:
            for ex, val in portfolio.exchange_cash.items():
                exchange_cash_map[ex.lower()] = val
                # ex가 ex_initial_cash_map에 없으면 기본 세팅
                ex_lower = ex.lower()
                if ex_lower not in ex_initial_cash_map:
                    ex_initial_cash_map[ex_lower] = val
        else:
            ex_id = 'upbit'
            exchange_cash_map[ex_id] = portfolio.cash
            if ex_id not in ex_initial_cash_map:
                ex_initial_cash_map[ex_id] = portfolio.cash

        # 2. 종목별 성과 상세 분석 결과(results) 생성
        trades_by_ex_sym = {}
        for t in trades:
            ex_lower = (t.get("exchange_id") or t.get("exchange") or "upbit").lower()
            sym = t["symbol"]
            key = (ex_lower, sym)
            if key not in trades_by_ex_sym:
                trades_by_ex_sym[key] = []
            trades_by_ex_sym[key].append(t)

        results = []
        all_keys = set(trades_by_ex_sym.keys())
        for pos_key, pos in portfolio.positions.items():
            if pos.quantity > 0:
                all_keys.add((pos_key[0].lower(), pos_key[1]))

        for ex_lower, sym in all_keys:
            sym_trades = trades_by_ex_sym.get((ex_lower, sym), [])
            pos_info = portfolio.positions.get((ex_lower, sym))
            current_qty = pos_info.quantity if pos_info else 0.0
            avg_price = pos_info.avg_price if pos_info else 0.0
            final_price = current_prices.get(sym, avg_price)

            buy_sum = sum(t["price"] * t["quantity"] for t in sym_trades if t["side"] == "BUY")
            sell_sum = sum(t["price"] * t["quantity"] for t in sym_trades if t["side"] == "SELL")
            valuation = current_qty * final_price
            symbol_fee = sum(t.get("fee", 0.0) for t in sym_trades)
            symbol_tax = sum(t.get("tax", 0.0) for t in sym_trades)
            symbol_profit = sell_sum + valuation - buy_sum - symbol_fee - symbol_tax

            buy_trades = [t for t in sym_trades if t["side"] == "BUY"]
            buy_count = len(buy_trades)
            symbol_roi = 0.0
            if buy_count > 0:
                avg_buy_val = buy_sum / buy_count
                symbol_roi = (symbol_profit / avg_buy_val * 100) if avg_buy_val > 0 else 0.0

            kor_name = stock_mapper.get_name(ex_lower, sym)
            symbol_init_cash = ex_initial_cash_map.get(ex_lower, portfolio.initial_cash)

            results.append({
                "exchange": ex_lower.upper(),
                "symbol": sym,
                "korean_name": kor_name,
                "portfolio_id": portfolio.id,
                "portfolio_name": portfolio.name,
                "initial_cash": symbol_init_cash,
                "final_value": round(valuation, 2),
                "roi": round(symbol_roi, 4),
                "fee": round(symbol_fee, 2),
                "tax": round(symbol_tax, 2),
                "profit": round(symbol_profit, 2),
                "trade_count": len(sym_trades),
                "trades": [
                    {
                        "side": t["side"],
                        "price": t["price"],
                        "quantity": t["quantity"],
                        "fee": t["fee"],
                        "tax": t.get("tax", 0.0),
                        "timestamp": t["timestamp"] * 1000 if t["timestamp"] < 10000000000 else t["timestamp"],
                        "reason": t.get("reason", "")
                     }
                    for t in sym_trades
                ],
                "candle_history": [],
                "quantity": current_qty,
                "avg_price": avg_price,
                "final_price": final_price
            })

        # 3. 거래소별 요약 지표 생성 (results 기반)
        ex_profit_sums = {ex.lower(): 0.0 for ex in ex_initial_cash_map.keys()}
        ex_fee_sums = {ex.lower(): 0.0 for ex in ex_initial_cash_map.keys()}
        ex_tax_sums = {ex.lower(): 0.0 for ex in ex_initial_cash_map.keys()}
        ex_trade_counts = {ex.lower(): 0 for ex in ex_initial_cash_map.keys()}

        for r in results:
            ex_lower = r["exchange"].lower()
            if ex_lower not in ex_profit_sums:
                ex_profit_sums[ex_lower] = 0.0
                ex_fee_sums[ex_lower] = 0.0
                ex_tax_sums[ex_lower] = 0.0
                ex_trade_counts[ex_lower] = 0
            ex_profit_sums[ex_lower] += r["profit"]
            ex_fee_sums[ex_lower] += r["fee"]
            ex_tax_sums[ex_lower] += r.get("tax", 0.0)
            ex_trade_counts[ex_lower] += r["trade_count"]

        for ex, init_cash in ex_initial_cash_map.items():
            ex_lower = ex.lower()
            ex_profit = ex_profit_sums.get(ex_lower, 0.0)
            ex_fee = ex_fee_sums.get(ex_lower, 0.0)
            ex_tax = ex_tax_sums.get(ex_lower, 0.0)
            ex_trades = ex_trade_counts.get(ex_lower, 0)
            curr_cash = exchange_cash_map.get(ex_lower, 0.0)
            
            ex_val = sum(r["final_value"] for r in results if r["exchange"].lower() == ex_lower)
            ex_total_val = curr_cash + ex_val

            exchanges_summary.append({
                "exchange_id": ex.upper(),
                "initial_cash": init_cash,
                "cash": curr_cash,
                "total_value": ex_total_val,
                "profit": ex_profit,
                "roi": round((ex_profit / init_cash * 100), 2) if init_cash > 0 else 0.0,
                "fee": ex_fee,
                "tax": ex_tax,
                "trade_count": ex_trades
            })

        # 4. 전체 종합 요약 지표 (summary) 생성
        total_initial = sum(ex_initial_cash_map.values())
        if portfolio.portfolio_type == 'live':
            total_positions_val = sum(pos.quantity * current_prices.get(pos.symbol, pos.avg_price) 
                                      for pos in portfolio.positions.values() if pos.quantity > 0)
            total_value = portfolio.cash + total_positions_val
            if total_initial <= 0.0:
                total_initial = total_value
            total_profit = total_value - total_initial
        else:
            total_profit = sum(ex_profit_sums.values())
            total_value = total_initial + total_profit
        total_roi = (total_profit / total_initial * 100) if total_initial > 0 else 0.0

        applied_strategies = []
        if strategy_info_str:
            try:
                meta = json.loads(strategy_info_str)
                if isinstance(meta, dict) and "applied_strategies" in meta:
                    applied_strategies = meta["applied_strategies"]
                elif isinstance(meta, list):
                    applied_strategies = meta
            except Exception:
                pass

        # display_history는 최신 50개만 보여줍니다. (trades는 ASC 정렬이므로 reversed 후 50개 슬라이싱)
        display_history = [
            {
                "symbol": t["symbol"],
                "side": t["side"],
                "price": t["price"],
                "quantity": t["quantity"],
                "fee": t["fee"],
                "tax": t.get("tax", 0.0),
                "timestamp": t["timestamp"],
                "reason": t.get("reason", "")
            }
            for t in reversed(trades)
        ][:50]

        return {
            "status": "success",
            "id": portfolio.id,
            "portfolio_id": portfolio.id,
            "name": portfolio.name,
            "initial_cash": portfolio.initial_cash,
            "cash": portfolio.cash,
            "total_value": total_value,
            "roi": round(total_roi, 2),
            "type": portfolio.portfolio_type,
            "created_at": getattr(portfolio, 'created_at', None),
            "updated_at": getattr(portfolio, 'updated_at', None),
            "ended_at": getattr(portfolio, 'ended_at', None),
            "duration": getattr(portfolio, 'duration', 0.0),
            "applied_strategies": applied_strategies,
            "exchanges": exchanges_summary,
            "exchange_initial_cash": ex_initial_cash_map,
            "exchange_cash": exchange_cash_map,
            "summary": {
                "initial_cash": total_initial,
                "final_value": total_value,
                "profit": total_profit,
                "roi": round(total_roi, 2),
                "fee": total_fee,
                "tax": total_tax,
                "trade_count": trade_count
            },
            "positions": [
                {
                    "exchange_id": pos.exchange_id,
                    "symbol": pos.symbol,
                    "quantity": pos.quantity,
                    "avg_price": pos.avg_price,
                    "current_price": current_prices.get(pos.symbol, pos.avg_price),
                    "korean_name": stock_mapper.get_name(pos.exchange_id.lower(), pos.symbol),
                    "updated_at": pos.updated_at
                }
                for pos in portfolio.positions.values() if pos.quantity > 0
            ],
            "results": results,
            "history": display_history
        }
