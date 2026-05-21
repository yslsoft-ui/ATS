/**
 * DataStream - 실시간 웹소켓 시세 스트림 관리 모듈
 * 
 * 백엔드 서버의 웹소켓 포트(/ws) 연결 관리, 수명 주기, 자동 재연결,
 * 그리고 실시간 구독 종목 제어 책임을 수행합니다.
 */
const DataStream = (() => {
    let ws = null;
    let reconnectTimer = null;
    let onTickCallback = null;
    let isExplicitlyClosed = false;

    /**
     * 웹소켓 연결 개설
     */
    function _connect() {
        if (ws) {
            try {
                ws.close();
            } catch (e) {}
        }

        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/ws`;
        
        console.log(`[DataStream] 웹소켓 연결 시도: ${wsUrl}`);
        ws = new WebSocket(wsUrl);

        ws.onopen = () => {
            console.log('[DataStream] 웹소켓 연결 성공');
            isExplicitlyClosed = false;
            
            // 전역 스토어 상태 변경
            if (window.state) {
                window.state.wsConnected = true;
                
                // 연결 즉시 현재 선택된 종목/거래소 시세 구독 실행
                const currentSym = window.state.currentSymbol;
                const currentExch = window.state.currentExchange;
                if (currentSym && currentExch) {
                    subscribe(currentSym, currentExch);
                }
            }
        };

        ws.onclose = (event) => {
            console.log(`[DataStream] 웹소켓 연결 종료 (코드: ${event.code})`);
            if (window.state) {
                window.state.wsConnected = false;
            }

            // 명시적 닫기가 아니면 5초 지연 후 재연결 시도
            if (!isExplicitlyClosed) {
                _scheduleReconnect();
            }
        };

        ws.onerror = (error) => {
            console.error('[DataStream] 웹소켓 오류 발생:', error);
            if (window.state) {
                window.state.wsConnected = false;
            }
        };

        ws.onmessage = (event) => {
            if (!onTickCallback) return;
            try {
                const tick = JSON.parse(event.data);
                onTickCallback(tick);
            } catch (e) {
                console.error('[DataStream] 메시지 수신/파싱 오류:', e);
            }
        };
    }

    /**
     * 재연결 지연 스케줄링
     */
    function _scheduleReconnect() {
        if (reconnectTimer) return; // 이미 예약 중이면 패스
        console.log('[DataStream] 5초 후에 재연결을 시도합니다...');
        reconnectTimer = setTimeout(() => {
            reconnectTimer = null;
            _connect();
        }, 5000);
    }

    /**
     * 특정 종목 및 거래소 실시간 구독 전송
     * @param {string} symbol - 종목 코드 (예: BTC, ETH)
     * @param {string} exchange - 거래소명 (예: upbit, bithumb)
     */
    function subscribe(symbol, exchange) {
        if (!ws || ws.readyState !== WebSocket.OPEN) {
            console.warn('[DataStream] 웹소켓이 열려있지 않아 구독 메시지 전송을 대기합니다.');
            return;
        }

        const msg = JSON.stringify({
            subscribe: symbol,
            exchange: exchange
        });
        
        console.log(`[DataStream] 실시간 구독 메시지 전송: ${symbol} (${exchange})`);
        ws.send(msg);
    }

    return {
        /**
         * 스트리밍 모듈 초기화 및 연결 가동
         * @param {Function} onTick - 실시간 데이터를 전달받을 콜백 함수
         */
        initialize: (onTick) => {
            onTickCallback = onTick;
            isExplicitlyClosed = false;
            _connect();
        },

        /**
         * 수동으로 특정 종목 구독 전환
         */
        subscribe: (symbol, exchange) => {
            subscribe(symbol, exchange);
        },

        /**
         * 웹소켓 연결 차단 및 자동 재연결 방지
         */
        disconnect: () => {
            isExplicitlyClosed = true;
            if (reconnectTimer) {
                clearTimeout(reconnectTimer);
                reconnectTimer = null;
            }
            if (ws) {
                try {
                    ws.close();
                } catch (e) {}
                ws = null;
            }
            if (window.state) {
                window.state.wsConnected = false;
            }
            console.log('[DataStream] 웹소켓 연결이 명시적으로 종료되었습니다.');
        }
    };
})();

// 전역 window 바인딩
window.DataStream = DataStream;
