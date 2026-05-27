/**
 * 매매 전략 관리 및 실시간 분석 모니터링 관련 기능 구현 모듈
 */

/**
 * 전체 전략 정보를 API로 가져옵니다.
 */
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

/**
 * 수집한 전략 정보를 바탕으로 카드 리스트 및 입력 폼을 렌더링합니다.
 */
function renderStrategyCards(strategies) {
    const listEl = document.getElementById('strategy-list');
    if (!listEl) return;
    listEl.innerHTML = '';

    // 정렬 순서 정의
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

/**
 * 웹소켓으로 수신된 실시간 전략 연산 상태를 화면에 반영합니다.
 */
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

/**
 * 수정한 전략 파라미터 설정을 API를 통해 백엔드에 보관합니다.
 */
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

/**
 * 특정 전략의 실행 및 사용 유무를 API로 전환합니다.
 */
async function toggleStrategyStatus(strategyId, currentEnabled) {
    try {
        await APIClient.toggleStrategyStatus(strategyId, currentEnabled);
        loadStrategies();
    } catch (e) {
        alert("상태 변경 실패");
    }
}

// 전역 window 바인딩
window.loadStrategies = loadStrategies;
window.saveStrategyParams = saveStrategyParams;
window.toggleStrategyStatus = toggleStrategyStatus;
window.updateStrategyStatusUI = updateStrategyStatusUI;
