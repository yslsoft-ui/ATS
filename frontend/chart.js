/**
 * Upbit Terminal 차트 렌더링 엔진 (Chart Engine) 모듈
 */
(function() {
    let _chartDiv = null;
    let _chart = null;
    let _candleSeries = null;
    let _volumeSeries = null;
    let _smaSeries = null;
    let _bbUpperSeries = null;
    let _bbLowerSeries = null;
    let _rsiSeries = null;

    let _clickCallback = null;

    const ChartEngine = {
        // 차트 초기화 및 이벤트 연결
        initialize(containerId, clickCallback) {
            _chartDiv = document.getElementById(containerId);
            if (!_chartDiv) return;

            _clickCallback = clickCallback;

            const lw = window.LightweightCharts || LightweightCharts;
            if (typeof lw === 'undefined') {
                console.error("LightweightCharts is not loaded.");
                return;
            }

            _chart = lw.createChart(_chartDiv, {
                width: _chartDiv.clientWidth || 800,
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

            _candleSeries = _chart.addCandlestickSeries({
                upColor: '#FF4B4B',
                downColor: '#0072FF',
                borderDownColor: '#0072FF',
                borderUpColor: '#FF4B4B',
                wickDownColor: '#0072FF',
                wickUpColor: '#FF4B4B',
                priceScaleId: 'left',
            });

            _volumeSeries = _chart.addHistogramSeries({
                color: '#26a69a',
                priceFormat: { type: 'volume' },
                priceScaleId: '', // 서브 레이어
            });

            _volumeSeries.priceScale().applyOptions({
                scaleMargins: { top: 0.7, bottom: 0.15 },
            });

            _smaSeries = _chart.addLineSeries({ color: '#FFA500', lineWidth: 2, title: 'SMA(20)', priceScaleId: 'left' });
            _bbUpperSeries = _chart.addLineSeries({ color: 'rgba(173, 216, 230, 0.4)', lineWidth: 1, lineStyle: 2, priceScaleId: 'left' });
            _bbLowerSeries = _chart.addLineSeries({ color: 'rgba(173, 216, 230, 0.4)', lineWidth: 1, lineStyle: 2, priceScaleId: 'left' });

            _rsiSeries = _chart.addLineSeries({
                color: '#FF00FF',
                lineWidth: 1.5,
                title: 'RSI(14)',
                priceScaleId: 'rsi-left',
            });

            _chart.priceScale('rsi-left').applyOptions({
                scaleMargins: { top: 0.85, bottom: 0 },
                visible: false,
                alignLabels: true,
                position: 'left',
            });

            // 클릭 리스너 연결 (drillDown 등)
            _chart.subscribeClick(param => {
                if (!param.time || param.point === undefined) return;
                if (_clickCallback) _clickCallback(param.time);
            });

            // 십자선 무브 및 툴팁 렌더링 설정
            this._initTooltip();

            // 차트 스크롤 등 유저 조작 감지 등록
            const stopAutoScroll = () => {
                if (state.autoScroll) {
                    state.autoScroll = false;
                    console.log("[INFO] User interaction detected: AutoScroll OFF");
                    const goLiveBtn = document.getElementById('go-live-btn');
                    if (goLiveBtn) goLiveBtn.style.display = 'block';
                }
            };

            _chartDiv.addEventListener('mousedown', stopAutoScroll);
            _chartDiv.addEventListener('wheel', stopAutoScroll, { passive: true });

            _chart.timeScale().subscribeVisibleLogicalRangeChange((logicalRange) => {
                const spacing = _chart.timeScale().options().barSpacing;
                if (spacing && spacing !== state.savedBarSpacing) {
                    state.savedBarSpacing = spacing;
                }

                // 왼쪽(과거) 스크롤 끝단 접근 감지 (Lazy Loading 트리거)
                if (logicalRange && logicalRange.from < 10) {
                    if (typeof window.loadMoreHistory === 'function') {
                        window.loadMoreHistory();
                    }
                }
            });

            _chartDiv.addEventListener('contextmenu', (e) => {
                e.preventDefault();
                if (!state.autoScroll || state.isExplorerMode) {
                    console.log("[INFO] Right-click detected: Returning to Real-time Mode");
                    exitExplorerMode();
                    showAlert({ msg: "실시간 모드로 복귀합니다." });
                }
            });

            window.addEventListener('resize', () => {
                if (_chart && _chartDiv) {
                    _chart.resize(_chartDiv.clientWidth, 500);
                }
            });
        },

        // 크로스헤어 툴팁 렌더링 내부 메소드
        _initTooltip() {
            let tooltip = _chartDiv.querySelector('.chart-floating-tooltip');
            if (!tooltip) {
                tooltip = document.createElement('div');
                tooltip.className = 'chart-floating-tooltip';
                _chartDiv.appendChild(tooltip);
            }

            _chart.subscribeCrosshairMove(param => {
                if (!param.time || param.point === undefined || !param.seriesData.get(_candleSeries)) {
                    tooltip.style.display = 'none';
                    return;
                }

                const data = param.seriesData.get(_candleSeries);
                const smaData = param.seriesData.get(_smaSeries);
                const rsiData = param.seriesData.get(_rsiSeries);

                const allLocal = [...state.candles, state.currentCandle].filter(c => c);
                const rawCandle = allLocal.find(c => c.timestamp === param.time);

                tooltip.style.display = 'block';
                const price = formatPrice(data.close);
                const dateObj = new Date(param.time * 1000);
                const timeStr = dateObj.getFullYear() + ". " + (dateObj.getMonth() + 1) + ". " + dateObj.getDate() + ". " + 
                                dateObj.getHours().toString().padStart(2, '0') + ":" + 
                                dateObj.getMinutes().toString().padStart(2, '0') + ":" + 
                                dateObj.getSeconds().toString().padStart(2, '0');

                let html = `<div class="tooltip-time">${timeStr}</div>`;
                html += `<div class="tooltip-row"><span>O</span><b>${formatPrice(data.open)}</b></div>`;
                html += `<div class="tooltip-row"><span>H</span><b>${formatPrice(data.high)}</b></div>`;
                html += `<div class="tooltip-row"><span>L</span><b>${formatPrice(data.low)}</b></div>`;
                html += `<div class="tooltip-row"><span>C</span><b class="${data.close >= data.open ? 'bull' : 'bear'}">${price}</b></div>`;

                if (rawCandle) {
                    html += `<div class="tooltip-row"><span>Vol</span><b>${formatTooltipVolume(rawCandle.volume)}</b></div>`;
                    if (rawCandle.count) {
                        html += `<div class="tooltip-row"><span>Count</span><b>${rawCandle.count}</b></div>`;
                    }
                }

                if (smaData) html += `<div class="tooltip-row"><span>SMA</span><b style="color:#FFA500">${smaData.value.toFixed(0)}</b></div>`;
                if (rsiData) html += `<div class="tooltip-row"><span>RSI</span><b style="color:#FF00FF">${rsiData.value.toFixed(2)}</b></div>`;

                tooltip.innerHTML = html;

                const y = param.point.y + 40;
                let x = param.point.x + 80;
                if (x > _chartDiv.clientWidth - 240) x = param.point.x - 250;

                tooltip.style.cssText = `
                    display: block !important; 
                    left: ${x}px !important; 
                    top: ${y}px !important; 
                    transform: none !important; 
                    margin: 0 !important;
                    position: absolute !important;
                `;
            });
        },

        // 차트 데이터 렌더링 실행
        render(candles, currentCandle) {
            if (!_chart) return;

            const allCandles = [...candles, currentCandle].filter(c => c);

            // 데이터 부재 시 처리: 데이터 없음 안내 레이어 활성화
            if (allCandles.length === 0) {
                _candleSeries.setData([]);
                _volumeSeries.setData([]);
                _smaSeries.setData([]);
                _bbUpperSeries.setData([]);
                _bbLowerSeries.setData([]);
                _rsiSeries.setData([]);
                showNoDataOverlay(true);
                return;
            } else {
                showNoDataOverlay(false);
            }

            const uniqueCandles = [];
            const seenTs = new Set();
            
            // 타임스탬프 단위를 10자리 초(s) 단위로 완벽하게 균일 정제한 사본 생성
            const normalizedCandles = allCandles.map(c => {
                let ts = parseInt(c.timestamp);
                if (ts > 9999999999) {
                    ts = Math.floor(ts / 1000);
                }
                return { ...c, timestamp: ts };
            });

            normalizedCandles.sort((a, b) => a.timestamp - b.timestamp).forEach(c => {
                if (!seenTs.has(c.timestamp)) {
                    uniqueCandles.push(c);
                    seenTs.add(c.timestamp);
                }
            });

            const showSma = document.getElementById('show-sma')?.checked;
            const showBb = document.getElementById('show-bb')?.checked;
            const showVol = document.getElementById('show-volume')?.checked;
            const showRsi = document.getElementById('show-rsi')?.checked;

            let lastAssignedColor = 'rgba(255, 75, 75, 0.5)';
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
                        const prev = i > 0 ? arr[i-1] : null;
                        if (prev) {
                            if (c.close > prev.close) color = '#FF4B4B';
                            else if (c.close < prev.close) color = '#0072FF';
                            else color = lastAssignedCandleColor;
                        }
                    }
                    lastAssignedCandleColor = color;
                    return {
                        time: c.timestamp,
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
                _candleSeries.setData(candleData);

                if (!state.isLoaded) {
                    _chart.timeScale().applyOptions({ barSpacing: state.savedBarSpacing });
                    _chart.timeScale().scrollToRealTime();
                }

                // 볼륨 데이터 세팅
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
                    _volumeSeries.setData(volData);
                } else {
                    _volumeSeries.setData([]);
                }

                // SMA 세팅
                if (showSma) {
                    const smaData = uniqueCandles
                        .filter(c => c.sma && !isNaN(c.sma))
                        .map(c => ({ time: parseInt(c.timestamp), value: parseFloat(c.sma) }));
                    _smaSeries.setData(smaData);
                } else {
                    _smaSeries.setData([]);
                }

                // Bollinger Bands 세팅
                if (showBb) {
                    const upperData = uniqueCandles.filter(c => c.bb_upper && !isNaN(c.bb_upper)).map(c => ({ time: parseInt(c.timestamp), value: parseFloat(c.bb_upper) }));
                    const lowerData = uniqueCandles.filter(c => c.bb_lower && !isNaN(c.bb_lower)).map(c => ({ time: parseInt(c.timestamp), value: parseFloat(c.bb_lower) }));
                    _bbUpperSeries.setData(upperData);
                    _bbLowerSeries.setData(lowerData);
                } else {
                    _bbUpperSeries.setData([]);
                    _bbLowerSeries.setData([]);
                }

                // RSI 세팅
                if (showRsi) {
                    const rsiData = uniqueCandles.filter(c => c.rsi && !isNaN(c.rsi)).map(c => ({ time: parseInt(c.timestamp), value: parseFloat(c.rsi) }));
                    _rsiSeries.setData(rsiData);
                    _chart.priceScale('rsi-left').applyOptions({ visible: true });
                } else {
                    _rsiSeries.setData([]);
                    _chart.priceScale('rsi-left').applyOptions({ visible: false });
                }

            } catch (e) {
                console.error("Chart render error", e);
            }

            if (state.isExplorerMode) {
                const goLiveBtn = document.getElementById('go-live-btn');
                if (goLiveBtn) goLiveBtn.style.display = 'block';
            } else if (state.autoScroll) {
                _chart.timeScale().scrollToRealTime();
                const goLiveBtn = document.getElementById('go-live-btn');
                if (goLiveBtn) goLiveBtn.style.display = 'none';
            }

            if (state.alertMarkerTs) {
                _candleSeries.setMarkers([{
                    time: state.alertMarkerTs,
                    position: 'aboveBar',
                    color: '#FFD700',
                    shape: 'arrowDown',
                    text: `🔔 ${new Date(state.alertMarkerTs * 1000).toLocaleTimeString()}`,
                }]);
            } else {
                _candleSeries.setMarkers([]);
            }
        },

        // 지표 토글 메소드
        toggleIndicator(type, isVisible) {
            if (type === 'rsi') {
                _chart.priceScale('rsi-left').applyOptions({
                    visible: isVisible
                });
            }
            // 전체 다시 그리기 트리거
            this.render(state.candles, state.currentCandle);
        },

        // 실시간 모드로 복귀 및 줌 초기화
        exitExplorerMode() {
            if (_chart) {
                _chart.timeScale().applyOptions({ barSpacing: state.savedBarSpacing });
                _chart.timeScale().scrollToRealTime();
            }
        },

        // 특정 포커스로 스크롤 이동
        scrollToPosition(offset, animated = false) {
            if (_chart) {
                _chart.timeScale().scrollToPosition(offset, animated);
            }
        },

        applyBarSpacing(spacing) {
            if (_chart) {
                _chart.timeScale().applyOptions({ barSpacing: spacing });
            }
        },

        // 화면 탭 전환 등으로 인한 크기 변화를 차트 엔진에 강제 반영
        resize() {
            if (_chart && _chartDiv) {
                _chart.resize(_chartDiv.clientWidth, 500);
            }
        }
    };

    // 전역 노출
    window.ChartEngine = ChartEngine;
})();
