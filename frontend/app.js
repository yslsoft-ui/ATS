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
                const changePercent = ((lastCandle.close - lastCandle.open) / lastCandle.open * 100).toFixed(2);
                changeEl.innerText = `${changePercent >= 0 ? '+' : ''}${changePercent}%`;
                changeEl.style.color = changePercent >= 0 ? '#FF4B4B' : '#0072FF';
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

// --- 실시간 캔들 생성 및 업데이트 로직 (PUSH) ---
function processTick(tick) {
    if (tick.type === 'strategy_status') {
        updateStrategyStatusUI(tick);
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
    if (tick.exchange !== state.currentExchange || tick.code !== state.currentSymbol) return; 

    // --- 실시간 틱 기반 캔들 조립 및 보조지표 점진 연산 ---
    const timestamp = Math.floor(tick.trade_timestamp / 1000);
    const bucket = Math.floor(timestamp / state.currentInterval) * state.currentInterval;
    const price = tick.trade_price;
    const volume = tick.trade_volume;

    let nextCurrentCandle = null;

    if (!state.currentCandle || state.currentCandle.timestamp !== bucket) {
        if (state.currentCandle) {
            state.candles = [...state.candles, state.currentCandle].slice(-500);
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
    
    const position = state.currentPortfolioData.positions.find(p => p.symbol === tick.code);
    if (!position) return;

    const currentPrice = tick.trade_price;
    const profitRate = ((currentPrice - position.avg_price) / position.avg_price * 100).toFixed(2);
    
    const rows = document.querySelectorAll('#positions-tbody tr');
    rows.forEach(row => {
        if (row.cells[0]?.innerText === tick.code) {
            const rateCell = row.cells[3];
            rateCell.innerText = `${profitRate}%`;
            rateCell.className = `num ${profitRate >= 0 ? 'bull' : 'bear'}`;
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
    
    if (Math.abs(newTotalValue - prevValue) > 0.01) {
        totalValueEl.innerText = formatPrice(newTotalValue);
        totalValueEl.classList.add('value-updating');
        setTimeout(() => totalValueEl.classList.remove('value-updating'), 400);
        
        const initialValue = 100000000; // 예시 원금
        const totalRoi = ((newTotalValue - initialValue) / initialValue * 100).toFixed(2);
        const roiEl = document.getElementById('port-total-roi');
        roiEl.innerText = `${totalRoi}%`;
        roiEl.className = `value ${totalRoi >= 0 ? 'bull' : 'bear'}`;
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
    priceEl.style.color = currentPrice >= prevPrice ? '#FF4B4B' : '#0072FF';

    // 거래소 공통 전일 대비율(signed_change_rate) 최우선 적용, 없으면 업비트 방식 폴백 계산
    let changePercent = 0.0;
    if (tick.signed_change_rate !== undefined && tick.signed_change_rate !== null) {
        changePercent = parseFloat((tick.signed_change_rate * 100).toFixed(2));
    } else if (tick.change_price && tick.prev_closing_price) {
        changePercent = parseFloat(((tick.change_price / tick.prev_closing_price) * 100).toFixed(2));
    }

    changeEl.innerText = `${changePercent >= 0 ? '+' : ''}${changePercent}%`;
    changeEl.style.color = changePercent >= 0 ? '#FF4B4B' : '#0072FF';
}

function updateHeaderInfo(exchange, symbol) {
    const coin = (window.marketData || []).find(c => c.exchange === exchange && c.market === symbol);
    const ticker = symbol;

    const iconEl = document.getElementById('header-coin-icon');
    const krNameEl = document.getElementById('current-symbol-kr');
    const codeEl = document.getElementById('current-symbol-code');

    if (!krNameEl || !codeEl) return;

    // 한글명 폴백: state.symbolNames에서 먼저 탐색 후 없으면 ticker
    const key = `${exchange}:${symbol}`;
    const fallbackName = (state.symbolNames && state.symbolNames[key]) ? state.symbolNames[key] : ticker;

    if (coin) {
        krNameEl.innerText = coin.korean_name || fallbackName;
    } else {
        krNameEl.innerText = fallbackName;
    }

    if (iconEl) {
        if (exchange === 'upbit') {
            iconEl.src = `https://static.upbit.com/logos/${ticker}.png`;
            iconEl.style.display = 'block';
        } else if (exchange === 'bithumb') {
            const symbolLower = ticker.toLowerCase();
            iconEl.src = `https://resource.bithumb.com/coin/icon/${symbolLower}.png`;
            iconEl.style.display = 'block';
            iconEl.onerror = () => {
                iconEl.onerror = () => iconEl.style.display = 'none';
                iconEl.src = `https://static.upbit.com/logos/${ticker.toUpperCase()}.png`;
            };
        } else if (exchange === 'kis') {
            // 국내 주식을 상징하는 세련된 네온 레드 주식 상승 차트 SVG 주입
            iconEl.src = `data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="%23FF4B4B" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3v18h18"/><path d="m18.7 8-5.1 5.2-2.8-2.7L7 14.3"/></svg>`;
            iconEl.style.display = 'block';
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
    
    const goLiveBtn = document.getElementById('go-live-btn');
    if (goLiveBtn) goLiveBtn.style.display = 'none';
    
    ChartEngine.render(state.candles, state.currentCandle);
    ChartEngine.exitExplorerMode();
    console.log("[INFO] Exited explorer mode, returned to real-time stream");
}



// (DOM 요소 참조 및 뷰 관리는 router.js의 ViewRouter가 전담합니다)



// --- 수집기 제어 로직 ---
let lastSeenErrors = {};
const exchangeState = {
    upbit: { isRunning: false },
    bithumb: { isRunning: false },
    kis: { isRunning: false }
};

async function updateCollectorStatus() {
    try {
        const data = await APIClient.fetchCollectorStatus();

        let hasEmergency = false;

        for (const [exch, status] of Object.entries(data)) {
            const statusEl = document.getElementById(`${exch}-status`);
            const btnEl = document.getElementById(`btn-toggle-${exch}`);
            const errorEl = document.getElementById(`${exch}-error-msg`);
            const sidebarStatusEl = document.getElementById(`sidebar-${exch}-status`);

            if (!statusEl || !btnEl) continue;

            exchangeState[exch].isRunning = status.is_running;
            
            if (sidebarStatusEl) {
                if (status.is_running && !status.error) {
                    sidebarStatusEl.innerText = '● RUNNING';
                    sidebarStatusEl.className = 'status-indicator status-on';
                } else if (status.error) {
                    sidebarStatusEl.innerText = '● ERROR';
                    sidebarStatusEl.className = 'status-indicator status-warn';
                } else {
                    sidebarStatusEl.innerText = '● STOPPED';
                    sidebarStatusEl.className = 'status-indicator status-off';
                }
            }

            if (status.is_running && !status.error) {
                statusEl.innerText = 'RUNNING';
                statusEl.className = 'status-badge status-on';
                btnEl.innerText = '⏹️ 중단';
                btnEl.className = 'btn sm danger';
            } else {
                statusEl.innerText = status.error ? 'ERROR' : 'STOPPED';
                statusEl.className = status.error ? 'status-badge status-warn' : 'status-badge status-off';
                btnEl.innerText = '▶️ 시작';
                btnEl.className = 'btn sm primary';
            }

            if (status.error) {
                errorEl.innerText = status.error;
                errorEl.style.display = 'block';
                
                if (lastSeenErrors[exch] !== status.error) {
                    showAlert({ msg: `⚠️ ${exch.toUpperCase()} 에러: ${status.error}`, alert_type: 'error' });
                    lastSeenErrors[exch] = status.error;
                }
            } else {
                errorEl.style.display = 'none';
                lastSeenErrors[exch] = null;
            }
            
            btnEl.disabled = false;

            if (!status.is_running || status.error) {
                hasEmergency = true;
            }
        }

        const emergencyBanner = document.getElementById('global-emergency-banner');
        if (emergencyBanner) {
            if (hasEmergency) {
                emergencyBanner.style.display = 'flex';
                emergencyBanner.querySelector('.warning-text').innerText = '[비상 경고] 일부 데이터 수집기가 중단되었거나 에러 상태입니다! 상시 시세 모니터링 수급이 어렵습니다.';
            } else {
                emergencyBanner.style.display = 'none';
            }
        }

    } catch (e) {
        console.error("Status check failed", e);
        const emergencyBanner = document.getElementById('global-emergency-banner');
        if (emergencyBanner) {
            emergencyBanner.style.display = 'flex';
            emergencyBanner.querySelector('.warning-text').innerText = '[비상 경고] 백엔드 거래 서버와의 실시간 API 통신이 차단되었습니다! 네트워크 연결을 확인하십시오.';
        }
    }
}



// --- 데이터베이스 관리 로직 ---
const btnCleanup = document.getElementById('btn-cleanup');
const cleanupDateInput = document.getElementById('cleanup-date');
const previewPanel = document.getElementById('cleanup-preview-panel');
const previewTrades = document.getElementById('cleanup-preview-trades');
const previewCandles = document.getElementById('cleanup-preview-candles');
const previewTotal = document.getElementById('cleanup-preview-total');

// 데이터 미리보기 갱신 함수
async function updateCleanupPreview() {
    if (!cleanupDateInput || !previewPanel) return;
    const selectedDate = cleanupDateInput.value;
    if (!selectedDate) {
        previewPanel.style.display = 'none';
        return;
    }

    try {
        const data = await APIClient.fetchCleanupPreview(selectedDate);
        if (previewTrades) previewTrades.innerText = `${data.trades_count.toLocaleString()}건`;
        if (previewCandles) previewCandles.innerText = `${data.candles_count.toLocaleString()}건`;
        if (previewTotal) previewTotal.innerText = `${data.total_count.toLocaleString()}건`;
        previewPanel.style.display = 'block';
        
        // 삭제 실행을 위한 임시 속성 보관
        if (btnCleanup) {
            btnCleanup.dataset.trades = data.trades_count;
            btnCleanup.dataset.candles = data.candles_count;
            btnCleanup.dataset.total = data.total_count;
        }
    } catch (e) {
        console.error("Cleanup preview check failed", e);
    }
}



// --- 전략 관리 로직 ---
async function loadStrategies() {
    const listEl = document.getElementById('strategy-list');
    if (!listEl) return;

    try {
        const strategies = await APIClient.fetchStrategies();
        renderStrategyCards(strategies);
    } catch (e) {
        listEl.innerHTML = '<p class="status-text">전략 정보를 불러오는데 실패했습니다.</p>';
    }
}

function renderStrategyCards(strategies) {
    const listEl = document.getElementById('strategy-list');
    if (!listEl) return;
    listEl.innerHTML = '';

    const typeOrder = { "ENTRY": 1, "BOTH": 2, "EXIT": 3 };
    strategies.sort((a, b) => (typeOrder[a.type] || 99) - (typeOrder[b.type] || 99));

    strategies.forEach(s => {
        const card = document.createElement('div');
        let paramsHtml = '';
        for (const [key, info] of Object.entries(s.params)) {
            const inputType = info.type === 'str' ? 'text' : 'number';
            const stepAttr = info.type === 'float' ? 'step="any"' : (info.type === 'int' ? 'step="1"' : '');
            paramsHtml += `
                <div class="param-group">
                    <div class="param-row">
                        <label>${key}</label>
                        <input type="${inputType}" ${stepAttr} class="dark-input param-input" 
                               data-strategy="${s.id}" data-key="${key}" data-type="${info.type}"
                               value="${info.current !== undefined ? info.current : info.default}">
                    </div>
                    <div class="param-desc">${info.description}</div>
                </div>
            `;
        }

        const isEnabled = s.enabled !== false;
        card.className = `strategy-item ${isEnabled ? '' : 'disabled'}`;
        card.style.opacity = isEnabled ? '1' : '0.5';

        const typeColors = { "ENTRY": "#4A90E2", "EXIT": "#FF4B4B", "BOTH": "#F5A623" };
        const typeLabel = s.type === "ENTRY" ? "매수" : (s.type === "EXIT" ? "매도" : "공용");

        card.innerHTML = `
            <h4>
                <div style="display: flex; flex-direction: column;">
                    <span data-id="${s.id}">${s.name}</span>
                    <span class="type-badge" style="background: ${typeColors[s.type] || '#666'}; font-size: 0.6rem; padding: 2px 6px; border-radius: 10px; width: fit-content; margin-top: 4px; color: white;">${typeLabel}</span>
                </div>
                <div style="display: flex; align-items: center; gap: 5px;">
                    <span class="badge" style="font-size: 0.7rem; background: ${isEnabled ? '#1a472a' : '#471a1a'}; color: ${isEnabled ? '#4caf50' : '#FF4B4B'};">${isEnabled ? '활성' : '비활성'}</span>
                    <button class="btn sm ${isEnabled ? 'danger' : 'primary'}" onclick="toggleStrategyStatus('${s.id}', ${isEnabled})" style="padding: 2px 8px; font-size: 0.7rem;">
                        ${isEnabled ? '사용 안함' : '사용함'}
                    </button>
                </div>
            </h4>
            <div class="desc">${s.description}</div>
            <div class="strategy-params">
                ${paramsHtml}
            </div>
            <div class="strategy-live-status" id="live-status-${s.id}">
                <div class="status-header">
                    <span class="pulse-dot"></span> LIVE MONITOR (${s.id})
                </div>
                <div class="status-body">
                    <span class="status-badge">분석 대기 중...</span>
                </div>
            </div>
            <div class="strategy-actions">
                <button class="btn primary sm" onclick="saveStrategyParams('${s.id}')" style="width: 100%;">설정 저장</button>
            </div>
        `;
        listEl.appendChild(card);
    });
}

function updateStrategyStatusUI(status) {
    const strategyId = status.strategy_id;
    let statusEl = document.getElementById(`live-status-${strategyId}`);
    
    if (!statusEl) {
        const card = document.querySelector(`.strategy-item h4 span[data-id="${strategyId}"]`)?.closest('.strategy-item');
        if (!card) return;
        statusEl = card.querySelector('.strategy-live-status');
        if (!statusEl) {
            statusEl = document.createElement('div');
            statusEl.className = 'strategy-live-status';
            card.querySelector('.desc').after(statusEl);
        }
    }

    const indicators = status.indicators || {};
    const indicatorHtml = Object.entries(indicators).map(([k, v]) => {
        const val = typeof v === 'number' ? v.toLocaleString(undefined, {maximumFractionDigits: 2}) : v;
        return `<span class="status-badge value-updating">${k.toUpperCase()}: ${val}</span>`;
    }).join('');

    statusEl.innerHTML = `
        <div class="status-header">
            <span class="pulse-dot"></span> 
            LIVE MONITOR (${status.symbol})
        </div>
        <div class="status-body">
            ${indicatorHtml}
            <span class="action-text">${status.last_action || '관망 중'}</span>
        </div>
    `;

    setTimeout(() => {
        statusEl.querySelectorAll('.value-updating').forEach(el => el.classList.remove('value-updating'));
    }, 400);
}

async function saveStrategyParams(strategyId) {
    const inputs = document.querySelectorAll(`.param-input[data-strategy="${strategyId}"]`);
    const params = {};
    inputs.forEach(input => {
        const type = input.dataset.type;
        if (type === 'str') {
            params[input.dataset.key] = input.value;
        } else if (type === 'int') {
            params[input.dataset.key] = parseInt(input.value) || 0;
        } else {
            params[input.dataset.key] = parseFloat(input.value) || 0;
        }
    });

    try {
        await APIClient.saveStrategyParams(strategyId, params);
        alert(`${strategyId} 전략 설정이 저장되었습니다.`);
        loadStrategies();
    } catch (e) {
        alert("설정 저장 실패");
    }
}

async function toggleStrategyStatus(strategyId, currentEnabled) {
    try {
        await APIClient.toggleStrategyStatus(strategyId, currentEnabled);
        loadStrategies();
    } catch (e) {
        alert("상태 변경 실패");
    }
}

window.saveStrategyParams = saveStrategyParams;
window.toggleStrategyStatus = toggleStrategyStatus;

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
    ViewRouter.initialize({
        routes: {
            'monitoring-view': () => {},
            'market-view': () => { exitExplorerMode(); loadMarket(); },
            'alert-view': () => loadAlertHistory(),
            'strategy-view': () => loadStrategies(),
            'portfolio-view': () => { loadPortfolioList(); loadPortfolio(); },
            'real-asset-view': () => loadRealAssets(),
            'settings-view': () => updateCollectorStatus(),
            'restored-view': () => { exitExplorerMode(); loadRestoredCandles(); }
        }
    });
}

function initTradingControls() {
    const btnTrading = document.getElementById('btn-toggle-trading');
    const tradingStatus = document.getElementById('trading-status');

    btnTrading?.addEventListener('click', () => {
        state.isAutoTrading = !state.isAutoTrading;
        if (state.isAutoTrading) {
            tradingStatus.innerText = '실행 중';
            tradingStatus.style.color = '#4caf50';
            btnTrading.innerText = '⏹️ 자동 매매 중단';
            btnTrading.className = 'btn danger';
        } else {
            tradingStatus.innerText = '비활성';
            tradingStatus.style.color = '#FF4B4B';
            btnTrading.innerText = '▶️ 자동 매매 시작';
            btnTrading.className = 'btn primary';
        }
    });

    ['show-sma', 'show-bb', 'show-volume', 'show-rsi'].forEach(id => {
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
        }
        
        if (key === 'wsConnected') {
            const badge = document.getElementById('status-badge');
            if (badge) {
                badge.innerText = val ? 'CONNECTED' : 'DISCONNECTED';
                badge.className = val ? 'badge' : 'badge disconnected';
            }
        }
        
        if (key === 'currentPortfolioId') {
            console.log(`[Reactive Load] Portfolio ID changed: ${val}`);
            loadPortfolio();
        }
    });
}

function initCollectorControls() {
    ['upbit', 'bithumb', 'kis'].forEach(exch => {
        const btn = document.getElementById(`btn-toggle-${exch}`);
        if (btn) {
            btn.addEventListener('click', async () => {
                btn.disabled = true;
                const action = exchangeState[exch].isRunning ? 'stop' : 'start';
                try {
                    await APIClient.controlCollector(exch, action);
                    showAlert({ msg: `Collector ${exch} ${action}ed` });
                    if (action === 'start') {
                        const statusEl = document.querySelector(`#${exch}-status`);
                        const errorEl = document.querySelector(`#${exch}-error-msg`);
                        if (statusEl) {
                            statusEl.innerText = 'STARTING...';
                            statusEl.className = 'status-badge status-on';
                        }
                        if (errorEl) {
                            errorEl.style.display = 'none';
                        }
                    }
                    updateCollectorStatus();
                } catch (e) {
                    showAlert({ msg: `${exch} 제어 실패`, alert_type: 'error' });
                    btn.disabled = false;
                }
            });
        }
    });

    setInterval(() => {
        if (ViewRouter.getActiveView() === 'settings-view') {
            updateCollectorStatus();
        }
    }, 2000);
}

function initDatabaseControls() {
    const btnCleanup = document.getElementById('btn-cleanup');
    const cleanupDateInput = document.getElementById('cleanup-date');

    if (cleanupDateInput) {
        cleanupDateInput.addEventListener('change', updateCleanupPreview);
    }

    if (btnCleanup && cleanupDateInput) {
        btnCleanup.addEventListener('click', async () => {
            const selectedDate = cleanupDateInput.value;
            if (!selectedDate) {
                alert("삭제할 기준 날짜를 선택해주세요.");
                return;
            }

            const tradesCount = parseInt(btnCleanup.dataset.trades || "0");
            const candlesCount = parseInt(btnCleanup.dataset.candles || "0");
            const totalCount = parseInt(btnCleanup.dataset.total || "0");

            const warnMessage = `⚠️ [데이터베이스 영구 삭제 경고]\n\n` +
                `선택하신 날짜 (${selectedDate}) 이전의 과거 데이터를 데이터베이스에서 영구히 삭제합니다.\n\n` +
                `[삭제 정리 대상]\n` +
                `- 체결 데이터 (Trades): ${tradesCount.toLocaleString()}건\n` +
                `- 캔들 데이터 (Candles): ${candlesCount.toLocaleString()}건\n` +
                `- 총 소거 대상: ${totalCount.toLocaleString()}건\n\n` +
                `이 작업은 데이터베이스를 물리적으로 축소시키며 되돌릴 수 없습니다.\n` +
                `정말로 영구 삭제를 진행하시겠습니까?`;

            if (!confirm(warnMessage)) {
                return;
            }

            btnCleanup.disabled = true;
            btnCleanup.innerText = "삭제 진행 중...";

            try {
                const data = await APIClient.runCleanup(selectedDate);
                alert(`🧹 정리 완료!\n\n${data.message}`);
                await updateCleanupPreview();
            } catch (e) {
                alert("정리 작업 도중 오류가 발생했습니다.");
            } finally {
                btnCleanup.disabled = false;
                btnCleanup.innerText = "선택 날짜 이전 삭제";
            }
        });
    }
}

// --- 복원 캔들 모니터링 로드 ---
async function loadRestoredCandles() {
    const tbody = document.getElementById('restored-tbody');
    if (!tbody) return;

    tbody.innerHTML = '<tr><td colspan="10" style="text-align:center;padding:30px;color:rgba(255,255,255,0.4);">📊 복원된 캔들 정보를 조회 중입니다...</td></tr>';

    const rangeSelect = document.getElementById('restored-range-select');
    if (!rangeSelect) return;

    const range = parseInt(rangeSelect.value) || 1440;

    try {
        // exchange와 symbol을 null로 주입하여 전체 데이터를 조회해옵니다.
        const data = await APIClient.fetchRestoredCandles(null, null, range);
        tbody.innerHTML = '';

        if (!data || data.length === 0) {
            tbody.innerHTML = '<tr><td colspan="10" style="text-align:center;padding:40px;color:var(--text-secondary);">✅ 최근 설정 범위 동안 DB에서 누락/복원된 캔들이 없습니다.</td></tr>';
            return;
        }

        data.forEach((c, idx) => {
            const tr = document.createElement('tr');
            const dateStr = new Date(c.timestamp * 1000).toLocaleString();

            // 거래소 배지 생성
            let badgeStyle = '';
            if (c.exchange === 'upbit') badgeStyle = 'background: #1e88e5; color: #ffffff;';
            else if (c.exchange === 'bithumb') badgeStyle = 'background: #f57c00; color: #ffffff;';
            else if (c.exchange === 'kis') badgeStyle = 'background: #e53935; color: #ffffff;';
            else badgeStyle = 'background: #546e7a; color: #ffffff;';
            const exBadge = `<span class="badge" style="font-size: 0.75rem; padding: 2px 8px; border-radius: 4px; font-weight: bold; ${badgeStyle}">${c.exchange.toUpperCase()}</span>`;

            // 한글 코인명 매핑 및 셀 데이터 구성
            const nameKey = `${c.exchange}:${c.symbol}`;
            const coinName = (state.symbolNames && state.symbolNames[nameKey]) ? state.symbolNames[nameKey] : c.symbol;
            const nameCell = `<span style="font-weight: bold; color: #F8FAFC;">${coinName}</span> <span style="font-size: 0.75rem; color: #94A3B8; font-family: 'Roboto Mono', monospace;">(${c.symbol})</span>`;

            tr.innerHTML = `
                <td style="text-align: center; color: var(--text-secondary);">${idx + 1}</td>
                <td style="text-align: center;">${exBadge}</td>
                <td style="text-align: left;">${nameCell}</td>
                <td style="color: var(--accent-color); font-weight: bold;">${dateStr}</td>
                <td class="num">${formatPrice(c.open)}</td>
                <td class="num bull">${formatPrice(c.high)}</td>
                <td class="num bear">${formatPrice(c.low)}</td>
                <td class="num">${formatPrice(c.close)}</td>
                <td class="num secondary">${formatPrice(c.volume)}</td>
                <td style="text-align: center;"><span class="restored-tick-count-badge">${c.tick_count}</span></td>
            `;
            tbody.appendChild(tr);
        });
    } catch (e) {
        tbody.innerHTML = '<tr><td colspan="10" style="text-align:center;padding:30px;color:var(--bull-color);">&#x26A0;&#xFE0F; 복원 캔들 조회 실패</td></tr>';
    }
}

async function init() {

    ChartEngine.initialize('main-chart', drillDown);
    updateHeaderInfo(state.currentExchange, state.currentSymbol);
    await loadSymbols();
    await loadHistory();
    await loadRecentTrades();
    loadMarket();
    loadPortfolioList();
    loadPortfolio();
    DataStream.initialize(processTick);
    updateCollectorStatus();
    initMarketTabs();

    // 뷰 네비게이션 및 컨트롤 바인딩 초기화
    initViewNavigation();
    initTradingControls();
    initCollectorControls();
    initDatabaseControls();
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
window.processTick = processTick;
window.updateMetrics = updateMetrics;
window.updateHeaderInfo = updateHeaderInfo;
window.updateTable = updateTable;
window.drillDown = drillDown;
window.exitExplorerMode = exitExplorerMode;
window.loadRecentTrades = loadRecentTrades;
window.loadRestoredCandles = loadRestoredCandles;
window.init = init;

