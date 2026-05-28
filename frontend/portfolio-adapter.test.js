/**
 * Upbit Terminal Portfolio Data Adapter Unit Tests
 */

const PortfolioAdapter = require('./portfolio-adapter');

describe('PortfolioAdapter Unit Tests', () => {
    
    // 1. formatKoreanAmount 테스트
    describe('formatKoreanAmount', () => {
        test('undefined, null, NaN 처리', () => {
            expect(PortfolioAdapter.formatKoreanAmount(undefined)).toBe('0원');
            expect(PortfolioAdapter.formatKoreanAmount(null)).toBe('0원');
            expect(PortfolioAdapter.formatKoreanAmount(NaN)).toBe('0원');
        });

        test('만 원 미만 정수 포맷팅', () => {
            expect(PortfolioAdapter.formatKoreanAmount(500)).toBe('500원');
            expect(PortfolioAdapter.formatKoreanAmount(9999)).toBe('9,999원');
        });

        test('만 원 이상 억 원 미만 포맷팅', () => {
            expect(PortfolioAdapter.formatKoreanAmount(10000)).toBe('1만 원');
            expect(PortfolioAdapter.formatKoreanAmount(15000)).toBe('1.5만 원');
            expect(PortfolioAdapter.formatKoreanAmount(99500000)).toBe('9950만 원');
        });

        test('억 원 이상 포맷팅', () => {
            expect(PortfolioAdapter.formatKoreanAmount(100000000)).toBe('1억 원');
            expect(PortfolioAdapter.formatKoreanAmount(125000000)).toBe('1.25억 원');
            expect(PortfolioAdapter.formatKoreanAmount(100050000000)).toBe('1000.50억 원');
        });
    });

    // 2. formatPricePort 테스트
    describe('formatPricePort', () => {
        test('예외값 처리', () => {
            expect(PortfolioAdapter.formatPricePort(undefined)).toBe('-');
            expect(PortfolioAdapter.formatPricePort(null)).toBe('-');
            expect(PortfolioAdapter.formatPricePort(NaN)).toBe('-');
        });

        test('100 미만 소수점 유지 포맷팅', () => {
            expect(PortfolioAdapter.formatPricePort(5)).toBe('5 원');
            expect(PortfolioAdapter.formatPricePort(99.5)).toBe('99.50 원');
            expect(PortfolioAdapter.formatPricePort(1.234)).toBe('1.23 원');
        });

        test('100 이상 정수 반올림 및 콤마 포맷팅', () => {
            expect(PortfolioAdapter.formatPricePort(100)).toBe('100 원');
            expect(PortfolioAdapter.formatPricePort(1500.6)).toBe('1,501 원');
            expect(PortfolioAdapter.formatPricePort(5000000)).toBe('5,000,000 원');
        });
    });

    // 3. transformRealtimeToPerformance 테스트 (현재 지원 중단된 인터페이스로 제외 처리)
    /*
    describe('transformRealtimeToPerformance', () => {
        test('로우 모의투자 데이터 가공 매핑 검증', () => {
            const mockRawData = {
                id: 'sim_test_123',
                name: '테스트 모의투자',
                initial_cash: 10000000,
                total_value: 10500000,
                cash: 5000000,
                exchanges: [
                    { exchange_id: 'upbit', initial_cash: 10000000, cash: 5000000 }
                ],
                positions: [
                    { symbol: 'KRW-BTC', exchange: 'upbit', quantity: 0.1, avg_price: 50000000 }
                ],
                history: [
                    { symbol: 'KRW-BTC', exchange: 'upbit', side: 'BUY', price: 50000000, quantity: 0.1, fee: 2500, timestamp: 1716670000 }
                ]
            };

            const result = PortfolioAdapter.transformRealtimeToPerformance(mockRawData, '5.00');

            expect(result.portfolio_id).toBe('sim_test_123');
            expect(result.name).toBe('테스트 모의투자');
            expect(result.summary.initial_cash).toBe(10000000);
            expect(result.summary.final_value).toBe(10500000);
            expect(result.summary.profit).toBe(500000);
            expect(result.summary.roi).toBe('5.00');
            expect(result.summary.fee).toBe(2500);
            expect(result.summary.trade_count).toBe(1);

            expect(result.exchange_initial_cash.upbit).toBe(10000000);
            expect(result.results.length).toBe(1);
            expect(result.results[0].symbol).toBe('KRW-BTC');
            expect(result.results[0].trades.length).toBe(1);
            expect(result.results[0].trades[0].side).toBe('BUY');
            expect(result.results[0].trades[0].price).toBe(50000000);
        });
    });
    */

    // 4. groupAssetsForAllocation 테스트
    describe('groupAssetsForAllocation', () => {
        beforeAll(() => {
            // 전역 state 및 symbolNames 캐시 모킹
            global.state = {
                symbolNames: {
                    'upbit:BTC': '비트코인',
                    'kis:005930': '삼성전자'
                }
            };
        });

        afterAll(() => {
            delete global.state;
        });

        test('보유 자산 및 현금 분배 그룹화 검증', () => {
            const mockPortfolioData = {
                positions: [
                    { symbol: 'KRW-BTC', exchange: 'upbit', quantity: 0.1, avg_price: 50000000 },
                    { symbol: 'KIS-005930', exchange: 'kis', quantity: 10, avg_price: 70000 }
                ],
                exchange_cash: {
                    upbit: 3000000,
                    kis: 2000000
                },
                cash: 5000000
            };

            const groups = PortfolioAdapter.groupAssetsForAllocation(mockPortfolioData, false, []);

            // 업비트와 국내주식 2개 그룹 생성 검증
            expect(groups.upbit).toBeDefined();
            expect(groups.kis).toBeDefined();

            // 업비트 그룹 가치 계산 검증 (BTC 500만원 + 현금 300만원 = 800만원)
            expect(groups.upbit.totalValue).toBe(8000000);
            // 업비트 자산 리스트에 BTC와 CASH가 정상 정렬되었는지 (금액이 큰 BTC가 위로)
            expect(groups.upbit.assets[0].label).toBe('BTC');
            expect(groups.upbit.assets[1].label).toBe('CASH');

            // KIS 그룹 가치 계산 검증 (삼성전자 70만원 + 현금 200만원 = 270만원)
            expect(groups.kis.totalValue).toBe(2700000);
            expect(groups.kis.assets[0].label).toBe('CASH'); // 금액이 큰 CASH(200만)가 위로
            expect(groups.kis.assets[1].label).toBe('005930');

            // 한글 종목명 캐시 매핑 검증
            expect(groups.upbit.assets[0].koreanName).toBe('비트코인');
            expect(groups.kis.assets[1].koreanName).toBe('삼성전자');
        });
    });
});
