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

    let cachedPortfolio = null;
    let activityEvents = []; // 최근 5건 거래/이벤트 저장용
    let allocationBarHoverStates = { upbit: false, bithumb: false, kis: false }; // [NEW] 호버 상태 추적용 변수

    /**
     * 초기화 함수
     */
    async function initialize() {
        console.log("[OverviewEngine] Initializing...");
        
        // [NEW] 대시보드 진입 시 세션 목록(드롭다운 옵션)을 선제적으로 로드하고 구성
        if (typeof loadPortfolioHistoryList === 'function') {
            await loadPortfolioHistoryList(true);
        }
        
        await refreshData();
        
        // 1. 초기 로드 시점의 최근 주문 이력을 가져와서 활동 피드에 삽입
        try {
            if (state.currentPortfolioId) {
                const portfolioData = state.currentPortfolioData;
                if (portfolioData && portfolioData.history) {
                    const sorted = [...portfolioData.history].sort((a, b) => b.timestamp - a.timestamp);
                    activityEvents = sorted.slice(0, 5).map(tx => ({
                        type: 'trade',
                        timestamp: tx.timestamp * 1000,
                        exchange: tx.exchange_id,
                        symbol: tx.symbol,
                        side: tx.side,
                        price: tx.price,
                        quantity: tx.quantity,
                        reason: tx.reason
                    }));
                }
            }
            renderActivityFeed();
        } catch (e) {
            console.error("[OverviewEngine] Failed to load initial activity feed:", e);
        }
        // [NEW] 세션 선택 드롭다운 바인딩
        bindSessionSelect();
    }

    /**
     * 포트폴리오 데이터를 서버에서 강제 새로고침
     */
    async function refreshData() {
        if (!state.currentPortfolioId) {
            const sessionInfoEl = document.getElementById('overview-session-info');
            if (sessionInfoEl) sessionInfoEl.innerText = "활성 포트폴리오 세션이 없습니다. 대기 중...";
            return;
        }

        try {
            const response = await APIClient.fetchPortfolio(state.currentPortfolioId);
            if (response && response.status === 'success') {
                cachedPortfolio = response;
                renderMetrics();
                renderAllocationBar();
                renderPositionsTable();
            }
        } catch (e) {
            console.error("[OverviewEngine] Refresh data failed:", e);
        }
    }

    /**
     * 1. 핵심 성과 메트릭 카드 렌더링
     */
    function renderMetrics() {
        if (!cachedPortfolio) return;

        // [NEW] 드롭다운 UI 상태를 현재 세션 정보와 동기화
        updateSessionSelectUI(cachedPortfolio.id, cachedPortfolio.type);

        const sessionInfoEl = document.getElementById('overview-session-info');
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
        const totalValueEl = document.getElementById('overview-total-value');
        if (totalValueEl) {
            updateValueWithFlash(totalValueEl, Math.round(totalValue).toLocaleString() + ' 원');
        }

        // ROI
        const roiEl = document.getElementById('overview-roi');
        if (roiEl) {
            const formatted = formatRate(roi);
            roiEl.innerText = formatted.text;
            roiEl.className = `card-value ${formatted.className}`;
        }
        const roiDiffEl = document.getElementById('overview-roi-diff');
        if (roiDiffEl) {
            const diff = totalValue - initialCash;
            roiDiffEl.innerText = `원금 대비 변동: ${diff >= 0 ? '+' : ''}${Math.round(diff).toLocaleString()} 원`;
        }

        // 보유 현금
        const cashEl = document.getElementById('overview-cash');
        if (cashEl) {
            updateValueWithFlash(cashEl, Math.round(cash).toLocaleString() + ' 원');
        }
        const cashRatioEl = document.getElementById('overview-cash-ratio');
        if (cashRatioEl && totalValue > 0) {
            const ratio = (cash / totalValue) * 100;
            cashRatioEl.innerText = `비중: ${ratio.toFixed(1)}%`;
        }

        // 보유종목 평가액
        const assetsValueEl = document.getElementById('overview-assets-value');
        if (assetsValueEl) {
            updateValueWithFlash(assetsValueEl, Math.round(assetsValue).toLocaleString() + ' 원');
        }
        const assetsRatioEl = document.getElementById('overview-assets-ratio');
        if (assetsRatioEl && totalValue > 0) {
            const ratio = (assetsValue / totalValue) * 100;
            assetsRatioEl.innerText = `비중: ${ratio.toFixed(1)}%`;
        }

        // [NEW] 투자 원금 및 누적 수수료 렌더링
        let initial = cachedPortfolio.initial_cash || 10000000.0;
        if (typeof initial === 'object') {
            initial = Object.values(initial).reduce((a, b) => a + b, 0);
        } else {
            initial = parseFloat(initial) || 0;
        }

        const initialCashEl = document.getElementById('overview-initial-cash');
        if (initialCashEl) {
            updateValueWithFlash(initialCashEl, Math.round(initial).toLocaleString() + ' 원');
        }

        const fee = summary.fee !== undefined ? summary.fee : 0.0;
        const totalFeeEl = document.getElementById('overview-total-fee');
        if (totalFeeEl) {
            updateValueWithFlash(totalFeeEl, Math.round(fee).toLocaleString() + ' 원');
        }
    }

    /**
     * 2. 거래소별 가로형 자산 비중 바 (Stacked Progress Bar) 렌더링
     */
    function renderAllocationBar() {
        if (!cachedPortfolio) return;

        const cash = cachedPortfolio.cash || 0.0;
        const positions = cachedPortfolio.positions || [];
        const exchangeCash = cachedPortfolio.exchange_cash || {};

        // 거래소 목록 정의
        const exchanges = ['upbit', 'bithumb', 'kis'];

        exchanges.forEach(ex => {
            // [NEW] 마우스 호버 중일 때는 렌더링을 일시 보류하여 툴팁이 끊어지는 현상 방지
            if (allocationBarHoverStates[ex]) return;

            const barContainer = document.getElementById(`overview-allocation-bar-${ex}`);
            const totalEl = document.getElementById(`overview-allocation-total-${ex}`);

            if (!barContainer) return;

            // [NEW] 호버 상태 감지 리스너 바인딩 (최초 1회)
            if (!barContainer.dataset.hoverBound) {
                barContainer.dataset.hoverBound = 'true';
                barContainer.addEventListener('mouseenter', () => {
                    allocationBarHoverStates[ex] = true;
                });
                barContainer.addEventListener('mouseleave', () => {
                    allocationBarHoverStates[ex] = false;
                    // 호버가 풀리면 즉시 최신 데이터로 업데이트 렌더링
                    setTimeout(() => {
                        renderAllocationBar();
                    }, 50);
                });
            }

            barContainer.innerHTML = '';

            // 2.1. 해당 거래소의 현금 분류
            let exCash = 0.0;
            if (exchangeCash && Object.keys(exchangeCash).length > 0) {
                exCash = exchangeCash[ex] || exchangeCash[ex.toUpperCase()] || 0.0;
            } else {
                // exchange_cash 정보가 없으면 포트폴리오 기본 거래소에 올인
                const mainEx = (cachedPortfolio.exchange_id || 'upbit').toLowerCase();
                if (mainEx === ex) {
                    exCash = cash;
                }
            }

            // 2.2. 해당 거래소의 보유종목 필터링 및 평가액 계산
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

            // 총액 및 현금 라벨 헤더 한 줄 반영 (상세 자산 메트릭 주입)
            if (totalEl) {
                const exSummary = (cachedPortfolio.exchanges || []).find(e => e && e.exchange_id && e.exchange_id.toLowerCase() === ex);
                const initCash = exSummary ? exSummary.initial_cash : 0.0;
                
                // 실시간 ROI 계산 (투자 원금 대비 실시간 평가액 변동률)
                const roi = initCash > 0 ? ((totalVal - initCash) / initCash * 100) : (exSummary ? exSummary.roi : 0.0);
                
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
                    <span style="color: #94A3B8;">총 평가: <strong style="color: #F8FAFC; font-family: 'Roboto Mono', monospace;">${Math.round(totalVal).toLocaleString()}</strong>원</span>
                    <span style="color: #94A3B8;">ROI: <strong style="color: ${roiColor}; font-family: 'Roboto Mono', monospace;">${roiSign}${roi.toFixed(2)}%</strong></span>
                    <span style="color: #94A3B8;">현금: <strong style="color: #F8FAFC; font-family: 'Roboto Mono', monospace;">${Math.round(exCash).toLocaleString()}</strong>원</span>
                    <span style="color: #94A3B8;">평가액: <strong style="color: #F8FAFC; font-family: 'Roboto Mono', monospace;">${Math.round(calculatedTotal).toLocaleString()}</strong>원</span>
                `;
            }

            if (totalVal <= 0) {
                barContainer.innerHTML = `<div style="width: 100%; text-align: center; line-height: 24px; color: #64748B; font-size: 0.75rem;">보유 자산 정보가 없습니다.</div>`;
                return;
            }

            // 2.3. 비중 세그먼트 빌드
            let segments = [];
            
            // 현금 세그먼트 추가
            if (exCash > 0) {
                const cashRatio = (exCash / totalVal) * 100;
                segments.push({
                    name: '원화 현금',
                    value: exCash,
                    ratio: cashRatio,
                    color: '#10B981' // 현금은 항상 초록색 테마
                });
            }
            
            // 종목 추가 (종목명 포맷: 한글명(종목명))
            exPositions.forEach((pos, idx) => {
                const evalValue = pos.eval_value !== undefined ? pos.eval_value : (pos.quantity * (pos.current_price ?? pos.avg_price));
                const ratio = (evalValue / totalVal) * 100;
                
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

            // 2.4. DOM 렌더링
            segments.forEach(seg => {
                if (seg.ratio <= 0.1) return;

                const segEl = document.createElement('div');
                segEl.className = 'bar-segment';
                segEl.style.width = `${seg.ratio}%`;
                segEl.style.backgroundColor = seg.color;
                
                const tooltipText = `${seg.name}: ${Math.round(seg.value).toLocaleString()} 원 (${seg.ratio.toFixed(1)}%)`;
                segEl.setAttribute('data-tooltip', tooltipText);

                // 표시폭 8% 이상 텍스트 노출
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
     * 3. 보유 포지션 간이 테이블 렌더링
     */
    function renderPositionsTable() {
        if (!cachedPortfolio) return;

        const positions = cachedPortfolio.positions || [];
        const tbody = document.getElementById('overview-positions-tbody');
        const countEl = document.getElementById('overview-position-count');
        if (!tbody) return;

        tbody.innerHTML = '';

        const activePositions = positions.filter(p => p.quantity > 0);
        if (countEl) countEl.innerText = `${activePositions.length}개 종목`;

        if (activePositions.length === 0) {
            tbody.innerHTML = `<tr><td colspan="5" style="text-align: center; color: #64748B; padding: 20px;">보유 포지션이 없습니다.</td></tr>`;
            return;
        }

        activePositions.forEach(pos => {
            const currentPrice = pos.current_price ?? pos.avg_price;
            const evalValue = pos.eval_value !== undefined ? pos.eval_value : (pos.quantity * currentPrice);
            const profitPercent = pos.roi !== undefined ? pos.roi : (((currentPrice - pos.avg_price) / pos.avg_price) * 100);
            
            const formatted = formatRate(profitPercent);

            const row = document.createElement('tr');
            row.innerHTML = `
                <td style="font-weight: bold; color: #E2E8F0;">${pos.symbol}</td>
                <td class="num">${formatPrice(pos.quantity)}</td>
                <td class="num">${formatPrice(pos.avg_price)}</td>
                <td class="num" id="overview-pos-price-${pos.symbol}">${formatPrice(currentPrice)}</td>
                <td class="num ${formatted.className}" id="overview-pos-profit-${pos.symbol}">${formatted.text}</td>
            `;
            tbody.appendChild(row);
        });
    }

    /**
     * 4. 실시간 거래 및 이벤트 피드 렌더링
     */
    function renderActivityFeed() {
        const feedContainer = document.getElementById('overview-activity-feed');
        if (!feedContainer) return;

        feedContainer.innerHTML = '';

        if (activityEvents.length === 0) {
            feedContainer.innerHTML = `<div class="feed-empty">실시간 거래 이벤트 대기 중...</div>`;
            return;
        }

        const sortedEvents = [...activityEvents].sort((a, b) => b.timestamp - a.timestamp);

        sortedEvents.forEach(ev => {
            const itemEl = document.createElement('div');
            itemEl.className = 'feed-item';

            const timeStr = new Date(ev.timestamp).toLocaleTimeString();
            let badgeClass = 'system';
            let badgeText = 'EVENT';
            let messageText = '';

            if (ev.type === 'trade') {
                badgeClass = ev.side.toLowerCase();
                badgeText = ev.side === 'BUY' ? '매수' : '매도';
                messageText = `<strong>${ev.symbol}</strong> ${formatPrice(ev.quantity)}개 체결 (${formatPrice(ev.price)} 원)`;
            } else if (ev.type === 'alert') {
                badgeClass = 'alert';
                badgeText = '감지';
                messageText = ev.message || ev.msg || '';
            } else if (ev.type === 'system') {
                badgeClass = 'system';
                badgeText = '시스템';
                messageText = ev.message;
            }

            itemEl.innerHTML = `
                <span class="feed-badge ${badgeClass}">${badgeText}</span>
                <span class="feed-content" title="${messageText}">${messageText}</span>
                <span class="feed-time">${timeStr}</span>
            `;
            feedContainer.appendChild(itemEl);
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
        // 1. 체결 신호나 알림이 오면 활동 피드에 긴급 인입
        if (tick.type === 'alert') {
            let evType = 'alert';
            let evMessage = tick.msg || tick.message || '';
            let evSide = '';
            let evQty = 0;
            let evPrice = 0;

            if (tick.alert_type === 'trade') {
                evType = 'trade';
                evSide = tick.side || 'BUY';
                evQty = tick.quantity || 0;
                evPrice = tick.price || 0;
            }

            activityEvents.unshift({
                type: evType,
                timestamp: tick.timestamp || Date.now(),
                symbol: tick.symbol || tick.code,
                side: evSide,
                quantity: evQty,
                price: evPrice,
                message: evMessage
            });

            // 5개 초과 시 버림
            if (activityEvents.length > 5) {
                activityEvents.pop();
            }

            renderActivityFeed();
            
            // 거래 관련 알림 시 포트폴리오 메트릭 새로고침
            if (tick.alert_type === 'trade') {
                refreshData();
            }
        }

        // 2. 현재 관찰 중인 종목의 시세 갱신 시 실시간 보유 종목 현재가 동기화
        if (cachedPortfolio && tick.trade_price && tick.code) {
            const pos = cachedPortfolio.positions.find(p => p.symbol === tick.code);
            if (pos) {
                pos.current_price = tick.trade_price;
                
                const priceEl = document.getElementById(`overview-pos-price-${tick.code}`);
                if (priceEl) {
                    priceEl.innerText = formatPrice(tick.trade_price);
                    priceEl.classList.add('value-updating');
                    setTimeout(() => priceEl.classList.remove('value-updating'), 400);
                }
                
                const profitEl = document.getElementById(`overview-pos-profit-${tick.code}`);
                if (profitEl) {
                    const profitPercent = ((tick.trade_price - pos.avg_price) / pos.avg_price) * 100;
                    const formatted = formatRate(profitPercent);
                    profitEl.className = `num ${formatted.className}`;
                    profitEl.innerText = formatted.text;
                    profitEl.classList.add('value-updating');
                    setTimeout(() => profitEl.classList.remove('value-updating'), 400);
                }
                
                // 시세 변화를 대시보드 전반에 실시간 기민하게 동기화
                renderMetrics();
                renderAllocationBar();
            }
        }
        
        // 3. 수집기 및 데몬 상태 변화 반영
        if (tick.type === 'collector_status' || tick.type === 'strategy_status') {
            renderMetrics();
        }
    }

    /**
     * [NEW] 세션 선택 드롭다운 핸들러 및 상태 동기화 함수
     */
    function bindSessionSelect() {
        const selectEl = document.getElementById('overview-session-select');
        if (!selectEl) return;
        
        // 중복 이벤트 바인딩 방지 (클론하여 교체)
        const newSelect = selectEl.cloneNode(true);
        selectEl.parentNode.replaceChild(newSelect, selectEl);

        newSelect.addEventListener('change', async (e) => {
            const selectedId = e.target.value;
            if (!selectedId) return;
            
            if (state.currentPortfolioId === selectedId) return;
            
            state.currentPortfolioId = selectedId;
            syncSidebarHighlight(selectedId);
            
            if (typeof loadPortfolio === 'function') {
                await loadPortfolio(true);
            }
            await refreshData();
        });
    }

    function updateSessionSelectUI(portfolioId, type) {
        const selectEl = document.getElementById('overview-session-select');
        const valToSet = portfolioId ? String(portfolioId) : '';
        if (selectEl && selectEl.value !== valToSet) {
            selectEl.value = valToSet;
        }

        if (selectEl) {
            if (type === 'live') {
                selectEl.style.borderColor = '#FF4B4B'; // 실거래: Red
            } else if (type === 'simulation') {
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

    return {
        initialize,
        refreshData,
        update
    };
})();

window.OverviewEngine = OverviewEngine;
