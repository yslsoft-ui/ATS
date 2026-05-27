/**
 * Upbit Terminal 과거 데이터 리플레이 백테스트 모듈
 */

let backtestStrategyMetadata = [];
let isBacktestRunning = false;


function formatPrice(val) {
    if (val === undefined || val === null || isNaN(val)) return '-';
    if (val < 100) {
        return val % 1 === 0 ? val.toLocaleString() + " 원" : val.toFixed(2).replace(/\B(?=(\d{3})+(?!\d))/g, ",") + " 원";
    }
    return Math.round(val).toLocaleString() + " 원";
}

function formatTimestamp(ts) {
    if (!ts) return '-';
    const ms = ts < 10000000000 ? ts * 1000 : ts;
    return new Date(ms).toLocaleString();
}

/**
 * 백테스트 화면 로드 시 실행되는 초기화 함수입니다.
 * 전략 목록과 기본 파라미터를 백엔드로부터 조회하여 입력 폼을 동적으로 빌드합니다.
 */
async function loadBacktestView() {
    const listEl = document.getElementById('backtest-strategy-tuning-list');
    if (!listEl) return;

    // 대상 마켓 변경 시 자금 입력 필드 동적 노출/숨김
    const exchangeSelect = document.getElementById('backtest-exchange');
    const updateCashInputs = () => {
        if (!exchangeSelect) return;
        const exVal = exchangeSelect.value;
        const rowUpbit = document.getElementById('row-cash-upbit');
        const rowBithumb = document.getElementById('row-cash-bithumb');
        const rowKis = document.getElementById('row-cash-kis');
        
        if (rowUpbit) rowUpbit.style.display = (exVal === 'all' || exVal === 'upbit') ? 'flex' : 'none';
        if (rowBithumb) rowBithumb.style.display = (exVal === 'all' || exVal === 'bithumb') ? 'flex' : 'none';
        if (rowKis) rowKis.style.display = (exVal === 'all' || exVal === 'kis') ? 'flex' : 'none';
    };
    if (exchangeSelect) {
        exchangeSelect.removeEventListener('change', updateCashInputs);
        exchangeSelect.addEventListener('change', updateCashInputs);
        updateCashInputs();
    }

    listEl.innerHTML = '<p class="status-text">전략 구성을 불러오는 중...</p>';

    // 기본 시간값을 설정 (시작: 오늘 00:00, 종료: 오늘 현재시간:00분)
    const startInput = document.getElementById('backtest-start-date');
    const endInput = document.getElementById('backtest-end-date');
    if (startInput && endInput && !startInput.value) {
        const now = new Date();
        
        // 오늘 00:00
        const startDay = new Date(now);
        startDay.setHours(0, 0, 0, 0);
        
        // 오늘 현재시간:00분
        const endDay = new Date(now);
        endDay.setMinutes(0, 0, 0);

        const toLocalISO = (date) => {
            const tzOffset = date.getTimezoneOffset() * 60000; // ms
            const localISOTime = (new Date(date - tzOffset)).toISOString().slice(0, 16);
            return localISOTime;
        };

        startInput.value = toLocalISO(startDay);
        endInput.value = toLocalISO(endDay);
    }

    try {
        const strategies = await APIClient.fetchBacktestDefaultConfigs();
        backtestStrategyMetadata = strategies;
        listEl.innerHTML = '';

        strategies.forEach(s => {
            const item = document.createElement('div');
            item.className = 'strategy-item';
            item.style.padding = '10px';
            item.style.border = '1px solid rgba(148, 163, 184, 0.15)';
            item.style.borderRadius = '8px';
            item.style.background = 'rgba(30, 41, 59, 0.3)';

            // 전략 이름 및 체크박스
            let paramsHtml = '';
            Object.entries(s.params || {}).forEach(([pName, pInfo]) => {
                const currentVal = pInfo.current !== undefined ? pInfo.current : pInfo.default;
                const inputType = pInfo.type === 'str' ? 'text' : 'number';
                paramsHtml += `
                    <div class="input-group" style="margin-top: 8px;">
                        <label style="font-size: 0.75rem; color: #94A3B8;">${pName} (${pInfo.description || ''})</label>
                        <input type="${inputType}" 
                               class="dark-input backtest-param-input" 
                               data-strategy="${s.id}" 
                               data-param="${pName}" 
                               value="${currentVal}" 
                               style="width: 100%; padding: 4px 8px; font-size: 0.8rem; margin-top: 2px;">
                    </div>
                `;
            });

            item.innerHTML = `
                <div style="display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid rgba(148,163,184,0.1); padding-bottom: 6px;">
                    <label style="font-weight: bold; color: #F8FAFC; font-size: 0.9rem; cursor: pointer; display: flex; align-items: center; gap: 8px;">
                        <input type="checkbox" class="backtest-strategy-enable" data-strategy="${s.id}">
                        ${s.name}
                    </label>
                </div>
                <div style="font-size: 0.75rem; color: #64748B; margin-top: 4px;">${s.description || ''}</div>
                <div class="strategy-tuning-params" style="margin-top: 5px; display: none;">
                    ${paramsHtml}
                </div>
            `;

            // 체크박스 선택 시 파라미터 영역 토글
            const checkbox = item.querySelector('.backtest-strategy-enable');
            const paramsDiv = item.querySelector('.strategy-tuning-params');
            checkbox.addEventListener('change', () => {
                paramsDiv.style.display = checkbox.checked ? 'block' : 'none';
            });

            listEl.appendChild(item);
        });

        // 전체 삭제 버튼 이벤트 매핑
        const clearAllBtn = document.getElementById('btn-clear-all-backtests');
        if (clearAllBtn) {
            clearAllBtn.onclick = async () => {
                if (!confirm("모든 백테스트 이력(포트폴리오 및 상세 체결 내역 전체)을 영구 삭제하시겠습니까? 이 작업은 되돌릴 수 없습니다.")) {
                    return;
                }
                try {
                    const res = await APIClient.clearAllBacktestHistory();
                    if (res.status === 'success') {
                        showAlert("모든 이력이 정상적으로 삭제되었습니다.", "success");
                        loadBacktestHistoryList();
                        hideBacktestResultPanels();
                    } else {
                        showAlert(res.message || "삭제 실패", "error");
                    }
                } catch (e) {
                    showAlert("이력 삭제 도중 오류가 발생했습니다.", "error");
                    console.error(e);
                }
            };
        }

        // 백테스트 이력 목록 불러오기
        loadBacktestHistoryList();

    } catch (e) {
        listEl.innerHTML = '<p class="status-text danger">전략 구성을 불러오는 데 실패했습니다.</p>';
        console.error(e);
    }
}

/**
 * 설정한 조건으로 백테스트를 실행합니다.
 */
async function runBacktest() {
    if (isBacktestRunning) return;
    isBacktestRunning = true;

    const exchange = document.getElementById('backtest-exchange').value;
    const symbol = document.getElementById('backtest-symbol').value.trim();
    const startDate = document.getElementById('backtest-start-date').value;
    const endDate = document.getElementById('backtest-end-date').value;

    // 거래소별 자금 수집
    const initialCashMap = {};
    let totalCash = 0;
    
    if (exchange === 'all' || exchange === 'upbit') {
        const val = parseFloat(document.getElementById('backtest-cash-upbit').value);
        if (isNaN(val) || val <= 0) {
            showAlert("업비트 초기 투자금을 올바르게 입력해 주세요.", "error");
            isBacktestRunning = false;
            return;
        }
        initialCashMap['upbit'] = val;
        totalCash += val;
    }
    if (exchange === 'all' || exchange === 'bithumb') {
        const val = parseFloat(document.getElementById('backtest-cash-bithumb').value);
        if (isNaN(val) || val <= 0) {
            showAlert("빗썸 초기 투자금을 올바르게 입력해 주세요.", "error");
            isBacktestRunning = false;
            return;
        }
        initialCashMap['bithumb'] = val;
        totalCash += val;
    }
    if (exchange === 'all' || exchange === 'kis') {
        const val = parseFloat(document.getElementById('backtest-cash-kis').value);
        if (isNaN(val) || val <= 0) {
            showAlert("한국투자증권(KIS) 초기 투자금을 올바르게 입력해 주세요.", "error");
            isBacktestRunning = false;
            return;
        }
        initialCashMap['kis'] = val;
        totalCash += val;
    }

    // 유효성 검사
    if (!startDate || !endDate) {
        showAlert("시작일과 종료일을 지정해 주세요.", "error");
        isBacktestRunning = false;
        return;
    }
    if (totalCash <= 0) {
        showAlert("초기 투자금을 올바르게 입력해 주세요.", "error");
        isBacktestRunning = false;
        return;
    }

    // 활성화된 전략 및 임시 파라미터 수집
    const strategyPayload = {};
    const strategyItems = document.querySelectorAll('.backtest-strategy-enable');
    let anyStrategyEnabled = false;

    strategyItems.forEach(checkbox => {
        const stratId = checkbox.dataset.strategy;
        const isEnabled = checkbox.checked;

        if (isEnabled) {
            anyStrategyEnabled = true;
            const params = {};
            const paramInputs = document.querySelectorAll(`.backtest-param-input[data-strategy="${stratId}"]`);
            paramInputs.forEach(input => {
                const paramName = input.dataset.param;
                params[paramName] = input.type === 'text' ? input.value : parseFloat(input.value);
            });

            strategyPayload[stratId] = {
                enabled: true,
                params: params
            };
        } else {
            strategyPayload[stratId] = {
                enabled: false,
                params: {}
            };
        }
    });

    if (!anyStrategyEnabled) {
        showAlert("백테스트에 적용할 전략을 하나 이상 선택해 주세요.", "error");
        isBacktestRunning = false;
        return;
    }

    // UI 상태: 로딩 시작
    const btn = document.getElementById('btn-run-backtest');
    const loadingBar = document.getElementById('backtest-loading-bar');
    btn.disabled = true;
    loadingBar.style.display = 'block';

    try {
        const payload = {
            exchange: exchange,
            symbol: symbol,
            start_date: startDate,
            end_date: endDate,
            initial_cash: initialCashMap,
            strategies: strategyPayload
        };

        const result = await APIClient.runBacktest(payload);

        if (result.status === 'success') {
            showAlert("백테스트가 성공적으로 완료되었습니다!", "success");
            
            // 통합 포트폴리오 탭으로 전환 및 로드
            state.currentPortfolioId = result.portfolio_id;
            
            if (typeof loadPortfolioHistoryList === 'function') {
                await loadPortfolioHistoryList();
            }
            const select = document.getElementById('portfolio-select');
            if (select) select.value = result.portfolio_id;
            
            if (typeof loadPortfolio === 'function') {
                await loadPortfolio();
            }
            
            if (typeof ViewRouter !== 'undefined' && typeof ViewRouter.navigateTo === 'function') {
                ViewRouter.navigateTo('portfolio-view');
            }

            loadBacktestHistoryList();
        } else {
            showAlert(result.message || "백테스트 실행 실패", "error");
        }
    } catch (e) {
        showAlert("백테스트 실행 도중 서버 오류가 발생했습니다.", "error");
        console.error(e);
    } finally {
        btn.disabled = false;
        loadingBar.style.display = 'none';
        isBacktestRunning = false;
    }
}

/**
 * 백테스트 이력(세트) 목록을 백엔드로부터 가져와 좌측 하단 테이블에 렌더링합니다.
 */
async function loadBacktestHistoryList() {
    const tbody = document.getElementById('backtest-history-list-tbody');
    if (!tbody) return;

    try {
        let history = await APIClient.fetchBacktestHistory();
        // default(실시간 모의투자) 포트폴리오는 이력 목록에서 제외
        history = history.filter(item => item.portfolio_id !== 'default');
        
        tbody.innerHTML = '';

        if (history.length === 0) {
            tbody.innerHTML = '<tr><td colspan="3" style="text-align:center; padding:15px; color:#64748B;">저장된 이력이 없습니다.</td></tr>';
            return;
        }

        history.forEach(item => {
            const tr = document.createElement('tr');
            tr.style.borderBottom = '1px solid rgba(148, 163, 184, 0.08)';
            
            const isLive = !item.portfolio_id.startsWith('backtest_');
            const roiClass = item.roi >= 0 ? 'bull' : 'bear';
            const roiText = `${item.roi >= 0 ? '+' : ''}${item.roi}%`;

            const dateStr = item.created_at ? new Date(item.created_at + " UTC").toLocaleString() : '-';

            tr.innerHTML = `
                <td style="padding:8px 6px; cursor:pointer;" onclick="loadBacktestFromHistory('${item.portfolio_id}')">
                    <div style="display:flex; align-items:center; gap:5px; flex-wrap:wrap;">
                        <span style="color:#F8FAFC; font-weight:bold;">${item.name}</span>
                        ${isLive ? `<span class="ctx-badge" style="background: rgba(59, 130, 246, 0.2); color: #60A5FA; font-size: 0.65rem; padding: 1px 4px; border-radius: 3px; font-weight: normal; flex-shrink: 0;">실시간</span>` : ''}
                    </div>
                    <span style="font-size:0.7rem; color:#64748B;">${dateStr} (거래 ${item.trade_count}건)</span>
                </td>
                <td style="padding:8px 6px; text-align:right;" class="num ${roiClass}">${roiText}</td>
                <td style="padding:8px 6px; text-align:center; display:flex; justify-content:center; gap:8px; align-items:center;">
                    <button class="btn secondary" style="padding:2px 6px; font-size:0.7rem;" onclick="loadBacktestFromHistory('${item.portfolio_id}')">조회</button>
                    ${isLive ? '' : `<button class="btn danger" style="padding:2px 6px; font-size:0.7rem; background:#EF4444; border:none; color:white; border-radius:4px; cursor:pointer;" onclick="deleteBacktestHistory('${item.portfolio_id}')">삭제</button>`}
                </td>
            `;
            tbody.appendChild(tr);
        });
    } catch (e) {
        tbody.innerHTML = '<tr><td colspan="3" style="text-align:center; padding:15px; color:#EF4444;">이력을 불러오지 못했습니다.</td></tr>';
        console.error(e);
    }
}

/**
 * 특정 백테스트 이력을 삭제합니다.
 */
async function deleteBacktestHistory(portfolioId) {
    if (!confirm("해당 백테스트 이력(포트폴리오 및 체결 이력 전체)을 삭제하시겠습니까? 복구할 수 없습니다.")) {
        return;
    }

    try {
        const res = await APIClient.deleteBacktestHistory(portfolioId);
        if (res.status === 'success') {
            showAlert("이력이 정상적으로 삭제되었습니다.", "success");
            loadBacktestHistoryList();
            
            if (typeof state !== 'undefined') {
                if (state.portfoliosCache) {
                    state.portfoliosCache = state.portfoliosCache.filter(p => p.id !== portfolioId);
                }
                if (state.currentPortfolioId === portfolioId) {
                    state.currentPortfolioId = 'default';
                    if (typeof loadPortfolioHistoryList === 'function') loadPortfolioHistoryList();
                    if (typeof loadPortfolio === 'function') loadPortfolio();
                }
            }
        } else {
            showAlert(res.message || "삭제 실패", "error");
        }
    } catch (e) {
        showAlert("이력 삭제 도중 오류가 발생했습니다.", "error");
        console.error(e);
    }
}

/**
 * 이력 리스트에서 특정 백테스트 세트를 불러와 화면에 복원 렌더링합니다.
 */
async function loadBacktestFromHistory(portfolioId) {
    const loadingBar = document.getElementById('backtest-loading-bar');
    if (loadingBar) loadingBar.style.display = 'block';

    try {
        // 통합 포트폴리오 탭으로 전환 및 로드
        state.currentPortfolioId = portfolioId;
        
        if (typeof loadPortfolioHistoryList === 'function') {
            await loadPortfolioHistoryList();
        }
        const select = document.getElementById('portfolio-select');
        if (select) select.value = portfolioId;
        
        if (typeof loadPortfolio === 'function') {
            await loadPortfolio();
        }
        
        if (typeof ViewRouter !== 'undefined' && typeof ViewRouter.navigateTo === 'function') {
            ViewRouter.navigateTo('portfolio-view');
        }
        
        showAlert("백테스트 이력을 성공적으로 불러왔습니다.", "success");
    } catch (e) {
        showAlert("이력을 가져오는 도중 오류가 발생했습니다.", "error");
        console.error(e);
    } finally {
        if (loadingBar) loadingBar.style.display = 'none';
    }
}

// 전역 window 바인딩
window.loadBacktestView = loadBacktestView;
window.runBacktest = runBacktest;
window.loadBacktestHistoryList = loadBacktestHistoryList;
window.deleteBacktestHistory = deleteBacktestHistory;
window.loadBacktestFromHistory = loadBacktestFromHistory;
