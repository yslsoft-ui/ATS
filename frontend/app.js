const state = {
    candles: [],
    currentCandle: null,
    currentInterval: 60,
    isLoaded: false,
    currentSymbol: "KRW-BTC",
    currentPortfolioId: 'default',
    ws: null,
    isExplorerMode: false,
    explorerCenterIdx: null,
    savedBarSpacing: 30,
    isAutoTrading: false,
    alertMarkerTs: null,
    isAlertEnabled: false,
    currentPortfolioData: null,
    autoScroll: true, // 자동 스크롤 활성화 여부 [NEW]
    activeAssetDetail: null // 현재 열려있는 상세 모달의 종목 코드 [NEW]
};

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
        leftPriceScale: {
            visible: true,
            borderColor: '#333',
            scaleMargins: { top: 0.05, bottom: 0.35 },
        },
        rightPriceScale: {
            visible: false,
        },
        timeScale: {
            borderColor: '#333',
            timeVisible: true,
            secondsVisible: true,
            barSpacing: state.savedBarSpacing,
            // 하단 시간축 레이블 포맷터 추가
            tickMarkFormatter: (time, tickMarkType, locale) => {
                const date = new Date(time * 1000);
                const options = { timeZone: 'Asia/Seoul', hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' };
                return date.toLocaleTimeString('ko-KR', options);
            },
        },
        localization: {
            locale: 'ko-KR',
            priceFormatter: price => price.toLocaleString(),
            timeFormatter: timestamp => {
                const date = new Date(timestamp * 1000);
                return date.toLocaleTimeString('ko-KR', {
                    timeZone: 'Asia/Seoul',
                    hour: '2-digit',
                    minute: '2-digit',
                    second: '2-digit',
                    hour12: false
                });
            },
        },
    });

    candleSeries = chart.addCandlestickSeries({
        upColor: '#FF4B4B',
        downColor: '#0072FF',
        borderDownColor: '#0072FF',
        borderUpColor: '#FF4B4B',
        wickDownColor: '#0072FF',
        wickUpColor: '#FF4B4B',
        priceScaleId: 'left',
    });

    volumeSeries = chart.addHistogramSeries({
        color: '#26a69a',
        priceFormat: { type: 'volume' },
        priceScaleId: '', // 서브 레이어 (본문 축 영향 없음)
    });

    volumeSeries.priceScale().applyOptions({
        scaleMargins: { top: 0.7, bottom: 0.15 },
    });

    smaSeries = chart.addLineSeries({ color: '#FFA500', lineWidth: 2, title: 'SMA(20)', priceScaleId: 'left' });
    bbUpperSeries = chart.addLineSeries({ color: 'rgba(173, 216, 230, 0.4)', lineWidth: 1, lineStyle: 2, priceScaleId: 'left' });
    bbLowerSeries = chart.addLineSeries({ color: 'rgba(173, 216, 230, 0.4)', lineWidth: 1, lineStyle: 2, priceScaleId: 'left' });

    // RSI는 별도 스케일 사용 (0-100)
    rsiSeries = chart.addLineSeries({
        color: '#FF00FF',
        lineWidth: 1.5,
        title: 'RSI(14)',
        priceScaleId: 'rsi-left',
    });

    chart.priceScale('rsi-left').applyOptions({
        scaleMargins: { top: 0.85, bottom: 0 },
        visible: false,
        alignLabels: true,
        position: 'left',
    });

    // 클릭 이벤트 (Drill-down)
    chart.subscribeClick(param => {
        if (!param.time || param.point === undefined) return;
        drillDown(param.time);
    });

    // 툴팁 구현 (Floating Tooltip - 중복 생성 방지)
    let tooltip = chartDiv.querySelector('.chart-floating-tooltip');
    if (!tooltip) {
        tooltip = document.createElement('div');
        tooltip.className = 'chart-floating-tooltip';
        chartDiv.appendChild(tooltip);
    }

    chart.subscribeCrosshairMove(param => {
        if (!param.time || param.point === undefined || !param.seriesData.get(candleSeries)) {
            tooltip.style.display = 'none';
            return;
        }

        const data = param.seriesData.get(candleSeries);
        const smaData = param.seriesData.get(smaSeries);
        const rsiData = param.seriesData.get(rsiSeries);

        // 전역 변수 state.candles와 state.currentCandle에서 데이터 찾기
        const allLocal = [...state.candles, state.currentCandle].filter(c => c);
        const rawCandle = allLocal.find(c => c.timestamp === param.time);

        tooltip.style.display = 'block';
        const price = data.close.toLocaleString();
        const dateObj = new Date(param.time * 1000);
        const timeStr = dateObj.getFullYear() + ". " + (dateObj.getMonth() + 1) + ". " + dateObj.getDate() + ". " + 
                        dateObj.getHours().toString().padStart(2, '0') + ":" + 
                        dateObj.getMinutes().toString().padStart(2, '0') + ":" + 
                        dateObj.getSeconds().toString().padStart(2, '0');

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

        // 위치 계산 (오프셋을 대폭 늘리고 모든 정렬 방해 요소 강제 제거)
        const y = param.point.y + 40; // 포인터 아래로 충분히
        let x = param.point.x + 80; // 포인터 오른쪽으로 충분히
        if (x > chartDiv.clientWidth - 240) x = param.point.x - 250;

        // cssText를 통해 모든 위치 관련 속성을 최우선순위로 강제 주입
        tooltip.style.cssText = `
            display: block !important; 
            left: ${x}px !important; 
            top: ${y}px !important; 
            transform: none !important; 
            margin: 0 !important;
            position: absolute !important;
        `;
    });

    window.addEventListener('resize', () => {
        chart.resize(chartDiv.clientWidth, 500);
    });

    // 사용자의 직접적인 조작(클릭, 드래그, 휠)이 발생하면 즉시 자동 스크롤 끄기
    const stopAutoScroll = () => {
        if (state.autoScroll) {
            state.autoScroll = false;
            console.log("[INFO] User interaction detected: AutoScroll OFF");
            document.getElementById('go-live-btn').style.display = 'block';
        }
    };

    chartDiv.addEventListener('mousedown', stopAutoScroll);
    chartDiv.addEventListener('wheel', stopAutoScroll, { passive: true });

    // barSpacing 저장용으로만 사용
    chart.timeScale().subscribeVisibleLogicalRangeChange(() => {
        const spacing = chart.timeScale().options().barSpacing;
        if (spacing && spacing !== state.savedBarSpacing) {
            state.savedBarSpacing = spacing;
        }
    });

    // 마우스 우클릭 -> 실시간 모드 복귀 [NEW]
    chartDiv.addEventListener('contextmenu', (e) => {
        e.preventDefault();
        if (!state.autoScroll || state.isExplorerMode) {
            console.log("[INFO] Right-click detected: Returning to Real-time Mode");
            exitExplorerMode();
            showAlert("실시간 모드로 복귀합니다.", "info");
        }
    });
}

// --- 과거 데이터 불러오기 (PULL) ---
async function loadHistory() {
    state.isLoaded = false;
    try {
        const history = await fetchAPI(`/candles?symbol=${state.currentSymbol}&interval=${state.currentInterval}&limit=10000`);

        if (history && history.length > 0) {
            state.candles = history;

            if (state.candles.length > 0) {
                state.currentCandle = state.candles.pop();
            }
        } else {
            state.candles = [];
            state.currentCandle = null;
        }

        if (!chart) initLWChart();

        // 저장된 폭 적용 및 렌더링
        chart.timeScale().applyOptions({ barSpacing: state.savedBarSpacing });
        renderChart();

        // 시점 이동 로직
        if (state.autoScroll) {
            chart.timeScale().scrollToRealTime();
        } else if (state.alertMarkerTs) {
            // 마커가 찍힌 시점으로 이동 (인터벌이 바뀌어도 같은 시점 유지)
            const all = [...state.candles, state.currentCandle].filter(c => c);
            const idx = all.findIndex(c => c.timestamp >= state.alertMarkerTs);
            if (idx !== -1) {
                // 특정 인덱스가 화면 중앙에 오도록 이동 (끝에서부터의 오프셋 계산)
                const offset = idx - all.length + 10;
                chart.timeScale().scrollToPosition(offset, false);
            }
        }
        state.isLoaded = true;
    } catch (e) {
        console.error("History load failed", e);
    }
}

// --- 실시간 지표 계산 엔진 ---
function calculateIndicators() {
    const allCandles = [...state.candles, state.currentCandle].filter(c => c);
    if (allCandles.length < 20) return;

    const closePrices = allCandles.map(c => c.close);
    const lastIdx = allCandles.length - 1;

    // 1. SMA (20)
    const sma20Range = closePrices.slice(-20);
    const sma20 = sma20Range.reduce((a, b) => a + b, 0) / 20;
    state.currentCandle.sma = sma20;

    // 2. Bollinger Bands (20, 2)
    const variance = sma20Range.reduce((a, b) => a + Math.pow(b - sma20, 2), 0) / 20;
    const stdDev = Math.sqrt(variance);
    state.currentCandle.bb_upper = sma20 + (stdDev * 2);
    state.currentCandle.bb_lower = sma20 - (stdDev * 2);

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
        if (avgLoss === 0) state.currentCandle.rsi = 100;
        else {
            const rs = avgGain / avgLoss;
            state.currentCandle.rsi = 100 - (100 / (1 + rs));
        }
    }
}

// --- 캔들 생성 및 업데이트 로직 (PUSH) ---
function processTick(tick) {
    // 0. 전략 실시간 상태(Audit Log) 처리 [NEW]
    if (tick.type === 'strategy_status') {
        updateStrategyStatusUI(tick);
        return;
    }

    // 0.1 글로벌 알림(Alert) 처리
    if (tick.type === 'alert') {
        showAlert(tick);
        // 만약 거래 알림이면 포트폴리오 즉시 갱신
        if (tick.alert_type === 'trade') {
            loadPortfolio();
        }
        return;
    }

    if (!state.isLoaded) return;
    if (tick.code !== state.currentSymbol) return; // 선택된 종목이 아니면 무시

    const timestamp = Math.floor(tick.trade_timestamp / 1000);
    const bucket = Math.floor(timestamp / state.currentInterval) * state.currentInterval;
    const price = tick.trade_price;
    const volume = tick.trade_volume;

    if (!state.currentCandle || state.currentCandle.timestamp !== bucket) {
        if (state.currentCandle) {
            state.candles.push(state.currentCandle);
            if (state.candles.length > 500) state.candles.shift();
        }
        state.currentCandle = {
            timestamp: bucket,
            open: price, high: price, low: price, close: price,
            volume: volume,
            count: 1
        };
    } else {
        state.currentCandle.high = Math.max(state.currentCandle.high, price);
        state.currentCandle.low = Math.min(state.currentCandle.low, price);
        state.currentCandle.close = price;
        state.currentCandle.volume += volume;
        state.currentCandle.count = (state.currentCandle.count || 0) + 1;
    }

    // 실시간 지표 계산 실행
    calculateIndicators();

    // 백엔드 전략 엔진이 매매를 담당하므로 프론트엔드의 하드코딩 로직은 제거되었습니다.

    updateMetrics(tick);
    updateTable(tick);
    updatePortfolioRealtime(tick);
    renderChart();
}

function updatePortfolioRealtime(tick) {
    if (!state.currentPortfolioData || !portfolioView || portfolioView.style.display === 'none') return;
    
    const position = state.currentPortfolioData.positions.find(p => p.symbol === tick.code);
    if (!position) return;

    // 실시간 가격 기반 수익률 계산
    const currentPrice = tick.trade_price;
    const profitRate = ((currentPrice - position.avg_price) / position.avg_price * 100).toFixed(2);
    
    // 테이블 내 해당 종목 행 찾기 및 업데이트
    const rows = document.querySelectorAll('#positions-tbody tr');
    rows.forEach(row => {
        if (row.cells[0]?.innerText === tick.code.replace('KRW-', '')) {
            const rateCell = row.cells[3];
            rateCell.innerText = `${profitRate}%`;
            rateCell.className = `num ${profitRate >= 0 ? 'bull' : 'bear'}`;
            rateCell.classList.add('value-updating');
            setTimeout(() => rateCell.classList.remove('value-updating'), 400);
        }
    });

    // 전체 평가액 및 수익률 업데이트
    let newTotalValue = state.currentPortfolioData.cash;
    state.currentPortfolioData.positions.forEach(p => {
        const coin = (p.symbol === tick.code) ? { trade_price: currentPrice } : marketData.find(m => m.market === p.symbol);
        const price = coin ? coin.trade_price : p.avg_price;
        newTotalValue += p.quantity * price;
    });

    const totalValueEl = document.getElementById('port-total-value');
    const prevValue = parseFloat(totalValueEl.innerText.replace(/,/g, '')) || newTotalValue;
    
    if (Math.abs(newTotalValue - prevValue) > 0.01) {
        totalValueEl.innerText = newTotalValue.toLocaleString();
        totalValueEl.classList.add('value-updating');
        setTimeout(() => totalValueEl.classList.remove('value-updating'), 400);
        
        // 전체 수익률 계산 (기본 모의투자는 원금이 10,000,000원이라고 가정하거나 데이터베이스에서 가져와야 함)
        // 여기서는 간단하게 초기 총 가치와의 차이로 계산 (임시 원금 1억 설정 또는 데이터 기반)
        const initialValue = 100000000; // 예시 원금
        const totalRoi = ((newTotalValue - initialValue) / initialValue * 100).toFixed(2);
        const roiEl = document.getElementById('port-total-roi');
        roiEl.innerText = `${totalRoi}%`;
        roiEl.className = `value ${totalRoi >= 0 ? 'bull' : 'bear'}`;
    }
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

function updateHeaderInfo(symbol) {
    const coin = marketData.find(c => c.market === symbol);
    const ticker = symbol.replace('KRW-', '');

    const iconEl = document.getElementById('header-coin-icon');
    const krNameEl = document.getElementById('current-symbol-kr');
    const codeEl = document.getElementById('current-symbol-code');

    if (coin) {
        krNameEl.innerText = coin.korean_name;
        iconEl.src = `https://static.upbit.com/logos/${ticker}.png`;
        iconEl.style.display = 'block';
    } else {
        krNameEl.innerText = ticker;
        iconEl.style.display = 'none';
    }
    codeEl.innerText = symbol;
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
    const allCandles = [...state.candles, state.currentCandle].filter(c => c);
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
    let lastAssignedColor = 'rgba(255, 75, 75, 0.5)'; // 기본값 빨강
    let lastAssignedCandleColor = '#FF4B4B';

    const candleData = uniqueCandles
        .filter(c => !isNaN(c.open) && !isNaN(c.high) && !isNaN(c.low) && !isNaN(c.close) && c.timestamp > 0)
        .map((c, i, arr) => {
            let color = lastAssignedCandleColor;
            if (c.close > c.open) {
                color = '#FF4B4B';
            } else if (c.close < c.open) {
                color = '#0072FF';
            } else {
                // close == open 인 경우 이전 종가와 비교
                const prev = i > 0 ? arr[i-1] : null;
                if (prev) {
                    if (c.close > prev.close) color = '#FF4B4B';
                    else if (c.close < prev.close) color = '#0072FF';
                    else color = lastAssignedCandleColor;
                }
            }
            lastAssignedCandleColor = color;
            return {
                time: parseInt(c.timestamp),
                open: parseFloat(c.open),
                high: parseFloat(c.high),
                low: parseFloat(c.low),
                close: parseFloat(c.close),
                color: color,
                wickColor: color,
                borderColor: color
            };
        });

    if (candleData.length === 0) return;

    try {
        candleSeries.setData(candleData);

        // 초기 로드 시 저장된 폭 적용 및 최신 시점으로 이동
        if (!state.isLoaded) {
            chart.timeScale().applyOptions({ barSpacing: state.savedBarSpacing });
            chart.timeScale().scrollToRealTime();
        }

        if (showVol) {
            const volData = uniqueCandles
                .filter(c => !isNaN(c.volume))
                .map((c, i, arr) => {
                    let color = lastAssignedColor;
                    const bull = 'rgba(255, 75, 75, 0.5)';
                    const bear = 'rgba(0, 114, 255, 0.5)';

                    if (c.close > c.open) {
                        color = bull;
                    } else if (c.close < c.open) {
                        color = bear;
                    } else {
                        const prev = i > 0 ? arr[i-1] : null;
                        if (prev) {
                            if (c.close > prev.close) color = bull;
                            else if (c.close < prev.close) color = bear;
                            else color = lastAssignedColor;
                        }
                    }
                    lastAssignedColor = color;
                    return {
                        time: parseInt(c.timestamp),
                        value: parseFloat(c.volume),
                        color: color
                    };
                });
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
            chart.priceScale('rsi-left').applyOptions({ visible: true });
        } else {
            rsiSeries.setData([]);
            chart.priceScale('rsi-left').applyOptions({ visible: false });
        }
    } catch (err) {
        // silent error
    }

    if (state.isExplorerMode) {
        document.getElementById('go-live-btn').style.display = 'block';
    } else if (state.autoScroll) {
        // 자동 스크롤 모드일 때만 맨 끝으로 이동
        chart.timeScale().scrollToRealTime();
        document.getElementById('go-live-btn').style.display = 'none';
    }

    // 알림 마커 표시
    if (state.alertMarkerTs) {
        candleSeries.setMarkers([{
            time: state.alertMarkerTs,
            position: 'aboveBar',
            color: '#FFD700',
            shape: 'arrowDown',
            text: `🔔 ${new Date(state.alertMarkerTs * 1000).toLocaleTimeString()}`,
        }]);
    } else {
        candleSeries.setMarkers([]);
    }
}


async function drillDown(timestamp) {
    console.log(`[INFO] Setting marker at ${new Date(timestamp * 1000).toLocaleString()}`);

    state.autoScroll = false;
    state.alertMarkerTs = timestamp;
    
    document.getElementById('go-live-btn').style.display = 'block';
    renderChart();
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
        state.currentSymbol = symbol;
        updateHeaderInfo(symbol);
        const select = document.getElementById('symbol-select');
        if (select) select.value = symbol;

        if (state.ws && state.ws.readyState === WebSocket.OPEN) {
            state.ws.send(JSON.stringify({ subscribe: symbol }));
        }

        menuItems.forEach(i => i.classList.remove('active'));
        menuItems[0].classList.add('active');
        allViews.forEach(v => { if (v) v.style.display = 'none'; });
        if (monitoringView) monitoringView.style.display = 'block';

        state.alertMarkerTs = (alert.timestamp || Date.now()) / 1000;
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

    if (!state.isAlertEnabled) return;

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
        state.currentSymbol = symbol;
        updateHeaderInfo(symbol);
        const select = document.getElementById('symbol-select');
        if (select) select.value = symbol;

        if (state.ws && state.ws.readyState === WebSocket.OPEN) {
            state.ws.send(JSON.stringify({ subscribe: symbol }));
        }

        isDrillDown = false;
        state.alertMarkerTs = null;
        state.candles = []; state.currentCandle = null;
        document.querySelector('#trade-table tbody').innerHTML = '';

        menuItems.forEach(i => i.classList.remove('active'));
        menuItems[0].classList.add('active');
        allViews.forEach(v => { if (v) v.style.display = 'none'; });
        if (monitoringView) monitoringView.style.display = 'block';

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
    state.ws = new WebSocket(`ws://${window.location.host}/ws`);
    const statusBadge = document.getElementById('status-badge');

    state.ws.onopen = () => {
        if (statusBadge) {
            statusBadge.innerText = 'CONNECTED';
            statusBadge.style.background = '#1a472a';
        }
        state.ws.send(JSON.stringify({ subscribe: state.currentSymbol }));
    };

    state.ws.onmessage = (e) => processTick(JSON.parse(e.data));

    state.ws.onclose = () => {
        if (statusBadge) {
            statusBadge.innerText = 'DISCONNECTED';
            statusBadge.style.background = '#471a1a';
        }
        setTimeout(connectWS, 3000);
    };
}


// --- 이벤트 리스너 ---
document.getElementById('symbol-select').addEventListener('change', (e) => {
    state.currentSymbol = e.target.value;
    updateHeaderInfo(state.currentSymbol);
    exitExplorerMode(); // 종목 변경 시엔 실시간 모드로 복귀
    // 서버에 구독 종목 변경 요청
    if (state.ws && state.ws.readyState === WebSocket.OPEN) {
        state.ws.send(JSON.stringify({ subscribe: state.currentSymbol }));
    }
    // 데이터 초기화 및 다시 불러오기
    state.candles = [];
    state.currentCandle = null;
    document.querySelector('#trade-table tbody').innerHTML = '';
    loadHistory();
    loadRecentTrades();
});

// 인터벌 버튼 이벤트 리스너
document.querySelectorAll('.interval-btn').forEach(btn => {
    btn.addEventListener('click', () => {
        const value = parseInt(btn.dataset.value);
        if (state.currentInterval === value) return;

        state.currentInterval = value;
        updateIntervalUI(value);
        
        // 인터벌 변경 시에도 현재 보고 있는 시점을 유지하기 위해 
        // 별도의 alertMarkerTs가 없다면 현재 화면 중앙의 시간을 임시 마커로 잡을 수 있음
        // 지금은 명시적으로 클릭한 마커가 있을 때 그 시점을 유지하도록 설계됨
        
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
    state.isExplorerMode = false;
    state.autoScroll = true; // 자동 스크롤 재개
    state.alertMarkerTs = null;
    explorerCenterIdx = null;
    document.getElementById('go-live-btn').style.display = 'none';
    const badge = document.getElementById('status-badge');
    badge.style.background = (state.ws && state.ws.readyState === WebSocket.OPEN) ? '#1a472a' : '#471a1a';
    badge.innerText = (state.ws && state.ws.readyState === WebSocket.OPEN) ? 'CONNECTED' : 'DISCONNECTED';
    
    // 즉시 실시간 시점으로 이동
    chart.timeScale().scrollToRealTime();
    renderChart();
}

document.getElementById('go-live-btn')?.addEventListener('click', exitExplorerMode);



// --- 자동 매매 토글 ---
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


// --- DOM 요소 참조 ---
const menuItems = document.querySelectorAll('.menu-item');
const monitoringView = document.getElementById('monitoring-view');
const marketView = document.getElementById('market-view');
const alertView = document.getElementById('alert-view');
const strategyView = document.getElementById('strategy-view');
const portfolioView = document.getElementById('portfolio-view');
const settingsView = document.getElementById('settings-view');

const allViews = [monitoringView, marketView, alertView, strategyView, portfolioView, settingsView];

// --- 메뉴 전환 로직 ---
const viewInitializers = {
    'market-view': () => { exitExplorerMode(); loadMarket(); },
    'alert-view': () => loadAlertHistory(),
    'strategy-view': () => loadStrategies(),
    'portfolio-view': () => { loadPortfolioList(); loadPortfolio(); },
    'settings-view': () => updateCollectorStatus()
};

menuItems.forEach((item) => {
    item.addEventListener('click', () => {
        const viewId = item.getAttribute('data-view');
        if (!viewId) return;

        // 메뉴 활성화 상태 변경
        menuItems.forEach(i => i.classList.remove('active'));
        item.classList.add('active');

        // 뷰 전환
        allViews.forEach(v => { 
            if (v) v.style.display = (v.id === viewId) ? 'block' : 'none'; 
        });

        // 해당 뷰 초기화 함수 실행
        if (viewInitializers[viewId]) {
            viewInitializers[viewId]();
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

    // 타입별 정렬 (ENTRY -> BOTH -> EXIT)
    const typeOrder = { "ENTRY": 1, "BOTH": 2, "EXIT": 3 };
    strategies.sort((a, b) => (typeOrder[a.type] || 99) - (typeOrder[b.type] || 99));

    strategies.forEach(s => {
        const card = document.createElement('div');
        card.className = 'strategy-item';

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

        // 타입별 배지 색상
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
            <div class="strategy-actions">
                <button class="btn primary sm" onclick="saveStrategyParams('${s.id}')" style="width: 100%;">설정 저장</button>
            </div>
        `;
        listEl.appendChild(card);
    });
}

function updateStrategyStatusUI(status) {
    // 특정 전략 카드를 찾음
    const strategyId = status.strategy_id;
    const card = document.querySelector(`.strategy-item h4 span[data-id="${strategyId}"]`)?.closest('.strategy-item');
    if (!card) return;

    // 상태 표시 영역 찾기 또는 생성
    let statusEl = card.querySelector('.strategy-live-status');
    if (!statusEl) {
        statusEl = document.createElement('div');
        statusEl.className = 'strategy-live-status';
        card.querySelector('.desc').after(statusEl);
    }

    // 지표 데이터를 배지로 변환
    const indicators = status.indicators || {};
    const indicatorHtml = Object.entries(indicators).map(([k, v]) => {
        const val = typeof v === 'number' ? v.toFixed(2) : v;
        return `<span class="status-badge">${k}: ${val}</span>`;
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
        const res = await fetch(`/api/strategies/${strategyId}`, {
            method: 'PUT',
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
    state.isAlertEnabled = !state.isAlertEnabled;
    const btn = document.getElementById('btn-toggle-alerts');
    if (btn) {
        btn.innerText = state.isAlertEnabled ? '🔔 알림 팝업: ON' : '🔕 알림 팝업: OFF';
        btn.className = `btn sm ${state.isAlertEnabled ? 'primary' : ''}`;
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
        const trades = await fetchAPI(`/trades?symbol=${state.currentSymbol}&limit=10`);
        const tbody = document.querySelector('#trade-table tbody');
        tbody.innerHTML = '';
        trades.reverse().forEach(tick => updateTable(tick));
    } catch (e) { /* fetchAPI에서 이미 에러 로깅함 */ }
}

// --- 마켓 페이지 로직 ---
let marketData = [];

// (utils.js에 정의된 공통 함수 사용)

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
            state.currentSymbol = coin.market;
            updateHeaderInfo(coin.market);
            const select = document.getElementById('symbol-select');
            if (select) select.value = coin.market;
            // WebSocket 구독 갱신
            if (state.ws && state.ws.readyState === WebSocket.OPEN) {
                state.ws.send(JSON.stringify({ subscribe: coin.market }));
            }
            // 모니터링 메뉴로 전환
            menuItems.forEach(i => i.classList.remove('active'));
            menuItems[0].classList.add('active');
            allViews.forEach(v => v.style.display = 'none');
            monitoringView.style.display = 'block';
            exitExplorerMode(); // 종목 선택 시 실시간 모드로 복귀
            state.candles = []; state.currentCandle = null;
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
        marketData = await fetchAPI('/market');
        document.getElementById('market-count').innerText = `${marketData.length}종목`;
        renderMarketTable(marketData);
        // 초기 로드시 헤더 정보 업데이트
        updateHeaderInfo(state.currentSymbol);
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
        const symbols = await fetchAPI('/symbols');
        const select = document.getElementById('symbol-select');
        select.innerHTML = '';
        symbols.forEach(sym => {
            const opt = document.createElement('option');
            opt.value = sym;
            opt.textContent = sym.replace('KRW-', '');
            if (sym === state.currentSymbol) opt.selected = true;
            select.appendChild(opt);
        });
    } catch (e) { console.error("Symbol list load failed", e); }
}

async function init() {
    await loadSymbols();
    await loadHistory();
    await loadRecentTrades();
    loadMarket();
    loadPortfolioList();
    loadPortfolio();
    connectWS();
    updateCollectorStatus();
}

// --- 포트폴리오 로직 ---
async function loadPortfolioList() {
    const select = document.getElementById('portfolio-select');
    if (!select) return;

    try {
        const portfolios = await fetchAPI('/api/portfolios');
        
        select.innerHTML = '';
        portfolios.forEach(p => {
            const opt = document.createElement('option');
            opt.value = p.id;
            opt.textContent = p.name;
            if (p.id === state.currentPortfolioId) opt.selected = true;
            select.appendChild(opt);
        });
    } catch (e) {
        console.error("Portfolio list load failed", e);
    }
}

document.getElementById('portfolio-select')?.addEventListener('change', (e) => {
    state.currentPortfolioId = e.target.value;
    loadPortfolio();
});

async function loadPortfolio() {
    try {
        const data = await fetchAPI(`/api/portfolio?portfolio_id=${state.currentPortfolioId}`);
        state.currentPortfolioData = data; // 전역 저장
        
        // 요약 정보 업데이트
        document.getElementById('port-total-value').innerText = data.total_value.toLocaleString();
        document.getElementById('port-cash').innerText = data.cash.toLocaleString();
        
        // 실제 원금을 기반으로 수익률 계산
        const initialValue = data.initial_cash || 10000000; // 원금이 없으면 기본값 1000만
        const totalRoi = ((data.total_value - initialValue) / initialValue * 100).toFixed(2);
        const roiEl = document.getElementById('port-total-roi');
        roiEl.innerText = `${totalRoi}%`;
        roiEl.className = `value ${totalRoi >= 0 ? 'bull' : 'bear'}`;

        // 포지션 테이블 업데이트
        const posTbody = document.getElementById('positions-tbody');
        posTbody.innerHTML = '';
        if (data.positions.length === 0) {
            posTbody.innerHTML = '<tr><td colspan="4" style="text-align:center;padding:20px;">보유 종목이 없습니다.</td></tr>';
        } else {
            data.positions.forEach(pos => {
                const tr = document.createElement('tr');
                const coin = marketData.find(c => c.market === pos.symbol);
                const currentPrice = coin ? coin.trade_price : pos.avg_price;
                const profitRate = ((currentPrice - pos.avg_price) / pos.avg_price * 100).toFixed(2);
                const rateClass = profitRate >= 0 ? 'bull' : 'bear';

                tr.innerHTML = `
                    <td><strong>${pos.symbol.replace('KRW-', '')}</strong></td>
                    <td class="num">${pos.quantity.toFixed(4)}</td>
                    <td class="num">${pos.avg_price.toLocaleString()}</td>
                    <td class="num ${rateClass}">${profitRate}%</td>
                `;
                
                // 행 클릭 시 상세 모달 열기 [NEW]
                tr.onclick = () => showAssetDetails(pos.symbol);
                
                posTbody.appendChild(tr);
            });
        }

        // 히스토리 테이블 업데이트
        const histTbody = document.getElementById('port-history-tbody');
        histTbody.innerHTML = '';
        
        // 데이터가 없으면 안내 메시지
        if (data.history.length === 0) {
            histTbody.innerHTML = '<tr><td colspan="5" style="text-align:center;padding:20px;">거래 내역이 없습니다.</td></tr>';
        } else {
            // 최신순으로 정렬하여 표시
            const sortedHistory = [...data.history].reverse();
            sortedHistory.forEach(h => {
                const tr = document.createElement('tr');
                
                // Context 데이터를 배지로 변환
                let contextHtml = '';
                if (h.context) {
                    const ctx = typeof h.context === 'string' ? JSON.parse(h.context) : h.context;
                    contextHtml = Object.entries(ctx).map(([k, v]) => 
                        `<span class="ctx-badge">${k}: ${v}</span>`
                    ).join('');
                }

                tr.innerHTML = `
                    <td>${new Date(h.timestamp * 1000).toLocaleTimeString()}</td>
                    <td>${h.symbol.replace('KRW-', '')}</td>
                    <td class="${h.side === 'BUY' ? 'bull' : 'bear'}">${h.side}</td>
                    <td class="num">${h.price.toLocaleString()}</td>
                    <td class="num">${h.quantity.toFixed(4)}</td>
                    <td>
                        <div class="reason-cell">
                            <span class="reason-text">${h.reason || '-'}</span>
                            <div class="context-badges">${contextHtml}</div>
                        </div>
                    </td>
                `;
                histTbody.appendChild(tr);
            });
        }

        // 자산 비중 차트 업데이트 [NEW]
        renderAllocationChart(data);

        // 만약 상세 모달이 열려있다면 내용 갱신 [NEW]
        if (state.activeAssetDetail) {
            updateModalContent(state.activeAssetDetail);
        }

    } catch (e) {
        console.error("Portfolio load failed", e);
    }
}

// --- 자산 비중 시각화 로직 [NEW] ---
const ASSET_COLORS = [
    '#FF6384', '#36A2EB', '#FFCE56', '#4BC0C0', '#9966FF', 
    '#FF9F40', '#C9CBCF', '#7BC225', '#FF4500', '#1E90FF'
];

function renderAllocationChart(data) {
    const chartContainer = document.getElementById('portfolio-pie-chart');
    const legendContainer = document.getElementById('portfolio-legend');
    if (!chartContainer || !legendContainer) return;

    // 데이터 준비: 현금 + 보유 종목 가치
    const assets = [];
    
    // 현금 추가
    if (data.cash > 0) {
        assets.push({ 
            symbol: null, 
            label: 'CASH', 
            koreanName: '현금',
            value: data.cash, 
            color: '#444' 
        });
    }

    // 종목 추가
    data.positions.forEach((pos, idx) => {
        const coin = (marketData || []).find(c => c.market === pos.symbol);
        const currentPrice = coin ? coin.trade_price : pos.avg_price;
        const totalValue = pos.quantity * currentPrice;
        assets.push({ 
            symbol: pos.symbol,
            label: pos.symbol.replace('KRW-', ''), 
            koreanName: coin ? coin.korean_name : pos.symbol.replace('KRW-', ''),
            value: totalValue,
            color: ASSET_COLORS[idx % ASSET_COLORS.length]
        });
    });

    // 가치 순으로 정렬 (비중이 큰 것부터)
    assets.sort((a, b) => b.value - a.value);

    const total = data.total_value;
    chartContainer.innerHTML = '';
    legendContainer.innerHTML = '';

    if (total <= 0) return;

    // SVG 도넛 차트 생성
    const size = 200;
    const center = size / 2;
    const radius = 80;
    const strokeWidth = 30;
    const circumference = 2 * Math.PI * radius;
    
    let accumulatedPercent = 0;

    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", `0 0 ${size} ${size}`);
    svg.style.width = "100%";
    svg.style.height = "100%";

    // 중앙 텍스트 요소 미리 생성 [NEW]
    const centerText = document.createElementNS("http://www.w3.org/2000/svg", "text");
    centerText.setAttribute("x", "50%");
    centerText.setAttribute("y", "48%");
    centerText.setAttribute("text-anchor", "middle");
    centerText.setAttribute("fill", "white");
    centerText.setAttribute("font-size", "14px");
    centerText.setAttribute("font-weight", "bold");
    centerText.textContent = "자산 비중";

    const centerSubText = document.createElementNS("http://www.w3.org/2000/svg", "text");
    centerSubText.setAttribute("x", "50%");
    centerSubText.setAttribute("y", "62%");
    centerSubText.setAttribute("text-anchor", "middle");
    centerSubText.setAttribute("fill", "rgba(255,255,255,0.6)");
    centerSubText.setAttribute("font-size", "11px");
    centerSubText.textContent = "Total Assets";

    assets.forEach(asset => {
        const percent = (asset.value / total);
        if (percent < 0.001) return; // 0.1% 미만은 시각화에서 제외

        const circle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
        circle.setAttribute("cx", center);
        circle.setAttribute("cy", center);
        circle.setAttribute("r", radius);
        circle.setAttribute("fill", "transparent");
        circle.setAttribute("stroke", asset.color);
        circle.setAttribute("stroke-width", strokeWidth);
        circle.setAttribute("stroke-dasharray", `${percent * circumference} ${circumference}`);
        circle.setAttribute("stroke-dashoffset", -accumulatedPercent * circumference);
        circle.setAttribute("transform", `rotate(-90 ${center} ${center})`);
        circle.style.transition = "stroke-width 0.3s ease, stroke 0.3s ease";
        
        // 클릭 및 호버 기능 개선 [NEW]
        circle.style.cursor = 'pointer';
        circle.onclick = () => {
            if (asset.symbol) showAssetDetails(asset.symbol);
        };
        
        circle.onmouseover = () => {
            circle.setAttribute("stroke-width", strokeWidth + 10);
            centerText.textContent = asset.koreanName;
            centerSubText.textContent = `${asset.label} (${(percent * 100).toFixed(1)}%)`;
            centerText.setAttribute("fill", asset.color);
        };
        
        circle.onmouseout = () => {
            circle.setAttribute("stroke-width", strokeWidth);
            centerText.textContent = "자산 비중";
            centerSubText.textContent = "Total Assets";
            centerText.setAttribute("fill", "white");
        };

        // 애니메이션
        const anim = document.createElementNS("http://www.w3.org/2000/svg", "animate");
        anim.setAttribute("attributeName", "stroke-dashoffset");
        anim.setAttribute("from", circumference);
        anim.setAttribute("to", -accumulatedPercent * circumference);
        anim.setAttribute("dur", "0.8s");
        anim.setAttribute("fill", "freeze");
        circle.appendChild(anim);

        svg.appendChild(circle);

        // 범례(Legend) 추가
        const legendItem = document.createElement('div');
        legendItem.className = 'legend-item';
        legendItem.onmouseover = () => circle.onmouseover();
        legendItem.onmouseout = () => circle.onmouseout();
        
        if (asset.symbol) {
            legendItem.style.cursor = 'pointer';
            legendItem.onclick = () => showAssetDetails(asset.symbol);
        }
        
        legendItem.innerHTML = `
            <div class="legend-color" style="background: ${asset.color}"></div>
            <div class="legend-info">
                <span class="legend-name">${asset.koreanName} <small style="color:rgba(255,255,255,0.4); font-size: 0.8em;">${asset.label}</small></span>
                <span class="legend-value">${(percent * 100).toFixed(1)}% (${Math.round(asset.value).toLocaleString()}원)</span>
            </div>
        `;
        legendContainer.appendChild(legendItem);

        accumulatedPercent += percent;
    });

    svg.appendChild(centerText);
    svg.appendChild(centerSubText);
    chartContainer.appendChild(svg);
}

async function executePanicSell() {
    if (!confirm("🚨 정말로 모든 보유 종목을 시장가로 긴급 매도하시겠습니까?\n이 작업은 즉시 실행되며 취소할 수 없습니다.")) {
        return;
    }

    const btn = document.getElementById('btn-panic-sell');
    const originalText = btn.innerText;
    btn.disabled = true;
    btn.innerText = "🚨 긴급 청산 중...";

    try {
        const result = await fetchAPI(`/api/portfolio/${state.currentPortfolioId}/panic`, {
            method: 'POST'
        });

        if (result.status === 'success') {
            showAlert(`전종목 긴급 청산 완료: ${result.message}`, "success");
            // 자동 매매도 중단 (안전 장치)
            if (state.isAutoTrading) {
                state.isAutoTrading = false;
                const tradingStatus = document.getElementById('trading-status');
                const btnTrading = document.getElementById('btn-toggle-trading');
                if (tradingStatus) {
                    tradingStatus.innerText = '비활성 (긴급 정지됨)';
                    tradingStatus.style.color = '#FF4B4B';
                }
                if (btnTrading) {
                    btnTrading.innerText = '▶️ 자동 매매 시작';
                    btnTrading.className = 'btn primary';
                }
            }
            await loadPortfolio();
        } else {
            showAlert(result.message || "청산 실패", "error");
        }
    } catch (e) {
        showAlert("긴급 청산 중 오류가 발생했습니다.", "error");
        console.error(e);
    } finally {
        btn.disabled = false;
        btn.innerText = originalText;
    }
}

window.executePanicSell = executePanicSell;

// --- 종목 상세 모달 관련 로직 [NEW] ---
function showAssetDetails(symbol) {
    state.activeAssetDetail = symbol;
    updateModalContent(symbol);
    const modal = document.getElementById('asset-modal');
    modal.style.display = 'flex';
    
    // 모달 바깥쪽 클릭 시 닫기
    modal.onclick = (e) => {
        if (e.target === modal) closeAssetModal();
    };
}

function updateModalContent(symbol) {
    if (!state.currentPortfolioData) return;
    const data = state.currentPortfolioData;
    const pos = data.positions.find(p => p.symbol === symbol);
    if (!pos) return;

    const coin = marketData.find(c => c.market === symbol);
    const currentPrice = coin ? coin.trade_price : pos.avg_price;
    const profitRate = ((currentPrice - pos.avg_price) / pos.avg_price * 100).toFixed(2);
    const pnl = (currentPrice - pos.avg_price) * pos.quantity;

    // 헤더 업데이트
    document.getElementById('modal-asset-symbol').innerText = symbol.replace('KRW-', '');
    document.getElementById('modal-asset-name').innerText = coin ? coin.korean_name : '';

    // 지표 업데이트
    document.getElementById('modal-asset-qty').innerText = pos.quantity.toFixed(4);
    document.getElementById('modal-asset-avg').innerText = pos.avg_price.toLocaleString();
    
    const roiEl = document.getElementById('modal-asset-roi');
    roiEl.innerText = `${profitRate}%`;
    roiEl.className = `value ${profitRate >= 0 ? 'bull' : 'bear'}`;
    
    const pnlEl = document.getElementById('modal-asset-pnl');
    pnlEl.innerText = pnl.toLocaleString();
    pnlEl.className = `value ${pnl >= 0 ? 'bull' : 'bear'}`;

    // 해당 종목 거래 내역 필터링
    const modalTbody = document.getElementById('modal-history-tbody');
    modalTbody.innerHTML = '';
    
    const assetHistory = data.history.filter(h => h.symbol === symbol).reverse();
    if (assetHistory.length === 0) {
        modalTbody.innerHTML = '<tr><td colspan="6" style="text-align:center;padding:20px;">거래 내역이 없습니다.</td></tr>';
    } else {
        assetHistory.forEach(h => {
            const tr = document.createElement('tr');
            
            // Context 데이터를 배지로 변환
            let contextHtml = '';
            if (h.context) {
                const ctx = typeof h.context === 'string' ? JSON.parse(h.context) : h.context;
                contextHtml = Object.entries(ctx).map(([k, v]) => 
                    `<span class="ctx-badge">${k}: ${v}</span>`
                ).join('');
            }

            tr.innerHTML = `
                <td>${new Date(h.timestamp * 1000).toLocaleTimeString()}</td>
                <td class="${h.side === 'BUY' ? 'bull' : 'bear'}">${h.side}</td>
                <td class="num">${h.price.toLocaleString()}</td>
                <td class="num">${h.quantity.toFixed(4)}</td>
                <td>
                    <div class="reason-cell">
                        <span class="reason-text">${h.reason || '-'}</span>
                        <div class="context-badges">${contextHtml}</div>
                    </div>
                </td>
                <td class="num">${(h.price * h.quantity).toLocaleString()}</td>
            `;
            modalTbody.appendChild(tr);
        });
    }
}

function closeAssetModal() {
    state.activeAssetDetail = null;
    document.getElementById('asset-modal').style.display = 'none';
}

// --- 앱 전체 초기화 진입점 ---
document.addEventListener('DOMContentLoaded', () => {
    // 1. 기존 앱 초기화
    if (typeof initApp === 'function') {
        initApp();
    } else if (typeof init === 'function') {
        init();
    }
    
    // 2. 시스템 상태 모니터링 시작
    async function updateSystemQueues() {
        try {
            const status = await fetchAPI('/api/system/queues');
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

    // 1초마다 시스템 상태 갱신
    setInterval(updateSystemQueues, 1000);
    updateSystemQueues();
});
