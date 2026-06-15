/**
 * 시스템 감사 로그 조회, 검색 및 렌더링 모듈
 */

async function loadSystemEventTypes() {
    const typeSelect = document.getElementById('system-events-type-select');
    if (!typeSelect) return;

    try {
        const types = await APIClient.fetchSystemEventTypes();
        const currentValue = typeSelect.value;
        
        // "전체 이벤트 타입" 제외한 기존 항목 초기화
        typeSelect.innerHTML = '<option value="all">전체 이벤트 타입</option>';
        
        types.forEach(t => {
            const opt = document.createElement('option');
            opt.value = t;
            opt.innerText = t;
            typeSelect.appendChild(opt);
        });

        // 기존 선택값 복구 (존재할 경우)
        if (types.includes(currentValue)) {
            typeSelect.value = currentValue;
        } else {
            typeSelect.value = 'all';
        }
    } catch (e) {
        console.error("Failed to load event types list", e);
    }
}

async function loadSystemEventLogs() {
    const tbody = document.getElementById('main-system-events-tbody');
    if (!tbody) return;

    tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;padding:30px;color:rgba(255,255,255,0.4);">&#x1F4CA; 감사 로그를 조회 중입니다...</td></tr>';

    const typeSelect = document.getElementById('system-events-type-select');
    const searchInput = document.getElementById('system-events-search');
    const limitSelect = document.getElementById('system-events-limit-select');

    const eventType = typeSelect ? typeSelect.value : 'all';
    const search = searchInput ? searchInput.value.trim() : '';
    const limit = limitSelect ? parseInt(limitSelect.value) || 50 : 50;

    try {
        const data = await APIClient.fetchSystemEventLogs(eventType, search, limit);
        tbody.innerHTML = '';

        if (!data || data.length === 0) {
            tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;padding:40px;color:var(--text-secondary);">&#x2705; 조건에 일치하는 시스템 감사 로그가 없습니다.</td></tr>';
            return;
        }

        data.forEach((event, idx) => {
            const tr = document.createElement('tr');
            
            // 이벤트 타입에 따른 커스텀 배지 스타일링
            let badgeStyle = '';
            let typeText = event.event_type;
            
            if (event.event_type === 'ASSET_LISTED') {
                const relisted = event.context && event.context.relisted;
                typeText = relisted ? '재상장 (LISTED)' : '신규상장 (LISTED)';
                badgeStyle = 'background: rgba(16, 185, 129, 0.2); color: #10B981; border: 1px solid rgba(16, 185, 129, 0.3);';
            } else if (event.event_type === 'ASSET_DELISTED') {
                typeText = '상장폐지 (DELISTED)';
                badgeStyle = 'background: rgba(239, 68, 68, 0.2); color: #EF4444; border: 1px solid rgba(239, 68, 68, 0.3);';
            } else if (event.event_type.includes('ERROR') || event.event_type.includes('FAIL') || event.event_type.includes('SUSPENDED')) {
                badgeStyle = 'background: rgba(239, 68, 68, 0.2); color: #EF4444; border: 1px solid rgba(239, 68, 68, 0.3);';
            } else if (event.event_type.includes('SUCCESS') || event.event_type.includes('RESUMED') || event.event_type.includes('APPROVED')) {
                badgeStyle = 'background: rgba(16, 185, 129, 0.2); color: #10B981; border: 1px solid rgba(16, 185, 129, 0.3);';
            } else if (event.event_type.includes('START') || event.event_type.includes('STOP') || event.event_type.includes('REQUEST')) {
                badgeStyle = 'background: rgba(59, 130, 246, 0.2); color: #3B82F6; border: 1px solid rgba(59, 130, 246, 0.3);';
            } else {
                badgeStyle = 'background: rgba(245, 158, 11, 0.2); color: #F59E0B; border: 1px solid rgba(245, 158, 11, 0.3);';
            }

            const typeBadge = `<span class="badge" style="font-size: 0.78rem; padding: 3px 8px; border-radius: 4px; font-weight: bold; display: inline-block; ${badgeStyle}">${typeText}</span>`;

            // 발생 시각 포맷팅
            const dateStr = new Date(event.timestamp).toLocaleString();

            // 대상 (Target)
            const targetEl = `<span style="font-family: 'Roboto Mono', monospace; color: #F8FAFC; font-weight: bold;">${event.target || '-'}</span>`;

            // 메시지 내의 JSON 또는 특이사항 하이라이팅
            let messageContent = event.message || '';
            if (event.context) {
                // context의 메타데이터(예: 종목명, 카테고리 등)가 있으면 메시지에 부가 정보 노출
                if (event.event_type === 'ASSET_LISTED' || event.event_type === 'ASSET_DELISTED') {
                    const cat = event.context.category ? `<span class="badge" style="background: rgba(148, 163, 184, 0.1); color: #94A3B8; font-size: 0.7rem; padding: 1px 4px; margin-left: 5px;">${event.context.category}</span>` : '';
                    messageContent += ` ${cat}`;
                }
            }

            tr.innerHTML = `
                <td style="text-align: center; color: var(--text-secondary);">${idx + 1}</td>
                <td style="text-align: center;">${typeBadge}</td>
                <td style="text-align: left;">${targetEl}</td>
                <td style="color: var(--accent-color); font-weight: bold;">${dateStr}</td>
                <td style="text-align: left; color: #E2E8F0; line-height: 1.4;">${messageContent}</td>
            `;
            tbody.appendChild(tr);
        });
    } catch (e) {
        tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;padding:30px;color:var(--bull-color);">&#x26A0;&#xFE0F; 시스템 감사 로그 조회 실패</td></tr>';
    }
}

// 초기화 함수
async function initSystemEventsView() {
    await loadSystemEventTypes();
    await loadSystemEventLogs();
}

// 이벤트 바인딩
document.addEventListener('DOMContentLoaded', () => {
    const searchInput = document.getElementById('system-events-search');
    if (searchInput) {
        searchInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                loadSystemEventLogs();
            }
        });
    }

    const typeSelect = document.getElementById('system-events-type-select');
    if (typeSelect) {
        typeSelect.addEventListener('change', loadSystemEventLogs);
    }
});

// 전역 window 바인딩
window.loadSystemEventLogs = loadSystemEventLogs;
window.initSystemEventsView = initSystemEventsView;

if (typeof ViewRouter !== 'undefined') {
    ViewRouter.registerRoute('system-events-view', () => {
        if (typeof exitExplorerMode === 'function') exitExplorerMode();
        initSystemEventsView();
    });
}
