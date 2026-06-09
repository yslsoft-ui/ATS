/**
 * APIClient - 백엔드 REST API 통신 전담 모듈
 * 
 * 모든 HTTP 요청 엔드포인트 URL과 메소드를 캡슐화하며, 
 * 호출한 모듈에서 개별적으로 예외 처리를 수행할 수 있도록 에러를 전파(throw)합니다.
 */
const APIClient = (() => {

    /**
     * API 호출을 위한 공통 private 헬퍼 함수
     * @param {string} url - API 엔드포인트
     * @param {object} options - Fetch 옵션
     * @returns {Promise<any>} API 응답 JSON 객체
     */
    async function _fetchAPI(url, options = {}) {
        try {
            const response = await fetch(url, options);
            if (!response.ok) {
                let errMsg = `${response.status} ${response.statusText}`;
                try {
                    const errData = await response.json();
                    if (errData && errData.detail) {
                        errMsg = errData.detail;
                    }
                } catch (e) {
                    // JSON 파싱 실패 또는 바디가 없을 경우 기본 statusText 사용
                }
                throw new Error(`API 오류: ${errMsg}`);
            }
            return await response.json();
        } catch (error) {
            console.error(`[API Client Error] ${options.method || 'GET'} ${url}:`, error);
            throw error;
        }
    }

    return {
        /**
         * 거래 가능한 심볼 목록 조회
         */
        fetchSymbols: () => _fetchAPI('/symbols'),

        /**
         * 특정 마켓/심볼/인터벌의 과거 캔들 시세 조회
         */
        fetchCandleHistory: (exchange, symbol, interval, limit = 10000) => 
            _fetchAPI(`/candles?exchange=${exchange}&symbol=${symbol}&interval=${interval}&limit=${limit}`),
            
        /**
         * 특정 시간 범위(Range)의 과거 캔들 데이터 조회 (지연 로딩 지원)
         */
        fetchCandlesRange: (exchange, symbol, interval, startTs, endTs, limit = 1000) => 
            _fetchAPI(`/candles?exchange=${exchange}&symbol=${symbol}&interval=${interval}&start_ts=${startTs}&end_ts=${endTs}&limit=${limit}`),

        /**
         * 특정 마켓/심볼의 최근 실시간 체결 내역 조회
         */
        fetchRecentTrades: (exchange, symbol, limit = 10) => 
            _fetchAPI(`/trades?exchange=${exchange}&symbol=${symbol}&limit=${limit}`),

        /**
         * 실시간 급등/체결 알림 이력 조회
         */
        fetchAlertHistory: () => _fetchAPI('/alerts'),

        /**
         * 모든 알림 이력 영구 삭제
         */
        clearAlertHistory: () => _fetchAPI('/api/alerts', { method: 'DELETE' }),

        /**
         * 전략 매개변수 설정 및 활성화 목록 조회
         */
        fetchStrategies: () => _fetchAPI('/api/strategies'),

        /**
         * 특정 전략의 파라미터 값 저장
         */
        saveStrategyParams: (strategyId, params) => 
            _fetchAPI(`/api/strategies/${strategyId}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(params)
            }),

        /**
         * 특정 전략의 활성화 여부 상태 토글
         */
        toggleStrategyStatus: (strategyId, currentEnabled) => {
            const endpoint = currentEnabled ? `/api/strategies/${strategyId}` : `/api/strategies/${strategyId}/enable`;
            const method = currentEnabled ? 'DELETE' : 'POST';
            return _fetchAPI(endpoint, { method });
        },

        /**
         * 사용 가능한 가상 포트폴리오 목록 조회
         */
        fetchPortfolioList: () => _fetchAPI('/api/portfolios'),

        /**
         * 특정 포트폴리오의 잔고 및 보유 자산 조회
         */
        fetchPortfolio: (portfolioId) => _fetchAPI(`/api/portfolio?portfolio_id=${portfolioId}`),

        /**
         * 특정 포트폴리오 내 모든 종목 긴급 청산
         */
        panicSellPortfolio: (portfolioId) => 
            _fetchAPI(`/api/portfolio/${portfolioId}/panic`, { method: 'POST' }),

        fetchRealAssets: (exchange = 'upbit', mode = 'active', sync = false) => 
            _fetchAPI(`/api/exchanges/${exchange}/assets?mode=${mode}&sync=${sync}`),

        /**
         * 실제 거래소의 최근 주문/체결 내역 조회
         */
        fetchRealOrderHistory: (exchange, symbol, limit = '') => 
            _fetchAPI(`/api/exchanges/${exchange}/orders?symbol=${symbol}${limit ? `&limit=${limit}` : ''}`),

        /**
         * 실제 거래소의 종목 호가창(Orderbook) 조회
         */
        fetchOrderbook: (exchange, symbol) => _fetchAPI(`/api/exchanges/${exchange}/orderbook/${symbol}`),

        /**
         * 실제 거래소에 매수/매도 주문 요청 제출
         */
        placeRealOrder: (exchange, orderData) => 
            _fetchAPI(`/api/exchanges/${exchange}/order`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(orderData)
            }),

        /**
         * 전체 실시간 마켓 현황 및 가격 통계 조회
         */
        fetchMarketData: () => _fetchAPI(`/market`),

        /**
         * 데이터 수집기 가동 상태 정보 조회
         */
        fetchCollectorStatus: () => _fetchAPI('/collector/status'),

        /**
         * 최근의 시스템 운영 및 시장정지 이력 목록을 반환합니다.
         */
        fetchSystemEvents: (limit = 20) => _fetchAPI(`/collector/system-events?limit=${limit}`),

        /**
         * 특정 거래소의 수집기 제어 (start / stop)
         */
        controlCollector: (exchange, action) => 
            _fetchAPI(`/collector/${action}/${exchange}`, { method: 'POST' }),

        /**
         * 수집 데몬 프로세스 자체를 자가 재기동
         */
        restartCollectorDaemon: () => 
            _fetchAPI('/collector/restart-daemon', { method: 'POST' }),

        /**
         * 전략 데몬 프로세스 자체를 자가 재기동
         */
        restartStrategyDaemon: () => 
            _fetchAPI('/api/strategies/restart-daemon', { method: 'POST' }),


        /**
         * 데이터 정리(Cleanup) 실행 전 예상 데이터 수 소거 미리보기
         */
        fetchCleanupPreview: (date) => _fetchAPI(`/data/cleanup/preview?date=${date}`),

        /**
         * 특정 기준 시간 이전의 과거 데이터 정리 실행
         */
        runCleanup: (date, limit = 20000) => _fetchAPI(`/data/cleanup?date=${date}&limit=${limit}`, { method: 'POST' }),

        /**
         * 백엔드 내부 지연 대기열 큐 모니터링 현황 조회
         */
        fetchSystemQueues: () => _fetchAPI('/api/system/queues'),

        /**
         * DB에 누락되었으나 틱으로 복구된 캔들 목록 조회
         */
        fetchRestoredCandles: (exchange, symbol, limitMinutes = 1440) => {
            let url = `/restored-candles?limit_minutes=${limitMinutes}`;
            if (exchange && exchange !== 'all') url += `&exchange=${exchange}`;
            if (symbol && symbol !== 'all') url += `&symbol=${symbol}`;
            return _fetchAPI(url);
        },

        /**
         * KIS 순위분석 항목 목록 조회
         */
        fetchRankingTypes: () => _fetchAPI('/market/ranking/types'),

        /**
         * 지정한 TR_ID의 순위 분석 결과 조회
         */
        fetchRankingResult: (trId) => _fetchAPI(`/market/ranking/fetch?tr_id=${trId}`),

        /**
         * KIS 수집 종목을 토글 (추가/제거)
         */
        toggleKisSymbol: (code, name, isActive) => 
            _fetchAPI('/market/symbols/kis/toggle', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ code, name, ...(isActive !== undefined && { is_active: isActive }) })
            }),

        /**
         * 백테스트용 기본 전략 및 파라미터 구성 로드
         */
        fetchBacktestDefaultConfigs: () => _fetchAPI('/api/backtest/default-configs'),

        /**
         * 리플레이 백테스트 실행
         */
        runBacktest: (data) => 
            _fetchAPI('/api/backtest/run', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(data)
            }),

        /**
         * 백테스트 이력(세트) 목록 조회
         */
        fetchBacktestHistory: () => _fetchAPI(`/api/backtest/history?t=${Date.now()}`),



        /**
         * 특정 백테스트 이력 영구 삭제
         */
        deleteBacktestHistory: (portfolioId) => _fetchAPI(`/api/backtest/history/${portfolioId}`, { method: 'DELETE' }),

        /**
         * 전체 백테스트 이력 영구 삭제
         */
        clearAllBacktestHistory: () => _fetchAPI('/api/backtest/history', { method: 'DELETE' }),

        /**
         * 실시간 모의투자 세션 시작
         */
        startPortfolioSession: (initialCash, strategies) => 
            _fetchAPI('/api/portfolio/start', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ initial_cash: initialCash, strategies })
            }),

        /**
         * 실시간 모의투자 세션 종료 (미실현 자산 평가가 박제 마감)
         */
        endPortfolioSession: (portfolioId) => 
            _fetchAPI(`/api/portfolio/${portfolioId}/end`, { method: 'POST' }),

        /**
         * 특정 전략의 상세 상태 조회
         */
        fetchStrategyDetail: (strategyId) => _fetchAPI(`/api/strategies/${strategyId}`),

        /**
         * 특정 전략의 제안 목록 조회
         */
        fetchProposals: (strategyId, includePruned = true) => _fetchAPI(strategyId ? `/api/proposals?strategy_id=${strategyId}&include_pruned=${includePruned}` : `/api/proposals?include_pruned=${includePruned}`),

        /**
         * 제안 승인 및 적용
         */
        approveProposal: (proposalId) => _fetchAPI(`/api/proposals/${proposalId}/approve`, { method: 'POST' }),

        /**
         * 특정 전략 지정 버전 롤백
         */
        rollbackStrategy: (strategyId, versionId) => _fetchAPI(`/api/strategies/${strategyId}/rollback/${versionId}`, { method: 'POST' }),

        /**
         * 특정 전략의 성과 스냅샷 리스트 조회
         */
        fetchStrategySnapshots: (strategyId) => _fetchAPI(`/api/strategies/${strategyId}/snapshots`),

        /**
         * 특정 전략의 파라미터 변경 이력 목록 조회
         */
        fetchStrategyHistory: (strategyId) => _fetchAPI(`/api/strategies/${strategyId}/history`)
    };
})();


// 전역 window 바인딩
window.APIClient = APIClient;
