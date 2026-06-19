/**
 * OverviewEngine - 통합 요약 대시보드 렌더러 및 상태 동기화 모듈
 */
const OverviewEngine = (() => {
    // 종목별 구분용 조화로운 파스텔/네온 톤 배색 (수익/손실 색상인 Red/Blue 제외)
    const segmentColors = [
        '#6366F1', // Indigo
        '#F59E0B', // Amber
        '#10B981', // Emerald
        '#D946EF', // Fuchsia
        '#8B5CF6', // Purple
        '#14B8A6', // Teal
        '#EC4899', // Pink
        '#06B6D4'  // Cyan
    ];

    let cachedPortfolios = {
        simulation: null,
        live: null
    };

    let selectedExchange = {
        simulation: null,
        live: null
    };

    let allocationBarHoverStates = {
        simulation: { upbit: false, bithumb: false, kis: false },
        live: { upbit: false, bithumb: false, kis: false }
    };

    /**
     * 초기화 함수
     */
    async function initialize() {
        console.log("[OverviewEngine] Initializing...");
        
        // 대시보드 진입 시 세션 목록(드롭다운 옵션)을 선제적으로 로드하고 구성
        if (typeof loadPortfolioHistoryList === 'function') {
            await loadPortfolioHistoryList(true);
        }
        
        await refreshData('simulation');
        await refreshData('live');
        
        // 세션 선택 드롭다운 바인딩
        bindSessionSelect('simulation');
        bindSessionSelect('live');

        // 거래소 클릭 바인딩
        bindExchangeClick('simulation');
        bindExchangeClick('live');
    }

    /**
     * 포트폴리오 데이터를 서버에서 강제 새로고침
     */
    async function refreshData(type) {
        // type이 없으면 현재 뷰 라우트 상태로 자동 판단
        if (!type) {
            const currentView = typeof ViewRouter !== 'undefined' ? ViewRouter.getActiveView() : 'overview-simulation-view';
            type = currentView === 'overview-live-view' ? 'live' : 'simulation';
        }

        const portfolioId = type === 'live' ? state.currentLivePortfolioId : state.currentSimPortfolioId;

        if (!portfolioId) {
            const sessionInfoEl = document.getElementById(`overview-${type}-session-info`);
            if (sessionInfoEl) sessionInfoEl.innerText = "활성 포트폴리오 세션이 없습니다. 대기 중...";
            return;
        }

        try {
            const response = await APIClient.fetchPortfolio(portfolioId);
            if (response && response.status === 'success') {
                cachedPortfolios[type] = response;
                renderMetrics(type);
                renderAllocationBar(type);

                // 선택되어 있던 거래소가 있다면 실시간 데이터 갱신 시 리렌더링
                if (selectedExchange[type]) {
                    selectExchange(type, selectedExchange[type]);
                }
            }
        } catch (e) {
            console.error(`[OverviewEngine] Refresh data failed for ${type}:`, e);
        }
    }

    /**
     * 1. 핵심 성과 메트릭 카드 렌더링
     */
    function renderMetrics(type) {
        const cachedPortfolio = cachedPortfolios[type];
        if (!cachedPortfolio) return;

        // 드롭다운 UI 상태를 현재 세션 정보와 동기화
        updateSessionSelectUI(type, cachedPortfolio.id, cachedPortfolio.type);

        const sessionInfoEl = document.getElementById(`overview-${type}-session-info`);
        if (sessionInfoEl) {
            sessionInfoEl.innerText = `활성 세션: ${cachedPortfolio.name || '미지정'} (유형: ${cachedPortfolio.type || 'simulation'})`;
        }

        const summary = cachedPortfolio.summary || {};
        const totalValue = cachedPortfolio.total_value || 0.0;
        const initialCash = cachedPortfolio.initial_cash || 10000000.0;
        const cash = cachedPortfolio.cash || 0.0;
        const roi = summary.roi !== undefined ? summary.roi : 0.0;
        
        // 보유종목 평가액 계산 (총 가치 - 보유 현금)
        const assetsValue = Math.max(0, totalValue - cash);

        // 총 평가 자산
        const totalValueEl = document.getElementById(`overview-${type}-total-value`);
        if (totalValueEl) {
            updateValueWithFlash(totalValueEl, Math.round(totalValue).toLocaleString() + '원');
        }

        // ROI
        const roiEl = document.getElementById(`overview-${type}-roi`);
        if (roiEl) {
            const formatted = formatRate(roi);
            roiEl.innerText = formatted.text;
            roiEl.className = `card-value ${formatted.className}`;
        }
        const roiDiffEl = document.getElementById(`overview-${type}-roi-diff`);
        if (roiDiffEl) {
            const diff = totalValue - initialCash;
            roiDiffEl.innerText = `수익금: ${diff >= 0 ? '+' : ''}${Math.round(diff).toLocaleString()}원`;
        }

        // 보유 현금
        const cashEl = document.getElementById(`overview-${type}-cash`);
        if (cashEl) {
            updateValueWithFlash(cashEl, Math.round(cash).toLocaleString() + '원');
        }
        const cashRatioEl = document.getElementById(`overview-${type}-cash-ratio`);
        if (cashRatioEl && totalValue > 0) {
            const ratio = (cash / totalValue) * 100;
            cashRatioEl.innerText = `비중: ${ratio.toFixed(1)}%`;
        }

        // 보유종목 평가액
        const assetsValueEl = document.getElementById(`overview-${type}-assets-value`);
        if (assetsValueEl) {
            updateValueWithFlash(assetsValueEl, Math.round(assetsValue).toLocaleString() + '원');
        }
        const assetsRatioEl = document.getElementById(`overview-${type}-assets-ratio`);
        if (assetsRatioEl && totalValue > 0) {
            const ratio = (assetsValue / totalValue) * 100;
            assetsRatioEl.innerText = `비중: ${ratio.toFixed(1)}%`;
        }

        // 투자 원금 및 누적 수수료 렌더링
        let initial = cachedPortfolio.initial_cash || 10000000.0;
        if (typeof initial === 'object') {
            initial = Object.values(initial).reduce((a, b) => a + b, 0);
        } else {
            initial = parseFloat(initial) || 0;
        }

        const initialCashEl = document.getElementById(`overview-${type}-initial-cash`);
        if (initialCashEl) {
            updateValueWithFlash(initialCashEl, Math.round(initial).toLocaleString() + '원');
        }

        const fee = summary.fee !== undefined ? summary.fee : 0.0;
        const totalFeeEl = document.getElementById(`overview-${type}-total-fee`);
        if (totalFeeEl) {
            updateValueWithFlash(totalFeeEl, Math.round(fee).toLocaleString() + '원');
        }

        const tax = summary.tax !== undefined ? summary.tax : 0.0;
        const totalTaxEl = document.getElementById(`overview-${type}-total-tax`);
        if (totalTaxEl) {
            updateValueWithFlash(totalTaxEl, Math.round(tax).toLocaleString() + '원');
        }
    }

    /**
     * 2. 거래소별 가로형 자산 비중 바 (Stacked Progress Bar) 렌더링
     */
    function renderAllocationBar(type) {
        const cachedPortfolio = cachedPortfolios[type];
        if (!cachedPortfolio) return;

        const cash = cachedPortfolio.cash || 0.0;
        const positions = cachedPortfolio.positions || [];
        const exchangeCash = cachedPortfolio.exchange_cash || {};

        // 거래소 목록 정의
        const exchanges = ['upbit', 'bithumb', 'kis'];

        exchanges.forEach(ex => {
            // 마우스 호버 중일 때는 렌더링을 일시 보류하여 툴팁이 끊어지는 현상 방지
            if (allocationBarHoverStates[type][ex]) return;

            const barContainer = document.getElementById(`overview-${type}-allocation-bar-${ex}`);
            const totalEl = document.getElementById(`overview-${type}-allocation-total-${ex}`);

            if (!barContainer) return;

            // 호버 상태 감지 리스너 바인딩 (최초 1회)
            if (!barContainer.dataset.hoverBound) {
                barContainer.dataset.hoverBound = 'true';
                barContainer.addEventListener('mouseenter', () => {
                    allocationBarHoverStates[type][ex] = true;
                });
                barContainer.addEventListener('mouseleave', () => {
                    allocationBarHoverStates[type][ex] = false;
                    // 호버가 풀리면 즉시 최신 데이터로 업데이트 렌더링
                    setTimeout(() => {
                        renderAllocationBar(type);
                    }, 50);
                });
            }

            barContainer.innerHTML = '';

            // 해당 거래소의 현금 분류
            let exCash = 0.0;
            if (exchangeCash && Object.keys(exchangeCash).length > 0) {
                exCash = exchangeCash[ex] || exchangeCash[ex.toUpperCase()] || 0.0;
            } else {
                const mainEx = (cachedPortfolio.exchange_id || 'upbit').toLowerCase();
                if (mainEx === ex) {
                    exCash = cash;
                }
            }

            // 해당 거래소의 보유종목 필터링 및 평가액 계산
            const exPositions = positions.filter(pos => {
                const posEx = (pos.exchange_id || '').toLowerCase();
                return posEx === ex && pos.quantity > 0;
            });

            let calculatedTotal = 0.0; // 종목 평가액 합계
            exPositions.forEach(pos => {
                const evalValue = pos.eval_value !== undefined ? pos.eval_value : (pos.quantity * (pos.current_price ?? pos.avg_price));
                calculatedTotal += evalValue;
            });
            
            const totalVal = exCash + calculatedTotal; // 현금 + 종목 합산 총 자산 평가액

            // 총액 및 현금 라벨 헤더 한 줄 반영
            if (totalEl) {
                const exSummary = (cachedPortfolio.exchanges || []).find(e => e && e.exchange_id && e.exchange_id.toLowerCase() === ex);
                const initCash = exSummary && exSummary.initial_cash !== undefined && exSummary.initial_cash !== null ? exSummary.initial_cash : 0.0;
                const fee = exSummary && exSummary.fee !== undefined && exSummary.fee !== null ? exSummary.fee : 0.0;
                const tax = exSummary && exSummary.tax !== undefined && exSummary.tax !== null ? exSummary.tax : 0.0;
                
                const roi = initCash > 0 ? ((totalVal - initCash) / initCash * 100) : (exSummary && exSummary.roi !== undefined && exSummary.roi !== null ? exSummary.roi : 0.0);
                
                let roiColor = '#94A3B8';
                let roiSign = '';
                if (roi > 0.005) {
                    roiColor = '#FF4B4B'; // Bull Red
                    roiSign = '+';
                } else if (roi < -0.005) {
                    roiColor = '#0072FF'; // Bear Blue
                }
                
                totalEl.style.display = 'inline-flex';
                totalEl.style.alignItems = 'center';
                totalEl.style.gap = '10px';
                totalEl.style.flexWrap = 'wrap';
                totalEl.style.fontSize = '0.78rem';
                
                totalEl.innerHTML = `
                    <span style="color: #475569;">|</span>
                    <span style="color: #94A3B8;">ROI: <strong style="color: ${roiColor}; font-family: 'Roboto Mono', monospace;">${roiSign}${roi.toFixed(2)}%</strong></span>
                    <span style="color: #94A3B8;">원금: <strong style="color: #F8FAFC; font-family: 'Roboto Mono', monospace;">${Math.round(initCash).toLocaleString()}</strong>원</span>
                    <span style="color: #94A3B8;">총 평가: <strong style="color: #F8FAFC; font-family: 'Roboto Mono', monospace;">${Math.round(totalVal).toLocaleString()}</strong>원</span>
                    <span style="color: #94A3B8;">현금: <strong style="color: #F8FAFC; font-family: 'Roboto Mono', monospace;">${Math.round(exCash).toLocaleString()}</strong>원</span>
                    <span style="color: #94A3B8;">평가액: <strong style="color: #F8FAFC; font-family: 'Roboto Mono', monospace;">${Math.round(calculatedTotal).toLocaleString()}</strong>원</span>
                    <span style="color: #94A3B8;">수수료: <strong style="color: #F8FAFC; font-family: 'Roboto Mono', monospace;">${Math.round(fee).toLocaleString()}</strong>원</span>
                    <span style="color: #94A3B8;">거래세: <strong style="color: #F8FAFC; font-family: 'Roboto Mono', monospace;">${Math.round(tax).toLocaleString()}</strong>원</span>
                `;
            }

            if (calculatedTotal <= 0) {
                barContainer.innerHTML = `<div style="width: 100%; text-align: center; line-height: 24px; color: #64748B; font-size: 0.75rem;">보유 종목 정보가 없습니다.</div>`;
                return;
            }

            // 비중 세그먼트 빌드 (현금 제외)
            let segments = [];
            
            // 종목 추가
            exPositions.forEach((pos, idx) => {
                const evalValue = pos.eval_value !== undefined ? pos.eval_value : (pos.quantity * (pos.current_price ?? pos.avg_price));
                const ratio = (evalValue / calculatedTotal) * 100;
                
                const korName = pos.korean_name;
                const displayName = (korName && korName !== pos.symbol) ? `${korName}(${pos.symbol})` : pos.symbol;

                segments.push({
                    name: displayName,
                    value: evalValue,
                    ratio: ratio,
                    color: segmentColors[idx % segmentColors.length]
                });
            });

            segments.sort((a, b) => b.value - a.value);

            // DOM 렌더링
            segments.forEach(seg => {
                if (seg.ratio <= 0.1) return;

                const segEl = document.createElement('div');
                segEl.className = 'bar-segment';
                segEl.style.width = `${seg.ratio}%`;
                segEl.style.backgroundColor = seg.color;
                
                const tooltipText = `${seg.name}: ${Math.round(seg.value).toLocaleString()}원 (${seg.ratio.toFixed(1)}%)`;
                segEl.setAttribute('data-tooltip', tooltipText);

                if (seg.ratio >= 8.0) {
                    const labelSpan = document.createElement('span');
                    labelSpan.innerText = `${seg.name} (${Math.round(seg.value).toLocaleString()}원) ${seg.ratio.toFixed(1)}%`;
                    segEl.appendChild(labelSpan);
                }
                barContainer.appendChild(segEl);
            });
        });
    }

    /**
     * 값 업데이트 시 미세 애니메이션 효과 부여
     */
    function updateValueWithFlash(element, newValue) {
        if (element.innerText !== newValue) {
            element.innerText = newValue;
            element.classList.add('value-updating');
            setTimeout(() => element.classList.remove('value-updating'), 500);
        }
    }

    /**
     * 외부 실시간 웹소켓 틱 데이터 주입 및 동기화 콜백
     */
    function update(tick) {
        // 1. 거래 발생 알림 시 양쪽 대시보드 포트폴리오 데이터 새로고침
        if (tick.type === 'alert' && tick.alert_type === 'trade') {
            refreshData('simulation');
            refreshData('live');
        }

        // 2. 현재 관찰 중인 종목의 시세 갱신 시 실시간 보유 종목 현재가 동기화
        if (tick.trade_price && tick.code) {
            ['simulation', 'live'].forEach(type => {
                const cachedPortfolio = cachedPortfolios[type];
                if (cachedPortfolio && cachedPortfolio.positions) {
                    const pos = cachedPortfolio.positions.find(p => p.symbol === tick.code);
                    if (pos) {
                        pos.current_price = tick.trade_price;
                        renderMetrics(type);
                        renderAllocationBar(type);
                    }
                }
            });
        }
        
        // 3. 수집기 및 데몬 상태 변화 반영
        if (tick.type === 'collector_status' || tick.type === 'strategy_status') {
            renderMetrics('simulation');
            renderMetrics('live');
        }
    }

    /**
     * 세션 선택 드롭다운 핸들러 및 상태 동기화 함수
     */
    function bindSessionSelect(type) {
        const selectEl = document.getElementById(`overview-${type}-session-select`);
        if (!selectEl) return;
        
        // 중복 이벤트 바인딩 방지 (클론하여 교체)
        const newSelect = selectEl.cloneNode(true);
        selectEl.parentNode.replaceChild(newSelect, selectEl);

        newSelect.addEventListener('change', async (e) => {
            const selectedId = e.target.value;
            if (!selectedId) return;
            
            const currentId = type === 'live' ? state.currentLivePortfolioId : state.currentSimPortfolioId;
            if (currentId === selectedId) return;

            // 세션 변경 시 선택된 거래소 및 하단 상세 영역 숨김 초기화
            selectedExchange[type] = null;
            const detailArea = document.getElementById(`overview-${type}-detail-area`);
            if (detailArea) {
                detailArea.style.display = 'none';
            }
            const blocks = document.querySelectorAll(`#overview-${type}-view .exchange-allocation-block`);
            blocks.forEach(block => block.classList.remove('selected'));
            
            if (type === 'live') {
                state.currentLivePortfolioId = selectedId;
            } else {
                state.currentSimPortfolioId = selectedId;
            }
            
            syncSidebarHighlight(selectedId);
            
            // 현재 화면과 일치하는 타입의 세션이 바뀐 경우 전역 currentPortfolioId 동기화하여 loadPortfolio() 촉발
            const activeView = typeof ViewRouter !== 'undefined' ? ViewRouter.getActiveView() : '';
            if ((type === 'live' && activeView === 'overview-live-view') || 
                (type === 'simulation' && activeView === 'overview-simulation-view')) {
                state.currentPortfolioId = selectedId;
            }
            
            await refreshData(type);
        });
    }

    function updateSessionSelectUI(type, portfolioId, portfolioType) {
        const selectEl = document.getElementById(`overview-${type}-session-select`);
        const valToSet = portfolioId ? String(portfolioId) : '';
        if (selectEl && selectEl.value !== valToSet) {
            selectEl.value = valToSet;
        }

        if (selectEl) {
            if (portfolioType === 'live') {
                selectEl.style.borderColor = '#FF4B4B'; // 실거래: Red
            } else if (portfolioType === 'simulation') {
                selectEl.style.borderColor = '#10B981'; // 진행중: Emerald
            } else {
                selectEl.style.borderColor = 'rgba(148, 163, 184, 0.2)'; // 기본 회색
            }
        }
    }

    function syncSidebarHighlight(portfolioId) {
        const tbody = document.getElementById('portfolio-history-list-tbody');
        if (!tbody) return;
        
        tbody.querySelectorAll('tr').forEach(r => {
            r.style.background = '';
        });
        
        const targetRow = tbody.querySelector(`tr[data-portfolio-id="${portfolioId}"]`);
        if (targetRow) {
            targetRow.style.background = 'rgba(99, 102, 241, 0.1)';
        }
    }

    function bindExchangeClick(type) {
        const blocks = document.querySelectorAll(`#overview-${type}-view .exchange-allocation-block`);
        blocks.forEach(block => {
            block.addEventListener('click', () => {
                const exchange = block.getAttribute('data-exchange');
                selectExchange(type, exchange);
            });
        });
    }

    function selectExchange(type, exchangeName) {
        selectedExchange[type] = exchangeName;

        // 선택 강조 클래스 추가
        const blocks = document.querySelectorAll(`#overview-${type}-view .exchange-allocation-block`);
        blocks.forEach(block => {
            if (block.getAttribute('data-exchange') === exchangeName) {
                block.classList.add('selected');
            } else {
                block.classList.remove('selected');
            }
        });

        // 상세 종목 및 거래내역 영역 보이기
        const detailArea = document.getElementById(`overview-${type}-detail-area`);
        if (detailArea) {
            detailArea.style.display = 'flex';
        }

        // 상세 종목 현황 테이블 렌더링
        const cachedPortfolio = cachedPortfolios[type];
        if (cachedPortfolio && typeof PortfolioView !== 'undefined') {
            PortfolioView.renderSymbolDetailTable(
                `overview-${type}-symbols-tbody`,
                `overview-${type}-symbols-title`,
                exchangeName,
                cachedPortfolio,
                `overview-${type}-symbols-tfoot`,
                (item) => {
                    // 종목 행 클릭 시 종목별 거래내역 렌더링
                    PortfolioView.renderHistoryTablePort(`overview-${type}-history-detail-tbody`, item);
                }
            );
        }
    }

    return {
        initialize,
        refreshData,
        update
    };
})();

window.OverviewEngine = OverviewEngine;
