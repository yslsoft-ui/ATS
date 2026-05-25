/**
 * Upbit Terminal 포트폴리오(Portfolio) 및 실자산 관리 모듈
 */

const ASSET_COLORS = [
    '#FF6384', '#36A2EB', '#FFCE56', '#4BC0C0', '#9966FF', 
    '#FF9F40', '#C9CBCF', '#7BC225', '#FF4500', '#1E90FF'
];

/**
 * 실시간 모의투자 및 과거 백테스트 목록 전체를 불러와 좌측 통합 이력 리스트 패널에 바인딩합니다.
 */
async function loadPortfolioHistoryList() {
    const tbody = document.getElementById('portfolio-history-list-tbody');
    if (!tbody) return;

    try {
        const [portfolios, backtestHistory] = await Promise.all([
            APIClient.fetchPortfolioList(),
            APIClient.fetchBacktestHistory()
        ]);

        state.portfoliosCache = portfolios;

        // 1. 데이터 가공 및 통합
        const items = [];
        const addedIds = new Set();

        // 실시간 모의투자 세션 처리
        portfolios.forEach(p => {
            if (p.id !== 'default') {
                let initial = p.initial_cash;
                if (typeof initial === 'object') {
                    initial = Object.values(initial).reduce((a, b) => a + b, 0);
                } else {
                    initial = parseFloat(initial) || 0;
                }
                const total = parseFloat(p.total_value) || initial;
                const roi = initial > 0 ? ((total - initial) / initial * 100).toFixed(2) : '0.00';
                
                items.push({
                    id: p.id,
                    name: p.name,
                    type: p.type, // 'simulation' 또는 'simulation_ended'
                    roi: parseFloat(roi),
                    trade_count: p.history ? p.history.length : 0,
                    created_at: p.created_at || new Date().toISOString(),
                    isLive: true
                });
                addedIds.add(p.id);
            }
        });

        // 과거 백테스트 기록 처리
        backtestHistory.forEach(h => {
            if (h.portfolio_id !== 'default' && !addedIds.has(h.portfolio_id)) {
                items.push({
                    id: h.portfolio_id,
                    name: h.name,
                    type: 'backtest',
                    roi: parseFloat(h.roi) || 0,
                    trade_count: h.trade_count || 0,
                    created_at: h.created_at || new Date().toISOString(),
                    isLive: false
                });
                addedIds.add(h.portfolio_id);
            }
        });

        // 정렬: 생성일시 역순 (최신이 위로)
        items.sort((a, b) => new Date(b.created_at) - new Date(a.created_at));

        tbody.innerHTML = '';

        if (items.length === 0) {
            tbody.innerHTML = '<tr><td colspan="4" style="text-align:center; padding:15px; color:#64748B;">저장된 이력이 없습니다.</td></tr>';
            // 세션 제어 UI 동기화
            updateSessionControlUI();
            return;
        }

        // 기본 선택 처리: 현재 currentPortfolioId가 유효하지 않거나 목록에 없는 경우 최신 포트폴리오 지정
        if (!state.currentPortfolioId || !addedIds.has(state.currentPortfolioId)) {
            const activeSim = items.find(item => item.type === 'simulation');
            if (activeSim) {
                state.currentPortfolioId = activeSim.id;
            } else if (items.length > 0) {
                state.currentPortfolioId = items[0].id;
            }
        }

        items.forEach(item => {
            const tr = document.createElement('tr');
            tr.style.borderBottom = '1px solid rgba(148, 163, 184, 0.08)';
            tr.style.cursor = 'pointer';
            if (item.id === state.currentPortfolioId) {
                tr.style.background = 'rgba(99, 102, 241, 0.1)';
            }

            const roiClass = item.roi >= 0 ? 'bull' : 'bear';
            const roiText = `${item.roi >= 0 ? '+' : ''}${item.roi}%`;

            let badgeHtml = '';
            if (item.type === 'simulation') {
                badgeHtml = `<span class="ctx-badge" style="background: rgba(16, 185, 129, 0.2); color: #10B981; font-size: 0.65rem; padding: 1px 4px; border-radius: 3px; font-weight: normal; flex-shrink: 0;">진행중</span>`;
            } else if (item.type === 'simulation_ended') {
                badgeHtml = `<span class="ctx-badge" style="background: rgba(100, 116, 139, 0.2); color: #94A3B8; font-size: 0.65rem; padding: 1px 4px; border-radius: 3px; font-weight: normal; flex-shrink: 0;">종료됨</span>`;
            } else {
                badgeHtml = `<span class="ctx-badge" style="background: rgba(217, 70, 239, 0.2); color: #D946EF; font-size: 0.65rem; padding: 1px 4px; border-radius: 3px; font-weight: normal; flex-shrink: 0;">백테스트</span>`;
            }

            // 날짜 표시 포맷
            const dateStr = item.created_at ? new Date(item.created_at).toLocaleString() : '-';

            // 행 클릭 시 해당 포트폴리오 로드
            tr.onclick = (e) => {
                // 삭제 버튼 클릭 시에는 행 클릭 동작 방지
                if (e.target.closest('.btn-delete-history')) return;
                
                state.currentPortfolioId = item.id;
                document.querySelectorAll('#portfolio-history-list-tbody tr').forEach(r => r.style.background = '');
                tr.style.background = 'rgba(99, 102, 241, 0.1)';
                
                loadPortfolio();
            };

            // 삭제 버튼: 진행중(simulation)이 아닐 때만 노출
            const showDelete = item.type !== 'simulation';
            const deleteBtnHtml = showDelete 
                ? `<button class="btn danger btn-delete-history" style="padding:2px 6px; font-size:0.7rem; background:#EF4444; border:none; color:white; border-radius:4px; cursor:pointer;" onclick="deletePortfolioHistory('${item.id}')">삭제</button>`
                : '';

            tr.innerHTML = `
                <td style="padding:8px 4px; width: 55%; overflow: hidden; text-overflow: ellipsis;">
                    <div style="display:flex; align-items:center; gap:5px; flex-wrap:wrap; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
                        <span style="color:#F8FAFC; font-weight:bold; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 100px;" title="${item.name}">${item.name}</span>
                        ${badgeHtml}
                    </div>
                    <span style="font-size:0.7rem; color:#64748B; display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${dateStr}</span>
                </td>
                <td style="padding:8px 4px; text-align:right; width: 25%;" class="num ${roiClass}">${roiText}</td>
                <td style="padding:8px 4px; text-align:center; width: 20%;">
                    ${deleteBtnHtml}
                </td>
            `;

            tbody.appendChild(tr);
        });

        // 세션 제어 UI 동기화
        updateSessionControlUI();

    } catch (e) {
        tbody.innerHTML = '<tr><td colspan="4" style="text-align:center; padding:15px; color:#EF4444;">이력을 불러오지 못했습니다.</td></tr>';
        console.error("Failed to load portfolio history list", e);
    }
}

/**
 * 특정 백테스트 또는 마감된 실시간 세션 이력을 영구 삭제합니다.
 */
async function deletePortfolioHistory(portfolioId) {
    if (!confirm("해당 세션 이력(포트폴리오 및 체결 이력 전체)을 삭제하시겠습니까? 복구할 수 없습니다.")) {
        return;
    }

    try {
        const res = await APIClient.deleteBacktestHistory(portfolioId);
        if (res.status === 'success') {
            showAlert("이력이 정상적으로 삭제되었습니다.", "success");
            if (state.currentPortfolioId === portfolioId) {
                state.currentPortfolioId = null;
            }
            await loadPortfolioHistoryList();
            await loadPortfolio();
        } else {
            showAlert(res.message || "삭제 실패", "error");
        }
    } catch (e) {
        showAlert("이력 삭제 도중 오류가 발생했습니다.", "error");
        console.error(e);
    }
}

/**
 * 활성 세션을 제외한 모든 이력을 일괄 삭제합니다.
 */
async function clearAllPortfolioHistory() {
    if (!confirm("모든 백테스트 및 종료된 모의투자 세션 이력을 영구 삭제하시겠습니까? 이 작업은 되돌릴 수 없습니다. (가동 중인 세션은 삭제되지 않습니다.)")) {
        return;
    }

    try {
        const res = await APIClient.clearAllBacktestHistory();
        if (res.status === 'success') {
            showAlert("모든 이력이 정상적으로 삭제되었습니다.", "success");
            state.currentPortfolioId = null;
            await loadPortfolioHistoryList();
            await loadPortfolio();
        } else {
            showAlert(res.message || "삭제 실패", "error");
        }
    } catch (e) {
        showAlert("이력 삭제 도중 오류가 발생했습니다.", "error");
        console.error(e);
    }
}


/**
 * 특정 포트폴리오(실시간 모의투자 또는 과거 백테스트 이력)의 상태를 불러와 화면에 업데이트합니다.
 */
async function loadPortfolio() {
    try {
        // 캐시를 참조하여 ID 결정 (currentPortfolioId가 없는 경우 최신 포트폴리오를 fall-back으로 결정)
        let portfolioId = state.currentPortfolioId;
        if (!portfolioId) {
            if (state.portfoliosCache && state.portfoliosCache.length > 0) {
                const activeSim = state.portfoliosCache.find(p => p.type === 'simulation');
                portfolioId = activeSim ? activeSim.id : state.portfoliosCache[0].id;
            }
        }
        
        if (!portfolioId) {
            console.warn("No active or saved portfolio sessions found.");
            return;
        }
        
        state.currentPortfolioId = portfolioId;
        
        // 포트폴리오 타이틀 텍스트 업데이트
        const titleEl = document.getElementById('current-portfolio-title-text');
        if (titleEl) {
            const currentItem = (state.portfoliosCache || []).find(p => p.id === portfolioId);
            titleEl.innerText = currentItem ? currentItem.name : '내 포트폴리오';
        }

        const cachedPort = (state.portfoliosCache || []).find(p => p.id === portfolioId);
        const isBacktest = portfolioId.startsWith('backtest_') || (cachedPort && cachedPort.type === 'simulation_ended');

        // UI 요소 캐시
        const typeBadge = document.getElementById('portfolio-type-badge');
        const panicBtn = document.getElementById('btn-panic-sell');
        const backtestSummary = document.getElementById('portfolio-backtest-summary');
        const appliedStrategies = document.getElementById('portfolio-applied-strategies');
        const backtestAnalysisPanels = document.getElementById('portfolio-backtest-analysis-panels');

        let data;
        if (isBacktest) {
            // 과거 백테스트 상세 복원 API 사용 (마감된 모의투자 세션도 지원)
            const res = await APIClient.fetchBacktestHistoryDetail(portfolioId);
            if (res.status !== 'success') {
                throw new Error(res.message || "세션 이력 로드 실패");
            }
            data = res;
        } else {
            // 실시간 모의투자 API 사용
            data = await APIClient.fetchPortfolio(portfolioId);
        }

        // --- 공통 렌더링을 위한 데이터 가공 ---
        let totalValue = 0;
        let cash = 0;
        let roi = 0;
        let positions = [];
        let history = [];
        let exchangeCashMap = {};

        if (isBacktest) {
            totalValue = data.summary.final_value;
            roi = data.summary.roi;
            
            // 백테스트의 포지션 정보 구성
            positions = data.results.map(r => ({
                symbol: r.symbol,
                quantity: r.quantity !== undefined ? r.quantity : (r.currentQty || 0),
                avg_price: r.avg_price !== undefined ? r.avg_price : (r.avgPrice || 0),
                current_price: r.final_price !== undefined ? r.final_price : (r.finalPrice || 0),
                exchange: r.exchange
            }));

            // 백테스트 평가현금 = 최종 평가액 - 총 보유 포지션 가치
            const positionsValue = positions.reduce((acc, pos) => acc + (pos.quantity * pos.current_price), 0);
            cash = Math.max(0, totalValue - positionsValue);

            // 거래소별 백테스트 잔여 현금 추정
            const exInit = data.exchange_initial_cash || {};
            Object.entries(exInit).forEach(([ex, initCash]) => {
                exchangeCashMap[ex] = initCash;
            });
            
            data.results.forEach(r => {
                const ex = (r.exchange || 'upbit').toLowerCase();
                if (exchangeCashMap[ex] === undefined) exchangeCashMap[ex] = 0; // fallback
                
                (r.trades || []).forEach(t => {
                    const impact = t.price * t.quantity;
                    if (t.side === 'BUY') {
                        exchangeCashMap[ex] -= impact;
                    } else if (t.side === 'SELL') {
                        exchangeCashMap[ex] += impact;
                    }
                    exchangeCashMap[ex] -= (t.fee || 0);
                });
            });

            // 체결 이력 구성 (모든 종목의 trades 병합 후 타임스탬프 역순 정렬)
            data.results.forEach(r => {
                (r.trades || []).forEach(t => {
                    history.push({
                        timestamp: t.timestamp / 1000,
                        symbol: r.symbol,
                        side: t.side,
                        price: t.price,
                        quantity: t.quantity,
                        reason: t.reason,
                        context: null
                    });
                });
            });
            history.sort((a, b) => b.timestamp - a.timestamp); // 화면에는 역순(최신이 위로) 표시
        } else {
            totalValue = data.total_value;
            cash = data.cash;
            const initialValue = data.initial_cash || 10000000;
            roi = ((totalValue - initialValue) / initialValue * 100).toFixed(2);
            positions = data.positions;
            history = [...data.history].reverse(); // 최신이 위로

            if (data.exchanges) {
                data.exchanges.forEach(ex => {
                    exchangeCashMap[ex.exchange_id.toLowerCase()] = ex.cash;
                });
            }
        }

        state.currentPortfolioData = {
            id: portfolioId,
            total_value: totalValue,
            cash: cash,
            exchange_cash: exchangeCashMap,
            positions: positions,
            history: history
        };

        // --- 화면 레이아웃 분기 제어 및 뱃지/메트릭 렌더링 ---
        if (isBacktest) {
            // 배지 변경
            if (typeBadge) {
                if (cachedPort && cachedPort.type === 'simulation_ended') {
                    typeBadge.innerText = '모의투자 종료';
                    typeBadge.style.background = '#64748B';
                } else {
                    typeBadge.innerText = '과거 백테스트';
                    typeBadge.style.background = '#D946EF';
                }
                typeBadge.style.display = 'inline-block';
            }
            // 긴급 손절 숨김
            if (panicBtn) panicBtn.style.display = 'none';

            // 백테스트 메타 요약 노출 및 값 맵핑
            if (backtestSummary) {
                backtestSummary.style.display = 'grid';
                document.getElementById('port-initial-cash').innerText = Math.round(data.summary.initial_cash).toLocaleString() + " 원";
                document.getElementById('port-total-fee').innerText = Math.round(data.summary.fee).toLocaleString() + " 원";
                document.getElementById('port-trade-count').innerText = data.summary.trade_count + " 건";
                document.getElementById('port-duration').innerText = (data.duration || 0) + "초";
            }

            // 적용된 전략 표기
            if (appliedStrategies) {
                appliedStrategies.style.display = 'block';
                let appliedHtml = '<strong>적용된 전략 정보:</strong><br>';
                data.applied_strategies.forEach(s => {
                    const paramStr = Object.entries(s.params).map(([k, v]) => `${k}: ${v}`).join(', ');
                    appliedHtml += `<span class="ctx-badge" style="margin-top: 5px; display: inline-block;">${s.name} (${paramStr})</span> `;
                });
                appliedStrategies.innerHTML = appliedHtml;
            }

            // 성과 분석 패널 보임
            if (backtestAnalysisPanels) backtestAnalysisPanels.style.display = 'flex';

            // 성과 상세 렌더링 호출
            renderBacktestPerformance(data);
        } else {
            // 배지 변경
            if (typeBadge) {
                typeBadge.innerText = '실시간 모의투자';
                typeBadge.style.background = '#3B82F6';
                typeBadge.style.display = 'inline-block';
            }
            // 긴급 손절 활성화
            if (panicBtn) {
                panicBtn.style.display = 'inline-block';
                panicBtn.disabled = false;
            }

            // 백테스트 메타 요약 숨김
            if (backtestSummary) backtestSummary.style.display = 'none';
            if (appliedStrategies) appliedStrategies.style.display = 'none';

            // 성과 분석 패널 노출 (실시간 통합 모의투자 보임)
            if (backtestAnalysisPanels) backtestAnalysisPanels.style.display = 'flex';
            
            // 실시간 데이터를 백테스트 성능 데이터 구조로 어댑팅하여 하단 카드 렌더링 호출
            const simulatedRes = transformRealtimeToPerformance(data, roi);
            renderBacktestPerformance(simulatedRes);
        }

        // 요약 정보 업데이트
        document.getElementById('port-total-value').innerText = Math.round(totalValue).toLocaleString();
        document.getElementById('port-cash').innerText = Math.round(cash).toLocaleString();
        
        const roiEl = document.getElementById('port-total-roi');
        if (roiEl) {
            roiEl.innerText = `${roi >= 0 ? '+' : ''}${roi}%`;
            roiEl.className = `value ${roi >= 0 ? 'bull' : 'bear'}`;
        }

        // 포지션 테이블 업데이트
        const posTbody = document.getElementById('positions-tbody');
        posTbody.innerHTML = '';
        if (positions.length === 0) {
            posTbody.innerHTML = '<tr><td colspan="4" style="text-align:center;padding:20px;">보유 종목이 없습니다.</td></tr>';
        } else {
            positions.forEach(pos => {
                const tr = document.createElement('tr');
                let currentPrice = pos.avg_price;
                let profitRate = 0;
                
                if (isBacktest) {
                    currentPrice = pos.current_price || pos.avg_price;
                    profitRate = pos.avg_price > 0 ? ((currentPrice - pos.avg_price) / pos.avg_price * 100).toFixed(4) : 0;
                } else {
                    const coin = marketData.find(c => c.market === pos.symbol);
                    currentPrice = coin ? coin.trade_price : pos.avg_price;
                    profitRate = pos.avg_price > 0 ? ((currentPrice - pos.avg_price) / pos.avg_price * 100).toFixed(2) : 0;
                }
                
                const rateClass = profitRate >= 0 ? 'bull' : 'bear';
                const exBadge = pos.exchange ? `<span class="ctx-badge" style="font-size: 0.65rem; padding: 2px 4px; margin-left: 5px; vertical-align: middle; background: rgba(148, 163, 184, 0.15);">${pos.exchange.toUpperCase()}</span>` : '';

                tr.innerHTML = `
                    <td>
                        <strong>${pos.symbol.replace(/^(KRW-|UPB-|KIS-)/, '')}</strong>
                        ${exBadge}
                    </td>
                    <td class="num">${pos.quantity.toFixed(4)}</td>
                    <td class="num">${pos.avg_price.toLocaleString()}</td>
                    <td class="num ${rateClass}">${profitRate}%</td>
                `;
                
                // 행 클릭 시 상세 모달 열기
                tr.onclick = () => showAssetDetails(pos.exchange || (pos.symbol.includes('KIS') ? 'kis' : 'upbit'), pos.symbol);
                
                posTbody.appendChild(tr);
            });
        }

        // 히스토리 테이블 업데이트 (최근 15개만 보임 - 실시간 탭 최적화)
        const histTbody = document.getElementById('port-history-tbody');
        histTbody.innerHTML = '';
        
        const recentHistory = isBacktest ? history.slice(0, 15) : history; // 백테스트 시에는 하단 상세 탭이 따로 있으므로 상단은 요약
        if (recentHistory.length === 0) {
            histTbody.innerHTML = '<tr><td colspan="6" style="text-align:center;padding:20px;">거래 내역이 없습니다.</td></tr>';
        } else {
            recentHistory.forEach(h => {
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
                    <td>${h.symbol.replace(/^(KRW-|UPB-|KIS-)/, '')}</td>
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

        // 자산 비중 차트 업데이트 (isBacktest 전달)
        renderAllocationChart(state.currentPortfolioData, isBacktest);

        // 상세 모달 열려있으면 갱신
        if (state.activeAssetDetail) {
            updateModalContent(state.activeAssetDetail.exchange, state.activeAssetDetail.symbol);
        }

        // --- 실시간 시세 구독 핫스왑 (실시간 모의투자일 때만) ---
        if (!isBacktest && state.ws && state.ws.readyState === WebSocket.OPEN) {
            const activeSymbols = new Set([`${state.currentExchange}:${state.currentSymbol}`]);
            positions.forEach(pos => {
                const exch = pos.exchange || 'upbit';
                activeSymbols.add(`${exch}:${pos.symbol}`);
            });
            activeSymbols.forEach(token => {
                const [exch, sym] = token.split(':');
                state.ws.send(JSON.stringify({ subscribe: sym, exchange: exch }));
            });
        }

        // 세션 제어 UI 동기화
        updateSessionControlUI();

    } catch (e) {
        console.error("Portfolio load failed", e);
    }
}

/**
 * 자산 비중 차트를 렌더링하기 위한 컨테이너와 모듈 초기화 로직입니다.
 * @param {object} data - 포트폴리오 데이터
 * @param {boolean} isBacktest - 백테스트 여부
 */
/**
 * 금액을 한국어 단위(억, 만)로 포맷팅하는 유틸리티 함수입니다.
 */
function formatKoreanAmount(val) {
    if (val === undefined || val === null || isNaN(val)) return '0원';
    if (val >= 100000000) {
        const eok = val / 100000000;
        return eok % 1 === 0 ? eok.toFixed(0) + "억 원" : eok.toFixed(2) + "억 원";
    }
    if (val >= 10000) {
        const man = val / 10000;
        return man % 1 === 0 ? man.toFixed(0) + "만 원" : man.toFixed(1) + "만 원";
    }
    return Math.round(val).toLocaleString() + "원";
}

/**
 * 자산 비중 차트를 렌더링합니다.
 * @param {object} data - 포트폴리오 데이터
 * @param {boolean} isBacktest - 백테스트 여부
 */
function renderAllocationChart(data, isBacktest = false) {
    const mainContainer = document.getElementById('portfolio-allocation-container');
    if (!mainContainer) return;

    mainContainer.innerHTML = ''; // 초기화
    
    // 가로 방향 3열 배치 스타일 지정
    mainContainer.style.display = 'flex';
    mainContainer.style.flexDirection = 'row';
    mainContainer.style.flexWrap = 'wrap';
    mainContainer.style.gap = '15px';
    mainContainer.style.justifyContent = 'flex-start';

    // 자산을 거래소별로 그룹화
    const exchangeGroups = {};
    
    // UI 복잡도를 줄이기 위해 비중 차트는 '순수 보유 포지션' 위주로 그리고,
    // 거래소가 명확한 종목들만 거래소 그룹에 할당합니다.
    data.positions.forEach((pos, idx) => {
        const ex = pos.exchange ? pos.exchange.toLowerCase() : 'upbit';
        if (!exchangeGroups[ex]) {
            exchangeGroups[ex] = {
                exchange: ex,
                assets: [],
                totalValue: 0
            };
        }
        
        let currentPrice = pos.avg_price;
        if (isBacktest) {
            currentPrice = pos.current_price || pos.avg_price;
        } else {
            const coin = (typeof marketData !== 'undefined' ? marketData : []).find(c => c.market === pos.symbol);
            currentPrice = coin ? coin.trade_price : pos.avg_price;
        }
        
        const value = pos.quantity * currentPrice;
        if (value > 0) {
            exchangeGroups[ex].assets.push({
                symbol: pos.symbol,
                label: pos.symbol.replace(/^(KRW-|UPB-|KIS-)/, ''), 
                koreanName: (!isBacktest && typeof marketData !== 'undefined') 
                    ? (marketData.find(c => c.market === pos.symbol)?.korean_name || pos.symbol.replace(/^(KRW-|UPB-|KIS-)/, '')) 
                    : pos.symbol.replace(/^(KRW-|UPB-|KIS-)/, ''),
                value: value,
                color: ASSET_COLORS[idx % ASSET_COLORS.length],
                exchange: ex
            });
            exchangeGroups[ex].totalValue += value;
        }
    });

    let exKeys = Object.keys(exchangeGroups);

    // 보유 현금이 있다면 거래소별 현금 맵(exchange_cash)을 참조하여 할당합니다.
    const exchangeCashMap = data.exchange_cash || {};
    const exCashKeys = Object.keys(exchangeCashMap);

    if (exCashKeys.length > 0) {
        exCashKeys.forEach(ex => {
            const exCash = exchangeCashMap[ex];
            if (exCash > 0) {
                if (!exchangeGroups[ex]) {
                    exchangeGroups[ex] = { exchange: ex, assets: [], totalValue: 0 };
                }
                exchangeGroups[ex].assets.push({
                    symbol: null,
                    label: 'CASH',
                    koreanName: '보유 현금',
                    value: exCash,
                    color: '#475569', // Slate 600 계열 중립 톤
                    exchange: ex
                });
                exchangeGroups[ex].totalValue += exCash;
            }
        });
        exKeys = Object.keys(exchangeGroups);
    } else if (data.cash > 0) {
        // Fallback: exchange_cash가 없는 경우 기본 upbit로 임시 할당
        const defaultEx = exKeys.length > 0 ? exKeys[0] : 'upbit';
        if (!exchangeGroups[defaultEx]) {
            exchangeGroups[defaultEx] = {
                exchange: defaultEx,
                assets: [],
                totalValue: 0
            };
        }
        exchangeGroups[defaultEx].assets.push({
            symbol: null,
            label: 'CASH',
            koreanName: '보유 현금',
            value: data.cash,
            color: '#475569',
            exchange: defaultEx
        });
        exchangeGroups[defaultEx].totalValue += data.cash;
        exKeys = Object.keys(exchangeGroups);
    }

    if (exKeys.length === 0) {
        const div = document.createElement('div');
        div.style.textAlign = 'center';
        div.style.padding = '20px';
        div.style.color = '#64748B';
        div.style.width = '100%';
        div.innerText = '보유 자산이 없습니다.';
        mainContainer.appendChild(div);
        return;
    }

    // 각 거래소별로 컨테이너를 생성하여 차트를 그립니다.
    exKeys.forEach(exKey => {
        const group = exchangeGroups[exKey];
        if (group.totalValue <= 0) return;
        
        group.assets.sort((a, b) => b.value - a.value);

        // 거래소별 서브 컨테이너
        const wrapper = document.createElement('div');
        wrapper.className = 'allocation-content';
        wrapper.style.display = 'flex';
        wrapper.style.justifyContent = 'center';
        wrapper.style.alignItems = 'center';
        wrapper.style.padding = '5px 0';

        const chartBox = document.createElement('div');
        chartBox.className = 'chart-box';
        chartBox.style.width = '100%';
        chartBox.style.display = 'flex';
        chartBox.style.justifyContent = 'center';
        
        const chartContainer = document.createElement('div');
        chartContainer.style.width = '100%';
        chartContainer.style.maxWidth = '180px'; // 3열 가로 배치를 위한 최적 사이즈
        chartContainer.style.aspectRatio = '1 / 1';
        
        chartBox.appendChild(chartContainer);
        wrapper.appendChild(chartBox);
        
        // 거래소 배지 헤더
        const header = document.createElement('div');
        header.style.marginBottom = '6px';
        header.style.textAlign = 'center';
        header.innerHTML = `<span class="ctx-badge" style="background: rgba(148,163,184,0.12); font-size: 0.75rem; border: 1px solid rgba(148,163,184,0.1);">${exKey.toUpperCase()}</span>`;
        
        const groupWrapper = document.createElement('div');
        groupWrapper.className = 'allocation-group-wrapper';
        groupWrapper.style.flex = '1 1 calc(33.3% - 15px)';
        groupWrapper.style.minWidth = '160px';
        groupWrapper.style.padding = '12px 10px';
        groupWrapper.style.background = 'rgba(30, 41, 59, 0.4)'; // Slate Surface 색상
        groupWrapper.style.borderRadius = '8px';
        groupWrapper.style.border = '1px solid rgba(148, 163, 184, 0.08)';
        groupWrapper.style.boxSizing = 'border-box';
        
        groupWrapper.appendChild(header);
        groupWrapper.appendChild(wrapper);
        mainContainer.appendChild(groupWrapper);

        // SVG 렌더링 (범례 없음)
        createAllocationSvg(group.assets, group.totalValue, chartContainer);
    });
}

/**
 * 순수 SVG 렌더링 어댑터입니다. 주어진 DOM 컨테이너에 차트를 주입합니다.
 */
function createAllocationSvg(assets, total, chartContainer) {
    if (total <= 0) return;

    const size = 200;
    const center = size / 2;
    const radius = 82;
    const strokeWidth = 24;
    const circumference = 2 * Math.PI * radius;
    
    let accumulatedPercent = 0;

    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", `0 0 ${size} ${size}`);
    svg.style.width = "100%";
    svg.style.height = "100%";

    const centerText1 = document.createElementNS("http://www.w3.org/2000/svg", "text");
    centerText1.setAttribute("x", "50%");
    centerText1.setAttribute("y", "38%");
    centerText1.setAttribute("text-anchor", "middle");
    centerText1.setAttribute("fill", "#94A3B8");
    centerText1.setAttribute("font-size", "10px");
    centerText1.textContent = "총 보유 자산";

    const centerText2 = document.createElementNS("http://www.w3.org/2000/svg", "text");
    centerText2.setAttribute("x", "50%");
    centerText2.setAttribute("y", "54%");
    centerText2.setAttribute("text-anchor", "middle");
    centerText2.setAttribute("fill", "#F8FAFC");
    centerText2.setAttribute("font-size", "14px");
    centerText2.setAttribute("font-weight", "bold");
    centerText2.textContent = formatKoreanAmount(total);

    const centerText3 = document.createElementNS("http://www.w3.org/2000/svg", "text");
    centerText3.setAttribute("x", "50%");
    centerText3.setAttribute("y", "68%");
    centerText3.setAttribute("text-anchor", "middle");
    centerText3.setAttribute("fill", "#64748B");
    centerText3.setAttribute("font-size", "10px");
    centerText3.textContent = "PORTFOLIO";

    assets.forEach(asset => {
        const percent = (asset.value / total);
        if (percent < 0.001) return;

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
        circle.style.transition = "stroke-width 0.2s ease, stroke 0.2s ease";
        
        circle.style.cursor = 'pointer';
        circle.onclick = () => {
            if (asset.symbol) showAssetDetails(asset.symbol.includes('KIS') ? 'kis' : 'upbit', asset.symbol);
        };
        
        circle.onmouseover = () => {
            circle.setAttribute("stroke-width", strokeWidth + 6);
            
            // 1. 상단: 한글 자산명 (없으면 영문 레이블)
            const displayName = asset.koreanName || asset.label;
            centerText1.textContent = displayName;
            centerText1.setAttribute("fill", asset.color);
            
            // 2. 중앙: 자산 개별 금액
            centerText2.textContent = formatKoreanAmount(asset.value);
            
            // 3. 하단: 영문 심볼 + 비율
            centerText3.textContent = asset.symbol ? `${asset.label} (${(percent * 100).toFixed(1)}%)` : `CASH (${(percent * 100).toFixed(1)}%)`;
            centerText3.setAttribute("fill", "rgba(148, 163, 184, 0.8)");
        };
        
        circle.onmouseout = () => {
            circle.setAttribute("stroke-width", strokeWidth);
            
            // 기본 상태 복원
            centerText1.textContent = "총 보유 자산";
            centerText1.setAttribute("fill", "#94A3B8");
            
            centerText2.textContent = formatKoreanAmount(total);
            
            centerText3.textContent = "PORTFOLIO";
            centerText3.setAttribute("fill", "#64748B");
        };

        const anim = document.createElementNS("http://www.w3.org/2000/svg", "animate");
        anim.setAttribute("attributeName", "stroke-dashoffset");
        anim.setAttribute("from", circumference);
        anim.setAttribute("to", -accumulatedPercent * circumference);
        anim.setAttribute("dur", "0.6s");
        anim.setAttribute("fill", "freeze");
        circle.appendChild(anim);

        svg.appendChild(circle);
        accumulatedPercent += percent;
    });

    svg.appendChild(centerText1);
    svg.appendChild(centerText2);
    svg.appendChild(centerText3);
    chartContainer.appendChild(svg);
}

/**
 * 현재 보유 중인 모든 종목을 시장가로 즉시 청산(매도)하고 시스템을 긴급 비상정지합니다.
 */
async function executePanicSell() {
    if (!confirm("🚨 정말로 모든 보유 종목을 시장가로 긴급 매도하시겠습니까?\n이 작업은 즉시 실행되며 취소할 수 없습니다.")) {
        return;
    }

    const btn = document.getElementById('btn-panic-sell');
    if (!btn) return;
    const originalText = btn.innerText;
    btn.disabled = true;
    btn.innerText = "🚨 긴급 청산 중...";

    try {
        const result = await APIClient.panicSellPortfolio(state.currentPortfolioId);

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

/**
 * 개별 자산 종목에 대한 상세 모달 창을 엽니다.
 * @param {string} exchange - 거래소 고유 ID
 * @param {string} symbol - 종목 코드
 */
function showAssetDetails(exchange, symbol) {
    state.activeAssetDetail = { exchange, symbol };
    updateModalContent(exchange, symbol);
    const modal = document.getElementById('asset-modal');
    if (modal) {
        modal.style.display = 'flex';
        modal.onclick = (e) => {
            if (e.target === modal) closeAssetModal();
        };
    }
}

/**
 * 자산 상세 모달 내 데이터를 갱신합니다.
 */
function updateModalContent(exchange, symbol) {
    if (!state.currentPortfolioData) return;
    const data = state.currentPortfolioData;
    const pos = data.positions.find(p => p.exchange === exchange && p.symbol === symbol);
    if (!pos) return;

    const coin = marketData.find(c => c.exchange === exchange && c.market === symbol);
    const currentPrice = coin ? coin.trade_price : pos.avg_price;
    const profitRate = ((currentPrice - pos.avg_price) / pos.avg_price * 100).toFixed(2);
    const pnl = (currentPrice - pos.avg_price) * pos.quantity;

    // 헤더 업데이트
    document.getElementById('modal-asset-symbol').innerText = symbol;
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
    if (modalTbody) {
        modalTbody.innerHTML = '';
        const assetHistory = data.history.filter(h => h.symbol === symbol).reverse();
        if (assetHistory.length === 0) {
            modalTbody.innerHTML = '<tr><td colspan="6" style="text-align:center;padding:20px;">거래 내역이 없습니다.</td></tr>';
        } else {
            assetHistory.forEach(h => {
                const tr = document.createElement('tr');
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
}

/**
 * 자산 상세 모달 창을 닫습니다.
 */
function closeAssetModal() {
    state.activeAssetDetail = null;
    const modal = document.getElementById('asset-modal');
    if (modal) modal.style.display = 'none';
}

/**
 * 업비트 API를 통해 실제 잔고를 불러와 화면에 요약 정보를 출력합니다.
 */
async function loadRealAssets() {
    const tbody = document.getElementById('real-assets-tbody');
    const totalValueEl = document.getElementById('real-total-value');
    const assetCountEl = document.getElementById('real-asset-count');
    
    if (!tbody) return;
    
    tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;padding:30px;color:rgba(255,255,255,0.4);">&#x23F3; 업비트 API에서 자산 명세를 안전하게 조회 중입니다...</td></tr>';
    
    try {
        const data = await APIClient.fetchRealAssets('upbit');
        if (!data || !data.assets) {
            tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;padding:20px;color:rgba(255,255,255,0.4);">자산 내역이 비어있거나 키를 확인하세요.</td></tr>';
            return;
        }
        
        // 헤더 메트릭스 업데이트
        if (totalValueEl) totalValueEl.innerText = `${data.formatted_total_value} 원`;
        if (assetCountEl) assetCountEl.innerText = `${data.assets.length} 개 종목`;
        
        tbody.innerHTML = '';
        
        if (data.assets.length === 0) {
            tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;padding:20px;">보유 자산이 없습니다.</td></tr>';
            return;
        }
        
        data.assets.forEach(asset => {
            const tr = document.createElement('tr');
            tr.className = 'market-row';
            
            // 평가 수익률 연산
            let roiHtml = '-';
            if (asset.avg_buy_price > 0 && asset.currency !== 'KRW') {
                const roi = ((asset.current_price - asset.avg_buy_price) / asset.avg_buy_price * 100).toFixed(2);
                roiHtml = `<span class="${roi >= 0 ? 'bull' : 'bear'}">${roi >= 0 ? '+' : ''}${roi}%</span>`;
            }
            
            // 수량 정밀도 처리
            const balanceStr = asset.currency === 'KRW' 
                ? Math.floor(asset.balance).toLocaleString() 
                : asset.balance.toFixed(4);
                
            // 게이지 비주얼 바 렌더링
            const barHtml = `
                <div class="progress-bar-container">
                    <div class="progress-bar-fill" style="width: ${asset.percent}%"></div>
                    <span class="progress-bar-text">${asset.percent}%</span>
                </div>
            `;
            
            // 코인 로고 아이콘 URL 및 Fallback SVG 처리
            let iconHtml = '';
            if (asset.currency === 'KRW') {
                iconHtml = `<img src="data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' width='24' height='24'><circle cx='12' cy='12' r='10' fill='%234caf50'/><text x='50%' y='62%' font-size='10' font-family='sans-serif' font-weight='bold' fill='white' text-anchor='middle'>₩</text></svg>" style="width:24px; height:24px; border-radius:50%; flex-shrink:0;">`;
            } else {
                const iconUrl = `https://static.upbit.com/logos/${asset.currency}.png`;
                iconHtml = `<img src="${iconUrl}" style="width:24px; height:24px; border-radius:50%; background:#1E293B; flex-shrink:0;" onerror="this.onerror=null; this.src='data:image/svg+xml;utf8,<svg xmlns=\\'http://www.w3.org/2000/svg\\' viewBox=\\'0 0 24 24\\' width=\\'24\\' height=\\'24\\'><circle cx=\\'12\\' cy=\\'12\\' r=\\'10\\' fill=\\'%231E293B\\' stroke=\\'%234b5563\\' stroke-width=\\'1\\'/><text x=\\'50%\\' y=\\'62%\\' font-size=\\'9\\' font-family=\\'sans-serif\\' font-weight=\\'bold\\' fill=\\'%2394A3B8\\' text-anchor=\\'middle\\'>${asset.currency.slice(0, 3)}</text></svg>';">`;
            }
            
            tr.innerHTML = `
                <td>
                    <div style="display:flex; align-items:center; gap: 10px;">
                        ${iconHtml}
                        <div style="display:flex; flex-direction:column; line-height:1.2;">
                            <span style="font-weight:bold; color:#F8FAFC; font-size:0.9rem;">${asset.korean_name}</span>
                            <span style="font-size:0.72rem; color:#94A3B8; font-family:'Roboto Mono', monospace;">${asset.currency}</span>
                        </div>
                    </div>
                </td>
                <td class="num" style="text-align:right; font-family:'Roboto Mono', monospace;">${balanceStr}</td>
                <td class="num" style="text-align:right;">${asset.avg_buy_price > 0 ? (asset.avg_buy_price >= 100 ? Math.floor(asset.avg_buy_price).toLocaleString() : asset.avg_buy_price.toLocaleString()) : '-'}</td>
                <td class="num" style="text-align:right;">${asset.current_price > 0 ? (asset.current_price >= 100 ? Math.floor(asset.current_price).toLocaleString() : asset.current_price.toLocaleString()) : '-'}</td>
                <td class="num" style="text-align:right; font-weight:bold; color:#F8FAFC;">${asset.formatted_eval_value} 원</td>
                <td>${barHtml}</td>
            `;
            
            // 더블 클릭 시 해당 코인 차트 뷰로 즉시 연동
            tr.addEventListener('dblclick', () => {
                if (asset.currency === 'KRW') return;
                const symbol = `KRW-${asset.currency}`;
                state.currentSymbol = symbol;
                state.currentExchange = 'upbit';
                updateHeaderInfo('upbit', symbol);
                
                const select = document.getElementById('symbol-select');
                if (select) select.value = `upbit:${symbol}`;
                
                if (state.ws && state.ws.readyState === WebSocket.OPEN) {
                    state.ws.send(JSON.stringify({ subscribe: symbol, exchange: 'upbit' }));
                }
                
                ViewRouter.navigateTo('monitoring-view');
                
                exitExplorerMode();
                loadHistory();
                showAlert(`${asset.korean_name} 차트로 이동합니다.`, 'info');
            });
            
            tbody.appendChild(tr);
        });
    } catch (e) {
        tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;padding:20px;color:#FF4B4B;">&#x26A0;&#xFE0F; 자산 조회 실패 (API 키 권한 또는 인터넷 연결 상태를 확인하세요)</td></tr>';
        console.error("Asset load failed", e);
    }
}

/**
 * 실시간 모의투자 데이터를 과거 백테스트 상세 성과 분석 포맷으로 변환해주는 어댑터 함수입니다.
 */
function transformRealtimeToPerformance(data, roi) {
    const totalFee = (data.history || []).reduce((acc, h) => acc + (h.fee || 0), 0);
    const totalTrades = (data.history || []).length;

    const simulatedRes = {
        portfolio_id: data.id,
        name: data.name,
        duration: 0,
        summary: {
            initial_cash: data.initial_cash,
            final_value: data.total_value,
            profit: data.total_value - data.initial_cash,
            roi: roi,
            fee: totalFee,
            trade_count: totalTrades
        },
        applied_strategies: [],
        exchange_initial_cash: {},
        results: []
    };

    // 1. 거래소별 초기금 설정
    if (data.exchanges && data.exchanges.length > 0) {
        data.exchanges.forEach(ex => {
            simulatedRes.exchange_initial_cash[ex.exchange_id] = ex.initial_cash;
        });
    } else {
        simulatedRes.exchange_initial_cash['upbit'] = data.initial_cash;
    }

    // 2. 종목별 거래 이력 맵핑
    const symbolTradesMap = {};
    (data.history || []).forEach(h => {
        const sym = h.symbol;
        if (!symbolTradesMap[sym]) {
            symbolTradesMap[sym] = [];
        }
        symbolTradesMap[sym].push({
            timestamp: h.timestamp * 1000, // ms
            side: h.side,
            price: h.price,
            quantity: h.quantity,
            fee: h.fee || 0,
            reason: h.reason || ""
        });
    });

    // 3. 종목별 결과 리스트 생성
    (data.positions || []).forEach(pos => {
        const sym = pos.symbol;
        const trades = symbolTradesMap[sym] || [];
        trades.sort((a, b) => a.timestamp - b.timestamp); // 과거 -> 최신 순 정렬

        let finalPrice = pos.current_price || pos.avg_price;
        if (pos.exchange.toLowerCase() === 'upbit' && typeof marketData !== 'undefined') {
            const coin = marketData.find(c => c.market === pos.symbol);
            if (coin) finalPrice = coin.trade_price;
        }

        simulatedRes.results.push({
            symbol: pos.symbol,
            exchange: pos.exchange || 'upbit',
            korean_name: pos.symbol,
            trades: trades,
            finalPrice: finalPrice,
            initial_cash: 0
        });
    });

    // 4. 포지션은 다 청산했지만 거래 이력이 남아있는 종목들 추가
    Object.entries(symbolTradesMap).forEach(([sym, trades]) => {
        const alreadyAdded = simulatedRes.results.some(r => r.symbol === sym);
        if (!alreadyAdded && trades.length > 0) {
            trades.sort((a, b) => a.timestamp - b.timestamp);
            const lastTrade = trades[trades.length - 1];
            const exch = (data.history.find(h => h.symbol === sym) || {}).exchange || 'upbit';
            simulatedRes.results.push({
                symbol: sym,
                exchange: exch,
                korean_name: sym,
                trades: trades,
                finalPrice: lastTrade.price,
                initial_cash: 0
            });
        }
    });

    return simulatedRes;
}

// 과거 백테스트 결과 성능 상세 분석 렌더러
window.lastBacktestResult = null;

function renderBacktestPerformance(res) {
    window.lastBacktestResult = res;
    renderExchangeSummaryPort();
}

function renderExchangeSummaryPort() {
    const posTbody = document.getElementById('port-exchanges-tbody');
    if (!posTbody) return;
    posTbody.innerHTML = '';
    
    const res = window.lastBacktestResult;
    const results = res ? (res.results || []) : [];
    const exInitialCashMap = res ? (res.exchange_initial_cash || {}) : {};

    if (results.length === 0 && Object.keys(exInitialCashMap).length === 0) {
        posTbody.innerHTML = '<tr><td colspan="7" style="text-align:center;padding:20px;color:#64748B;">매매 거래가 발생한 종목이 없습니다.</td></tr>';
        const detailTbody = document.getElementById('port-history-detail-tbody');
        if (detailTbody) {
            detailTbody.innerHTML = '<tr><td colspan="8" style="text-align:center;padding:20px;">거래 내역이 없습니다.</td></tr>';
        }
        return;
    }

    const exchangeSummary = {};
    
    Object.entries(exInitialCashMap).forEach(([ex, cashVal]) => {
        const exKey = ex.toLowerCase();
        exchangeSummary[exKey] = {
            exchange: exKey,
            symbolCount: 0,
            tradeCount: 0,
            fee: 0,
            profit: 0,
            initialCash: cashVal
        };
    });

    results.forEach(item => {
        const exKey = item.exchange.toLowerCase();
        if (!exchangeSummary[exKey]) {
            exchangeSummary[exKey] = {
                exchange: exKey,
                symbolCount: 0,
                tradeCount: 0,
                fee: 0,
                profit: 0,
                initialCash: item.initial_cash || 0
            };
        }
        
        const trades = item.trades || [];
        const finalPrice = item.finalPrice || (trades.length > 0 ? trades[trades.length - 1].price : 0);

        let currentQty = 0;
        let feeSum = 0;
        let sellSum = 0;
        let buySum = 0;

        trades.forEach(t => {
            feeSum += t.fee || 0;
            if (t.side === 'BUY') {
                currentQty += t.quantity;
                buySum += t.price * t.quantity;
            } else {
                currentQty -= t.quantity;
                sellSum += t.price * t.quantity;
                if (currentQty <= 0) currentQty = 0;
            }
        });

        const valuation = currentQty * finalPrice;
        const profit = sellSum + valuation - buySum - feeSum;

        exchangeSummary[exKey].symbolCount += 1;
        exchangeSummary[exKey].tradeCount += trades.length;
        exchangeSummary[exKey].fee += feeSum;
        exchangeSummary[exKey].profit += profit;
    });

    let totalInitial = 0;
    let totalFinal = 0;
    let totalSymbols = 0;
    let totalTrades = 0;
    let totalFees = 0;
    let totalProfit = 0;

    Object.values(exchangeSummary).forEach(sum => {
        const tr = document.createElement('tr');
        tr.style.cursor = 'pointer';
        tr.id = `port-ex-row-${sum.exchange}`;
        
        const profitClass = sum.profit >= 0 ? 'bull' : 'bear';
        const profitText = (sum.profit >= 0 ? '+' : '') + Math.round(sum.profit).toLocaleString() + " 원";
        
        const finalValue = sum.initialCash + sum.profit;

        totalInitial += sum.initialCash;
        totalFinal += finalValue;
        totalSymbols += sum.symbolCount;
        totalTrades += sum.tradeCount;
        totalFees += sum.fee;
        totalProfit += sum.profit;

        tr.innerHTML = `
            <td><strong>${sum.exchange.toUpperCase()}</strong></td>
            <td class="num">${Math.round(sum.initialCash).toLocaleString()} 원</td>
            <td class="num">${Math.round(finalValue).toLocaleString()} 원</td>
            <td class="num">${sum.symbolCount} 개</td>
            <td class="num">${sum.tradeCount} 건</td>
            <td class="num">${Math.round(sum.fee).toLocaleString()} 원</td>
            <td class="num ${profitClass}">${profitText}</td>
        `;

        tr.onclick = () => {
            document.querySelectorAll('#port-exchanges-table tbody tr').forEach(r => r.classList.remove('selected'));
            tr.classList.add('selected');
            renderSymbolDetailTablePort(sum.exchange);
        };
        posTbody.appendChild(tr);
    });

    if (Object.keys(exchangeSummary).length > 0) {
        const totalTr = document.createElement('tr');
        totalTr.style.background = 'rgba(148, 163, 184, 0.08)';
        totalTr.style.fontWeight = 'bold';
        totalTr.style.borderTop = '2px solid rgba(148, 163, 184, 0.2)';
        
        const totProfitClass = totalProfit >= 0 ? 'bull' : 'bear';
        const totProfitText = (totalProfit >= 0 ? '+' : '') + Math.round(totalProfit).toLocaleString() + " 원";

        totalTr.innerHTML = `
            <td><strong>합계 (TOTAL)</strong></td>
            <td class="num">${Math.round(totalInitial).toLocaleString()} 원</td>
            <td class="num">${Math.round(totalFinal).toLocaleString()} 원</td>
            <td class="num">${totalSymbols} 개</td>
            <td class="num">${totalTrades} 건</td>
            <td class="num">${Math.round(totalFees).toLocaleString()} 원</td>
            <td class="num ${totProfitClass}">${totProfitText}</td>
        `;
        posTbody.appendChild(totalTr);
    }

    const firstEx = Object.keys(exchangeSummary)[0];
    if (firstEx) {
        const firstRow = document.getElementById(`port-ex-row-${firstEx}`);
        if (firstRow) {
            firstRow.classList.add('selected');
        }
        renderSymbolDetailTablePort(firstEx);
    }
}

function renderSymbolDetailTablePort(exchangeName) {
    const titleEl = document.getElementById('port-symbols-title');
    const posTbody = document.getElementById('port-symbols-tbody');
    if (!posTbody || !titleEl) return;
    
    titleEl.innerText = `${exchangeName.toUpperCase()} 상세 종목 현황`;
    posTbody.innerHTML = '';
    
    const results = window.lastBacktestResult ? (window.lastBacktestResult.results || []) : [];
    const exchangeItems = results.filter(r => r.exchange.toLowerCase() === exchangeName.toLowerCase());

    const processedItems = exchangeItems.map(item => {
        const trades = item.trades || [];
        const finalPrice = item.finalPrice || (trades.length > 0 ? trades[trades.length - 1].price : 0);

        let currentQty = 0;
        let avgPrice = 0;
        let totalCost = 0;
        let feeSum = 0;
        let sellSum = 0;
        let buySum = 0;

        trades.forEach(t => {
            feeSum += t.fee || 0;
            if (t.side === 'BUY') {
                totalCost += t.price * t.quantity;
                currentQty += t.quantity;
                buySum += t.price * t.quantity;
                if (currentQty > 0) {
                    avgPrice = totalCost / currentQty;
                }
            } else {
                currentQty -= t.quantity;
                sellSum += t.price * t.quantity;
                if (currentQty <= 0) {
                    currentQty = 0;
                    avgPrice = 0;
                    totalCost = 0;
                }
            }
        });

        const valuation = currentQty * finalPrice;
        const profit = sellSum + valuation - buySum - feeSum;
        const investCash = avgPrice * currentQty;
        
        let profitRate = 0;
        const buyTrades = trades.filter(t => t.side === 'BUY');
        const buyCount = buyTrades.length;
        if (buyCount > 0) {
            const avgBuyVal = buySum / buyCount;
            profitRate = avgBuyVal > 0 ? (profit / avgBuyVal * 100) : 0;
        }

        return {
            ...item,
            currentQty,
            avgPrice,
            finalPrice,
            profitRate,
            profit,
            investCash,
            valuation,
            tradeCount: trades.length,
            fee: feeSum,
            buySum,
            sellSum
        };
    });

    processedItems.sort((a, b) => b.profit - a.profit);

    const tfoot = document.getElementById('port-symbols-tfoot');
    if (tfoot) {
        tfoot.innerHTML = '';
        tfoot.style.display = 'none';
    }

    if (processedItems.length === 0) {
        posTbody.innerHTML = '<tr><td colspan="10" style="text-align:center;padding:20px;color:#64748B;">매매 거래가 발생한 종목이 없습니다.</td></tr>';
        return;
    }

    let totTradeCount = 0;
    let totBuySum = 0;
    let totSellSum = 0;
    let totValuation = 0;
    let totFee = 0;
    let totProfit = 0;
    let totAvgBuyVal = 0;

    processedItems.forEach(item => {
        totTradeCount += item.tradeCount || 0;
        totBuySum += item.buySum || 0;
        totSellSum += item.sellSum || 0;
        totValuation += item.valuation || 0;
        totFee += item.fee || 0;
        totProfit += item.profit || 0;

        const buyTrades = item.trades ? item.trades.filter(t => t.side === 'BUY') : [];
        const buyCount = buyTrades.length;
        if (buyCount > 0) {
            totAvgBuyVal += (item.buySum / buyCount);
        }

        const tr = document.createElement('tr');
        tr.style.cursor = 'pointer';
        tr.id = `port-pos-row-${item.exchange}-${item.symbol}`;
        
        const rateClass = item.profitRate >= 0 ? 'bull' : 'bear';
        const profitClass = item.profit >= 0 ? 'bull' : 'bear';
        
        const rateText = item.tradeCount > 0 ? `${item.profitRate.toFixed(4)}%` : '-';
        const profitText = (item.profit >= 0 ? '+' : '') + Math.round(item.profit).toLocaleString() + " 원";

        const assetDisplayName = item.korean_name && item.korean_name !== item.symbol
            ? `${item.symbol} <span style="font-size:0.75rem; color:#94A3B8; font-weight:normal;">(${item.korean_name})</span>`
            : item.symbol;

        tr.innerHTML = `
            <td><strong>${assetDisplayName}</strong></td>
            <td class="num">${item.tradeCount} 건</td>
            <td class="num">${Math.round(item.buySum).toLocaleString()} 원</td>
            <td class="num">${Math.round(item.sellSum).toLocaleString()} 원</td>
            <td class="num">${item.currentQty.toFixed(4)}</td>
            <td class="num">${formatPricePort(item.finalPrice)}</td>
            <td class="num">${Math.round(item.valuation).toLocaleString()} 원</td>
            <td class="num">${Math.round(item.fee).toLocaleString()} 원</td>
            <td class="num ${profitClass}">${profitText}</td>
            <td class="num ${rateClass}">${rateText}</td>
        `;

        tr.onclick = () => {
            document.querySelectorAll('#port-symbols-table tbody tr').forEach(r => r.classList.remove('selected'));
            tr.classList.add('selected');
            renderHistoryTablePort(item);
        };

        posTbody.appendChild(tr);
    });

    if (tfoot && processedItems.length > 0) {
        tfoot.style.display = 'table-footer-group';
        const tr = document.createElement('tr');
        
        let totProfitRate = 0;
        if (totAvgBuyVal > 0) {
            totProfitRate = (totProfit / totAvgBuyVal * 100);
        }

        const totProfitClass = totProfit >= 0 ? 'bull' : 'bear';
        const totProfitText = (totProfit >= 0 ? '+' : '') + Math.round(totProfit).toLocaleString() + " 원";
        const totRateClass = totProfitRate >= 0 ? 'bull' : 'bear';
        const totRateText = totAvgBuyVal > 0 ? `${totProfitRate.toFixed(4)}%` : '-';

        tr.innerHTML = `
            <td><strong>합계 (TOTAL)</strong></td>
            <td class="num">${totTradeCount} 건</td>
            <td class="num">${Math.round(totBuySum).toLocaleString()} 원</td>
            <td class="num">${Math.round(totSellSum).toLocaleString()} 원</td>
            <td class="num">-</td>
            <td class="num">-</td>
            <td class="num">${Math.round(totValuation).toLocaleString()} 원</td>
            <td class="num">${Math.round(totFee).toLocaleString()} 원</td>
            <td class="num ${totProfitClass}">${totProfitText}</td>
            <td class="num ${totRateClass}">${totRateText}</td>
        `;
        tfoot.appendChild(tr);
    }

    if (processedItems.length > 0) {
        const firstItem = processedItems[0];
        renderHistoryTablePort(firstItem);
        const firstRow = document.getElementById(`port-pos-row-${firstItem.exchange}-${firstItem.symbol}`);
        if (firstRow) {
            firstRow.classList.add('selected');
        }
    }
}

function renderHistoryTablePort(item) {
    const histTbody = document.getElementById('port-history-detail-tbody');
    if (!histTbody) return;
    histTbody.innerHTML = '';

    const trades = item.trades || [];
    if (trades.length === 0) {
        histTbody.innerHTML = '<tr><td colspan="8" style="text-align:center;padding:20px;">거래 내역이 없습니다.</td></tr>';
        return;
    }

    const sortedTrades = [...trades].reverse();
    sortedTrades.forEach(t => {
        const hTr = document.createElement('tr');
        const dateStr = formatTimestampPort(t.timestamp);

        hTr.innerHTML = `
            <td>${dateStr}</td>
            <td><strong>${item.symbol}</strong> <span style="font-size:0.7rem; color:#64748B;">(${item.exchange})</span></td>
            <td class="${t.side === 'BUY' ? 'bull' : 'bear'}">${t.side}</td>
            <td class="num">${formatPricePort(t.price)}</td>
            <td class="num">${t.quantity.toFixed(4)}</td>
            <td class="num">${formatPricePort(t.price * t.quantity)}</td>
            <td class="num">${Math.round(t.fee).toLocaleString()} 원</td>
            <td>${t.reason || '-'}</td>
        `;
        histTbody.appendChild(hTr);
    });
}

function formatPricePort(val) {
    if (val === undefined || val === null || isNaN(val)) return '-';
    if (val < 100) {
        return val % 1 === 0 ? val.toLocaleString() + " 원" : val.toFixed(2).replace(/\B(?=(\d{3})+(?!\d))/g, ",") + " 원";
    }
    return Math.round(val).toLocaleString() + " 원";
}

function formatTimestampPort(ts) {
    if (!ts) return '-';
    const ms = ts < 10000000000 ? ts * 1000 : ts;
    return new Date(ms).toLocaleString();
}

document.addEventListener('DOMContentLoaded', () => {
    // 최초 통합 세션 이력 로드 및 초기 포트폴리오 자동 조회
    loadPortfolioHistoryList().then(() => {
        loadPortfolio();
    });
});

let currentRunMode = 'simulation'; // 'simulation' 또는 'backtest'

/**
 * 공통 전략 기동 모달을 열고, 모드에 맞춰 화면 필드를 동적으로 온오프합니다.
 */
async function openStrategyRunModal(mode) {
    currentRunMode = mode;
    const modal = document.getElementById('strategy-run-modal');
    if (!modal) return;

    // 모달 타이틀 설정
    const titleEl = document.getElementById('modal-run-title');
    if (titleEl) {
        titleEl.innerText = mode === 'backtest' ? '⚙️ 과거 백테스트 실행 설정' : '⚙️ 실시간 모의투자 실행 설정';
    }

    // 백테스트 전용 필드 제어
    const backtestFields = document.getElementById('modal-backtest-fields');
    if (backtestFields) {
        backtestFields.style.display = mode === 'backtest' ? 'flex' : 'none';
    }

    // 버튼 텍스트 변경
    const submitBtn = document.getElementById('btn-modal-submit');
    if (submitBtn) {
        submitBtn.innerText = mode === 'backtest' ? '🚀 백테스트 실행' : '▶️ 모의투자 가동';
    }

    // 백테스트 시간 기본값 설정 (만약 아직 값이 없거나 비어있는 경우)
    if (mode === 'backtest') {
        const startInput = document.getElementById('modal-backtest-start-date');
        const endInput = document.getElementById('modal-backtest-end-date');
        if (startInput && endInput && !startInput.value) {
            const now = new Date();
            const startDay = new Date(now);
            startDay.setHours(0, 0, 0, 0); // 오늘 00:00
            const endDay = new Date(now);
            endDay.setMinutes(0, 0, 0); // 오늘 현재시:00분

            const toLocalISO = (date) => {
                const tzOffset = date.getTimezoneOffset() * 60000;
                return (new Date(date - tzOffset)).toISOString().slice(0, 16);
            };

            startInput.value = toLocalISO(startDay);
            endInput.value = toLocalISO(endDay);
        }
    }

    // 동적 전략 리스트 렌더링 호출
    await renderModalStrategyForm();

    modal.style.display = 'flex';
    modal.onclick = (e) => {
        if (e.target === modal) closeStrategyRunModal();
    };
}

/**
 * 공통 전략 기동 모달을 닫습니다.
 */
function closeStrategyRunModal() {
    const modal = document.getElementById('strategy-run-modal');
    if (modal) modal.style.display = 'none';
}

/**
 * 모달 내부에 전략 및 파라미터 튜닝 영역을 동적으로 렌더링합니다.
 */
async function renderModalStrategyForm() {
    const container = document.getElementById('modal-strategy-container');
    if (!container) return;

    try {
        const configs = await APIClient.fetchBacktestDefaultConfigs();
        container.innerHTML = '';

        configs.forEach(cfg => {
            const card = document.createElement('div');
            card.className = 'strategy-card';
            card.style.border = '1px solid rgba(148, 163, 184, 0.1)';
            card.style.padding = '10px';
            card.style.borderRadius = '6px';
            card.style.background = 'rgba(15, 23, 42, 0.3)';

            let paramInputsHtml = '';
            if (cfg.params) {
                paramInputsHtml = '<div class="strategy-tuning-params" style="display: flex; flex-direction: column; gap: 5px; margin-top: 8px; border-top: 1px dashed rgba(148, 163, 184, 0.1); padding-top: 8px; display: none;">';
                Object.entries(cfg.params).forEach(([paramName, paramVal]) => {
                    const currentVal = paramVal.current !== undefined ? paramVal.current : paramVal.default;
                    const inputType = paramVal.type === 'str' ? 'text' : 'number';
                    paramInputsHtml += `
                        <label style="display: flex; justify-content: space-between; align-items: center; font-size: 0.8rem; color: #94A3B8;">
                            ${paramName}:
                            <input type="${inputType}" data-modal-strategy="${cfg.id}" data-param="${paramName}" class="dark-input modal-param-input" style="width: 70px; padding: 2px 5px; font-size: 0.8rem; margin: 0; text-align: right;" value="${currentVal}">
                        </label>
                    `;
                });
                paramInputsHtml += '</div>';
            }

            card.innerHTML = `
                <div style="display: flex; align-items: center; gap: 8px;">
                    <input type="checkbox" id="modal-strat-${cfg.id}" data-modal-strategy-checkbox="${cfg.id}" style="margin: 0;">
                    <label for="modal-strat-${cfg.id}" style="font-weight: bold; cursor: pointer; color: #F8FAFC;">${cfg.name || cfg.id}</label>
                </div>
                <p style="font-size: 0.75rem; color: #64748B; margin: 5px 0 0 20px;">${cfg.description || ''}</p>
                ${paramInputsHtml}
            `;

            // 체크박스 선택 시 파라미터 영역 토글
            const checkbox = card.querySelector(`input[data-modal-strategy-checkbox="${cfg.id}"]`);
            const paramsDiv = card.querySelector('.strategy-tuning-params');
            if (checkbox && paramsDiv) {
                checkbox.addEventListener('change', () => {
                    paramsDiv.style.display = checkbox.checked ? 'flex' : 'none';
                });
            }

            container.appendChild(card);
        });
    } catch (e) {
        console.error("Failed to load modal strategies configs", e);
        container.innerHTML = '<p class="status-text error">전략 설정을 불러오지 못했습니다.</p>';
    }
}

/**
 * 실시간 모의투자 세션의 상태에 따라 좌측 사이드바 제어 UI를 업데이트합니다.
 */
function updateSessionControlUI(portfolio) {
    const badge = document.getElementById('session-status-badge');
    const actionGroup = document.getElementById('session-action-group');
    if (!badge || !actionGroup) return;

    // 현재 기동 중인 활성 세션(type == 'simulation')이 있는지 감사
    const activeSession = (state.portfoliosCache || []).find(p => p.type === 'simulation');

    if (activeSession) {
        // 활성 세션이 있는 경우 ➔ 마감(종료) 버튼 노출
        badge.innerText = '가동 중';
        badge.style.background = 'rgba(16, 185, 129, 0.2)';
        badge.style.color = '#10B981';

        actionGroup.innerHTML = `
            <div style="font-size: 0.8rem; color: #94A3B8; margin-bottom: 5px; text-align: center;">
                활성 세션: <strong style="color: #F8FAFC;">${activeSession.name}</strong>
            </div>
            <button class="btn danger" style="width: 100%; font-size: 0.85rem;" onclick="endSimulationSession('${activeSession.id}')">⏹️ 모의투자 세션 마감</button>
        `;
    } else {
        // 활성 세션이 없는 경우 ➔ 기동 시작 버튼 노출
        badge.innerText = '미가동';
        badge.style.background = 'rgba(239, 68, 68, 0.2)';
        badge.style.color = '#EF4444';

        actionGroup.innerHTML = `
            <button class="btn success" style="width: 100%; font-size: 0.85rem;" onclick="openStrategyRunModal('simulation')">▶️ 실시간 모의투자 시작</button>
        `;
    }
}

/**
 * 모달에서 실행 버튼 클릭 시 기동 처리 함수
 */
async function submitStrategyRun() {
    // 1. 공통 거래소별 초기 투자 원금 수집
    const upbitCash = parseFloat(document.getElementById('modal-cash-upbit').value) || 0;
    const bithumbCash = parseFloat(document.getElementById('modal-cash-bithumb').value) || 0;
    const kisCash = parseFloat(document.getElementById('modal-cash-kis').value) || 0;

    const initialCashMap = {
        upbit: upbitCash,
        bithumb: bithumbCash,
        kis: kisCash
    };

    const totalCash = upbitCash + bithumbCash + kisCash;
    if (totalCash <= 0) {
        alert("최소 하나의 거래소에 초기 투자 원금을 입력해주세요.");
        return;
    }

    // 2. 선택된 전략 및 파라미터 수집
    const strategies = {};
    const checkedCheckboxes = document.querySelectorAll('input[data-modal-strategy-checkbox]:checked');
    
    if (checkedCheckboxes.length === 0) {
        alert("가동할 전략을 최소 1개 이상 선택해주세요.");
        return;
    }

    checkedCheckboxes.forEach(cb => {
        const strategyId = cb.getAttribute('data-modal-strategy-checkbox');
        strategies[strategyId] = {
            enabled: true,
            params: {}
        };

        const paramInputs = document.querySelectorAll(`input[data-modal-strategy="${strategyId}"][data-param]`);
        paramInputs.forEach(input => {
            const paramName = input.getAttribute('data-param');
            const paramVal = input.value.trim();
            const numVal = Number(paramVal);
            strategies[strategyId].params[paramName] = isNaN(numVal) ? paramVal : numVal;
        });
    });

    const submitBtn = document.getElementById('btn-modal-submit');
    const originalText = submitBtn ? submitBtn.innerText : '실행하기';

    if (currentRunMode === 'simulation') {
        // --- 실시간 모의투자 기동 분기 ---
        if (!confirm("선택한 전략 구성 및 자본금으로 실시간 모의투자를 시작하시겠습니까?\n기존 활성 세션이 있었다면 자동으로 마감 처리됩니다.")) {
            return;
        }

        if (submitBtn) {
            submitBtn.disabled = true;
            submitBtn.innerText = "가동 중...";
        }

        try {
            const res = await APIClient.startPortfolioSession(initialCashMap, strategies);
            if (res.status === 'success') {
                alert(`새로운 모의투자 세션이 시작되었습니다! (${res.name})`);
                state.currentPortfolioId = res.portfolio_id;
                closeStrategyRunModal();
                await loadPortfolioHistoryList();
                await loadPortfolio();
            } else {
                alert("세션 시작 실패: " + (res.message || "알 수 없는 에러"));
            }
        } catch (e) {
            console.error(e);
            alert("세션 시작 오류: " + e.message);
        } finally {
            if (submitBtn) {
                submitBtn.disabled = false;
                submitBtn.innerText = originalText;
            }
        }

    } else if (currentRunMode === 'backtest') {
        // --- 과거 백테스트 기동 분기 ---
        const exchange = document.getElementById('modal-backtest-exchange').value;
        const symbol = document.getElementById('modal-backtest-symbol').value.trim();
        const startDate = document.getElementById('modal-backtest-start-date').value;
        const endDate = document.getElementById('modal-backtest-end-date').value;

        if (!startDate || !endDate) {
            alert("시작일과 종료일을 지정해 주세요.");
            return;
        }

        // 유효성 검사: 선택한 거래소에 예산이 있는지 확인
        if (exchange !== 'all' && initialCashMap[exchange] <= 0) {
            alert(`선택한 거래소(${exchange.toUpperCase()})에 초기 투자 원금을 입력해주세요.`);
            return;
        }

        if (submitBtn) {
            submitBtn.disabled = true;
            submitBtn.innerText = "백테스트 진행 중...";
        }

        try {
            const payload = {
                exchange: exchange,
                symbol: symbol,
                start_date: startDate,
                end_date: endDate,
                initial_cash: initialCashMap,
                strategies: strategies
            };

            const result = await APIClient.runBacktest(payload);

            if (result.status === 'success') {
                alert("백테스트가 성공적으로 완료되었습니다!");
                state.currentPortfolioId = result.portfolio_id;
                closeStrategyRunModal();
                await loadPortfolioHistoryList();
                await loadPortfolio();
            } else {
                alert("백테스트 실행 실패: " + (result.message || "알 수 없는 에러"));
            }
        } catch (e) {
            console.error(e);
            alert("백테스트 실행 오류: " + e.message);
        } finally {
            if (submitBtn) {
                submitBtn.disabled = false;
                submitBtn.innerText = originalText;
            }
        }
    }
}

/**
 * 실시간 모의투자 세션을 청산 없이 마감합니다.
 */
async function endSimulationSession(portfolioId) {
    if (!confirm("보유한 포지션 매도(청산) 없이, 현재 자산 평가액 기준으로 실시간 모의투자를 마감하시겠습니까?\n마감된 세션은 더 이상 매매가 진행되지 않고 실시간 성과가 고정됩니다.")) {
        return;
    }

    try {
        const res = await APIClient.endPortfolioSession(portfolioId);
        if (res.status === 'success') {
            alert("모의투자 세션이 정상적으로 마감되었습니다.");
            await loadPortfolioHistoryList();
            await loadPortfolio();
        } else {
            alert("세션 마감 실패: " + (res.message || "알 수 없는 에러"));
        }
    } catch (e) {
        console.error(e);
        alert("세션 마감 오류: " + e.message);
    }
}

// 전역 window 바인딩으로 타 JS 파일 및 HTML 인라인 호출 지원
window.loadPortfolioList = loadPortfolioHistoryList;
window.loadPortfolio = loadPortfolio;
window.renderAllocationChart = renderAllocationChart;
window.executePanicSell = executePanicSell;
window.showAssetDetails = showAssetDetails;
window.updateModalContent = updateModalContent;
window.closeAssetModal = closeAssetModal;
window.loadRealAssets = loadRealAssets;
window.renderBacktestPerformance = renderBacktestPerformance;
window.renderExchangeSummaryPort = renderExchangeSummaryPort;
window.renderSymbolDetailTablePort = renderSymbolDetailTablePort;
window.renderHistoryTablePort = renderHistoryTablePort;
window.openStrategyRunModal = openStrategyRunModal;
window.closeStrategyRunModal = closeStrategyRunModal;
window.submitStrategyRun = submitStrategyRun;
window.loadPortfolioHistoryList = loadPortfolioHistoryList;
window.deletePortfolioHistory = deletePortfolioHistory;
window.clearAllPortfolioHistory = clearAllPortfolioHistory;
window.endSimulationSession = endSimulationSession;
