/**
 * 복원 캔들 이력 조회 및 렌더링 모듈
 */

/**
 * 백엔드에서 누락 및 복원된 캔들 정보를 호출하여 테이블로 시각화합니다.
 */
async function loadRestoredCandles() {
    const tbody = document.getElementById('restored-tbody');
    if (!tbody) return;

    tbody.innerHTML = '<tr><td colspan="10" style="text-align:center;padding:30px;color:rgba(255,255,255,0.4);">&#x1F4CA; 복원된 캔들 정보를 조회 중입니다...</td></tr>';

    const rangeSelect = document.getElementById('restored-range-select');
    if (!rangeSelect) return;

    const range = parseInt(rangeSelect.value) || 60;

    try {
        // exchange와 symbol을 null로 주입하여 전체 데이터를 조회해옵니다.
        const data = await APIClient.fetchRestoredCandles(null, null, range);
        tbody.innerHTML = '';

        if (!data || data.length === 0) {
            tbody.innerHTML = '<tr><td colspan="10" style="text-align:center;padding:40px;color:var(--text-secondary);">&#x2705; 최근 설정 범위 동안 DB에서 누락/복원된 캔들이 없습니다.</td></tr>';
            return;
        }

        data.forEach((c, idx) => {
            const tr = document.createElement('tr');
            const dateStr = new Date(c.timestamp * 1000).toLocaleString();

            // 거래소 배지 생성
            let badgeStyle = '';
            if (c.exchange === 'upbit') badgeStyle = 'background: #1e88e5; color: #ffffff;';
            else if (c.exchange === 'bithumb') badgeStyle = 'background: #f57c00; color: #ffffff;';
            else if (c.exchange === 'kis') badgeStyle = 'background: #e53935; color: #ffffff;';
            else badgeStyle = 'background: #546e7a; color: #ffffff;';
            const exBadge = `<span class="badge" style="font-size: 0.75rem; padding: 2px 8px; border-radius: 4px; font-weight: bold; ${badgeStyle}">${c.exchange.toUpperCase()}</span>`;

            // 한글 코인명 매핑 및 셀 데이터 구성
            const nameKey = `${c.exchange}:${c.symbol}`;
            const coinName = (state.symbolNames && state.symbolNames[nameKey]) ? state.symbolNames[nameKey] : c.symbol;
            const nameCell = `<span style="font-weight: bold; color: #F8FAFC;">${coinName}</span> <span style="font-size: 0.75rem; color: #94A3B8; font-family: 'Roboto Mono', monospace;">(${c.symbol})</span>`;

            tr.innerHTML = `
                <td style="text-align: center; color: var(--text-secondary);">${idx + 1}</td>
                <td style="text-align: center;">${exBadge}</td>
                <td style="text-align: left;">${nameCell}</td>
                <td style="color: var(--accent-color); font-weight: bold;">${dateStr}</td>
                <td class="num">${formatPrice(c.open)}</td>
                <td class="num bull">${formatPrice(c.high)}</td>
                <td class="num bear">${formatPrice(c.low)}</td>
                <td class="num">${formatPrice(c.close)}</td>
                <td class="num secondary">${formatPrice(c.volume)}</td>
                <td style="text-align: center;"><span class="restored-tick-count-badge">${c.tick_count}</span></td>
            `;
            tbody.appendChild(tr);
        });
    } catch (e) {
        tbody.innerHTML = '<tr><td colspan="10" style="text-align:center;padding:30px;color:var(--bull-color);">&#x26A0;&#xFE0F; 복원 캔들 조회 실패</td></tr>';
    }
}

// 전역 window 바인딩
window.loadRestoredCandles = loadRestoredCandles;

if (typeof ViewRouter !== 'undefined') {
    ViewRouter.registerRoute('restored-view', () => {
        if (typeof exitExplorerMode === 'function') exitExplorerMode();
        loadRestoredCandles();
    });
}

