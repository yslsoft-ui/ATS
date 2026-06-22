// (state 객체는 store.js에서 Proxy로 정의되어 전역에 노출됨)

const chartDiv = document.getElementById('main-chart');

// initLWChart는 chart.js 내부의 ChartEngine.initialize로 이관되었습니다.

// --- 과거 데이터 불러오기 (PULL) ---
async function loadHistory() {
    state.isLoaded = false;
    try {
        const history = await APIClient.fetchCandleHistory(state.currentExchange, state.currentSymbol, state.currentInterval, 10000);

        if (history && history.length > 0) {
            // 백엔드가 이미 계산해서 전달한 지표 리스트 그대로 활용
            state.currentCandle = history.pop();
            state.candles = history;
        } else {
            state.candles = [];
            state.currentCandle = null;
        }

        // 차트 엔진에 렌더링 지시
        ChartEngine.render(state.candles, state.currentCandle);

        // 헤더 현재가/변동률 초기 표시 (첫 틱 도달 전까지 공백이 되는 현상 방지)
        const lastCandle = state.currentCandle || (state.candles.length ? state.candles[state.candles.length - 1] : null);
        if (lastCandle) {
            const priceEl = document.getElementById('price-metric');
            const changeEl = document.getElementById('change-metric');
            if (priceEl) priceEl.innerText = formatPrice(lastCandle.close);
            if (changeEl && lastCandle.open) {
                const changePercent = ((lastCandle.close - lastCandle.open) / lastCandle.open * 100);
                const formatted = formatRate(changePercent);
                changeEl.innerText = formatted.text;
                changeEl.style.color = getTrendColor(changePercent);
            }
        }

        if (state.autoScroll) {
            ChartEngine.exitExplorerMode();
        } else if (state.alertMarkerTs) {
            const all = [...state.candles, state.currentCandle].filter(c => c);
            const idx = all.findIndex(c => c.timestamp >= state.alertMarkerTs);
            if (idx !== -1) {
                const offset = idx - all.length + 10;
                ChartEngine.scrollToPosition(offset, false);
            }
        }
        state.isLoaded = true;
    } catch (e) {
        console.error("History load failed", e);
    }
}

// --- 과거 데이터 비동기 추가 지연 로딩 (Lazy Loading) ---
state.isHistoryLoading = false;

async function loadMoreHistory() {
    if (state.isHistoryLoading) return;
    if (!state.candles || state.candles.length === 0) return;

    state.isHistoryLoading = true;
    console.log("[Lazy Loading] Fetching older history...");

    try {
        const oldestCandle = state.candles[0];
        let oldestTs = parseInt(oldestCandle.timestamp);
        if (oldestTs > 9999999999) {
            oldestTs = Math.floor(oldestTs / 1000);
        }

        // 이전 30분(1800초) 분량의 캔들 데이터를 요청
        const fetchDuration = 1800; // 30분 (초)
        const startTs = oldestTs - fetchDuration;
        const endTs = oldestTs - 1;

        const additionalHistory = await APIClient.fetchCandlesRange(
            state.currentExchange,
            state.currentSymbol,
            state.currentInterval,
            startTs,
            endTs,
            1000
        );

        if (additionalHistory && additionalHistory.length > 0) {
            console.log(`[Lazy Loading] Loaded ${additionalHistory.length} older candles.`);
            
            // 기존 캔들 배열 맨 앞에 안전하게 머지
            state.candles = [...additionalHistory, ...state.candles];
            
            // 차트 렌더링
            ChartEngine.render(state.candles, state.currentCandle);
        } else {
            console.log("[Lazy Loading] No older history found in DB.");
        }
    } catch (e) {
        console.error("[Lazy Loading] Failed to load older history", e);
    } finally {
        // 스크롤 중 휠 바운싱으로 인한 연속 다중 호출 방지 락 해제 지연
        setTimeout(() => {
            state.isHistoryLoading = false;
        }, 800);
    }
}

// --- 실시간 캔들 생성 및 업데이트 로직 (PUSH) ---
function processTick(tick) {
    if (tick.type === 'toast_alert') {
        const alertType = tick.event_type && (tick.event_type.includes('delisted') || tick.event_type.includes('delisting')) ? 'error' : 'success';
        showToast(tick.message, alertType, true);
        if (typeof checkUpcomingAssetEvents === 'function') {
            checkUpcomingAssetEvents();
        }
        return;
    }

    if (typeof OverviewEngine !== 'undefined') {
        OverviewEngine.update(tick);
    }

    if (tick.type === 'system_event') {
        const isErrorOrSuspended = tick.event_type.includes('ERROR') || tick.event_type.includes('SUSPENDED');
        const alertType = isErrorOrSuspended ? 'error' : 'success';
        
        let displayMsg = `[${tick.event_type}] ${tick.message}`;
        if (tick.event_type === 'EXCHANGE_SUSPENDED') {
            displayMsg = `⚠️ 시장 정지 감지: ${tick.message}`;
        } else if (tick.event_type === 'EXCHANGE_RESUMED') {
            displayMsg = `🚀 시장 정상 복구: ${tick.message}`;
        }
        
        showToast(displayMsg, alertType, !isErrorOrSuspended);

        // 설정 탭이 활성화되어 있다면 테이블 즉시 갱신
        if (typeof ViewRouter !== 'undefined' && ViewRouter.getActiveView() === 'settings-view') {
            if (typeof updateSystemEvents === 'function') {
                updateSystemEvents();
            }
        }
        // [NEW] 데몬 상태 모니터링 통합 뷰가 활성화되어 있다면 각 활성 탭별 감사 로그 즉시 갱신
        if (typeof ViewRouter !== 'undefined' && ViewRouter.getActiveView() === 'daemon-monitoring-view' && typeof DaemonMonitoringView !== 'undefined') {
            const activeTab = DaemonMonitoringView.getActiveTab();
            if (activeTab === 'collector') {
                if (typeof CollectorView !== 'undefined' && typeof CollectorView.loadEvents === 'function') {
                    CollectorView.loadEvents();
                }
            } else if (activeTab === 'strategy') {
                if (typeof StrategyDaemonView !== 'undefined' && typeof StrategyDaemonView.loadEvents === 'function') {
                    StrategyDaemonView.loadEvents();
                }
            } else if (activeTab === 'evaluation') {
                if (typeof EvaluationDaemonView !== 'undefined') {
                    if (typeof EvaluationDaemonView.loadEvents === 'function') EvaluationDaemonView.loadEvents();
                    if (typeof EvaluationDaemonView.loadEvaluationsTable === 'function') EvaluationDaemonView.loadEvaluationsTable();
                    if (typeof EvaluationDaemonView.loadJobsTable === 'function') EvaluationDaemonView.loadJobsTable();
                }
            } else if (activeTab === 'cleanup') {
                if (typeof CleanupView !== 'undefined' && typeof CleanupView.loadEvents === 'function') {
                    CleanupView.loadEvents();
                }
            }
        }
        return;
    }

    // [NEW] 실시간 전략 데몬 상세 정보 수신 시 라우팅
    if (tick.type === 'strategy_daemon_detail') {
        if (typeof StrategyDaemonView !== 'undefined' && typeof StrategyDaemonView.handleDaemonDetail === 'function') {
            StrategyDaemonView.handleDaemonDetail(tick);
        }
        return;
    }

    // [NEW] 실시간 평가 데몬 상세 정보 수신 시 라우팅
    if (tick.type === 'evaluation_daemon_detail') {
        if (typeof EvaluationDaemonView !== 'undefined' && typeof EvaluationDaemonView.handleDaemonDetail === 'function') {
            EvaluationDaemonView.handleDaemonDetail(tick);
        }
        return;
    }

    // [NEW] 실시간 전략 제어 명령 완료 ACK 수신 시 라우팅
    if (tick.type === 'strategy_command_result') {
        if (typeof StrategyDaemonView !== 'undefined' && typeof StrategyDaemonView.handleCommandResult === 'function') {
            StrategyDaemonView.handleCommandResult(tick);
        }
        return;
    }

    // [NEW] 실시간 수집기 데몬 상세 정보 수신 시 라우팅
    if (tick.type === 'collector_daemon_detail') {
        if (typeof CollectorView !== 'undefined' && typeof CollectorView.handleDaemonDetail === 'function') {
            CollectorView.handleDaemonDetail(tick);
        }
        return;
    }

    // [NEW] 실시간 수집기 종목 동기화 수신 시 라우팅
    if (tick.type === 'collector_symbols_sync') {
        if (typeof CollectorView !== 'undefined' && typeof CollectorView.handleSymbolsSync === 'function') {
            CollectorView.handleSymbolsSync(tick);
        }
        return;
    }

    // [NEW] 실시간 제어 명령 완료 ACK 수신 시 라우팅
    if (tick.type === 'collector_command_result') {
        if (typeof CollectorView !== 'undefined' && typeof CollectorView.handleCommandResult === 'function') {
            CollectorView.handleCommandResult(tick);
        }
        return;
    }

    // [NEW] 실시간 클린업 데몬 상태 정보 수신 시 라우팅
    if (tick.type === 'market_cleanup_status') {
        if (typeof CleanupView !== 'undefined' && typeof CleanupView.handleStatusUpdate === 'function') {
            CleanupView.handleStatusUpdate(tick);
        }
        return;
    }

    // [NEW] 실시간 클린업 제어 명령 완료 ACK 수신 시 라우팅
    if (tick.type === 'cleanup_command_result') {
        if (typeof CleanupView !== 'undefined' && typeof CleanupView.handleCommandResult === 'function') {
            CleanupView.handleCommandResult(tick);
        }
        return;
    }

    if (tick.type === 'collector_status') {
        const current = { ...Store.get('collectorStatuses') };
        current[tick.exchange] = {
            is_running: tick.is_running,
            status: tick.status,
            status_reason: tick.status_reason,
            error: tick.error
        };
        Store.set('collectorStatuses', current);
        return;
    }

    if (tick.type === 'strategy_status') {
        if (tick.strategy_id !== undefined) {
            updateStrategyStatusUI(tick);
        } else {
            const current = { ...Store.get('collectorStatuses') };
            current.strategy = {
                is_running: tick.is_running,
                active_engines: tick.active_engines,
                error: tick.error
            };
            Store.set('collectorStatuses', current);
        }
        return;
    }

    if (tick.type === 'alert') {
        state.alertHistory.unshift(tick);
        if (state.alertHistory.length > 500) {
            state.alertHistory.pop();
        }
        
        const countEl = document.getElementById('alert-count');
        if (countEl) countEl.innerText = `${state.alertHistory.length}개 기록`;

        showAlert(tick);
        addAlertToTable(tick, true);
        
        if (tick.alert_type === 'trade') {
            loadPortfolio();
        }
        return;
    }

    if (!state.isLoaded) return;
    if (tick.exchange_id !== state.currentExchange || tick.code !== state.currentSymbol) return; 

    // --- 실시간 틱 기반 캔들 조립 및 보조지표 점진 연산 ---
    const timestamp = Math.floor(tick.trade_timestamp / 1000);
    const bucket = Math.floor(timestamp / state.currentInterval) * state.currentInterval;
    const price = tick.trade_price;
    const volume = tick.trade_volume;

    let nextCurrentCandle = null;

    if (!state.currentCandle || state.currentCandle.timestamp !== bucket) {
        if (state.currentCandle) {
            state.candles = [...state.candles, state.currentCandle];
            if (state.autoScroll && !state.isExplorerMode) {
                state.candles = state.candles.slice(-500);
            }
        }
        nextCurrentCandle = {
            timestamp: bucket,
            open: price, high: price, low: price, close: price,
            volume: volume,
            count: 1
        };
    } else {
        nextCurrentCandle = {
            ...state.currentCandle,
            high: Math.max(state.currentCandle.high, price),
            low: Math.min(state.currentCandle.low, price),
            close: price,
            volume: state.currentCandle.volume + volume,
            count: (state.currentCandle.count || 0) + 1
        };
    }

    // 1개 미완성 캔들 부분 계산 (상태 오염 방지)
    state.currentCandle = IndicatorEngine.calculateSingle(state.candles, nextCurrentCandle);

    updateMetrics(tick);
    updateTable(tick);
    updatePortfolioRealtime(tick);
    ChartEngine.render(state.candles, state.currentCandle);
}

function updatePortfolioRealtime(tick) {
    if (!state.currentPortfolioData || ViewRouter.getActiveView() !== 'portfolio-view') return;
    
    // 백테스트이거나 종료된 모의투자 세션(ended_at이 존재하는 경우)이면 실시간 갱신을 차단
    if (state.currentPortfolioData.type === 'backtest' || 
        state.currentPortfolioData.ended_at || 
        String(state.currentPortfolioData.id).startsWith('backtest_')) {
        return;
    }
    
    const position = state.currentPortfolioData.positions.find(p => p.symbol === tick.code);
    if (!position) return;

    const currentPrice = tick.trade_price;
    const profitPercent = ((currentPrice - position.avg_price) / position.avg_price * 100);
    const formatted = formatRate(profitPercent);
    
    const rows = document.querySelectorAll('#positions-tbody tr');
    rows.forEach(row => {
        if (row.cells[0]?.innerText === tick.code) {
            const rateCell = row.cells[3];
            rateCell.innerText = formatted.text;
            rateCell.className = `num ${formatted.className}`;
            rateCell.classList.add('value-updating');
            setTimeout(() => rateCell.classList.remove('value-updating'), 400);
        }
    });

    let newTotalValue = state.currentPortfolioData.cash;
    state.currentPortfolioData.positions.forEach(p => {
        const coin = (p.symbol === tick.code) ? { trade_price: currentPrice } : marketData.find(m => m.market === p.symbol);
        const price = coin ? coin.trade_price : p.avg_price;
        newTotalValue += p.quantity * price;
    });

    const totalValueEl = document.getElementById('port-total-value');
    const prevValue = parseFloat(totalValueEl.innerText.replace(/,/g, '')) || newTotalValue;
    
    // 갱신 임계값을 1원 이상으로 상향 조정 (불필요한 미세 수수료/소수 시세 갱신 방지)
    if (Math.abs(newTotalValue - prevValue) >= 1) {
        totalValueEl.innerText = Math.round(newTotalValue).toLocaleString();
        totalValueEl.classList.add('value-updating');
        setTimeout(() => totalValueEl.classList.remove('value-updating'), 400);
        
        const initialValue = 100000000; // 예시 원금
        const totalRoiPercent = ((newTotalValue - initialValue) / initialValue * 100);
        const formattedRoi = formatRate(totalRoiPercent);
        const roiEl = document.getElementById('port-total-roi');
        roiEl.innerText = formattedRoi.text;
        roiEl.className = `value ${formattedRoi.className}`;
    }
}

// --- UI 및 차트 업데이트 ---
function updateMetrics(tick) {
    const priceEl = document.getElementById('price-metric');
    const changeEl = document.getElementById('change-metric');
    if (!priceEl || !changeEl) return;

    const currentPrice = tick.trade_price;
    const prevPrice = parseFloat(priceEl.innerText.replace(/,/g, '')) || currentPrice;

    priceEl.innerText = formatPrice(currentPrice);
    priceEl.style.color = getPriceColor(currentPrice, prevPrice);

    // 거래소 공통 전일 대비율(signed_change_rate) 최우선 적용, 없으면 업비트 방식 폴백 계산
    let changePercent = 0.0;
    if (tick.signed_change_rate !== undefined && tick.signed_change_rate !== null) {
        changePercent = tick.signed_change_rate * 100;
    } else if (tick.change_price && tick.prev_closing_price) {
        changePercent = (tick.change_price / tick.prev_closing_price) * 100;
    }

    const formatted = formatRate(changePercent);
    changeEl.innerText = formatted.text;
    changeEl.style.color = getTrendColor(changePercent);
}

function updateHeaderInfo(exchange, symbol) {
    const ticker = symbol;

    const iconEl = document.getElementById('header-coin-icon');
    const krNameEl = document.getElementById('current-symbol-kr');
    const codeEl = document.getElementById('current-symbol-code');

    if (!krNameEl || !codeEl) return;

    // 1순위: state.symbolNames (loadSymbols에서 전종목 적재됨)
    // 2순위: window.marketData (마켓 탭 로드 후 사용 가능)
    // 3순위: ticker 코드 그대로
    const key = `${exchange}:${symbol}`;
    const nameFromSymbols = state.symbolNames && state.symbolNames[key];
    const nameFromMarket = (window.marketData || []).find(c => c.exchange === exchange && c.market === symbol)?.korean_name;
    const krName = nameFromSymbols || nameFromMarket || ticker;

    krNameEl.innerText = krName;

    if (iconEl) {
        const fallbackSvg = `data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' width='24' height='24'><circle cx='12' cy='12' r='10' fill='%231E293B' stroke='%234b5563' stroke-width='1'/><text x='50%' y='62%' font-size='9' font-family='sans-serif' font-weight='bold' fill='%2394A3B8' text-anchor='middle'>${ticker.slice(0, 3)}</text></svg>`;

        if (exchange === 'upbit') {
            iconEl.src = `https://static.upbit.com/logos/${ticker}.png`;
            iconEl.style.display = 'block';
            iconEl.onerror = () => {
                iconEl.onerror = null;
                iconEl.src = fallbackSvg;
            };
        } else if (exchange === 'bithumb') {
            iconEl.src = `https://static.upbit.com/logos/${ticker.toUpperCase()}.png`;
            iconEl.style.display = 'block';
            iconEl.onerror = () => {
                iconEl.onerror = null;
                iconEl.src = fallbackSvg;
            };
        } else if (exchange === 'kis') {
            iconEl.src = `https://ssl.pstatic.net/imgstock/fn/real/logo/png/stock/Stock${ticker}.png`;
            iconEl.style.display = 'block';
            iconEl.onerror = () => {
                iconEl.onerror = null;
                iconEl.src = fallbackSvg;
            };
        } else {
            iconEl.style.display = 'none';
        }
    }
    codeEl.innerText = `${exchange.toUpperCase()}:${symbol}`;
}

function updateTable(tick) {
    const tbody = document.querySelector('#trade-table tbody');
    if (!tbody) return;
    const row = document.createElement('tr');
    row.innerHTML = `
        <td>${new Date(tick.trade_timestamp).toLocaleTimeString()}</td>
        <td class="${tick.ask_bid === 'BID' ? 'bull' : 'bear'}">${formatPrice(tick.trade_price)}</td>
        <td>${formatPrice(tick.trade_volume)}</td>
        <td>${tick.ask_bid}</td>
    `;
    tbody.prepend(row);
    if (tbody.children.length > 10) tbody.lastChild.remove();
}

// 차트 데이터 부재 오버레이 제어 함수
function showNoDataOverlay(show) {
    if (!chartDiv) return;
    let overlay = chartDiv.querySelector('.chart-no-data-overlay');
    if (!overlay) {
        overlay = document.createElement('div');
        overlay.className = 'chart-no-data-overlay';
        overlay.style.position = 'absolute';
        overlay.style.top = '0';
        overlay.style.left = '0';
        overlay.style.width = '100%';
        overlay.style.height = '100%';
        overlay.style.background = 'rgba(15, 23, 42, 0.85)';
        overlay.style.display = 'none';
        overlay.style.flexDirection = 'column';
        overlay.style.justifyContent = 'center';
        overlay.style.alignItems = 'center';
        overlay.style.zIndex = '50';
        overlay.style.color = '#94A3B8';
        overlay.style.fontFamily = 'Pretendard, Inter, sans-serif';
        overlay.innerHTML = `
            <div style="font-size: 3rem; margin-bottom: 15px;">📊</div>
            <div style="font-size: 1.2rem; font-weight: bold; color: #F8FAFC; margin-bottom: 8px;">체결 데이터가 없습니다</div>
            <div style="font-size: 0.85rem; color: #64748B;">정규 장시간(평일 09:00 ~ 15:30) 외에는 수집이 일시 정지됩니다.</div>
        `;
        chartDiv.style.position = 'relative';
        chartDiv.appendChild(overlay);
    }
    overlay.style.display = show ? 'flex' : 'none';
}

async function drillDown(timestamp) {
    console.log(`[INFO] Setting marker at ${new Date(timestamp * 1000).toLocaleString()}`);
    state.autoScroll = false;
    state.alertMarkerTs = timestamp;
    document.getElementById('go-live-btn').style.display = 'block';
    ChartEngine.render(state.candles, state.currentCandle);
}



function updateIntervalUI(value) {
    const btns = document.querySelectorAll('.interval-btn');
    btns.forEach(btn => {
        if (parseInt(btn.dataset.value) === value) {
            btn.classList.add('active');
        } else {
            btn.classList.remove('active');
        }
    });
}

function exitExplorerMode() {
    state.isExplorerMode = false;
    state.explorerCenterIdx = null;
    state.alertMarkerTs = null;
    state.autoScroll = true;
    
    // 실시간 복귀 시 메모리 크기를 최근 500개로 축소
    if (state.candles.length > 500) {
        state.candles = state.candles.slice(-500);
    }
    
    const goLiveBtn = document.getElementById('go-live-btn');
    if (goLiveBtn) goLiveBtn.style.display = 'none';
    
    ChartEngine.render(state.candles, state.currentCandle);
    ChartEngine.exitExplorerMode();
    console.log("[INFO] Exited explorer mode, returned to real-time stream");
}


// --- 공통 상태 리로더 ---
async function loadRecentTrades() {
    try {
        const trades = await APIClient.fetchRecentTrades(state.currentExchange, state.currentSymbol, 10);
        const tbody = document.querySelector('#trade-table tbody');
        if (!tbody) return;
        tbody.innerHTML = '';
        trades.reverse().forEach(tick => updateTable(tick));
    } catch (e) { /* silent logging */ }
}

// --- UI 초기화 및 바인딩 컨트롤러 ---
function initViewNavigation() {
    ViewRouter.registerRoute('overview-simulation-view', () => {
        if (typeof OverviewEngine !== 'undefined') {
            OverviewEngine.refreshData('simulation');
        }
    });

    ViewRouter.registerRoute('overview-live-view', () => {
        if (typeof OverviewEngine !== 'undefined') {
            OverviewEngine.refreshData('live');
        }
    });

    ViewRouter.registerRoute('monitoring-view', () => {
        // 항상 '차트' 탭으로 초기화
        document.querySelectorAll('.monitoring-tab').forEach(t => t.classList.remove('active'));
        const defaultTab = document.querySelector('.monitoring-tab[data-tab="chart"]');
        if (defaultTab) defaultTab.classList.add('active');
        
        const chartContent = document.getElementById('monitoring-tab-content-chart');
        const detailContent = document.getElementById('monitoring-tab-content-detail');
        const outstandingContent = document.getElementById('monitoring-tab-content-outstanding');
        const historyContent = document.getElementById('monitoring-tab-content-history');
        if (chartContent) chartContent.style.display = 'block';
        if (detailContent) detailContent.style.display = 'none';
        if (outstandingContent) outstandingContent.style.display = 'none';
        if (historyContent) historyContent.style.display = 'none';

        if (typeof ChartEngine !== 'undefined' && typeof ChartEngine.resize === 'function') {
            setTimeout(() => ChartEngine.resize(), 0);
        }
    });

    ViewRouter.registerRoute('daemon-monitoring-view', () => {
        if (typeof DaemonMonitoringView !== 'undefined' && typeof DaemonMonitoringView.init === 'function') {
            DaemonMonitoringView.init();
        }
    });

    ViewRouter.initialize();
}


function initTradingControls() {
    // 뒤로가기 버튼 바인딩
    const btnMonitoringBack = document.getElementById('btn-monitoring-back');
    if (btnMonitoringBack) {
        btnMonitoringBack.addEventListener('click', () => {
            ViewRouter.back();
        });
    }

    // 모니터링 페이지 주문 버튼 바인딩
    const btnMonitoringOrder = document.getElementById('btn-monitoring-order');
    if (btnMonitoringOrder) {
        btnMonitoringOrder.addEventListener('click', () => {
            if (typeof openRealAssetOrderModalFromMonitoring === 'function') {
                openRealAssetOrderModalFromMonitoring();
            } else {
                console.error("openRealAssetOrderModalFromMonitoring is not defined");
            }
        });
    }

    // 모니터링 탭 클릭 스위칭 바인딩
    document.querySelectorAll('.monitoring-tab').forEach(tab => {
        tab.addEventListener('click', () => {
            document.querySelectorAll('.monitoring-tab').forEach(t => t.classList.remove('active'));
            tab.classList.add('active');
            
            const target = tab.dataset.tab;
            const chartContent = document.getElementById('monitoring-tab-content-chart');
            const detailContent = document.getElementById('monitoring-tab-content-detail');
            const outstandingContent = document.getElementById('monitoring-tab-content-outstanding');
            const historyContent = document.getElementById('monitoring-tab-content-history');
            
            // 모든 콘텐츠 숨김
            if (chartContent) chartContent.style.display = 'none';
            if (detailContent) detailContent.style.display = 'none';
            if (outstandingContent) outstandingContent.style.display = 'none';
            if (historyContent) historyContent.style.display = 'none';
            
            if (target === 'chart') {
                if (chartContent) chartContent.style.display = 'block';
                if (typeof ChartEngine !== 'undefined' && typeof ChartEngine.resize === 'function') {
                    setTimeout(() => ChartEngine.resize(), 50);
                }
            } else if (target === 'detail') {
                if (detailContent) detailContent.style.display = 'block';
                if (typeof KisDetailView !== 'undefined' && typeof KisDetailView.loadKisDetail === 'function') {
                    KisDetailView.loadKisDetail();
                }
            } else if (target === 'outstanding') {
                if (outstandingContent) outstandingContent.style.display = 'block';
                if (typeof loadOutstandingOrders === 'function') {
                    loadOutstandingOrders();
                }
            } else if (target === 'real-history') {
                if (historyContent) historyContent.style.display = 'block';
                if (typeof loadRealOrderHistory === 'function') {
                    loadRealOrderHistory();
                }
            }
        });
    });

    // 미체결 내역 새로고침 버튼 바인딩
    const btnRefreshOutstanding = document.getElementById('btn-refresh-outstanding');
    if (btnRefreshOutstanding) {
        btnRefreshOutstanding.addEventListener('click', () => {
            if (typeof loadOutstandingOrders === 'function') {
                loadOutstandingOrders();
            }
        });
    }

    // 거래 이력 새로고침 버튼 바인딩
    const btnRefreshHistory = document.getElementById('btn-refresh-history');
    if (btnRefreshHistory) {
        btnRefreshHistory.addEventListener('click', () => {
            if (typeof loadRealOrderHistory === 'function') {
                loadRealOrderHistory();
            }
        });
    }

    const btnTrading = document.getElementById('btn-toggle-trading');
    const tradingStatus = document.getElementById('trading-status');

    btnTrading?.addEventListener('click', () => {
        state.isAutoTrading = !state.isAutoTrading;
        if (state.isAutoTrading) {
            tradingStatus.innerText = '실행 중';
            tradingStatus.style.color = SUCCESS_COLOR;
            btnTrading.innerText = '⏹️ 자동 매매 중단';
            btnTrading.className = 'btn danger';
        } else {
            tradingStatus.innerText = '비활성';
            tradingStatus.style.color = BULL_COLOR;
            btnTrading.innerText = '▶️ 자동 매매 시작';
            btnTrading.className = 'btn primary';
        }
    });

    ['show-sma', 'show-ema', 'show-bb', 'show-volume', 'show-rsi', 'show-macd', 'show-atr'].forEach(id => {
        const el = document.getElementById(id);
        if (el) {
            el.addEventListener('change', () => {
                ChartEngine.toggleIndicator(id.replace('show-', ''), el.checked);
            });
        }
    });

    document.getElementById('go-live-btn')?.addEventListener('click', exitExplorerMode);

    // 차트 캔들 인터벌 선택 제어 위임 등록
    document.getElementById('interval-btn-group')?.addEventListener('click', async (e) => {
        const btn = e.target.closest('.interval-btn');
        if (!btn) return;
        
        const newInterval = parseInt(btn.dataset.value);
        if (isNaN(newInterval)) return;
        
        state.currentInterval = newInterval;
        updateIntervalUI(newInterval);
        console.log(`[Interval] Changed to ${newInterval}s`);
        
        // 차트 데이터 즉각 리로드 및 렌더링
        await loadHistory();
    });

    // 차트 종목 드롭다운(symbol-select) 변경 리스너
    document.getElementById('symbol-select')?.addEventListener('change', (e) => {
        const val = e.target.value; // "upbit:BTC" 또는 "kis:005930" 형태
        const parts = val.split(':');
        if (parts.length === 2) {
            Store.update({
                currentExchange: parts[0],
                currentSymbol: parts[1]
            });
        }
    });

    // 복원 캔들 화면 필터 변경 리스너
    document.getElementById('restored-range-select')?.addEventListener('change', loadRestoredCandles);


    // 스토어 상태 변경 구독 (반응형 리로드)
    Store.subscribe((key, val) => {
        if (key === 'currentSymbol' || key === 'currentExchange') {
            const symbol = Store.get('currentSymbol');
            const exchange = Store.get('currentExchange');
            
            console.log(`[Reactive Load] Exchange/Symbol changed: ${exchange}:${symbol}`);
            
            // 헤더 정보 갱신
            updateHeaderInfo(exchange, symbol);
            
            // 셀렉트 박스 동기화
            const select = document.getElementById('symbol-select');
            if (select) select.value = `${exchange}:${symbol}`;
            
            // 실시간 웹소켓 구독 갱신
            DataStream.subscribe(symbol, exchange);
            
            // 탐색(Explorer) 모드 해제
            exitExplorerMode();
            
            // 캔들 및 최신 체결 초기화 후 데이터 PULL 로드
            state.candles = []; 
            state.currentCandle = null;
            const tbody = document.querySelector('#trade-table tbody');
            if (tbody) tbody.innerHTML = '';
            
            loadHistory();
            loadRecentTrades();

            // 미체결/예약 내역 탭이 활성화되어 있다면 내역도 재조회
            const outstandingTab = document.querySelector('.monitoring-tab[data-tab="outstanding"]');
            if (outstandingTab && outstandingTab.classList.contains('active')) {
                if (typeof loadOutstandingOrders === 'function') {
                    loadOutstandingOrders();
                }
            }

            // 거래이력 탭이 활성화되어 있다면 내역도 재조회
            const historyTab = document.querySelector('.monitoring-tab[data-tab="real-history"]');
            if (historyTab && historyTab.classList.contains('active')) {
                if (typeof loadRealOrderHistory === 'function') {
                    loadRealOrderHistory();
                }
            }
        }
        
        if (key === 'wsConnected') {
            const badge = document.getElementById('status-badge');
            const container = document.getElementById('status-badge-container');
            if (badge) {
                badge.style.color = val ? SUCCESS_COLOR : BULL_COLOR;
            }
            if (container) {
                container.title = `WebSocket Link: ${val ? 'CONNECTED' : 'DISCONNECTED'}`;
            }
        }

        if (key === 'collectorStatuses') {
            renderCollectorStatuses(val);
        }
        
        if (key === 'currentSimPortfolioId') {
            console.log(`[Reactive Load] Simulation Portfolio ID changed: ${val}`);
            const selectEl = document.getElementById('overview-simulation-session-select');
            if (selectEl && selectEl.value !== val) {
                selectEl.value = val || '';
            }
            if (ViewRouter.getActiveView() === 'overview-simulation-view') {
                state.currentPortfolioId = val;
            }
        }

        if (key === 'currentLivePortfolioId') {
            console.log(`[Reactive Load] Live Portfolio ID changed: ${val}`);
            const selectEl = document.getElementById('overview-live-session-select');
            if (selectEl && selectEl.value !== val) {
                selectEl.value = val || '';
            }
            if (ViewRouter.getActiveView() === 'overview-live-view') {
                state.currentPortfolioId = val;
            }
        }

        if (key === 'currentPortfolioId') {
            console.log(`[Reactive Load] Portfolio ID changed: ${val}`);
            loadPortfolio();
        }
    });
}


async function init() {
    state.currentExchange = Store.get('currentExchange') || 'upbit';
    state.currentSymbol = Store.get('currentSymbol') || 'BTC';
    
    ChartEngine.initialize('main-chart', drillDown);
    updateHeaderInfo(state.currentExchange, state.currentSymbol);
    await loadSymbols();
    await loadHistory();
    await loadRecentTrades();

    DataStream.initialize(processTick);
    updateCollectorStatus();

    // 뷰 네비게이션 및 메인 트레이딩 바인딩 초기화
    initViewNavigation();
    initTradingControls();

    if (typeof OverviewEngine !== 'undefined') {
        OverviewEngine.initialize();
    }

    if (typeof checkUpcomingAssetEvents === 'function') {
        checkUpcomingAssetEvents();
    }
    if (typeof checkMissedAssetEvents === 'function') {
        checkMissedAssetEvents();
    }
}


// --- 앱 전체 초기화 진입점 ---
document.addEventListener('DOMContentLoaded', () => {
    init();
    
    // 시스템 상태 모니터링 시작
    async function updateSystemQueues() {
        try {
            const status = await APIClient.fetchSystemQueues();
            if (!status) return;

            const updateEl = (id, val) => {
                const el = document.getElementById(id);
                if (!el) return;
                el.innerText = val.toLocaleString();
                if (val > 1000) {
                    el.classList.add('warning');
                } else {
                    el.classList.remove('warning');
                }
            };

            updateEl('queue-processing', status.processing);
            updateEl('queue-database', status.database);
            updateEl('queue-candle', status.candle);
            updateEl('queue-total', status.total || 0);
        } catch (e) {
            console.error("Queue status update failed", e);
        }
    }

    setInterval(updateSystemQueues, 1000);
    updateSystemQueues();
});

// 전역 window 바인딩으로 격리된 모듈과의 연동 보장
window.state = state;
window.loadHistory = loadHistory;
window.loadMoreHistory = loadMoreHistory;
window.processTick = processTick;
window.updateMetrics = updateMetrics;
window.updateHeaderInfo = updateHeaderInfo;
window.updateTable = updateTable;
window.drillDown = drillDown;
window.exitExplorerMode = exitExplorerMode;
window.loadRecentTrades = loadRecentTrades;
window.init = init;

