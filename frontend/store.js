/**
 * Upbit Terminal 전역 상태 저장소 (State Store)
 */
(function() {
    // 초기 기본 상태 정의
    const _state = {
        candles: [],
        currentCandle: null,
        currentInterval: 60,
        isLoaded: false,
        currentSymbol: "BTC",
        currentExchange: "upbit",
        currentMarketTab: "upbit",
        currentPortfolioId: 'default',
        currentSimPortfolioId: null,
        currentLivePortfolioId: '1',
        ws: null,
        wsConnected: false,
        isExplorerMode: false,
        explorerCenterIdx: null,
        savedBarSpacing: 30,
        isAutoTrading: false,
        alertMarkerTs: null,
        isAlertEnabled: false,
        currentPortfolioData: null,
        autoScroll: true,
        activeAssetDetail: null,
        alertFilter: 'high',
        alertHistory: [],
        symbolNames: {},
        collectorStatuses: {},
        marketSortKey: null,
        marketSortOrder: 'none'
    };


    // 상태 변경 이벤트 감지를 위한 리스너 목록
    const _listeners = [];

    const Store = {
        // 특정 키의 상태 획득
        get(key) {
            return _state[key];
        },

        // 전체 상태 객체 반환 (읽기 전용 참조 제공)
        getState() {
            return _state;
        },

        // 단일 키 상태 업데이트 및 리스너 전파
        set(key, value) {
            const prev = _state[key];
            if (prev === value) return;
            _state[key] = value;
            this._notify(key, value, prev);
        },

        // 여러 키 상태 일괄 업데이트
        update(updates) {
            const changed = [];
            for (const [key, value] of Object.entries(updates)) {
                const prev = _state[key];
                if (prev !== value) {
                    _state[key] = value;
                    changed.push({ key, value, prev });
                }
            }
            changed.forEach(({ key, value, prev }) => {
                this._notify(key, value, prev);
            });
        },

        // 상태 변경 이벤트 구독
        subscribe(callback) {
            _listeners.push(callback);
            // 구독 취소 헬퍼
            return () => {
                const idx = _listeners.indexOf(callback);
                if (idx !== -1) _listeners.splice(idx, 1);
            };
        },

        _notify(key, value, prev) {
            _listeners.forEach(cb => {
                try {
                    cb(key, value, prev);
                } catch (e) {
                    console.error("[Store Event Error]", e);
                }
            });
        }
    };

    // 전역 노출
    window.Store = Store;

    // 하위 호환성 보장: window.state를 Proxy로 래핑하여 
    // 레거시 코드의 직접 대입/참조(state.currentSymbol = ...)가 정상 작동하며 스토어 이벤트를 발행하게 만듦
    window.state = new Proxy(_state, {
        set(target, prop, value) {
            Store.set(prop, value);
            return true;
        },
        get(target, prop) {
            return target[prop];
        }
    });
})();
