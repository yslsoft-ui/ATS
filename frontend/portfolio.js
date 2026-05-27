/**
 * Upbit Terminal 포트폴리오(Portfolio) 및 실자산 관리 모듈 (Controller)
 */

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

            const dateStr = item.created_at ? new Date(item.created_at).toLocaleString() : '-';

            // 행 클릭 시 해당 포트폴리오 로드
            tr.onclick = (e) => {
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
            
            // 캐시에서 삭제된 포트폴리오를 선제적으로 필터링하여 동기화 꼬임 차단
            if (state.portfoliosCache) {
                state.portfoliosCache = state.portfoliosCache.filter(p => p.id !== portfolioId);
            }

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
            
            // 캐시 일괄 비우기 (진행중인 simulation 세션 제외)
            if (state.portfoliosCache) {
                state.portfoliosCache = state.portfoliosCache.filter(p => p.type === 'simulation');
            }

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
            type: cachedPort ? cachedPort.type : (portfolioId.startsWith('backtest_') ? 'backtest' : 'simulation'),
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

            if (appliedStrategies && data.applied_strategies) {
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
            }

            if (backtestAnalysisPanels) backtestAnalysisPanels.style.display = 'flex';

            renderBacktestPerformance(data);
        } else {
            if (typeBadge) {
                typeBadge.innerText = '실시간 모의투자';
                typeBadge.style.background = '#3B82F6';
                typeBadge.style.display = 'inline-block';
            }
            if (panicBtn) {
                panicBtn.style.display = 'inline-block';
                panicBtn.disabled = false;
            }

            if (backtestSummary) backtestSummary.style.display = 'none';
            if (appliedStrategies) appliedStrategies.style.display = 'none';

            if (backtestAnalysisPanels) backtestAnalysisPanels.style.display = 'flex';
            
            renderBacktestPerformance(data);
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
async function loadRealAssets() {
    const tbody = document.getElementById('real-assets-tbody');
    const totalValueEl = document.getElementById('real-total-value');
    const assetCountEl = document.getElementById('real-asset-count');
    
    if (!tbody) return;
    
    tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;padding:30px;color:rgba(255,255,255,0.4);">&#x23F3; 업비트 API에서 자산 명세를 안전하게 조회 중입니다...</td></tr>';
    
    try {
        const data = await APIClient.fetchRealAssets('upbit');
        
        // 실시간 더블클릭 차트 이동 콜백 어댑터
        const onAssetDblClick = (asset) => {
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
        };

        PortfolioView.renderRealAssetsTable('real-assets-tbody', data, totalValueEl, assetCountEl, onAssetDblClick);
    } catch (e) {
        tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;padding:20px;color:#FF4B4B;">&#x26A0;&#xFE0F; 자산 조회 실패 (API 키 권한 또는 인터넷 연결 상태를 확인하세요)</td></tr>';
        console.error("Asset load failed", e);
    }
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
async function openStrategyRunModal(mode) {
    currentRunMode = mode;
    const modal = document.getElementById('strategy-run-modal');
    if (!modal) return;

    const titleEl = document.getElementById('modal-run-title');
    if (titleEl) {
        titleEl.innerText = mode === 'backtest' ? '⚙️ 과거 백테스트 실행 설정' : '⚙️ 실시간 모의투자 실행 설정';
    }

    const backtestFields = document.getElementById('modal-backtest-fields');
    if (backtestFields) {
        backtestFields.style.display = mode === 'backtest' ? 'flex' : 'none';
    }

    const submitBtn = document.getElementById('btn-modal-submit');
    if (submitBtn) {
        submitBtn.innerText = mode === 'backtest' ? '🚀 백테스트 실행' : '▶️ 모의투자 가동';
    }

    if (mode === 'backtest') {
        const startInput = document.getElementById('modal-backtest-start-date');
        const endInput = document.getElementById('modal-backtest-end-date');
        if (startInput && endInput && !startInput.value) {
            const now = new Date();
            const startDay = new Date(now);
            startDay.setHours(0, 0, 0, 0);
            const endDay = new Date(now);
            endDay.setMinutes(0, 0, 0);

            const toLocalISO = (date) => {
                const tzOffset = date.getTimezoneOffset() * 60000;
                return (new Date(date - tzOffset)).toISOString().slice(0, 16);
            };
            startInput.value = toLocalISO(startDay);
            endInput.value = toLocalISO(endDay);
        }
    }

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
    const activeCheckboxes = document.querySelectorAll('.modal-strategy-checkbox:checked');
    if (activeCheckboxes.length === 0) {
        alert("최소 한 개 이상의 전략을 선택해야 합니다.");
        return;
    }

    const strategies = {};
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
        if (currentRunMode === 'backtest') {
            showAlert("백테스트 시뮬레이션을 생성하여 수행 중입니다... 완료 시까지 잠시 대기하세요.", "info");
            
            const reqData = {
                exchange: document.getElementById('modal-backtest-exchange').value,
                symbol: document.getElementById('modal-backtest-symbol').value.trim() || "",
                start_date: document.getElementById('modal-backtest-start-date').value,
                end_date: document.getElementById('modal-backtest-end-date').value,
                initial_cash: cash_config,
                strategies: strategies
            };

            const res = await APIClient.runBacktest(reqData);
            if (res.status === 'success') {
                showAlert(`백테스트 완료: ROI ${res.summary.roi}%`, "success");
                state.currentPortfolioId = res.portfolio_id;
                closeStrategyRunModal();
                await loadPortfolioHistoryList();
                await loadPortfolio();
            } else {
                showAlert(res.message || "백테스트 실패", "error");
            }
        } else {
            const res = await APIClient.startPortfolioSession(cash_config, strategies);
            if (res.status === 'success') {
                showAlert(`실시간 모의투자가 가동되었습니다.`, "success");
                state.currentPortfolioId = res.portfolio_id;
                closeStrategyRunModal();
                await loadPortfolioHistoryList();
                await loadPortfolio();
            } else {
                showAlert(res.message || "모의투자 기동 실패", "error");
            }
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
            await loadPortfolioHistoryList();
            await loadPortfolio();
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
