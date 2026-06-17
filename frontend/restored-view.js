/**
 * 복원 캔들 및 고스트 캔들 이력 조회 및 렌더링 모듈
 */

let activeRestoredTab = 'missing'; // 'missing' 또는 'ghost'

/**
 * 탭을 스위칭하고 화면의 안내 및 테이블 헤더를 전환한 후 목록을 리로드합니다.
 */
function switchRestoredTab(tab) {
    if (activeRestoredTab === tab) return;
    activeRestoredTab = tab;

    // 탭 버튼 스타일 업데이트
    const btnMissing = document.getElementById('btn-tab-missing');
    const btnGhost = document.getElementById('btn-tab-ghost');
    if (btnMissing && btnGhost) {
        if (tab === 'missing') {
            btnMissing.classList.add('active');
            btnGhost.classList.remove('active');
        } else {
            btnGhost.classList.add('active');
            btnMissing.classList.remove('active');
        }
    }

    // 헤더 타이틀 및 배너 텍스트 업데이트
    const bannerTitle = document.getElementById('restored-banner-title');
    const bannerDesc = document.getElementById('restored-banner-desc');
    const bannerWarning = document.getElementById('restored-banner-warning');
    const timeHeader = document.getElementById('restored-time-header');
    const actionHeader = document.getElementById('restored-action-header');

    if (tab === 'missing') {
        if (bannerTitle) bannerTitle.innerHTML = '💡 모니터링 안내';
        if (bannerDesc) bannerDesc.innerHTML = `
            데이터베이스(<code>candles</code> 테이블)에 기록되지 않았으나, 체결 내역(<code>trades</code> 테이블)을 기반으로 임시 복원(재조립) 가능한
            1분봉 목록입니다.<br>
            이 목록에 나타나는 캔들들은 DB 적재 유실이 실제로 발생했던 구간을 나타냅니다.
        `;
        if (bannerWarning) {
            bannerWarning.style.display = 'block';
            bannerWarning.innerHTML = `
                ⚠️ <strong style="color: #94A3B8;">일시적 깜박임 현상 (정상 동작)</strong><br>
                매 분이 끝나는 순간, 체결 틱은 즉시 DB에 저장되지만 분봉 캔들은 최대 약 0.5초 후 배치로 기록됩니다.
                이 미세한 쓰기 지연(Write Lag) 구간 동안 페이지를 조회하면 방금 완성된 분봉이 잠시 누락으로 표시됐다가 사라질 수 있습니다.
                이는 데이터 손실이 아닌 정상적인 파이프라인 동작입니다.
            `;
        }
        if (timeHeader) timeHeader.innerText = '복원 시간 (Timestamp)';
        if (actionHeader) actionHeader.innerText = '조립 틱 개수';
    } else {
        if (bannerTitle) bannerTitle.innerHTML = '👻 고스트 캔들 감지 안내';
        if (bannerDesc) bannerDesc.innerHTML = `
            데이터베이스(<code>candles</code> 테이블)에는 데이터가 존재하지만, 정합성 대조를 위한 실제 체결 틱(<code>trades</code> 테이블)이 전혀 존재하지 않는 비정상 분봉 목록입니다.<br>
            수집기 오동작이나 장외 오류 데이터 적재 등으로 생성된 비정상 캔들일 가능성이 높으므로 개별 삭제를 통해 클린업할 것을 권장합니다.
        `;
        if (bannerWarning) {
            bannerWarning.style.display = 'block';
            bannerWarning.innerHTML = `
                ⚠️ <strong style="color: #FF4B4B;">고스트 캔들 개별 삭제 경고</strong><br>
                삭제를 수행하면 해당 분봉 데이터가 DB에서 즉각 영구 삭제되며 복구할 수 없습니다.
                실제 체결 데이터가 없기 때문에 재조립도 불가능합니다. 신중히 확인 후 실행해 주시기 바랍니다.
            `;
        }
        if (timeHeader) timeHeader.innerText = '발생 시간 (Timestamp)';
        if (actionHeader) actionHeader.innerText = '관리';
    }

    loadRestoredCandles();
}

/**
 * 백엔드에서 누락 및 복원된 캔들 혹은 고스트 캔들 정보를 호출하여 테이블로 시각화합니다.
 */
async function loadRestoredCandles() {
    const tbody = document.getElementById('restored-tbody');
    if (!tbody) return;

    tbody.innerHTML = `<tr><td colspan="10" style="text-align:center;padding:30px;color:rgba(255,255,255,0.4);">&#x1F4CA; ${
        activeRestoredTab === 'missing' ? '복원된 캔들' : '고스트 캔들'
    } 정보를 조회 중입니다...</td></tr>`;

    const rangeSelect = document.getElementById('restored-range-select');
    if (!rangeSelect) return;

    const range = parseInt(rangeSelect.value) || 60;

    try {
        let data = [];
        if (activeRestoredTab === 'missing') {
            data = await APIClient.fetchRestoredCandles(null, null, range);
        } else {
            data = await APIClient.fetchGhostCandles(null, null, range);
        }
        
        tbody.innerHTML = '';

        if (!data || data.length === 0) {
            tbody.innerHTML = `<tr><td colspan="10" style="text-align:center;padding:40px;color:var(--text-secondary);">&#x2705; 최근 설정 범위 동안 감지된 ${
                activeRestoredTab === 'missing' ? '누락/복원된 캔들이' : '고스트 캔들이'
            } 없습니다.</td></tr>`;
            return;
        }

        data.forEach((c, idx) => {
            const tr = document.createElement('tr');
            const dateStr = new Date(c.timestamp * 1000).toLocaleString();

            // 거래소 배지 생성
            let badgeStyle = '';
            if (c.exchange_id === 'upbit') badgeStyle = 'background: #1e88e5; color: #ffffff;';
            else if (c.exchange_id === 'bithumb') badgeStyle = 'background: #f57c00; color: #ffffff;';
            else if (c.exchange_id === 'kis') badgeStyle = 'background: #e53935; color: #ffffff;';
            else badgeStyle = 'background: #546e7a; color: #ffffff;';
            const exBadge = `<span class="badge" style="font-size: 0.75rem; padding: 2px 8px; border-radius: 4px; font-weight: bold; ${badgeStyle}">${c.exchange_id.toUpperCase()}</span>`;

            // 한글 코인명 매핑 및 셀 데이터 구성
            const nameKey = `${c.exchange_id}:${c.symbol}`;
            const coinName = (state.symbolNames && state.symbolNames[nameKey]) ? state.symbolNames[nameKey] : c.symbol;
            const nameCell = `<span style="font-weight: bold; color: #F8FAFC;">${coinName}</span> <span style="font-size: 0.75rem; color: #94A3B8; font-family: 'Roboto Mono', monospace;">(${c.symbol})</span>`;

            let actionCell = '';
            if (activeRestoredTab === 'missing') {
                actionCell = `<span class="restored-tick-count-badge">${c.tick_count}</span>`;
            } else {
                // 고스트 캔들 삭제 버튼 구성 (interval 누락 시 1분봉 기본값 60초 적용)
                const deleteParams = `'${c.exchange_id}', '${c.symbol}', ${c.interval || 60}, ${c.timestamp}`;
                actionCell = `<button class="btn sm danger" onclick="deleteGhostCandle(${deleteParams})" style="padding: 2px 10px; font-weight: bold; font-size: 0.8rem; background: #FF4B4B; border-color: #FF4B4B;">🗑️ 삭제</button>`;
            }

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
                <td style="text-align: center;">${actionCell}</td>
            `;
            tbody.appendChild(tr);
        });
    } catch (e) {
        tbody.innerHTML = `<tr><td colspan="10" style="text-align:center;padding:30px;color:var(--bull-color);">&#x26A0;&#xFE0F; ${
            activeRestoredTab === 'missing' ? '복원 캔들' : '고스트 캔들'
        } 조회 실패</td></tr>`;
    }
}

/**
 * 고스트 캔들을 개별 삭제 요청하고 목록을 리로드합니다.
 */
async function deleteGhostCandle(exchangeId, symbol, interval, timestamp) {
    const timeStr = new Date(timestamp * 1000).toLocaleString();
    const isConfirmed = confirm(`정말 이 고스트 캔들을 DB에서 삭제하시겠습니까?\n\n- 거래소: ${exchangeId.toUpperCase()}\n- 종목: ${symbol}\n- 시간: ${timeStr}\n- 분봉 간격: ${interval}분`);
    
    if (!isConfirmed) return;

    try {
        const response = await APIClient.deleteCandle(exchangeId, symbol, interval, timestamp);
        if (typeof showToast === 'function') {
            showToast(`${symbol} (${timeStr}) 고스트 캔들이 성공적으로 삭제되었습니다.`, 'success');
        } else {
            alert('고스트 캔들이 성공적으로 삭제되었습니다.');
        }
        // 목록 다시 읽기
        loadRestoredCandles();
    } catch (error) {
        console.error('고스트 캔들 삭제 오류:', error);
        if (typeof showToast === 'function') {
            showToast(`삭제 실패: ${error.message || error}`, 'danger');
        } else {
            alert(`삭제 실패: ${error.message || error}`);
        }
    }
}

// 전역 window 바인딩
window.loadRestoredCandles = loadRestoredCandles;
window.switchRestoredTab = switchRestoredTab;
window.deleteGhostCandle = deleteGhostCandle;

if (typeof ViewRouter !== 'undefined') {
    ViewRouter.registerRoute('restored-view', () => {
        if (typeof exitExplorerMode === 'function') exitExplorerMode();
        // 진입 시 누락 탭으로 기본화
        activeRestoredTab = 'missing';
        // 탭 버튼 active 클래스 초기화
        const btnMissing = document.getElementById('btn-tab-missing');
        const btnGhost = document.getElementById('btn-tab-ghost');
        if (btnMissing && btnGhost) {
            btnMissing.classList.add('active');
            btnGhost.classList.remove('active');
        }
        loadRestoredCandles();
    });
}


