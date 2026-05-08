let candles = [];
let currentCandle = null;
let currentInterval = 60; 
let isLoaded = false;
let currentSymbol = "KRW-BTC";
let ws = null;
let isExplorerMode = false;
let explorerCenterIdx = null; // 인덱스 기반 탐색
let isAutoTrading = false;
let alertMarkerTs = null; 
let isAlertEnabled = true;

const chartDiv = document.getElementById('main-chart');
let chart, candleSeries, volumeSeries, smaSeries, bbUpperSeries, bbLowerSeries, rsiSeries;

// --- Lightweight Charts 초기화 ---
function initLWChart() {
    if (!chartDiv) return;
    
    const lw = window.LightweightCharts || LightweightCharts;
    if (typeof lw === 'undefined') return;

    chart = lw.createChart(chartDiv, {
        width: chartDiv.clientWidth || 800,
        height: 500,
        layout: {
            background: { type: 'solid', color: '#262730' },
            textColor: '#FAFAFA',
        },
        grid: {
            vertLines: { color: '#333' },
            horzLines: { color: '#333' },
        },
        crosshair: {
            mode: lw.CrosshairMode.Normal,
        },
        rightPriceScale: {
            borderColor: '#333',
            scaleMargins: { top: 0.05, bottom: 0.35 },
        },
        timeScale: {
            borderColor: '#333',
            timeVisible: true,
            secondsVisible: true,
        },
    });

    candleSeries = chart.addCandlestickSeries({
        upColor: '#FF4B4B',
        downColor: '#0072FF',
        borderDownColor: '#0072FF',
        borderUpColor: '#FF4B4B',
        wickDownColor: '#0072FF',
        wickUpColor: '#FF4B4B',
    });

    volumeSeries = chart.addHistogramSeries({
        color: '#26a69a',
        priceFormat: { type: 'volume' },
        priceScaleId: '', // 서브 레이어
    });

    volumeSeries.priceScale().applyOptions({
        scaleMargins: { top: 0.7, bottom: 0.15 },
    });

    smaSeries = chart.addLineSeries({ color: '#FFA500', lineWidth: 2, title: 'SMA(20)' });
    bbUpperSeries = chart.addLineSeries({ color: 'rgba(173, 216, 230, 0.4)', lineWidth: 1, lineStyle: 2 });
    bbLowerSeries = chart.addLineSeries({ color: 'rgba(173, 216, 230, 0.4)', lineWidth: 1, lineStyle: 2 });
    
    // RSI는 별도 스케일 사용 (0-100)
    rsiSeries = chart.addLineSeries({
        color: '#FF00FF',
        lineWidth: 1.5,
        title: 'RSI(14)',
        priceScaleId: 'rsi',
    });

    chart.priceScale('rsi').applyOptions({
        scaleMargins: { top: 0.85, bottom: 0 }, // 최하단 15%
        visible: false,
    });

    // 클릭 이벤트 (Drill-down)
    chart.subscribeClick(param => {
        if (!param.time || param.point === undefined) return;
        drillDown(param.time);
    });

    // 툴팁 구현 (Floating Tooltip)
    const tooltip = document.createElement('div');
    tooltip.className = 'lw-tooltip';
    chartDiv.appendChild(tooltip);

    chart.subscribeCrosshairMove(param => {
        if (!param.time || param.point === undefined || !param.seriesData.get(candleSeries)) {
            tooltip.style.display = 'none';
            return;
        }

        const data = param.seriesData.get(candleSeries);
        const smaData = param.seriesData.get(smaSeries);
        const rsiData = param.seriesData.get(rsiSeries);
        
        // 전역 변수 candles와 currentCandle에서 데이터 찾기
        const allLocal = [...candles, currentCandle].filter(c => c);
        const rawCandle = allLocal.find(c => c.timestamp === param.time);
        
        tooltip.style.display = 'block';
        const price = data.close.toLocaleString();
        const timeStr = new Date(param.time * 1000).toLocaleString();
        
        let html = `<div class="tooltip-time">${timeStr}</div>`;
        html += `<div class="tooltip-row"><span>O</span><b>${data.open.toLocaleString()}</b></div>`;
        html += `<div class="tooltip-row"><span>H</span><b>${data.high.toLocaleString()}</b></div>`;
        html += `<div class="tooltip-row"><span>L</span><b>${data.low.toLocaleString()}</b></div>`;
        html += `<div class="tooltip-row"><span>C</span><b class="${data.close >= data.open ? 'bull' : 'bear'}">${price}</b></div>`;
        
        if (rawCandle) {
            html += `<div class="tooltip-row"><span>Vol</span><b>${rawCandle.volume.toLocaleString()}</b></div>`;
            if (rawCandle.count) {
                html += `<div class="tooltip-row"><span>Count</span><b>${rawCandle.count}</b></div>`;
            }
        }

        if (smaData) html += `<div class="tooltip-row"><span>SMA</span><b style="color:#FFA500">${smaData.value.toFixed(0)}</b></div>`;
        if (rsiData) html += `<div class="tooltip-row"><span>RSI</span><b style="color:#FF00FF">${rsiData.value.toFixed(2)}</b></div>`;

        tooltip.innerHTML = html;

        // 위치 계산
        const y = param.point.y;
        let x = param.point.x + 15;
        if (x > chartDiv.clientWidth - 150) x = param.point.x - 160;

        tooltip.style.left = x + 'px';
        tooltip.style.top = y + 'px';
    });

    window.addEventListener('resize', () => {
        chart.resize(chartDiv.clientWidth, 500);
    });
}

// --- 과거 데이터 불러오기 (PULL) ---
async function loadHistory() {
    isLoaded = false;
    const response = await fetch(`/candles?symbol=${currentSymbol}&interval=${currentInterval}&limit=10000`);
    const history = await response.json();
    
    if (history && history.length > 0) {
        candles = history;
        
        if (candles.length > 0) {
            currentCandle = candles.pop();
        }
    } else {
        candles = [];
        currentCandle = null;
    }
    
    if (!chart) initLWChart();
    renderChart();
    // 과거 데이터 로드 시 화면에 가득 채우기
    if (candles.length > 0) {
        chart.timeScale().fitContent();
    }
    isLoaded = true;
}

// --- 실시간 지표 계산 엔진 ---
function calculateIndicators() {
    const allCandles = [...candles, currentCandle].filter(c => c);
    if (allCandles.length < 20) return;

    const closePrices = allCandles.map(c => c.close);
    const lastIdx = allCandles.length - 1;

    // 1. SMA (20)
    const sma20Range = closePrices.slice(-20);
    const sma20 = sma20Range.reduce((a, b) => a + b, 0) / 20;
    currentCandle.sma = sma20;

    // 2. Bollinger Bands (20, 2)
    const variance = sma20Range.reduce((a, b) => a + Math.pow(b - sma20, 2), 0) / 20;
    const stdDev = Math.sqrt(variance);
    currentCandle.bb_upper = sma20 + (stdDev * 2);
    currentCandle.bb_lower = sma20 - (stdDev * 2);

    // 3. RSI (14)
    if (allCandles.length >= 15) {
        let gains = 0;
        let losses = 0;
        for (let i = allCandles.length - 14; i < allCandles.length; i++) {
            const diff = allCandles[i].close - allCandles[i - 1].close;
            if (diff > 0) gains += diff;
            else losses -= diff;
        }
        const avgGain = gains / 14;
        const avgLoss = losses / 14;
        if (avgLoss === 0) currentCandle.rsi = 100;
        else {
            const rs = avgGain / avgLoss;
            currentCandle.rsi = 100 - (100 / (1 + rs));
        }
    }
}

// --- 캔들 생성 및 업데이트 로직 (PUSH) ---
function processTick(tick) {
    // 0. 글로벌 알림(Alert) 처리
    if (tick.type === 'alert') {
        showAlert(tick);
        return;
    }

    if (!isLoaded) return; 
    if (tick.code !== currentSymbol) return; // 선택된 종목이 아니면 무시

    const timestamp = Math.floor(tick.trade_timestamp / 1000);
    const bucket = Math.floor(timestamp / currentInterval) * currentInterval;
    const price = tick.trade_price;
    const volume = tick.trade_volume;

    if (!currentCandle || currentCandle.timestamp !== bucket) {
        if (currentCandle) {
            candles.push(currentCandle);
            if (candles.length > 500) candles.shift();
        }
        currentCandle = {
            timestamp: bucket,
            open: price, high: price, low: price, close: price,
            volume: volume,
            count: 1
        };
    } else {
        currentCandle.high = Math.max(currentCandle.high, price);
        currentCandle.low = Math.min(currentCandle.low, price);
        currentCandle.close = price;
        currentCandle.volume += volume;
        currentCandle.count = (currentCandle.count || 0) + 1;
    }

    // 실시간 지표 계산 실행
    calculateIndicators();

    // 백엔드 전략 엔진이 매매를 담당하므로 프론트엔드의 하드코딩 로직은 제거되었습니다.

    updateMetrics(tick);
    updateTable(tick);
    renderChart();
}

// --- UI 및 차트 업데이트 (이전과 동일) ---
function updateMetrics(tick) {
    const priceEl = document.getElementById('price-metric');
    const changeEl = document.getElementById('change-metric');
    const currentPrice = tick.trade_price;
    const prevPrice = parseFloat(priceEl.innerText.replace(/,/g, '')) || currentPrice;
    
    priceEl.innerText = currentPrice.toLocaleString();
    priceEl.style.color = currentPrice >= prevPrice ? '#FF4B4B' : '#0072FF';
    const changePercent = ((tick.change_price || 0) / (tick.prev_closing_price || currentPrice) * 100).toFixed(2);
    changeEl.innerText = `${changePercent}%`;
    changeEl.style.color = changePercent >= 0 ? '#FF4B4B' : '#0072FF';
}

function updateTable(tick) {
    const tbody = document.querySelector('#trade-table tbody');
    const row = document.createElement('tr');
    row.innerHTML = `
        <td>${new Date(tick.trade_timestamp).toLocaleTimeString()}</td>
        <td class="${tick.ask_bid === 'BID' ? 'bull' : 'bear'}">${tick.trade_price.toLocaleString()}</td>
        <td>${tick.trade_volume.toFixed(4)}</td>
        <td>${tick.ask_bid}</td>
    `;
    tbody.prepend(row);
    if (tbody.children.length > 10) tbody.lastChild.remove();
}
function renderChart() {
    if (!chart) return;
    const allCandles = [...candles, currentCandle].filter(c => c);
    if (allCandles.length === 0) return;

    // 중복 제거 및 시간순 정렬 (LW Charts 필수 사항)
    const uniqueCandles = [];
    const seenTs = new Set();
    allCandles.sort((a, b) => a.timestamp - b.timestamp).forEach(c => {
        if (!seenTs.has(c.timestamp)) {
            uniqueCandles.push(c);
            seenTs.add(c.timestamp);
        }
    });

    const showSMA = document.getElementById('show-sma').checked;
    const showBB = document.getElementById('show-bb').checked;
    const showVol = document.getElementById('show-volume').checked;
    const showRSI = document.getElementById('show-rsi').checked;

    // 데이터 변환 및 유효성 검사 (NaN/Undefined 제거)
    const candleData = uniqueCandles
        .filter(c => !isNaN(c.open) && !isNaN(c.high) && !isNaN(c.low) && !isNaN(c.close) && c.timestamp > 0)
        .map(c => ({
            time: parseInt(c.timestamp),
            open: parseFloat(c.open),
            high: parseFloat(c.high),
            low: parseFloat(c.low),
            close: parseFloat(c.close)
        }));

    if (candleData.length === 0) return;

    try {
        candleSeries.setData(candleData);
        // 처음 로드될 때만 화면에 맞춤
        if (!isLoaded) chart.timeScale().fitContent();

        if (showVol) {
            const volData = uniqueCandles
                .filter(c => !isNaN(c.volume))
                .map(c => ({
                    time: parseInt(c.timestamp),
                    value: parseFloat(c.volume),
                    color: c.close >= c.open ? 'rgba(255, 75, 75, 0.5)' : 'rgba(0, 114, 255, 0.5)'
                }));
            volumeSeries.setData(volData);
        } else {
            volumeSeries.setData([]);
        }

        if (showSMA) {
            const smaData = uniqueCandles
                .filter(c => c.sma && !isNaN(c.sma))
                .map(c => ({ time: parseInt(c.timestamp), value: parseFloat(c.sma) }));
            smaSeries.setData(smaData);
        } else {
            smaSeries.setData([]);
        }

        if (showBB) {
            const upperData = uniqueCandles.filter(c => c.bb_upper && !isNaN(c.bb_upper)).map(c => ({ time: parseInt(c.timestamp), value: parseFloat(c.bb_upper) }));
            const lowerData = uniqueCandles.filter(c => c.bb_lower && !isNaN(c.bb_lower)).map(c => ({ time: parseInt(c.timestamp), value: parseFloat(c.bb_lower) }));
            bbUpperSeries.setData(upperData);
            bbLowerSeries.setData(lowerData);
        } else {
            bbUpperSeries.setData([]);
            bbLowerSeries.setData([]);
        }

        if (showRSI) {
            const rsiData = uniqueCandles.filter(c => c.rsi && !isNaN(c.rsi)).map(c => ({ time: parseInt(c.timestamp), value: parseFloat(c.rsi) }));
            rsiSeries.setData(rsiData);
            chart.priceScale('rsi').applyOptions({ visible: true });
        } else {
            rsiSeries.setData([]);
            chart.priceScale('rsi').applyOptions({ visible: false });
        }
    } catch (err) {
        // silent error
    }

    // 탐색 모드 시점 고정
    if (isExplorerMode && explorerCenterIdx !== null) {
        const centerTs = allCandles[explorerCenterIdx]?.timestamp;
        if (centerTs) {
            chart.timeScale().scrollToPosition(0, false); 
        }
        document.getElementById('go-live-btn').style.display = 'block';
    } else {
        chart.timeScale().scrollToRealTime();
        document.getElementById('go-live-btn').style.display = 'none';
    }

    // 알림 마커 표시
    if (alertMarkerTs) {
        candleSeries.setMarkers([{
            time: alertMarkerTs,
            position: 'aboveBar',
            color: '#FFD700',
            shape: 'arrowDown',
            text: `🔔 ${new Date(alertMarkerTs * 1000).toLocaleTimeString()}`,
        }]);
    } else {
        candleSeries.setMarkers([]);
    }
}


async function drillDown(timestamp) {
    console.log(`[INFO] Entering Explorer Mode at ${new Date(timestamp * 1000).toLocaleString()}`);
    
    isExplorerMode = true;
    alertMarkerTs = timestamp;

    // 1초봉으로 전환
    const prevInterval = currentInterval;
    currentInterval = 1;
    updateIntervalUI(1);

    // 클릭한 시간 기준 전후 5분 데이터를 1초봉으로 충분히 가져옴 (표시 범위 확보)
    const startTs = (timestamp - 300) * 1000;
    const endTs = (timestamp + 300) * 1000;
    
    try {
        const res = await fetch(`/candles?symbol=${currentSymbol}&interval=1&start_ts=${startTs.toFixed(0)}&end_ts=${endTs.toFixed(0)}`);
        const data = await res.json();
        if (data && data.length > 0) {
            candles = data;
            currentCandle = null;
            isLoaded = true;
            
            // 데이터 내에서 해당 시각의 인덱스 찾기
            const allCandles = [...candles, currentCandle].filter(c => c);
            explorerCenterIdx = allCandles.findIndex(c => c.timestamp >= timestamp);
            if (explorerCenterIdx === -1) explorerCenterIdx = allCandles.length - 1;
            
            renderChart();
            // 알림 표시
            const badge = document.getElementById('status-badge');
            const originalText = badge.innerText;
            badge.innerText = 'DRILL-DOWN MODE (1S)';
            badge.style.background = '#FFA500';
            setTimeout(() => {
                badge.innerText = originalText;
                badge.style.background = originalText === 'CONNECTED' ? '#1a472a' : '#471a1a';
            }, 3000);
        }
    } catch (e) {
        console.error("Drill-down failed", e);
        currentInterval = prevInterval;
        document.getElementById('interval-select').value = prevInterval.toString();
    }
}

// 지표 토글 이벤트 리스너 추가
['show-sma', 'show-bb', 'show-volume', 'show-rsi'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.addEventListener('change', renderChart);
});

// --- 알림 UI 로직 ---
async function loadAlertHistory(silent = false) {
    const tbody = document.getElementById('alert-tbody');
    if (!tbody) return;
    
    if (!silent) {
        tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;padding:30px;">&#x23F3; 알림 기록 로딩 중...</td></tr>';
    }

    try {
        const res = await fetch('/alerts');
        const alerts = await res.json();
        const countEl = document.getElementById('alert-count');
        if (countEl) countEl.innerText = `${alerts.length}개 기록`;
        
        tbody.innerHTML = '';
        alerts.forEach(alert => {
            addAlertToTable(alert, false);
        });
    } catch (e) {
        tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;">&#x26A0;&#xFE0F; 알림 기록 로드 실패</td></tr>';
    }
}

function addAlertToTable(alert, prepend = true) {
    const tbody = document.getElementById('alert-tbody');
    if (!tbody) return;

    const tr = document.createElement('tr');
    tr.className = 'market-row';
    tr.innerHTML = `
        <td>${new Date(alert.timestamp || Date.now()).toLocaleString()}</td>
        <td><strong>${(alert.symbol || alert.code || '').replace('KRW-', '')}</strong></td>
        <td class="num">${(alert.price || 0).toLocaleString()}</td>
        <td class="num bull">+${alert.change || 0}%</td>
        <td class="num">${alert.buy_ratio || 0}%</td>
        <td>${alert.msg || ''}</td>
    `;
    
    tr.addEventListener('click', () => {
        const symbol = alert.symbol || alert.code;
        currentSymbol = symbol;
        document.getElementById('current-symbol').innerText = symbol;
        const select = document.getElementById('symbol-select');
        if (select) select.value = symbol;

        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ subscribe: symbol }));
        }

        menuItems.forEach(i => i.classList.remove('active'));
        menuItems[0].classList.add('active');
        allViews.forEach(v => { if(v) v.style.display = 'none'; });
        if(monitoringView) monitoringView.style.display = 'block';
        
        alertMarkerTs = (alert.timestamp || Date.now()) / 1000;
        drillDown((alert.timestamp || Date.now()) / 1000);
    });

    if (prepend) {
        tbody.prepend(tr);
        if (tbody.children.length > 100) tbody.lastChild.remove();
    } else {
        tbody.appendChild(tr);
    }
}

function showAlert(alert) {
    const alertView = document.getElementById('alert-view');
    if (alertView && alertView.style.display === 'block') {
        addAlertToTable(alert, true);
        const countEl = document.getElementById('alert-count');
        if (countEl) {
            const currentCount = parseInt(countEl.innerText) || 0;
            countEl.innerText = `${currentCount + 1}개 기록`;
        }
    }

    if (!isAlertEnabled) return;

    const container = document.getElementById('alert-container');
    if (!container) return;

    const symbol = alert.code || alert.symbol || '';
    const card = document.createElement('div');
    card.className = 'alert-card';
    card.innerHTML = `
        <div class="alert-header">
            <span class="alert-title">🚀 급등 신호</span>
            <span class="alert-time">${new Date().toLocaleTimeString()}</span>
        </div>
        <div class="alert-body">
            <strong>${symbol.replace('KRW-', '')}</strong> 종목이 급등 중입니다!
        </div>
        <div class="alert-footer">
            <span>변동: +${alert.change || 0}%</span>
            <span>매수비중: ${alert.buy_ratio || 0}%</span>
        </div>
    `;
    
    card.onclick = () => {
        currentSymbol = symbol;
        document.getElementById('current-symbol').innerText = symbol;
        const select = document.getElementById('symbol-select');
        if (select) select.value = symbol;
        
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ subscribe: symbol }));
        }
        
        isDrillDown = false;
        alertMarkerTs = null;
        candles = []; currentCandle = null;
        document.querySelector('#trade-table tbody').innerHTML = '';
        
        menuItems.forEach(i => i.classList.remove('active'));
        menuItems[0].classList.add('active');
        allViews.forEach(v => { if(v) v.style.display = 'none'; });
        if(monitoringView) monitoringView.style.display = 'block';

        loadHistory();
        loadRecentTrades();
        card.remove();
    };

    container.appendChild(card);
    
    setTimeout(() => {
        if (card.parentNode) {
            card.style.opacity = '0';
            setTimeout(() => card.remove(), 500);
        }
    }, 8000);
}

function connectWS() {
    ws = new WebSocket(`ws://${window.location.host}/ws`);
    const statusBadge = document.getElementById('status-badge');

    ws.onopen = () => {
        if (statusBadge) {
            statusBadge.innerText = 'CONNECTED';
            statusBadge.style.background = '#1a472a';
        }
        ws.send(JSON.stringify({ subscribe: currentSymbol }));
    };

    ws.onmessage = (e) => processTick(JSON.parse(e.data));
    
    ws.onclose = () => {
        if (statusBadge) {
            statusBadge.innerText = 'DISCONNECTED';
            statusBadge.style.background = '#471a1a';
        }
        setTimeout(connectWS, 3000);
    };
}

// --- 이벤트 리스너 ---
document.getElementById('symbol-select').addEventListener('change', (e) => {
    currentSymbol = e.target.value;
    document.getElementById('current-symbol').innerText = currentSymbol;
    exitExplorerMode(); // 종목 변경 시엔 실시간으로 복귀
    // 서버에 구독 종목 변경 요청
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ subscribe: currentSymbol }));
    }
    // 데이터 초기화 및 다시 불러오기
    candles = [];
    currentCandle = null;
    document.querySelector('#trade-table tbody').innerHTML = '';
    loadHistory();
    loadRecentTrades();
});

// 인터벌 버튼 이벤트 리스너
document.querySelectorAll('.interval-btn').forEach(btn => {
    btn.addEventListener('click', () => {
        const value = parseInt(btn.dataset.value);
        if (currentInterval === value) return;
        
        currentInterval = value;
        updateIntervalUI(value);
        loadHistory();
    });
});

function updateIntervalUI(value) {
    document.querySelectorAll('.interval-btn').forEach(btn => {
        if (parseInt(btn.dataset.value) === value) {
            btn.classList.add('active');
        } else {
            btn.classList.remove('active');
        }
    });
}

function exitExplorerMode() {
    isExplorerMode = false;
    alertMarkerTs = null;
    explorerCenterIdx = null;
    document.getElementById('go-live-btn').style.display = 'none';
    const badge = document.getElementById('status-badge');
    badge.style.background = (ws && ws.readyState === WebSocket.OPEN) ? '#1a472a' : '#471a1a';
    badge.innerText = (ws && ws.readyState === WebSocket.OPEN) ? 'CONNECTED' : 'DISCONNECTED';
    renderChart();
}

document.getElementById('go-live-btn')?.addEventListener('click', exitExplorerMode);


// --- 알림 설정 저장 ---
document.getElementById('btn-save-alerts')?.addEventListener('click', async () => {
    const settings = {
        price_threshold: parseFloat(document.getElementById('setting-price-threshold').value),
        vol_multiplier: parseFloat(document.getElementById('setting-vol-multiplier').value),
        rsi_sell_threshold: parseFloat(document.getElementById('setting-rsi-sell').value),
        rsi_buy_threshold: parseFloat(document.getElementById('setting-rsi-buy').value),
        enabled_alerts: { spike: true, volume: true, rsi: true, cross: true }
    };

    try {
        const res = await fetch('/settings/alerts', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(settings)
        });
        const data = await res.json();
        alert(data.message);
    } catch (e) {
        alert("설정 저장 실패");
    }
});

// --- 자동 매매 토글 ---
const btnTrading = document.getElementById('btn-toggle-trading');
const tradingStatus = document.getElementById('trading-status');

btnTrading?.addEventListener('click', () => {
    isAutoTrading = !isAutoTrading;
    if (isAutoTrading) {
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


// --- 메뉴 전환 로직 ---
const menuItems = document.querySelectorAll('.menu-item');
const monitoringView = document.getElementById('monitoring-view');
const marketView = document.getElementById('market-view');
const alertView = document.getElementById('alert-view');
const settingsView = document.getElementById('settings-view');

const allViews = [monitoringView, marketView, alertView, settingsView];

menuItems.forEach((item, index) => {
    item.addEventListener('click', () => {
        menuItems.forEach(i => i.classList.remove('active'));
        item.classList.add('active');
        allViews.forEach(v => { if(v) v.style.display = 'none'; });

        if (index === 0) {
            monitoringView.style.display = 'block';
        } else if (index === 1) {
            marketView.style.display = 'block';
            exitExplorerMode(); // 마켓 진입 시 실시간 모드로 복귀
            loadMarket();
        } else if (index === 2) {
            alertView.style.display = 'block';
            loadAlertHistory();
        } else {
            settingsView.style.display = 'block';
            updateCollectorStatus();
            loadStrategies();
        }
    });
});

// --- 수집기 제어 로직 ---
const statusEl = document.getElementById('collector-status');
const btnToggle = document.getElementById('btn-toggle');
let isCollectorRunning = false;

async function updateCollectorStatus() {
    try {
        const res = await fetch('/collector/status');
        const data = await res.json();
        isCollectorRunning = data.is_running;
        
        if (isCollectorRunning) {
            statusEl.innerText = '실행 중';
            statusEl.style.color = '#4caf50';
            btnToggle.innerText = '⏹️ 수집 중단';
            btnToggle.className = 'btn danger';
        } else {
            statusEl.innerText = '중단됨';
            statusEl.style.color = '#FF4B4B';
            btnToggle.innerText = '▶️ 수집 시작';
            btnToggle.className = 'btn primary';
        }
        btnToggle.disabled = false;
    } catch (e) {
        console.error("Status check failed", e);
    }
}

btnToggle.addEventListener('click', async () => {
    btnToggle.disabled = true;
    const endpoint = isCollectorRunning ? '/collector/stop' : '/collector/start';
    const res = await fetch(endpoint, { method: 'POST' });
    const data = await res.json();
    console.log(data.message);
    await updateCollectorStatus();
});

// --- 데이터베이스 관리 로직 ---
const btnCleanup = document.getElementById('btn-cleanup');
const cleanupDateInput = document.getElementById('cleanup-date');

btnCleanup.addEventListener('click', async () => {
    const selectedDate = cleanupDateInput.value;
    if (!selectedDate) {
        alert("삭제할 기준 날짜를 선택해주세요.");
        return;
    }

    if (!confirm(`${selectedDate} 이전의 모든 데이터를 영구적으로 삭제하시겠습니까?`)) {
        return;
    }

    btnCleanup.disabled = true;
    btnCleanup.innerText = "삭제 중...";

    try {
        const res = await fetch(`/data/cleanup?date=${selectedDate}`, { method: 'POST' });
        const data = await res.json();
        alert(data.message);
    } catch (e) {
        alert("오류가 발생했습니다.");
    } finally {
        btnCleanup.disabled = false;
        btnCleanup.innerText = "선택 날짜 이전 삭제";
    }
});

// --- 전략 관리 로직 ---
async function loadStrategies() {
    const listEl = document.getElementById('strategy-list');
    if (!listEl) return;

    try {
        const res = await fetch('/api/strategies');
        const strategies = await res.json();
        renderStrategyCards(strategies);
    } catch (e) {
        listEl.innerHTML = '<p class="status-text">전략 정보를 불러오는데 실패했습니다.</p>';
    }
}

function renderStrategyCards(strategies) {
    const listEl = document.getElementById('strategy-list');
    listEl.innerHTML = '';

    strategies.forEach(s => {
        const card = document.createElement('div');
        card.className = 'strategy-item';
        
        let paramsHtml = '';
        for (const [key, info] of Object.entries(s.params)) {
            const inputType = info.type === 'str' ? 'text' : 'number';
            const stepAttr = info.type === 'float' ? 'step="any"' : (info.type === 'int' ? 'step="1"' : '');
            paramsHtml += `
                <div class="param-row">
                    <label title="${info.description}">${key}</label>
                    <input type="${inputType}" ${stepAttr} class="dark-input param-input" 
                           data-strategy="${s.id}" data-key="${key}" data-type="${info.type}"
                           value="${info.current !== undefined ? info.current : info.default}">
                </div>
            `;
        }

        const isEnabled = s.enabled !== false;
        card.className = `strategy-item ${isEnabled ? '' : 'disabled'}`;
        card.style.opacity = isEnabled ? '1' : '0.5';

        card.innerHTML = `
            <h4>
                ${s.name}
                <div style="display: flex; align-items: center; gap: 5px;">
                    <span class="badge" style="font-size: 0.7rem; background: ${isEnabled ? '#4A90E2' : '#666'};">${isEnabled ? '활성' : '비활성'}</span>
                    <button class="btn sm ${isEnabled ? 'danger' : 'primary'}" onclick="toggleStrategyStatus('${s.id}', ${isEnabled})" style="padding: 2px 8px; font-size: 0.7rem;">
                        ${isEnabled ? '사용 안함' : '사용함'}
                    </button>
                </div>
            </h4>
            <div class="desc">${s.description}</div>
            <div class="strategy-params">
                ${paramsHtml}
            </div>
            <div class="strategy-actions">
                <button class="btn primary sm" onclick="saveStrategyParams('${s.id}')" style="width: 100%;">설정 저장</button>
            </div>
        `;
        listEl.appendChild(card);
    });
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
        const res = await fetch(`/api/strategies/${strategyId}/params`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(params)
        });
        const data = await res.json();
        alert(`${strategyId} 전략 설정이 저장되었습니다.`);
        loadStrategies(); // 최신 상태로 갱신
    } catch (e) {
        alert("설정 저장 실패");
    }
}

// 전역 범위에서 접근 가능하도록 노출
window.saveStrategyParams = saveStrategyParams;
window.toggleStrategyStatus = toggleStrategyStatus;

async function toggleStrategyStatus(strategyId, currentEnabled) {
    const endpoint = currentEnabled ? `/api/strategies/${strategyId}` : `/api/strategies/${strategyId}/enable`;
    const method = currentEnabled ? 'DELETE' : 'POST';

    try {
        const res = await fetch(endpoint, { method: method });
        const data = await res.json();
        loadStrategies();
    } catch (e) {
        alert("상태 변경 실패");
    }
}

// --- 알림 관리 로직 ---
function toggleAlerts() {
    isAlertEnabled = !isAlertEnabled;
    const btn = document.getElementById('btn-toggle-alerts');
    if (btn) {
        btn.innerText = isAlertEnabled ? '🔔 알림 팝업: ON' : '🔕 알림 팝업: OFF';
        btn.className = `btn sm ${isAlertEnabled ? 'primary' : ''}`;
    }
}

async function clearAlertHistory() {
    if (!confirm("정말로 모든 알림 내역을 삭제하시겠습니까? 이 작업은 되돌릴 수 없습니다.")) {
        return;
    }

    try {
        const res = await fetch('/api/alerts', { method: 'DELETE' });
        const data = await res.json();
        loadAlertHistory(); // 목록 갱신
        alert(data.message);
    } catch (e) {
        alert("내역 삭제 실패");
    }
}

window.toggleAlerts = toggleAlerts;
window.clearAlertHistory = clearAlertHistory;

// 주기적으로 상태 체크 (설정 화면이 보일 때만)
setInterval(() => {
    if (settingsView.style.display === 'block') {
        updateCollectorStatus();
    }
}, 2000);

async function loadRecentTrades() {
    try {
        const res = await fetch(`/trades?symbol=${currentSymbol}&limit=10`);
        const trades = await res.json();
        const tbody = document.querySelector('#trade-table tbody');
        tbody.innerHTML = '';
        trades.reverse().forEach(tick => updateTable(tick));
    } catch (e) { console.error("History trades load failed", e); }
}

// --- 마켓 페이지 로직 ---
let marketData = [];

function formatPrice(price) {
    if (price >= 1000000) return (price / 1000000).toFixed(2) + 'M';
    if (price >= 1000) return price.toLocaleString();
    return price.toFixed(price < 1 ? 4 : 2);
}

function formatVolume(vol) {
    if (vol >= 1e12) return (vol / 1e12).toFixed(1) + '조';
    if (vol >= 1e8) return (vol / 1e8).toFixed(1) + '억';
    return (vol / 1e4).toFixed(0) + '만';
}

function renderMarketTable(data) {
    const tbody = document.getElementById('market-tbody');
    tbody.innerHTML = '';
    data.forEach((coin, idx) => {
        const ticker = coin.market.replace('KRW-', '');
        const rate = coin.signed_change_rate * 100;
        const rateClass = rate >= 0 ? 'bull' : 'bear';
        const rateStr = (rate >= 0 ? '+' : '') + rate.toFixed(2) + '%';
        const iconUrl = `https://static.upbit.com/logos/${ticker}.png`;

        const tr = document.createElement('tr');
        tr.className = 'market-row';
        tr.innerHTML = `
            <td class="rank">${idx + 1}</td>
            <td class="coin-cell">
                <img src="${iconUrl}" alt="${ticker}" class="coin-icon"
                     onerror="this.style.display='none'">
                <div class="coin-names">
                    <span class="coin-kr">${coin.korean_name}</span>
                    <span class="coin-code">${ticker}</span>
                </div>
            </td>
            <td class="num">${formatPrice(coin.trade_price)}</td>
            <td class="num ${rateClass}">${rateStr}</td>
            <td class="num">${formatPrice(coin.high_price)}</td>
            <td class="num">${formatPrice(coin.low_price)}</td>
            <td class="num secondary">${formatVolume(coin.acc_trade_price_24h)}</td>
        `;
        // 클릭 시 모니터링 페이지로 전환
        tr.addEventListener('click', () => {
            currentSymbol = coin.market;
            document.getElementById('current-symbol').innerText = coin.market;
            const select = document.getElementById('symbol-select');
            if (select) select.value = coin.market;
            // WebSocket 구독 갱신
            if (ws && ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify({ subscribe: coin.market }));
            }
            // 모니터링 메뉴로 전환
            menuItems.forEach(i => i.classList.remove('active'));
            menuItems[0].classList.add('active');
            allViews.forEach(v => v.style.display = 'none');
            monitoringView.style.display = 'block';
            exitExplorerMode(); // 종목 선택 시 실시간 모드로 복귀
            candles = []; currentCandle = null;
            document.querySelector('#trade-table tbody').innerHTML = '';
            loadHistory();
            loadRecentTrades();
        });
        tbody.appendChild(tr);
    });
}

async function loadMarket() {
    const tbody = document.getElementById('market-tbody');
    tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;padding:30px;">&#x23F3; 데이터 로딩 중...</td></tr>';
    try {
        const res = await fetch('/market');
        marketData = await res.json();
        document.getElementById('market-count').innerText = `${marketData.length}종목`;
        renderMarketTable(marketData);
    } catch (e) {
        tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;">&#x26A0;&#xFE0F; 데이터 로드 실패</td></tr>';
    }
}

// 실시간 검색 필터
document.getElementById('market-search').addEventListener('input', (e) => {
    const q = e.target.value.toLowerCase();
    if (!q) { renderMarketTable(marketData); return; }
    const filtered = marketData.filter(c =>
        c.korean_name.toLowerCase().includes(q) ||
        c.market.toLowerCase().includes(q)
    );
    renderMarketTable(filtered);
});

async function loadSymbols() {
    try {
        const res = await fetch('/symbols');
        const symbols = await res.json();
        const select = document.getElementById('symbol-select');
        select.innerHTML = '';
        symbols.forEach(sym => {
            const opt = document.createElement('option');
            opt.value = sym;
            opt.textContent = sym.replace('KRW-', '');
            if (sym === currentSymbol) opt.selected = true;
            select.appendChild(opt);
        });
    } catch (e) { console.error("Symbol list load failed", e); }
}

async function init() {
    await loadSymbols();
    await loadHistory();
    await loadRecentTrades();
    loadMarket();
    connectWS();
    updateCollectorStatus();
}

window.onload = () => {
    init();
};
