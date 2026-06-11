/**
 * Upbit Terminal 포트폴리오(Portfolio) 및 실자산 관리 모듈 (Controller)
 */

let lastPortfolioFetchedAt = null;
let lastPortfolioListFetchedAt = null;

/**
 * 실시간 모의투자 및 과거 백테스트 목록 전체를 불러와 좌측 통합 이력 리스트 패널에 바인딩합니다.
 */
async function loadPortfolioHistoryList(force = false) {
    const tbody = document.getElementById('portfolio-history-list-tbody');
    if (!tbody) return;

    if (!force && lastPortfolioListFetchedAt && (Date.now() - lastPortfolioListFetchedAt.getTime() < 3000)) {
        return;
    }


    try {
        const portfolios = await APIClient.fetchPortfolioList();
        const backtestHistory = [];

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

        // 'live' 항목 최상단 고정 배치
        const liveIndex = items.findIndex(item => item.id === 'live');
        if (liveIndex > -1) {
            const liveItem = items.splice(liveIndex, 1)[0];
            items.unshift(liveItem);
        } else {
            items.unshift({
                id: 'live',
                name: '실계좌 자동매매',
                type: 'live',
                roi: 0.0,
                trade_count: 0,
                created_at: new Date().toISOString(),
                isLive: true
            });
            addedIds.add('live');
        }

        tbody.innerHTML = '';

        if (items.length === 0) {
            tbody.innerHTML = '<tr><td colspan="4" style="text-align:center; padding:15px; color:#64748B;">저장된 이력이 없습니다.</td></tr>';
            state.currentPortfolioId = null;
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
            tr.setAttribute('data-portfolio-id', item.id);
            tr.style.borderBottom = '1px solid rgba(148, 163, 184, 0.08)';
            tr.style.cursor = 'pointer';
            if (item.id === state.currentPortfolioId) {
                tr.style.background = 'rgba(99, 102, 241, 0.1)';
            }

            const roiClass = item.roi >= 0 ? 'bull' : 'bear';
            const roiText = `${item.roi >= 0 ? '+' : ''}${item.roi}%`;

            let badgeHtml = '';
            if (item.type === 'live') {
                badgeHtml = `<span class="ctx-badge" style="background: rgba(239, 68, 68, 0.2); color: #EF4444; border: 1px solid rgba(239, 68, 68, 0.4); font-size: 0.65rem; padding: 1px 4px; border-radius: 3px; font-weight: normal; flex-shrink: 0;">실거래</span>`;
            } else if (item.type === 'simulation') {
                badgeHtml = `<span class="ctx-badge" style="background: rgba(16, 185, 129, 0.2); color: #10B981; font-size: 0.65rem; padding: 1px 4px; border-radius: 3px; font-weight: normal; flex-shrink: 0;">진행중</span>`;
            } else if (item.type === 'simulation_ended') {
                badgeHtml = `<span class="ctx-badge" style="background: rgba(100, 116, 139, 0.2); color: #94A3B8; font-size: 0.65rem; padding: 1px 4px; border-radius: 3px; font-weight: normal; flex-shrink: 0;">종료됨</span>`;
            } else {
                badgeHtml = `<span class="ctx-badge" style="background: rgba(217, 70, 239, 0.2); color: #D946EF; font-size: 0.65rem; padding: 1px 4px; border-radius: 3px; font-weight: normal; flex-shrink: 0;">백테스트</span>`;
            }

            const dateStr = item.created_at ? new Date(item.created_at).toLocaleString() : '-';

            // 행 클릭 시 해당 포트폴리오 로드
            tr.onclick = (e) => {
                if (e.target.closest('.btn-delete-history')) return;
                
                state.currentPortfolioId = item.id;
                document.querySelectorAll('#portfolio-history-list-tbody tr').forEach(r => r.style.background = '');
                tr.style.background = 'rgba(99, 102, 241, 0.1)';
                
                loadPortfolio(true);
            };

            // 삭제 버튼: 진행중(simulation) 또는 실거래(live)가 아닐 때만 노출
            const showDelete = item.type !== 'simulation' && item.type !== 'live';
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

        updateSessionControlUI();
        lastPortfolioListFetchedAt = new Date();
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
        const res = await APIClient.deletePortfolioHistory(portfolioId);
        if (res.status === 'success') {
            showAlert("이력이 정상적으로 삭제되었습니다.", "success");
            
            // 캐시에서 삭제된 포트폴리오를 선제적으로 필터링하여 동기화 꼬임 차단
            if (state.portfoliosCache) {
                state.portfoliosCache = state.portfoliosCache.filter(p => p.id !== portfolioId);
            }

            if (state.currentPortfolioId === portfolioId) {
                state.currentPortfolioId = null;
            }
            await loadPortfolioHistoryList(true);
            await loadPortfolio(true);
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
        const res = await APIClient.clearAllPortfolioHistory();
        if (res.status === 'success') {
            showAlert("모든 이력이 정상적으로 삭제되었습니다.", "success");
            
            // 캐시 일괄 비우기 (진행중인 simulation 세션 제외)
            if (state.portfoliosCache) {
                state.portfoliosCache = state.portfoliosCache.filter(p => p.type === 'simulation');
            }

            state.currentPortfolioId = null;
            await loadPortfolioHistoryList(true);
            await loadPortfolio(true);
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
async function loadPortfolio(force = false) {
    if (!force && lastPortfolioFetchedAt && (Date.now() - lastPortfolioFetchedAt.getTime() < 3000)) {
        return;
    }
    try {
        let portfolioId = state.currentPortfolioId;
        if (!portfolioId) {
            if (state.portfoliosCache && state.portfoliosCache.length > 0) {
                const activeSim = state.portfoliosCache.find(p => p.type === 'simulation');
                portfolioId = activeSim ? activeSim.id : state.portfoliosCache[0].id;
            }
        }
        
        if (!portfolioId) {
            console.warn("No active or saved portfolio sessions found.");
            const typeBadge = document.getElementById('portfolio-type-badge');
            const panicBtn = document.getElementById('btn-panic-sell');
            const backtestSummary = document.getElementById('portfolio-backtest-summary');
            const appliedStrategies = document.getElementById('portfolio-applied-strategies');
            const backtestAnalysisPanels = document.getElementById('portfolio-backtest-analysis-panels');

            if (typeBadge) typeBadge.style.display = 'none';
            if (panicBtn) {
                panicBtn.style.display = 'none';
                panicBtn.disabled = true;
            }
            if (backtestSummary) backtestSummary.style.display = 'none';
            if (appliedStrategies) appliedStrategies.style.display = 'none';
            if (backtestAnalysisPanels) backtestAnalysisPanels.style.display = 'flex';

            const simulatedRes = {
                type: 'none',
                initial_cash: 0,
                total_value: 0,
                cash: 0,
                exchanges: [],
                positions: [],
                history: []
            };

            renderBacktestPerformance(simulatedRes);
            PortfolioView.updateMetrics(0, 0, 0);
            PortfolioView.renderPositionsTable('positions-tbody', [], false, []);
            PortfolioView.renderHistoryTable('port-history-tbody', [], false);
            
            if (typeof PortfolioChart !== 'undefined' && typeof PortfolioChart.render === 'function') {
                PortfolioChart.render('portfolio-allocation-container', {
                    type: 'none',
                    total_value: 0,
                    cash: 0,
                    exchange_cash: {},
                    positions: [],
                    history: []
                }, false, []);
            }
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

        const data = await APIClient.fetchPortfolio(portfolioId);

        // --- 공통 렌더링을 위한 데이터 가공 ---
        let totalValue = 0;
        let cash = 0;
        let roi = 0;
        let positions = [];
        let history = [];
        let exchangeCashMap = {};

        const currentMarketData = (typeof marketData !== 'undefined' ? marketData : []);

        totalValue = data.total_value !== undefined ? data.total_value : (data.summary ? data.summary.final_value : 0);
        cash = data.cash !== undefined ? data.cash : 0;
        
        const initialValue = data.initial_cash || (data.summary ? data.summary.initial_cash : 10000000);
        roi = data.roi !== undefined ? data.roi : ((totalValue - initialValue) / initialValue * 100).toFixed(2);
        
        positions = data.positions || [];
        
        // 거래소별 요약 리스트에서 각 거래소의 잔여 현금을 단일 경로로 매핑
        if (data.exchanges) {
            data.exchanges.forEach(ex => {
                exchangeCashMap[ex.exchange_id.toLowerCase()] = ex.cash;
            });
        }

        if (isBacktest) {
            data.results.forEach(r => {
                (r.trades || []).forEach(t => {
                    history.push({
                        timestamp: t.timestamp < 10000000000 ? t.timestamp : t.timestamp / 1000,
                        symbol: r.symbol,
                        side: t.side,
                        price: t.price,
                        quantity: t.quantity,
                        reason: t.reason,
                        context: null
                    });
                });
            });
            history.sort((a, b) => b.timestamp - a.timestamp);
        } else {
            history = [...(data.history || [])].reverse();
        }

        state.currentPortfolioData = {
            id: portfolioId,
            type: portfolioId === 'live' ? 'live' : (cachedPort ? cachedPort.type : (portfolioId.startsWith('backtest_') ? 'backtest' : 'simulation')),
            total_value: totalValue,
            cash: cash,
            exchange_cash: exchangeCashMap,
            positions: positions,
            history: history
        };

        // --- 화면 레이아웃 분기 제어 및 뱃지/메트릭 렌더링 ---
        if (data.type === 'none' || !data.id) {
            if (typeBadge) typeBadge.style.display = 'none';
            if (panicBtn) {
                panicBtn.style.display = 'none';
                panicBtn.disabled = true;
            }
            if (backtestSummary) backtestSummary.style.display = 'none';
            if (appliedStrategies) appliedStrategies.style.display = 'none';
            if (backtestAnalysisPanels) backtestAnalysisPanels.style.display = 'flex';

            renderBacktestPerformance(data);
        } else if (isBacktest) {
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
            if (panicBtn) panicBtn.style.display = 'none';

            if (backtestSummary) {
                backtestSummary.style.display = 'grid';
                document.getElementById('port-initial-cash').innerText = Math.round(data.summary.initial_cash).toLocaleString() + " 원";
                document.getElementById('port-total-fee').innerText = Math.round(data.summary.fee).toLocaleString() + " 원";
                document.getElementById('port-trade-count').innerText = data.summary.trade_count + " 건";
                document.getElementById('port-duration').innerText = (data.duration || 0) + "초";
            }

            if (backtestAnalysisPanels) backtestAnalysisPanels.style.display = 'flex';

            renderBacktestPerformance(data);
        } else {
            if (typeBadge) {
                if (portfolioId === 'live') {
                    typeBadge.innerText = '실계좌 자동매매';
                    typeBadge.style.background = '#EF4444';
                } else {
                    typeBadge.innerText = '실시간 모의투자';
                    typeBadge.style.background = '#3B82F6';
                }
                typeBadge.style.display = 'inline-block';
            }
            if (panicBtn) {
                panicBtn.style.display = 'inline-block';
                panicBtn.disabled = false;
            }

            if (backtestSummary) backtestSummary.style.display = 'none';

            if (backtestAnalysisPanels) backtestAnalysisPanels.style.display = 'flex';
            
            renderBacktestPerformance(data);
        }

        // 적용된 전략 정보 표시 (백테스트 및 실시간/실계좌 세션 공통 적용)
        if (appliedStrategies && data.applied_strategies && Object.keys(data.applied_strategies).length > 0) {
            appliedStrategies.style.display = 'block';
            let appliedHtml = '<strong>적용된 전략 정보:</strong><br>';
            
            const strategies = Array.isArray(data.applied_strategies)
                ? data.applied_strategies
                : Object.entries(data.applied_strategies).map(([name, params]) => ({ name, params }));

            strategies.forEach(s => {
                const params = s.params || {};
                const paramStr = Object.entries(params).map(([k, v]) => `${k}: ${v}`).join(', ');
                appliedHtml += `<span class="ctx-badge" style="margin-top: 5px; display: inline-block;">${s.name} (${paramStr})</span> `;
            });
            appliedStrategies.innerHTML = appliedHtml;
        } else if (appliedStrategies) {
            appliedStrategies.style.display = 'none';
        }

        // 요약 정보 업데이트
        PortfolioView.updateMetrics(totalValue, roi, cash);

        // 포지션 테이블 업데이트
        PortfolioView.renderPositionsTable('positions-tbody', positions, isBacktest, currentMarketData);

        // 히스토리 테이블 업데이트
        PortfolioView.renderHistoryTable('port-history-tbody', history, isBacktest);

        // 자산 비중 차트 업데이트
        PortfolioChart.render('portfolio-allocation-container', state.currentPortfolioData, isBacktest, currentMarketData);

        // 상세 모달 열려있으면 갱신
        if (state.activeAssetDetail) {
            PortfolioView.updateModalContent(state.currentPortfolioData, state.activeAssetDetail.exchange, state.activeAssetDetail.symbol, currentMarketData);
        }

        // 실시간 시세 구독 핫스왑 (실시간 모의투자일 때만)
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

        updateSessionControlUI();
        lastPortfolioFetchedAt = new Date();
    } catch (e) {
        console.error("Portfolio load failed", e);
    }
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
            await loadPortfolio(true);
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
 */
function showAssetDetails(exchange, symbol) {
    state.activeAssetDetail = { exchange, symbol };
    const currentMarketData = (typeof marketData !== 'undefined' ? marketData : []);
    PortfolioView.updateModalContent(state.currentPortfolioData, exchange, symbol, currentMarketData);
    
    const modal = document.getElementById('asset-modal');
    if (modal) {
        modal.style.display = 'flex';
        modal.onclick = (e) => {
            if (e.target === modal) closeAssetModal();
        };
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
async function loadRealAssets(sync = false) {
    const tbody = document.getElementById('real-assets-tbody');
    const totalValueEl = document.getElementById('real-total-value');
    const assetCountEl = document.getElementById('real-asset-count');
    
    if (!tbody) return;
    
    const exchange = state.realAssetExchange || 'upbit';
    const exchangeName = (exchange === 'upbit' ? '업비트' : (exchange === 'bithumb' ? '빗썸' : '한국투자증권(KIS)'));
    tbody.innerHTML = `<tr><td colspan="7" style="text-align:center;padding:30px;color:rgba(255,255,255,0.4);">&#x23F3; ${exchangeName} API에서 자산 명세를 안전하게 조회 중입니다...</td></tr>`;
    
    try {
        const filter = state.realAssetFilter || 'active';
        
        // 테이블 헤더 텍스트 동적 변경 (보유자산 vs 처분완료자산)
        const thElements = document.querySelectorAll('#real-assets-table th');
        if (thElements.length >= 5) {
            if (filter === 'liquidated') {
                thElements[2].innerText = '매각 체결가';
                thElements[4].innerText = '매각 총액';
            } else {
                thElements[2].innerText = '평균 매수가';
                thElements[4].innerText = '평가금액';
            }
        }

        const data = await APIClient.fetchRealAssets(exchange, filter, sync);
        
        if (data && data.assets) {
            const krwAsset = data.assets.find(asset => asset.currency === 'KRW');
            if (krwAsset) {
                state.realKRWBalance = krwAsset.balance;
            }
        }

        // liquidated 일 때는 평가액이 0 원이 되므로 active 기준 평가액 캐시 유지
        if (totalValueEl) {
            if (filter === 'active') {
                state.realTotalValue = data.formatted_total_value;
                totalValueEl.innerText = `${data.formatted_total_value} 원`;
            } else {
                totalValueEl.innerText = `${state.realTotalValue || '0'} 원`;
            }
        }
        
        // 실시간 더블클릭 차트 이동 콜백 어댑터
        const onAssetDblClick = (asset) => {
            const targetExchange = asset.exchange || exchange;
            let symbol;
            if (targetExchange === 'kis') {
                symbol = asset.currency;
            } else {
                symbol = `KRW-${asset.currency}`;
            }
            state.currentSymbol = symbol;
            state.currentExchange = targetExchange;
            updateHeaderInfo(targetExchange, symbol);
            
            const select = document.getElementById('symbol-select');
            if (select) select.value = `${targetExchange}:${symbol}`;
            
            if (state.ws && state.ws.readyState === WebSocket.OPEN) {
                state.ws.send(JSON.stringify({ subscribe: symbol, exchange: targetExchange }));
            }
            
            ViewRouter.navigateTo('monitoring-view');
            exitExplorerMode();
            loadHistory();
            showAlert(`${asset.korean_name} 차트로 이동합니다.`, 'info');
        };

        const onOrderClick = (asset) => {
            openRealAssetOrderModal(asset);
        };

        const onHistoryClick = (asset) => {
            openRealAssetHistoryModal(asset);
        };

        PortfolioView.renderRealAssetsTable('real-assets-tbody', data, totalValueEl, assetCountEl, onOrderClick, onHistoryClick, onAssetDblClick);
    } catch (e) {
        tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;padding:20px;color:#FF4B4B;">&#x26A0;&#xFE0F; 자산 조회 실패 (API 키 권한 또는 인터넷 연결 상태를 확인하세요)</td></tr>';
        console.error("Asset load failed", e);
    }
}

/**
 * 실자산 필터를 변경합니다 (보유 자산 / 처분 완료 자산).
 */
function changeRealAssetFilter(filter) {
    state.realAssetFilter = filter;
    const activeBtn = document.getElementById('btn-real-asset-filter-active');
    const liquidatedBtn = document.getElementById('btn-real-asset-filter-liquidated');
    if (activeBtn && liquidatedBtn) {
        if (filter === 'active') {
            activeBtn.classList.add('active');
            activeBtn.style.background = 'var(--accent-color)';
            activeBtn.style.color = 'white';
            liquidatedBtn.classList.remove('active');
            liquidatedBtn.style.background = '#475569';
            liquidatedBtn.style.color = '#94A3B8';
        } else {
            liquidatedBtn.classList.add('active');
            liquidatedBtn.style.background = 'var(--accent-color)';
            liquidatedBtn.style.color = 'white';
            activeBtn.classList.remove('active');
            activeBtn.style.background = '#475569';
            activeBtn.style.color = '#94A3B8';
        }
    }
    loadRealAssets(false);
}

/**
 * 실자산 조회 거래소를 변경합니다 (업비트 / 빗썸 / KIS).
 */
function changeRealAssetExchange(exchange) {
    state.realAssetExchange = exchange;
    
    // 거래소 버튼 활성화 UI 갱신
    const exchanges = ['upbit', 'bithumb', 'kis'];
    exchanges.forEach(ex => {
        const btn = document.getElementById(`btn-real-exchange-${ex}`);
        if (btn) {
            if (ex === exchange) {
                btn.classList.add('active');
                btn.style.background = 'var(--accent-color)';
                btn.style.color = 'white';
            } else {
                btn.classList.remove('active');
                btn.style.background = '#475569';
                btn.style.color = '#94A3B8';
            }
        }
    });
    
    // 거래소 변경 시 원화 잔고 초기화 및 재조회
    state.realKRWBalance = 0;
    loadRealAssets(false);
}

/**
 * 거래소로부터 과거 MTS/외부 주문 내역을 동기화하여 로컬 DB를 최신화합니다.
 */
async function syncRealOrderHistory() {
    const exchange = state.realAssetExchange || 'upbit';
    const exchangeName = (exchange === 'upbit' ? '업비트' : (exchange === 'bithumb' ? '빗썸' : '한국투자증권(KIS)'));
    showAlert(`${exchangeName}로부터 과거 주문/체결 내역을 동기화하고 있습니다. 잠시만 기다려주세요...`, "info");
    try {
        await loadRealAssets(true);
        showAlert(`${exchangeName} 이력 동기화가 완료되었습니다.`, "success");
    } catch (e) {
        showAlert(`${exchangeName} 이력 동기화 중 오류가 발생했습니다.`, "error");
        console.error(e);
    }
}

// 실자산 주문 모달 상태
state.realOrderState = {
    exchange: 'upbit',
    symbol: '',
    side: 'BUY',
    asset: null,
    orderbookTimer: null
};

/**
 * 실자산 매수/매도 주문 모달을 엽니다.
 */
function openRealAssetOrderModal(asset) {
    if (asset.currency === 'KRW') {
        showAlert("원화 자산은 매수/매도 주문을 할 수 없습니다.", "warning");
        return;
    }
    
    state.realOrderState.asset = asset;
    state.realOrderState.symbol = asset.currency;
    state.realOrderState.exchange = asset.exchange || state.realAssetExchange || 'upbit';
    
    // 모달 타이틀 설정
    const orderExchange = document.getElementById('real-order-exchange');
    const orderSymbol = document.getElementById('real-order-symbol');
    const orderName = document.getElementById('real-order-name');
    
    if (orderExchange) orderExchange.innerText = state.realOrderState.exchange.toUpperCase();
    if (orderSymbol) orderSymbol.innerText = asset.currency;
    if (orderName) orderName.innerText = asset.korean_name;
    
    // 주문가능 정보 바인딩
    const availableKrw = document.getElementById('real-order-available-krw');
    const availableQty = document.getElementById('real-order-available-qty');
    
    if (availableKrw) {
        const balance = state.realKRWBalance || 0;
        availableKrw.innerText = `${Math.floor(balance).toLocaleString()} 원`;
    }
    
    const isStock = state.realOrderState.exchange === 'kis';
    const unit = isStock ? '주' : asset.currency;
    
    if (availableQty) {
        availableQty.innerText = `${asset.balance} ${unit}`;
    }
    
    const qtyUnit = document.getElementById('real-order-volume-unit');
    if (qtyUnit) qtyUnit.innerText = unit;
    
    // 인풋 초기화
    const priceInput = document.getElementById('real-order-price');
    const volInput = document.getElementById('real-order-volume');
    const totalInput = document.getElementById('real-order-total');
    
    if (priceInput) priceInput.value = '';
    if (volInput) volInput.value = '';
    if (totalInput) totalInput.value = '';
    
    // 기본 주문 사이드: BUY
    setOrderSide('BUY');
    
    // 라디오 버튼 초기화 (limit)
    const limitRadio = document.querySelector('input[name="real-order-type"][value="limit"]');
    if (limitRadio) limitRadio.checked = true;
    onOrderTypeChange();
    
    // 모달 보이기
    const modal = document.getElementById('real-asset-order-modal');
    if (modal) {
        modal.style.display = 'flex';
        modal.onclick = (e) => {
            if (e.target === modal) closeRealAssetOrderModal();
        };
    }
    
    // 호가창 폴링 시작
    pollRealOrderbook();
}

/**
 * 실자산 주문 모달을 닫습니다.
 */
function closeRealAssetOrderModal() {
    const modal = document.getElementById('real-asset-order-modal');
    if (modal) modal.style.display = 'none';
    
    if (state.realOrderState.orderbookTimer) {
        clearTimeout(state.realOrderState.orderbookTimer);
        state.realOrderState.orderbookTimer = null;
    }
}

/**
 * 호가창 데이터를 지속적으로 조회하여 실시간 반영합니다.
 */
async function pollRealOrderbook() {
    if (!state.realOrderState.asset) return;
    
    try {
        const exchange = state.realOrderState.exchange || 'upbit';
        const symbol = exchange === 'kis' ? state.realOrderState.symbol : `KRW-${state.realOrderState.symbol}`;
        const data = await APIClient.fetchOrderbook(exchange, symbol);
        
        if (data && data.orderbook) {
            renderRealOrderbook(data);
            
            // 만약 가격 인풋이 비어있으면 현재가를 기본값으로 설정
            const priceInput = document.getElementById('real-order-price');
            const orderType = document.querySelector('input[name="real-order-type"]:checked').value;
            if (priceInput) {
                if (orderType === 'limit') {
                    if (!priceInput.value) {
                        priceInput.value = data.trade_price;
                    }
                } else {
                    priceInput.value = data.trade_price;
                }
            }
        }
    } catch (e) {
        console.error("Failed to poll orderbook", e);
    }
    
    // 2초 뒤 재호출
    const modal = document.getElementById('real-asset-order-modal');
    if (modal && modal.style.display === 'flex') {
        state.realOrderState.orderbookTimer = setTimeout(pollRealOrderbook, 2000);
    }
}

/**
 * 호가창 HTML을 생성하여 렌더링합니다.
 */
function renderRealOrderbook(data) {
    const orderbookList = document.getElementById('real-orderbook-list');
    if (!orderbookList || !data.orderbook || !data.orderbook.orderbook_units) return;
    
    const units = data.orderbook.orderbook_units;
    let html = '';
    
    const total_ask_size = data.orderbook.total_ask_size || units.reduce((a, b) => a + b.ask_size, 0);
    const total_bid_size = data.orderbook.total_bid_size || units.reduce((a, b) => a + b.bid_size, 0);
    
    const exchange = state.realOrderState.exchange || 'upbit';
    const isCrypto = (exchange === 'upbit' || exchange === 'bithumb');
    const sizeFormat = (size) => isCrypto ? size.toFixed(4) : Math.floor(size).toLocaleString();
    
    // 매도 호가 (Asks) - 내림차순 정렬 (높은 가격이 위로 가도록)
    for (let i = units.length - 1; i >= 0; i--) {
        const u = units[i];
        const percentage = Math.min(100, (u.ask_size / total_ask_size) * 100);
        html += `
            <div class="orderbook-row ask" onclick="setOrderPrice(${u.ask_price})" style="cursor:pointer; display:flex; justify-content:space-between; padding:4px 8px; font-family:\'Roboto Mono\', monospace; font-size:0.75rem; background:rgba(0, 114, 255, 0.04); border-bottom:1px solid rgba(148, 163, 184, 0.08);">
                <span class="price bear" style="color:#0072FF; font-weight:bold;">${u.ask_price.toLocaleString()}</span>
                <div style="position:relative; width:50%; text-align:right;">
                    <div style="position:absolute; right:0; top:0; bottom:0; background:rgba(0, 114, 255, 0.1); width:${percentage}%;"></div>
                    <span class="size" style="position:relative; z-index:1; color:#94A3B8;">${sizeFormat(u.ask_size)}</span>
                </div>
            </div>
        `;
    }
    
    // 현재가 구분선
    const changeSign = data.change_rate >= 0 ? '+' : '';
    const rateClass = data.change_rate >= 0 ? 'bull' : 'bear';
    const rateColor = data.change_rate >= 0 ? '#FF4B4B' : '#0072FF';
    html += `
        <div class="orderbook-current-price" style="display:flex; justify-content:space-between; align-items:center; padding:6px 8px; background:#1E293B; border-top:1px solid #334155; border-bottom:1px solid #334155; font-size:0.8rem; font-weight:bold; color:#F8FAFC;">
            <span class="${rateClass}" style="color:${rateColor}">현재가: ${data.trade_price.toLocaleString()}</span>
            <span class="${rateClass}" style="color:${rateColor}">${changeSign}${(data.change_rate * 100).toFixed(2)}%</span>
        </div>
    `;
    
    // 매수 호가 (Bids) - 내림차순 정렬 (높은 가격이 위로 가도록)
    for (let i = 0; i < units.length; i++) {
        const u = units[i];
        const percentage = Math.min(100, (u.bid_size / total_bid_size) * 100);
        html += `
            <div class="orderbook-row bid" onclick="setOrderPrice(${u.bid_price})" style="cursor:pointer; display:flex; justify-content:space-between; padding:4px 8px; font-family:\'Roboto Mono\', monospace; font-size:0.75rem; background:rgba(255, 75, 75, 0.04); border-bottom:1px solid rgba(148, 163, 184, 0.08);">
                <span class="price bull" style="color:#FF4B4B; font-weight:bold;">${u.bid_price.toLocaleString()}</span>
                <div style="position:relative; width:50%; text-align:right;">
                    <div style="position:absolute; right:0; top:0; bottom:0; background:rgba(255, 75, 75, 0.1); width:${percentage}%;"></div>
                    <span class="size" style="position:relative; z-index:1; color:#94A3B8;">${sizeFormat(u.bid_size)}</span>
                </div>
            </div>
        `;
    }
    
    orderbookList.innerHTML = html;
}

/**
 * 호가창에서 호가를 클릭하면 가격 인풋에 적용합니다.
 */
function setOrderPrice(price) {
    const priceInput = document.getElementById('real-order-price');
    if (priceInput && !priceInput.disabled) {
        priceInput.value = price;
        calculateTotalOrderAmount();
    }
}

/**
 * 주문의 매수/매도 사이드를 토글합니다.
 */
function setOrderSide(side) {
    state.realOrderState.side = side;
    
    const buyTab = document.getElementById('order-tab-buy');
    const sellTab = document.getElementById('order-tab-sell');
    const orderBtn = document.getElementById('real-order-btn');
    
    if (buyTab && sellTab && orderBtn) {
        if (side === 'BUY') {
            buyTab.classList.add('active');
            sellTab.classList.remove('active');
            orderBtn.innerText = '실계좌 매수 주문';
            orderBtn.className = 'btn block buy';
            orderBtn.style.background = '#FF4B4B';
        } else {
            sellTab.classList.add('active');
            buyTab.classList.remove('active');
            orderBtn.innerText = '실계좌 매도 주문';
            orderBtn.className = 'btn block sell';
            orderBtn.style.background = '#0072FF';
        }
    }
    
    onOrderTypeChange();
    calculateTotalOrderAmount();
}

/**
 * 주문 유형 (지정가 / 시장가) 선택에 따라 폼 배치를 변경합니다.
 */
function onOrderTypeChange() {
    const orderType = document.querySelector('input[name="real-order-type"]:checked').value;
    const side = state.realOrderState.side;
    const exchange = state.realOrderState.exchange || 'upbit';
    const isStock = exchange === 'kis';
    
    const groupPrice = document.getElementById('group-order-price');
    const groupVolume = document.getElementById('group-order-volume');
    const groupTotal = document.getElementById('group-order-total');
    
    const priceInput = document.getElementById('real-order-price');
    const volumeInput = document.getElementById('real-order-volume');
    const totalInput = document.getElementById('real-order-total');
    
    if (orderType === 'limit') {
        if (priceInput) priceInput.disabled = false;
        if (volumeInput) volumeInput.disabled = false;
        if (totalInput) totalInput.disabled = false;
        if (groupPrice) groupPrice.style.display = 'block';
        if (groupVolume) groupVolume.style.display = 'block';
        if (groupTotal) groupTotal.style.display = 'block';
    } else {
        // 시장가
        if (side === 'BUY') {
            if (isStock) {
                // 주식 시장가 매수는 수량 입력
                if (priceInput) { priceInput.disabled = true; priceInput.value = ''; }
                if (volumeInput) volumeInput.disabled = false;
                if (totalInput) { totalInput.disabled = true; totalInput.value = ''; }
                
                if (groupPrice) groupPrice.style.display = 'none';
                if (groupVolume) groupVolume.style.display = 'block';
                if (groupTotal) groupTotal.style.display = 'none';
            } else {
                // 코인 시장가 매수는 금액 입력
                if (priceInput) { priceInput.disabled = true; priceInput.value = ''; }
                if (volumeInput) { volumeInput.disabled = true; volumeInput.value = ''; }
                if (totalInput) totalInput.disabled = false;
                
                if (groupPrice) groupPrice.style.display = 'none';
                if (groupVolume) groupVolume.style.display = 'none';
                if (groupTotal) groupTotal.style.display = 'block';
            }
        } else {
            // 시장가 매도는 코인/주식 모두 수량 입력
            if (priceInput) { priceInput.disabled = true; priceInput.value = ''; }
            if (volumeInput) volumeInput.disabled = false;
            if (totalInput) { totalInput.disabled = true; totalInput.value = ''; }
            
            if (groupPrice) groupPrice.style.display = 'none';
            if (groupVolume) groupVolume.style.display = 'block';
            if (groupTotal) groupTotal.style.display = 'none';
        }
    }
}

/**
 * 수량 * 단가 = 총액을 연산합니다.
 */
function calculateTotalOrderAmount() {
    const orderType = document.querySelector('input[name="real-order-type"]:checked').value;
    if (orderType !== 'limit') return;
    
    const priceInput = document.getElementById('real-order-price');
    const volumeInput = document.getElementById('real-order-volume');
    const totalInput = document.getElementById('real-order-total');
    
    if (priceInput && volumeInput && totalInput) {
        const price = parseFloat(priceInput.value) || 0;
        const volume = parseFloat(volumeInput.value) || 0;
        if (price > 0 && volume > 0) {
            totalInput.value = Math.floor(price * volume);
        }
    }
}

/**
 * 총액 입력을 통한 수량 역산
 */
function onTotalAmountInput() {
    const orderType = document.querySelector('input[name="real-order-type"]:checked').value;
    if (orderType !== 'limit') return;
    
    const priceInput = document.getElementById('real-order-price');
    const volumeInput = document.getElementById('real-order-volume');
    const totalInput = document.getElementById('real-order-total');
    const exchange = state.realOrderState.exchange || 'upbit';
    const isStock = exchange === 'kis';
    
    if (priceInput && volumeInput && totalInput) {
        const price = parseFloat(priceInput.value) || 0;
        const total = parseFloat(totalInput.value) || 0;
        if (price > 0 && total > 0) {
            if (isStock) {
                volumeInput.value = Math.floor(total / price);
            } else {
                volumeInput.value = (total / price).toFixed(8);
            }
        }
    }
}

/**
 * 비율 버튼 클릭 시 주문 설정 처리
 */
function setOrderRatio(ratio) {
    const side = state.realOrderState.side;
    const orderType = document.querySelector('input[name="real-order-type"]:checked').value;
    const exchange = state.realOrderState.exchange || 'upbit';
    const isStock = exchange === 'kis';
    
    const priceInput = document.getElementById('real-order-price');
    const volumeInput = document.getElementById('real-order-volume');
    const totalInput = document.getElementById('real-order-total');
    
    const currentPrice = priceInput ? (parseFloat(priceInput.value) || 0) : 0;
    
    if (side === 'BUY') {
        const krwBalance = state.realKRWBalance || 0;
        const targetKrw = Math.floor(krwBalance * ratio);
        
        if (orderType === 'limit') {
            if (currentPrice > 0) {
                if (isStock) {
                    const qty = Math.floor(targetKrw / currentPrice);
                    if (volumeInput) volumeInput.value = qty;
                    if (totalInput) totalInput.value = qty * currentPrice;
                } else {
                    if (totalInput) totalInput.value = targetKrw;
                    if (volumeInput) volumeInput.value = (targetKrw / currentPrice).toFixed(8);
                }
            } else {
                showAlert("가격을 먼저 선택하거나 입력해주세요.", "warning");
            }
        } else {
            // 시장가 매수
            if (isStock) {
                if (currentPrice > 0) {
                    const qty = Math.floor(targetKrw / currentPrice);
                    if (volumeInput) volumeInput.value = qty;
                } else {
                    showAlert("현재가 정보를 수신할 때까지 잠시만 기다려주세요.", "warning");
                }
            } else {
                if (totalInput) totalInput.value = targetKrw;
            }
        }
    } else {
        const qtyBalance = state.realOrderState.asset ? state.realOrderState.asset.balance : 0;
        const targetQty = qtyBalance * ratio;
        
        if (orderType === 'limit') {
            if (isStock) {
                const qty = Math.floor(targetQty);
                if (volumeInput) volumeInput.value = qty;
                if (currentPrice > 0 && totalInput) {
                    totalInput.value = Math.floor(currentPrice * qty);
                }
            } else {
                if (volumeInput) volumeInput.value = targetQty.toFixed(8);
                if (currentPrice > 0 && totalInput) {
                    totalInput.value = Math.floor(currentPrice * targetQty);
                }
            }
        } else {
            // 시장가 매도
            if (isStock) {
                if (volumeInput) volumeInput.value = Math.floor(targetQty);
            } else {
                if (volumeInput) volumeInput.value = targetQty.toFixed(8);
            }
        }
    }
}

/**
 * 주문을 실제로 거래소에 제출합니다.
 */
async function executeRealOrder() {
    const asset = state.realOrderState.asset;
    if (!asset) return;
    
    const exchange = state.realOrderState.exchange || 'upbit';
    const isStock = exchange === 'kis';
    const side = state.realOrderState.side;
    const orderType = document.querySelector('input[name="real-order-type"]:checked').value;
    
    const priceInput = document.getElementById('real-order-price');
    const volumeInput = document.getElementById('real-order-volume');
    const totalInput = document.getElementById('real-order-total');
    
    const price = priceInput ? parseFloat(priceInput.value) : null;
    const volume = volumeInput ? parseFloat(volumeInput.value) : null;
    const total = totalInput ? parseFloat(totalInput.value) : null;
    
    let orderData = {
        symbol: isStock ? asset.currency : `KRW-${asset.currency}`,
        side: side,
        order_type: orderType
    };
    
    let confirmMsg = `[실계좌 주문 경고]\n정말로 실제 자산을 사용해 주문하시겠습니까?\n\n`;
    confirmMsg += `거래소: ${exchange.toUpperCase()}\n`;
    confirmMsg += `종목: ${asset.korean_name} (${asset.currency})\n`;
    confirmMsg += `구분: ${side === 'BUY' ? '매수' : '매도'} / ${orderType === 'limit' ? '지정가' : '시장가'}\n`;
    
    const unit = isStock ? '주' : asset.currency;
    
    if (orderType === 'limit') {
        if (!price || price <= 0 || !volume || volume <= 0) {
            alert("지정가 주문은 가격과 수량을 올바르게 입력해야 합니다.");
            return;
        }
        orderData.price = price;
        orderData.volume = volume;
        confirmMsg += `가격: ${price.toLocaleString()} 원\n`;
        confirmMsg += `수량: ${volume} ${unit}\n`;
        confirmMsg += `총액: ${Math.floor(price * volume).toLocaleString()} 원\n`;
    } else {
        if (side === 'BUY') {
            if (isStock) {
                if (!volume || volume <= 0) {
                    alert("시장가 매수는 매수 수량을 올바르게 입력해야 합니다.");
                    return;
                }
                orderData.order_type = 'market';
                orderData.volume = volume;
                confirmMsg += `매수 수량: ${volume} 주 (시장가)\n`;
            } else {
                if (!total || total <= 0) {
                    alert("시장가 매수는 매수 총액을 올바르게 입력해야 합니다.");
                    return;
                }
                orderData.order_type = 'price';
                orderData.price = total;
                confirmMsg += `총 매수액: ${total.toLocaleString()} 원 (시장가)\n`;
            }
        } else {
            if (!volume || volume <= 0) {
                alert("시장가 매도는 매도 수량을 올바르게 입력해야 합니다.");
                return;
            }
            orderData.order_type = 'market';
            orderData.volume = volume;
            confirmMsg += `매도 수량: ${volume} ${unit} (시장가)\n`;
        }
    }
    
    if (!confirm(confirmMsg)) {
        return;
    }
    
    const orderBtn = document.getElementById('real-order-btn');
    const originalText = orderBtn ? orderBtn.innerText : '';
    if (orderBtn) {
        orderBtn.disabled = true;
        orderBtn.innerText = "⏳ 주문 제출 중...";
    }
    
    try {
        const res = await APIClient.placeRealOrder(exchange, orderData);
        showAlert("주문이 성공적으로 제출되었습니다.", "success");
        closeRealAssetOrderModal();
        await loadRealAssets(false);
    } catch (e) {
        showAlert(e.message || "주문 제출에 실패했습니다.", "error");
        console.error(e);
    } finally {
        if (orderBtn) {
            orderBtn.disabled = false;
            orderBtn.innerText = originalText;
        }
    }
}

/**
 * 실자산 거래 이력 모달을 엽니다.
 */
async function openRealAssetHistoryModal(asset) {
    const histExchange = document.getElementById('real-hist-exchange');
    const histSymbol = document.getElementById('real-hist-symbol');
    const histName = document.getElementById('real-hist-name');
    const tbody = document.getElementById('real-order-history-tbody');
    
    // 요약 카드 메트릭스 엘리먼트 6개
    const totalBuyEl = document.getElementById('real-hist-total-buy');
    const totalSellEl = document.getElementById('real-hist-total-sell');
    const evalValueEl = document.getElementById('real-hist-eval-value');
    const totalFeeEl = document.getElementById('real-hist-total-fee');
    const totalPnlEl = document.getElementById('real-hist-total-pnl');
    const totalRoiEl = document.getElementById('real-hist-total-roi');
    
    if (histExchange) histExchange.innerText = 'UPBIT';
    if (histSymbol) histSymbol.innerText = asset.currency;
    if (histName) histName.innerText = asset.korean_name;
    
    if (tbody) {
        tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;padding:15px;color:rgba(255,255,255,0.4);">&#x23F3; 업비트에서 거래 이력을 조회 중입니다...</td></tr>';
    }
    
    // 요약 메트릭스 초기화
    if (totalBuyEl) totalBuyEl.innerText = '0 원';
    if (totalSellEl) totalSellEl.innerText = '0 원';
    if (evalValueEl) evalValueEl.innerText = '0 원';
    if (totalFeeEl) totalFeeEl.innerText = '0 원';
    if (totalPnlEl) {
        totalPnlEl.innerText = '0 원';
        totalPnlEl.style.color = '#F8FAFC';
    }
    if (totalRoiEl) {
        totalRoiEl.innerText = '0.00%';
        totalRoiEl.style.color = '#F8FAFC';
    }
    
    const modal = document.getElementById('real-asset-history-modal');
    if (modal) {
        modal.style.display = 'flex';
        modal.onclick = (e) => {
            if (e.target === modal) closeRealAssetHistoryModal();
        };
    }
    
    try {
        const symbol = `KRW-${asset.currency}`;
        
        // 현재가를 가져오기 위해 오더북과 이력을 병렬 요청
        const [orders, orderbookRes] = await Promise.all([
            APIClient.fetchRealOrderHistory('upbit', symbol),
            APIClient.fetchOrderbook('upbit', symbol).catch(() => null)
        ]);
        
        const currentPrice = (orderbookRes && orderbookRes.trade_price) ? orderbookRes.trade_price : (asset.current_price || 0);
        
        if (!tbody) return;
        tbody.innerHTML = '';
        
        if (!orders || orders.length === 0) {
            tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;padding:15px;color:#64748B;">최근 20건 내 체결 완료된 거래 내역이 없습니다.</td></tr>';
            return;
        }
        
        let totalBuyAmount = 0;
        let totalSellAmount = 0;
        let paidFee = 0;
        
        orders.forEach(order => {
            const tr = document.createElement('tr');
            tr.style.borderBottom = '1px solid rgba(148, 163, 184, 0.08)';
            
            let timeStr = '-';
            if (order.created_at) {
                const d = new Date(order.created_at);
                const pad = (n) => String(n).padStart(2, '0');
                timeStr = `${d.getFullYear()}. ${d.getMonth() + 1}. ${d.getDate()}. ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
            }
            const typeStr = order.side === 'BUY' ? '매수' : '매도';
            const typeClass = order.side === 'BUY' ? 'bull' : 'bear';
            const priceStr = order.price ? order.price.toLocaleString() : '-';
            const volumeStr = order.executed_volume ? order.executed_volume.toString() : '0';
            const feeVal = order.fee || 0;
            const feeStr = feeVal > 0 ? `${feeVal.toLocaleString()} 원` : '0 원';
            const stateStr = order.state === 'done' ? '완료' : (order.state === 'cancel' ? '취소' : order.state);
            
            const rawTotal = (order.price && order.executed_volume) ? (order.price * order.executed_volume) : 0;
            const totalStr = rawTotal ? Math.floor(rawTotal).toLocaleString() : '0';
            
            // 체결 완료 상태일 때만 합산 연산
            if (order.state === 'done') {
                paidFee += feeVal;
                if (order.side === 'BUY') {
                    totalBuyAmount += rawTotal;
                } else if (order.side === 'SELL') {
                    totalSellAmount += rawTotal;
                }
            }
            
            tr.innerHTML = `
                <td style="padding:8px; font-size:0.75rem; color:#94A3B8;">${timeStr}</td>
                <td style="padding:8px; font-weight:bold; font-size:0.75rem;" class="${typeClass}">${typeStr}</td>
                <td style="padding:8px; text-align:right;" class="num">${priceStr}</td>
                <td style="padding:8px; text-align:right;" class="num">${volumeStr}</td>
                <td style="padding:8px; text-align:right; color:#94A3B8;" class="num">${feeStr}</td>
                <td style="padding:8px; text-align:center; font-size:0.72rem; color:#94A3B8;">${stateStr}</td>
                <td style="padding:8px; text-align:right; font-weight:bold; color:#F8FAFC;" class="num">${totalStr} 원</td>
            `;
            tbody.appendChild(tr);
        });
        
        // 6개 요약 정보 계산 및 바인딩
        const balance = asset.balance || 0;
        const evalValue = currentPrice * balance; // 실시간 평가액
        const estSellFee = evalValue * 0.0005; // 평가액 매각 시 예상 수수료 (0.05%)
        const totalFee = paidFee + estSellFee; // 총 수수료 = 이미 지불한 수수료 + 평가액 매각 예상 수수료
        
        // 실현 손익 = 총 매도액 + 평가액 - 총 매수액 - 총 수수료
        const pnl = totalSellAmount + evalValue - totalBuyAmount - totalFee;
        // 실현 수익률 = 실현 손익 / 총 매수액 * 100
        const roi = totalBuyAmount > 0 ? ((pnl / totalBuyAmount) * 100) : 0;
        
        if (totalBuyEl) totalBuyEl.innerText = `${Math.floor(totalBuyAmount).toLocaleString()} 원`;
        if (totalSellEl) totalSellEl.innerText = `${Math.floor(totalSellAmount).toLocaleString()} 원`;
        if (evalValueEl) evalValueEl.innerText = `${Math.floor(evalValue).toLocaleString()} 원`;
        if (totalFeeEl) totalFeeEl.innerText = `${Math.floor(totalFee).toLocaleString()} 원`;
        
        if (totalPnlEl) {
            totalPnlEl.innerText = `${pnl >= 0 ? '+' : ''}${Math.floor(pnl).toLocaleString()} 원`;
            if (pnl > 0) {
                totalPnlEl.style.color = '#FF4B4B';
            } else if (pnl < 0) {
                totalPnlEl.style.color = '#0072FF';
            } else {
                totalPnlEl.style.color = '#F8FAFC';
            }
        }
        
        if (totalRoiEl) {
            totalRoiEl.innerText = `${roi >= 0 ? '+' : ''}${roi.toFixed(2)}%`;
            if (roi > 0) {
                totalRoiEl.style.color = '#FF4B4B';
            } else if (roi < 0) {
                totalRoiEl.style.color = '#0072FF';
            } else {
                totalRoiEl.style.color = '#F8FAFC';
            }
        }
    } catch (e) {
        if (tbody) {
            tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;padding:15px;color:#FF4B4B;">&#x26A0;&#xFE0F; 이력 조회 실패</td></tr>';
        }
        console.error("Failed to load real order history", e);
    }
}

/**
 * 실자산 거래 이력 모달을 닫습니다.
 */
function closeRealAssetHistoryModal() {
    const modal = document.getElementById('real-asset-history-modal');
    if (modal) modal.style.display = 'none';
}

// 과거 백테스트 결과 성능 상세 분석 렌더러
window.lastBacktestResult = null;

function renderBacktestPerformance(res) {
    window.lastBacktestResult = res;
    
    // 데이터가 유효하지 않거나 비어있는 경우 하위 성과 분석 테이블 전체 초기화
    if (!res || res.type === 'none' || !res.exchanges || res.exchanges.length === 0) {
        const exchangesTbody = document.getElementById('port-exchanges-tbody');
        if (exchangesTbody) {
            exchangesTbody.innerHTML = '<tr><td colspan="7" style="text-align:center; padding:20px; color:#64748B;">거래 발생 이력이 없습니다.</td></tr>';
        }
        const symbolsTitle = document.getElementById('port-symbols-title');
        if (symbolsTitle) {
            symbolsTitle.innerText = '종목별 상세 현황';
        }
        const symbolsTbody = document.getElementById('port-symbols-tbody');
        if (symbolsTbody) {
            symbolsTbody.innerHTML = '<tr><td colspan="10" style="text-align:center; padding:20px; color:#64748B;">조회할 거래 데이터가 없습니다.</td></tr>';
        }
        const symbolsTfoot = document.getElementById('port-symbols-tfoot');
        if (symbolsTfoot) {
            symbolsTfoot.style.display = 'none';
            symbolsTfoot.innerHTML = '';
        }
        const historyTbody = document.getElementById('port-history-detail-tbody');
        if (historyTbody) {
            historyTbody.innerHTML = '<tr><td colspan="8" style="text-align:center; padding:20px; color:#64748B;">체결 내역이 없습니다.</td></tr>';
        }
        return;
    }
    
    // 거래소 요약 테이블 렌더링 호출
    PortfolioView.renderExchangeSummary('port-exchanges-tbody', res, (exchangeName) => {
        // 거래소 행 클릭 콜백
        PortfolioView.renderSymbolDetailTable(
            'port-symbols-tbody', 
            'port-symbols-title', 
            exchangeName, 
            res, 
            'port-symbols-tfoot',
            (item) => {
                // 종목 행 클릭 콜백
                PortfolioView.renderHistoryTablePort('port-history-detail-tbody', item);
            }
        );
    });
}

document.addEventListener('DOMContentLoaded', () => {
    loadPortfolioHistoryList().then(() => {
        loadPortfolio();
    });
});

let currentRunMode = 'simulation'; // 'simulation' 또는 'backtest'

/**
 * 공통 전략 기동 모달을 열고, 모드에 맞춰 화면 필드를 동적으로 온오프합니다.
 */
function toggleStrategySelectionMode() {
    const autoRadio = document.getElementById('modal-strat-mode-auto');
    const selectionSection = document.getElementById('modal-strategy-selection-section');
    if (!selectionSection) return;

    if (autoRadio && autoRadio.checked) {
        selectionSection.style.display = 'none';
    } else {
        selectionSection.style.display = 'flex';
    }
}
window.toggleStrategySelectionMode = toggleStrategySelectionMode;

async function openStrategyRunModal(mode) {
    currentRunMode = 'simulation';
    const modal = document.getElementById('strategy-run-modal');
    if (!modal) return;

    const titleEl = document.getElementById('modal-run-title');
    if (titleEl) {
        titleEl.innerText = '⚙️ 실시간 모의투자 실행 설정';
    }

    const submitBtn = document.getElementById('btn-modal-submit');
    if (submitBtn) {
        submitBtn.innerText = '▶️ 모의투자 가동';
    }

    // 전략 모드 라디오 버튼 초기화: 챔피언 자동 기용이 기본값
    const autoRadio = document.getElementById('modal-strat-mode-auto');
    const manualRadio = document.getElementById('modal-strat-mode-manual');
    if (autoRadio) autoRadio.checked = true;
    if (manualRadio) manualRadio.checked = false;

    const selectionSection = document.getElementById('modal-strategy-selection-section');
    if (selectionSection) selectionSection.style.display = 'none';

    const listContainer = document.getElementById('modal-strategy-container');
    if (listContainer) {
        listContainer.innerHTML = '<p class="status-text">사용 가능한 거래 전략 조회 중...</p>';
        try {
            const strategies = await APIClient.fetchStrategies();
            listContainer.innerHTML = '';
            
            strategies.forEach((strategy, idx) => {
                const card = document.createElement('div');
                card.className = 'strategy-card';
                card.style.background = 'rgba(15, 23, 42, 0.3)';
                card.style.border = '1px solid rgba(148, 163, 184, 0.1)';
                card.style.padding = '10px';
                card.style.borderRadius = '8px';
                card.style.display = 'flex';
                card.style.flexDirection = 'column';
                card.style.gap = '6px';
                
                const checked = idx === 0 ? 'checked' : '';
                
                let paramFieldsHtml = '';
                if (strategy.default_params) {
                    paramFieldsHtml = '<div style="display:grid; grid-template-columns: repeat(2, 1fr); gap:6px; margin-top:5px;">';
                    Object.entries(strategy.default_params).forEach(([k, v]) => {
                        const inputId = `param-${strategy.name}-${k}`;
                        paramFieldsHtml += `
                            <label style="display:flex; flex-direction:column; gap:2px; font-size:0.7rem; color:#94A3B8;">
                                ${k}:
                                <input type="text" id="${inputId}" class="dark-input" style="padding: 2px 5px; font-size:0.75rem; margin:0;" value="${v}">
                            </label>
                        `;
                    });
                    paramFieldsHtml += '</div>';
                }

                card.innerHTML = `
                    <div style="display:flex; align-items:center; gap:8px;">
                        <input type="checkbox" class="modal-strategy-checkbox" id="modal-chk-${strategy.name}" data-strategy-name="${strategy.name}" ${checked}>
                        <label for="modal-chk-${strategy.name}" style="font-weight:bold; color:#F8FAFC; cursor:pointer; font-size:0.85rem;">${strategy.name}</label>
                    </div>
                    <p style="margin: 0; font-size:0.7rem; color:#64748B;">${strategy.description || '설명이 없습니다.'}</p>
                    ${paramFieldsHtml}
                `;
                listContainer.appendChild(card);
            });
        } catch (e) {
            listContainer.innerHTML = '<p class="status-text error">전략 목록 조회 실패</p>';
            console.error(e);
        }
    }

    modal.style.display = 'flex';
}

function closeStrategyRunModal() {
    const modal = document.getElementById('strategy-run-modal');
    if (modal) modal.style.display = 'none';
}

/**
 * 모달 설정을 종합하여 실시간 모의투자 기동 혹은 과거 백테스트를 실행합니다.
 */
async function submitStrategyRun() {
    const autoRadio = document.getElementById('modal-strat-mode-auto');
    const isAutoMode = autoRadio ? autoRadio.checked : true;

    const strategies = {};

    if (!isAutoMode) {
        const activeCheckboxes = document.querySelectorAll('.modal-strategy-checkbox:checked');
        if (activeCheckboxes.length === 0) {
            alert("최소 한 개 이상의 전략을 선택해야 합니다.");
            return;
        }

        activeCheckboxes.forEach(chk => {
            const name = chk.getAttribute('data-strategy-name');
            const params = {};
            
            const inputs = document.querySelectorAll(`[id^="param-${name}-"]`);
            inputs.forEach(input => {
                const key = input.id.replace(`param-${name}-`, '');
                const val = input.value.trim();
                const numVal = Number(val);
                params[key] = isNaN(numVal) ? val : numVal;
            });

            strategies[name] = {
                enabled: true,
                params: params
            };
        });
    }

    const cash_config = {
        upbit: parseFloat(document.getElementById('modal-cash-upbit').value) || 0,
        bithumb: parseFloat(document.getElementById('modal-cash-bithumb').value) || 0,
        kis: parseFloat(document.getElementById('modal-cash-kis').value) || 0
    };

    const submitBtn = document.getElementById('btn-modal-submit');
    const prevText = submitBtn ? submitBtn.innerText : '';
    if (submitBtn) {
        submitBtn.disabled = true;
        submitBtn.innerText = "⏳ 처리 중...";
    }

    try {
        const res = await APIClient.startPortfolioSession(cash_config, strategies);
        if (res.status === 'success') {
            showAlert(`실시간 모의투자가 가동되었습니다.`, "success");
            state.currentPortfolioId = res.portfolio_id;
            closeStrategyRunModal();
            await loadPortfolioHistoryList(true);
            await loadPortfolio(true);
        } else {
            showAlert(res.message || "모의투자 기동 실패", "error");
        }
    } catch (e) {
        showAlert("전략 기동 과정에 장애가 발생했습니다.", "error");
        console.error(e);
    } finally {
        if (submitBtn) {
            submitBtn.disabled = false;
            submitBtn.innerText = prevText;
        }
    }
}

/**
 * 포트폴리오 좌측 사이드바 내 모의투자 제어 버튼 및 상태 라벨을 동적으로 동기화합니다.
 */
function updateSessionControlUI() {
    const badge = document.getElementById('session-status-badge');
    const actionGroup = document.getElementById('session-action-group');
    if (!badge || !actionGroup) return;

    const portfolios = state.portfoliosCache || [];
    const activeSession = portfolios.find(p => p.type === 'simulation');

    if (activeSession) {
        badge.innerText = "가동중";
        badge.style.background = "rgba(16, 185, 129, 0.2)";
        badge.style.color = "#10B981";

        actionGroup.innerHTML = `
            <button class="btn danger" style="width: 100%; font-size: 0.85rem;" onclick="endSimulationSession('${activeSession.id}')">⏹️ 모의투자 즉시 종료</button>
        `;
    } else {
        badge.innerText = "미가동";
        badge.style.background = "rgba(239, 68, 68, 0.2)";
        badge.style.color = "#EF4444";

        actionGroup.innerHTML = `
            <button class="btn success" style="width: 100%; font-size: 0.85rem;" onclick="openStrategyRunModal('simulation')">▶️ 실시간 모의투자 시작</button>
        `;
    }
}

/**
 * 실시간 가동 중인 모의투자 세션을 종료(마감)시킵니다.
 */
async function endSimulationSession(portfolioId) {
    if (!confirm("현재 실행 중인 모의투자를 안전하게 마감하고 기록을 보존하시겠습니까? (자동 매매가 정지됩니다.)")) {
        return;
    }

    try {
        const res = await APIClient.endPortfolioSession(portfolioId);
        if (res.status === 'success') {
            showAlert("모의투자 세션이 성공적으로 마감되었습니다.", "success");
            await loadPortfolioHistoryList(true);
            await loadPortfolio(true);
        } else {
            showAlert(res.message || "종료 실패", "error");
        }
    } catch (e) {
        showAlert("세션 마감 중 오류가 발생했습니다.", "error");
        console.error(e);
    }
}

// 전역 window 바인딩으로 타 JS 파일 및 HTML 인라인 호출 지원
window.loadPortfolioList = loadPortfolioHistoryList;
window.loadPortfolio = loadPortfolio;
window.executePanicSell = executePanicSell;
window.showAssetDetails = showAssetDetails;
window.closeAssetModal = closeAssetModal;
window.loadRealAssets = loadRealAssets;
window.renderBacktestPerformance = renderBacktestPerformance;
window.openStrategyRunModal = openStrategyRunModal;
window.closeStrategyRunModal = closeStrategyRunModal;
window.submitStrategyRun = submitStrategyRun;
window.loadPortfolioHistoryList = loadPortfolioHistoryList;
window.deletePortfolioHistory = deletePortfolioHistory;
window.clearAllPortfolioHistory = clearAllPortfolioHistory;
window.endSimulationSession = endSimulationSession;

// 실자산 및 실계좌 관련 신규 바인딩
window.changeRealAssetFilter = changeRealAssetFilter;
window.changeRealAssetExchange = changeRealAssetExchange;
window.syncRealOrderHistory = syncRealOrderHistory;
window.openRealAssetOrderModal = openRealAssetOrderModal;
window.closeRealAssetOrderModal = closeRealAssetOrderModal;
window.setOrderSide = setOrderSide;
window.onOrderTypeChange = onOrderTypeChange;
window.calculateTotalOrderAmount = calculateTotalOrderAmount;
window.onTotalAmountInput = onTotalAmountInput;
window.setOrderRatio = setOrderRatio;
window.executeRealOrder = executeRealOrder;
window.openRealAssetHistoryModal = openRealAssetHistoryModal;
window.closeRealAssetHistoryModal = closeRealAssetHistoryModal;
window.setOrderPrice = setOrderPrice;

if (typeof ViewRouter !== 'undefined') {
    ViewRouter.registerRoute('portfolio-view', () => {
        loadPortfolioHistoryList();
        loadPortfolio();
    });
    ViewRouter.registerRoute('real-asset-view', () => {
        loadRealAssets();
    });
}

