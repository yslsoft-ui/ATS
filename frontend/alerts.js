/**
 * Upbit Terminal 알림(Alerts) 시스템 모듈
 */

/**
 * DB 또는 서버에서 최신 알림 기록 목록을 불러옵니다.
 * @param {boolean} silent - 로딩 중 메시지 노출 생략 여부
 */
async function loadAlertHistory(silent = false) {
    const tbody = document.getElementById('alert-tbody');
    if (!tbody) return;

    if (!silent) {
        tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;padding:30px;">&#x23F3; 알림 기록 로딩 중...</td></tr>';
    }

    try {
        const alerts = await APIClient.fetchAlertHistory();
        
        // 로컬 알림 기록 전체 보관
        state.alertHistory = alerts;

        const countEl = document.getElementById('alert-count');
        if (countEl) countEl.innerText = `${alerts.length}개 기록`;

        renderAlerts();
    } catch (e) {
        tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;">&#x26A0;&#xFE0F; 알림 기록 로드 실패</td></tr>';
    }
}

/**
 * 로컬에 보관된 알림 내역을 필터에 맞춰 화면에 렌더링합니다.
 */
function renderAlerts() {
    const tbody = document.getElementById('alert-tbody');
    if (!tbody) return;
    tbody.innerHTML = '';

    // 현재 필터 상태에 따라 필터링
    let filteredAlerts = state.alertHistory;
    if (state.alertFilter === 'high') {
        filteredAlerts = state.alertHistory.filter(a => a.alert_type === 'trade' || a.alert_type === 'skip');
    }

    if (filteredAlerts.length === 0) {
        tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;padding:20px;color:rgba(255,255,255,0.4);">알림 내역이 없습니다.</td></tr>';
        return;
    }

    filteredAlerts.forEach(alert => {
        addAlertToTable(alert, false);
    });
}

/**
 * 단일 알림 아이템을 알림 기록 테이블에 동적으로 추가합니다.
 * @param {object} alert - 알림 데이터
 * @param {boolean} prepend - 테이블 맨 위에 추가할지 여부
 */
function addAlertToTable(alert, prepend = true) {
    const tbody = document.getElementById('alert-tbody');
    if (!tbody) return;

    const alertType = alert.alert_type || alert.type || 'detect';
    
    // 고신호 모드이고 감지 알림(detect)인 경우, 테이블 렌더링 스킵 (단, 로컬 기록에는 보관)
    if (state.alertFilter === 'high' && alertType === 'detect') {
        return;
    }

    const tr = document.createElement('tr');
    tr.className = 'market-row';
    if (prepend) {
        tr.classList.add('new-alert-row');
    }

    // 알림 유형별 배지 정보
    let badgeHtml = '';
    if (alertType === 'trade') {
        badgeHtml = `<span class="alert-type-badge badge-trade">TRADE</span>`;
    } else if (alertType === 'skip') {
        badgeHtml = `<span class="alert-type-badge badge-skip">SKIP</span>`;
    } else {
        badgeHtml = `<span class="alert-type-badge badge-detect">DETECT</span>`;
    }

    // 가격 렌더링 보완
    const priceStr = typeof alert.price === 'number' ? alert.price.toLocaleString() : (alert.price || '0');
    
    // 변동률 및 매수비중 렌더링
    const changeRate = alert.change || 0;
    const changeClass = changeRate >= 0 ? 'bull' : 'bear';
    const changeSign = changeRate >= 0 ? '+' : '';
    const changeStr = typeof changeRate === 'number' ? `${changeSign}${changeRate.toFixed(2)}%` : changeRate;

    tr.innerHTML = `
        <td>${new Date(alert.timestamp || Date.now()).toLocaleTimeString()}</td>
        <td><strong>${alert.symbol || alert.code || ''}</strong></td>
        <td class="num">${priceStr}</td>
        <td class="num ${changeClass}">${changeStr}</td>
        <td class="num">${alert.buy_ratio || 0}%</td>
        <td>
            <div style="display:flex; align-items:flex-start; gap: 8px;">
                ${badgeHtml}
                <span style="font-size:0.88rem; line-height: 1.4; word-break: break-all; white-space: normal;">${alert.msg || ''}</span>
            </div>
        </td>
    `;

    tr.addEventListener('click', () => {
        const symbol = alert.symbol || alert.code;
        const exchange = alert.exchange_id || 'upbit';
        
        // 1. 상태 변경 전 마커 설정 (loadHistory에서 활용하도록)
        state.alertMarkerTs = (alert.timestamp || Date.now()) / 1000;
        
        // 2. 스토어 상태 변경 -> 반응형 로드 수행
        Store.update({
            currentExchange: exchange,
            currentSymbol: symbol
        });

        // 3. 뷰 전환 및 드릴다운 실행
        ViewRouter.navigateTo('monitoring-view');

        drillDown((alert.timestamp || Date.now()) / 1000);
    });

    if (prepend) {
        tbody.prepend(tr);
        if (tbody.children.length > 100) tbody.lastChild.remove();
    } else {
        tbody.appendChild(tr);
    }
}

/**
 * 알림 뷰의 신호 강도 필터('high' / 'all')를 설정합니다.
 * @param {string} filterType - 필터 타입
 */
function setAlertFilter(filterType) {
    state.alertFilter = filterType;
    
    // 버튼 스타일 토글
    const btnHigh = document.getElementById('alert-filter-high');
    const btnAll = document.getElementById('alert-filter-all');
    if (btnHigh && btnAll) {
        if (filterType === 'high') {
            btnHigh.classList.add('active');
            btnAll.classList.remove('active');
        } else {
            btnAll.classList.add('active');
            btnHigh.classList.remove('active');
        }
    }

    renderAlerts();
}

/**
 * 실시간으로 감지된 알림을 우측 하단 푸시 팝업으로 사용자에게 보여줍니다.
 * @param {object} alert - 알림 데이터
 */
function showAlert(alert) {
    if (ViewRouter.getActiveView() === 'alert-view') {
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
            <strong>${alert.code || ''}</strong> 종목이 급등 중입니다!
        </div>
        <div class="alert-footer">
            <span>변동: +${alert.change || 0}%</span>
            <span>매수비중: ${alert.buy_ratio || 0}%</span>
        </div>
    `;

    card.onclick = () => {
        // 1. 상태 변경 전 탐색 변수 초기화
        state.alertMarkerTs = null;

        // 2. 스토어 상태 변경 -> 반응형 구독에 의해 자동 리로드 실행됨
        Store.update({
            currentExchange: alert.exchange_id || 'upbit',
            currentSymbol: alert.code || ''
        });

        ViewRouter.navigateTo('monitoring-view');

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

/**
 * 실시간 우측 하단 푸시 알림 팝업 창의 사용 여부를 토글합니다.
 */
function toggleAlerts() {
    state.isAlertEnabled = !state.isAlertEnabled;
    const btn = document.getElementById('btn-toggle-alerts');
    if (btn) {
        btn.innerText = state.isAlertEnabled ? '🔔 알림 팝업: ON' : '🔕 알림 팝업: OFF';
        btn.className = `btn sm ${state.isAlertEnabled ? 'primary' : ''}`;
    }
}

/**
 * 서버에 저장된 모든 급등 알림 기록을 영구적으로 삭제합니다.
 */
async function clearAlertHistory() {
    if (!confirm("정말로 모든 알림 내역을 삭제하시겠습니까? 이 작업은 되돌릴 수 없습니다.")) {
        return;
    }

    try {
        const data = await APIClient.clearAlertHistory();
        loadAlertHistory(); // 목록 갱신
        alert(data.message);
    } catch (e) {
        alert("내역 삭제 실패");
    }
}

// 전역 window 바인딩으로 HTML 및 타 JS 파일과의 호환성 유지
window.loadAlertHistory = loadAlertHistory;
window.renderAlerts = renderAlerts;
window.addAlertToTable = addAlertToTable;
window.setAlertFilter = setAlertFilter;
window.showAlert = showAlert;
window.toggleAlerts = toggleAlerts;
window.clearAlertHistory = clearAlertHistory;

/**
 * 상장 및 상장폐지 예정 이벤트를 확인하여 대시보드 상단에 고정형 배너를 노출합니다.
 */
async function checkUpcomingAssetEvents() {
    try {
        const events = await APIClient.fetchPlannedEvents('PLANNED');
        if (!events || events.length === 0) {
            const existing = document.getElementById('planned-events-banner');
            if (existing) existing.remove();
            return;
        }

        const existing = document.getElementById('planned-events-banner');
        if (existing) existing.remove();

        const eventTexts = events.map(ev => {
            const typeKo = ev.event_type === 'listing' ? '상장' : '상장폐지';
            const exchKo = ev.exchange_id === 'bithumb' ? '빗썸' : (ev.exchange_id === 'upbit' ? '업비트' : '한국투자증권');
            return `[${exchKo}] ${ev.symbol}(${ev.korean_name}) ${typeKo} 예정 (${ev.scheduled_at})`;
        });

        const banner = document.createElement('div');
        banner.id = 'planned-events-banner';
        banner.className = 'planned-events-banner';
        banner.innerHTML = `
            <div class="banner-content">
                <span class="banner-icon">🔔</span>
                <span class="banner-text"><strong>상장/상장폐지 일정 안내:</strong> ${eventTexts.join(' | ')}</span>
            </div>
            <button class="banner-close-btn" onclick="document.getElementById('planned-events-banner').remove()">&times;</button>
        `;

        document.body.prepend(banner);
    } catch (e) {
        console.error("[checkUpcomingAssetEvents] Failed to fetch or render planned events:", e);
    }
}

window.checkUpcomingAssetEvents = checkUpcomingAssetEvents;

if (typeof ViewRouter !== 'undefined') {
    ViewRouter.registerRoute('alert-view', () => {
        loadAlertHistory();
    });
}

