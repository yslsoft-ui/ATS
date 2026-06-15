/**
 * KIS 순위분석 및 토글 관련 기능 구현 모듈
 */
let isRankingLoading = false;
let activeRankingTrId = ''; // 현재 선택된 순위 TR_ID
let rankingTypesCache = [];  // 순위 유형 목록 캐시

/**
 * KIS 순위 유형 카드들을 로드하여 렌더링합니다.
 */
async function loadRankingView() {
    const cardsContainer = document.getElementById('ranking-cards-container');
    if (!cardsContainer) return;
    
    cardsContainer.innerHTML = '<div style="color: #94A3B8; padding: 20px; text-align: center; grid-column: 1/-1;">순위 유형을 불러오는 중...</div>';
    
    try {
        const types = await APIClient.fetchRankingTypes();
        rankingTypesCache = types || [];
        cardsContainer.innerHTML = '';
        
        if (rankingTypesCache.length === 0) {
            cardsContainer.innerHTML = '<div style="color: #94A3B8; padding: 20px; text-align: center; grid-column: 1/-1;">불러온 순위 유형이 없습니다.</div>';
            return;
        }
        
        // 카드 목록 생성
        rankingTypesCache.forEach((item, index) => {
            const card = document.createElement('div');
            card.className = 'ranking-card';
            card.dataset.trId = item.tr_id;
            
            // HSL 색상을 생성해서 각 카드에 개성 있는 다크 색조 부여
            const hue = (index * (360 / rankingTypesCache.length)) % 360;
            card.style.background = `linear-gradient(135deg, hsl(${hue}, 20%, 15%) 0%, #1e293b 100%)`;
            
            card.innerHTML = `
                <div class="ranking-card-title">${item.title}</div>
                <div class="ranking-card-desc">${item.description}</div>
                <div class="ranking-card-badge">${item.tr_id}</div>
            `;
            
            card.addEventListener('click', () => {
                selectRankingCard(item.tr_id, item.title);
            });
            
            cardsContainer.appendChild(card);
        });
        
        // 첫 번째 카드를 자동으로 선택하여 데이터 로드
        if (rankingTypesCache.length > 0) {
            selectRankingCard(rankingTypesCache[0].tr_id, rankingTypesCache[0].title);
        }
    } catch (e) {
        cardsContainer.innerHTML = `<div style="color: #FF4B4B; padding: 20px; text-align: center; grid-column: 1/-1;">순위 유형 로드 실패: ${e.message}</div>`;
    }
}

/**
 * 카드를 클릭했을 때 해당 순위를 활성화하고 결과를 조회합니다.
 */
function selectRankingCard(trId, title) {
    activeRankingTrId = trId;
    
    // UI 액티브 상태 표시 변경
    const cards = document.querySelectorAll('.ranking-card');
    cards.forEach(c => {
        if (c.dataset.trId === trId) {
            c.classList.add('active');
        } else {
            c.classList.remove('active');
        }
    });
    
    const titleEl = document.getElementById('ranking-active-title');
    if (titleEl) {
        titleEl.innerText = ` - ${title}`;
    }
    
    // 순위 결과 조회
    loadRankingResult(trId);
}

/**
 * 특정 TR_ID에 대한 순위 상세 결과를 API로 조회하여 테이블에 렌더링합니다.
 */
async function loadRankingResult(trId) {
    if (isRankingLoading) return;
    
    const thead = document.getElementById('ranking-thead');
    const tbody = document.querySelector('#ranking-table tbody');
    if (!tbody) return;
    
    isRankingLoading = true;
    
    tbody.innerHTML = `<tr><td colspan="12" style="text-align: center; color: #94A3B8; padding: 40px;">📊 순위 분석 데이터 로드 중...</td></tr>`;
    
    try {
        const responseData = await APIClient.fetchRankingResult(trId);
        const columns = responseData.columns || [];
        const results = responseData.data || [];
        tbody.innerHTML = '';
        
        // <thead> 동적 구성
        if (thead) {
            let theadHtml = `
                <tr>
                    <th style="width: 65px; text-align: center;">수집</th>
                    <th style="width: 60px; text-align: center;">순위</th>
                    <th style="width: 80px; text-align: center;">코드</th>
                    <th style="text-align: left; width: 180px;">종목명</th>
            `;
            
            columns.forEach(col => {
                let align = 'center';
                if (col.type === 'price' || col.type === 'integer' || col.type === 'percent' || col.type === 'rate') {
                    align = 'right';
                }
                theadHtml += `<th style="text-align: ${align}; white-space: nowrap;">${col.name}</th>`;
            });
            
            theadHtml += `</tr>`;
            thead.innerHTML = theadHtml;
        }
        
        const totalColSpan = 4 + columns.length;
        if (!results || results.length === 0) {
            tbody.innerHTML = `<tr><td colspan="${totalColSpan}" style="text-align: center; color: #64748B; padding: 40px;">분석 데이터가 존재하지 않거나 KIS 통신에 실패했습니다.</td></tr>`;
            return;
        }
        
        const table = document.getElementById('ranking-table');
        if (table) {
            if (columns.length > 5) {
                table.style.minWidth = '1400px';
            } else {
                table.style.minWidth = '1200px';
            }
        }
        
        results.forEach((item, index) => {
            const tr = document.createElement('tr');
            tr.classList.add('market-row');
            
            const checked = item.is_collected ? 'checked' : '';
            
            let cellsHtml = `
                <td style="text-align: center; width: 65px;">
                    <div class="collect-checkbox-wrapper">
                        <input type="checkbox" class="collect-checkbox" data-code="${item.code}" data-name="${item.name}" ${checked}>
                    </div>
                </td>
                <td style="color: #F8FAFC; font-weight: bold; text-align: center; width: 60px;">${index + 1}</td>
                <td style="color: #94A3B8; text-align: center; width: 80px; font-family: 'Roboto Mono', monospace;">${item.code}</td>
                <td class="coin-cell" style="color: #F8FAFC; font-weight: bold; cursor: pointer; text-align: left; width: 180px;">
                    <img src="https://ssl.pstatic.net/imgstock/fn/real/logo/png/stock/Stock${item.code}.png" alt="${item.code}" class="coin-icon" style="width:24px; height:24px; border-radius:50%; background:#1E293B; flex-shrink:0;" onerror="this.onerror=null; this.src='data:image/svg+xml;utf8,<svg xmlns=\\'http://www.w3.org/2000/svg\\' viewBox=\\'0 0 24 24\\' width=\\'24\\' height=\\'24\\'><circle cx=\\'12\\' cy=\\'12\\' r=\\'10\\' fill=\\'%233B82F6\\' stroke=\\'%234b5563\\' stroke-width=\\'1\\'/><text x=\\'50%\\' y=\\'62%\\' font-size=\\'8\\' font-family=\\'sans-serif\\' font-weight=\\'bold\\' fill=\\'white\\' text-anchor=\\'middle\\'>ST</text></svg>';">
                    <span class="coin-kr">${item.name}</span>
                </td>
            `;
            
            columns.forEach(col => {
                const rawVal = item.raw ? item.raw[col.key] : null;
                const formatted = formatValueByType(rawVal, col, item.raw || {});
                
                let align = 'center';
                if (col.type === 'price' || col.type === 'integer' || col.type === 'percent' || col.type === 'rate') {
                    align = 'right';
                }
                
                cellsHtml += `<td style="text-align: ${align}; white-space: nowrap;">${formatted}</td>`;
            });
            
            tr.innerHTML = cellsHtml;
            
            const nameTd = tr.querySelector('.coin-cell');
            if (nameTd) {
                nameTd.addEventListener('click', () => {
                    Store.update({
                        currentExchange: 'kis',
                        currentSymbol: item.code
                    });
                    ViewRouter.navigateTo('monitoring-view');
                });
            }
            
            const checkbox = tr.querySelector('.collect-checkbox');
            if (checkbox) {
                checkbox.addEventListener('change', async (e) => {
                    const code = e.target.dataset.code;
                    const name = e.target.dataset.name;
                    const isChecked = e.target.checked;
                    
                    const actionText = isChecked ? '수집 시작' : '수집 해제';
                    const confirmMsg = `${name} (${code}) ${actionText}을 진행하시겠습니까?`;
                    
                    if (!confirm(confirmMsg)) {
                        e.target.checked = !isChecked; // 체크 상태 원복
                        return;
                    }
                    
                    try {
                        const result = await APIClient.toggleKisSymbol(code, name, isChecked);
                        const statusMsg = result.is_collected ? '수집 등록 완료' : '수집 해제 완료';
                        
                        showToast(`${name} (${code}) ${statusMsg}`, result.is_collected ? 'success' : 'info');
                        
                        if (window.updateCollectorStatus) {
                            window.updateCollectorStatus();
                        }
                    } catch (err) {
                        e.target.checked = !isChecked;
                        showToast(`수집 변경 실패: ${err.message}`, 'error');
                    }
                });
            }
            
            tbody.appendChild(tr);
        });
    } catch (e) {
        tbody.innerHTML = `<tr><td colspan="12" style="text-align: center; color: #FF4B4B; padding: 40px;">조회 실패: ${e.message}</td></tr>`;
    } finally {
        isRankingLoading = false;
    }
}

/**
 * 순위분석 탭 관련 이벤트 바인딩
 */
function initRankingControls() {
    document.getElementById('ranking-refresh-btn')?.addEventListener('click', () => {
        if (activeRankingTrId) {
            loadRankingResult(activeRankingTrId);
        }
    });
}

// 전역 window 바인딩으로 격리된 모듈과의 연동 보장
window.loadRankingView = loadRankingView;
window.loadRankingResult = loadRankingResult;
window.selectRankingCard = selectRankingCard;
window.initRankingControls = initRankingControls;

if (typeof ViewRouter !== 'undefined') {
    ViewRouter.registerRoute('ranking-view', () => {
        if (typeof exitExplorerMode === 'function') exitExplorerMode();
        loadRankingView();
    });
}

document.addEventListener('DOMContentLoaded', () => {
    if (typeof initRankingControls === 'function') initRankingControls();
});


