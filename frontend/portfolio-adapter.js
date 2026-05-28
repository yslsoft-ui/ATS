/**
 * Upbit Terminal Portfolio Data Adapter
 * API 데이터 정제 및 UI 렌더링용 데이터 가공 전담 모듈
 */

const PortfolioAdapter = {
    /**
     * 금액을 한국어 단위(억, 만)로 포맷팅하는 유틸리티 함수입니다.
     */
    formatKoreanAmount(val) {
        if (val === undefined || val === null || isNaN(val)) return '0원';
        if (val >= 100000000) {
            const eok = val / 100000000;
            return eok % 1 === 0 ? eok.toFixed(0) + "억 원" : eok.toFixed(2) + "억 원";
        }
        if (val >= 10000) {
            const man = val / 10000;
            return man % 1 === 0 ? man.toFixed(0) + "만 원" : man.toFixed(1) + "만 원";
        }
        return Math.round(val).toLocaleString() + "원";
    },

    /**
     * 가격 데이터를 포맷팅합니다.
     */
    formatPricePort(val) {
        if (val === undefined || val === null || isNaN(val)) return '-';
        if (val < 100) {
            return val % 1 === 0 ? val.toLocaleString() + " 원" : val.toFixed(2).replace(/\B(?=(\d{3})+(?!\d))/g, ",") + " 원";
        }
        return Math.round(val).toLocaleString() + " 원";
    },

    /**
     * 타임스탬프를 로컬 시간 문자열로 변환합니다.
     */
    formatTimestampPort(ts) {
        if (!ts) return '-';
        const ms = ts < 10000000000 ? ts * 1000 : ts;
        return new Date(ms).toLocaleString();
    },



    /**
     * 자산 데이터를 거래소별로 그룹화 및 정렬하여 비중 차트 및 테이블 렌더링에 적합한 데이터로 가공합니다.
     */
    groupAssetsForAllocation(portfolioData, isBacktest = false, marketData = []) {
        const exchangeGroups = {};
        const colors = [
            '#FF6384', '#36A2EB', '#FFCE56', '#4BC0C0', '#9966FF', 
            '#FF9F40', '#C9CBCF', '#7BC225', '#FF4500', '#1E90FF'
        ];

        // 1. 보유 포지션 그룹화
        portfolioData.positions.forEach((pos, idx) => {
            const ex = pos.exchange ? pos.exchange.toLowerCase() : 'upbit';
            if (!exchangeGroups[ex]) {
                exchangeGroups[ex] = {
                    exchange: ex,
                    assets: [],
                    totalValue: 0
                };
            }
            
            let currentPrice = pos.avg_price;
            if (isBacktest) {
                currentPrice = pos.current_price || pos.avg_price;
            } else {
                const coin = marketData.find(c => c.market === pos.symbol);
                currentPrice = coin ? coin.trade_price : pos.avg_price;
            }
            
            const value = pos.quantity * currentPrice;
            if (value > 0) {
                const displaySymbol = pos.symbol.replace(/^(KRW-|UPB-|KIS-)/, '');
                
                // 전역 state.symbolNames 캐시에서 한글 종목명 조회 (키: "upbit:BTC" 또는 "kis:005930" 형태)
                const cacheKey = `${ex}:${displaySymbol}`;
                const cachedName = (typeof state !== 'undefined' && state.symbolNames) ? state.symbolNames[cacheKey] : null;
                const koreanName = cachedName || displaySymbol;

                exchangeGroups[ex].assets.push({
                    symbol: pos.symbol,
                    label: displaySymbol, 
                    koreanName: koreanName,
                    value: value,
                    color: colors[idx % colors.length],
                    exchange: ex
                });
                exchangeGroups[ex].totalValue += value;
            }
        });

        // 2. 거래소별 잔여 현금 처리
        const exchangeCashMap = portfolioData.exchange_cash || {};
        const exCashKeys = Object.keys(exchangeCashMap);

        if (exCashKeys.length > 0) {
            exCashKeys.forEach(ex => {
                const exCash = exchangeCashMap[ex];
                if (exCash > 0) {
                    if (!exchangeGroups[ex]) {
                        exchangeGroups[ex] = { exchange: ex, assets: [], totalValue: 0 };
                    }
                    exchangeGroups[ex].assets.push({
                        symbol: null,
                        label: 'CASH',
                        koreanName: '보유 현금',
                        value: exCash,
                        color: '#475569', // Slate 600 계열 중립 톤
                        exchange: ex
                    });
                    exchangeGroups[ex].totalValue += exCash;
                }
            });
        } else if (portfolioData.cash > 0) {
            const defaultEx = Object.keys(exchangeGroups).length > 0 ? Object.keys(exchangeGroups)[0] : 'upbit';
            if (!exchangeGroups[defaultEx]) {
                exchangeGroups[defaultEx] = {
                    exchange: defaultEx,
                    assets: [],
                    totalValue: 0
                };
            }
            exchangeGroups[defaultEx].assets.push({
                symbol: null,
                label: 'CASH',
                koreanName: '보유 현금',
                value: portfolioData.cash,
                color: '#475569',
                exchange: defaultEx
            });
            exchangeGroups[defaultEx].totalValue += portfolioData.cash;
        }

        // 3. 자산 비중 정렬
        Object.values(exchangeGroups).forEach(group => {
            group.assets.sort((a, b) => b.value - a.value);
        });

        return exchangeGroups;
    }
};

if (typeof module !== 'undefined' && module.exports) {
    module.exports = PortfolioAdapter;
} else {
    window.PortfolioAdapter = PortfolioAdapter;
}
