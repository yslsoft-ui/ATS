/**
 * Upbit Terminal 마켓(Market) 관리 모듈
 */

// 전역 마켓 데이터 적재 변수
let marketData = [];

/**
 * 마켓 데이터를 테이블 포맷에 맞춰 화면에 렌더링합니다.
 * @param {Array} data - 마켓 데이터 배열
 */
function renderMarketTable(data) {
    const tbody = document.getElementById('market-tbody');
    if (!tbody) return;
    tbody.innerHTML = '';
    
    // 현재 선택된 탭에 맞는 거래소 데이터만 필터링
    const filteredByExch = data.filter(c => c.exchange === state.currentMarketTab);
    
    filteredByExch.forEach((coin, idx) => {
        const ticker = coin.market;
        const exchange = coin.exchange || 'upbit';
        const symbolLower = ticker.toLowerCase();
        let iconUrl = '';
        let fallbackUrl = '';

        if (exchange === 'upbit') {
            iconUrl = `https://static.upbit.com/logos/${ticker}.png`;
        } else if (exchange === 'bithumb') {
            iconUrl = `https://resource.bithumb.com/coin/icon/${symbolLower}.png`;
            fallbackUrl = `https://static.upbit.com/logos/${ticker.toUpperCase()}.png`;
        }

        const rate = coin.signed_change_rate * 100;
        const rateClass = rate >= 0 ? 'bull' : 'bear';
        const rateStr = (rate >= 0 ? '+' : '') + rate.toFixed(2) + '%';

        const tr = document.createElement('tr');
        tr.className = 'market-row';
        tr.innerHTML = `
            <td class="rank">${idx + 1}</td>
            <td class="coin-cell">
                <img src="${iconUrl}" alt="${ticker}" class="coin-icon"
                     onerror="if(this.src !== '${fallbackUrl}' && '${fallbackUrl}') { this.src='${fallbackUrl}'; } else { this.style.display='none'; }">
                <div class="coin-names">
                    <span class="coin-kr">${coin.korean_name}</span>
                    <span class="coin-code">${ticker}</span>
                </div>
            </td>
            <td class="num">${formatPrice(coin.trade_price)}</td>
            <td class="num ${rateClass}">${rateStr}</td>
            <td class="num">${formatPrice(coin.high_price)}</td>
            <td class="num">${formatPrice(coin.low_price)}</td>
            <td class="num secondary">${formatVolume(coin.acc_trade_price_24h)}</td>
        `;

        // 클릭 시 모니터링 페이지로 전환
        tr.addEventListener('click', () => {
            Store.update({
                currentExchange: coin.exchange || 'upbit',
                currentSymbol: coin.market
            });

            // 모니터링 메뉴로 전환
            ViewRouter.navigateTo('monitoring-view');
        });
        tbody.appendChild(tr);
    });
    
    const countEl = document.getElementById('market-count');
    if (countEl) countEl.innerText = `${filteredByExch.length} 종목`;
}

/**
 * 서버에서 전체 마켓 데이터를 비동기로 로드하고 테이블에 렌더링합니다.
 */
async function loadMarket() {
    const tbody = document.getElementById('market-tbody');
    if (!tbody) return;
    tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;padding:30px;">&#x23F3; 데이터 로딩 중...</td></tr>';
    try {
        marketData = await APIClient.fetchMarketData();
        const countEl = document.getElementById('market-count');
        if (countEl) countEl.innerText = `${marketData.length}종목`;
        renderMarketTable(marketData);
        // 초기 로드시 헤더 정보 업데이트
        updateHeaderInfo(state.currentExchange, state.currentSymbol);
    } catch (e) {
        tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;">&#x26A0;&#xFE0F; 데이터 로드 실패</td></tr>';
    }
}

/**
 * 서버에서 거래 가능한 심볼 목록을 가져와 모니터링 드롭다운 메뉴를 초기화합니다.
 */
async function loadSymbols() {
    try {
        const symbols = await APIClient.fetchSymbols();
        const select = document.getElementById('symbol-select');
        if (!select) return;
        select.innerHTML = '';
        
        symbols.forEach(symObj => {
            // 한글명 매핑 저장
            if (window.state) {
                if (!window.state.symbolNames) window.state.symbolNames = {};
                window.state.symbolNames[`${symObj.exchange}:${symObj.symbol}`] = symObj.name;
            }
            const opt = document.createElement('option');
            opt.value = `${symObj.exchange}:${symObj.symbol}`;
            opt.textContent = symObj.name || symObj.symbol;
            if (symObj.symbol === state.currentSymbol && symObj.exchange === state.currentExchange) opt.selected = true;
            select.appendChild(opt);
        });
    } catch (e) { 
        console.error("Symbol list load failed", e); 
    }
}


/**
 * 마켓 탭 버튼의 클릭 이벤트를 바인딩하여 빗썸/업비트/KIS 거래소 전환을 지원합니다.
 */
function initMarketTabs() {
    const tabs = document.querySelectorAll('.market-tab');
    tabs.forEach(tab => {
        tab.addEventListener('click', () => {
            tabs.forEach(t => t.classList.remove('active'));
            tab.classList.add('active');
            state.currentMarketTab = tab.dataset.tab;
            
            // 검색어 초기화 및 테이블 다시 그리기
            const searchInput = document.getElementById('market-search');
            if (searchInput) searchInput.value = '';
            renderMarketTable(marketData);
        });
    });
}

// 마켓 검색 실시간 이벤트 리스너 정의
document.addEventListener('DOMContentLoaded', () => {
    const searchInput = document.getElementById('market-search');
    if (searchInput) {
        searchInput.addEventListener('input', (e) => {
            const q = e.target.value.toLowerCase();
            if (!q) { 
                renderMarketTable(marketData); 
                return; 
            }
            const filtered = marketData.filter(c =>
                c.korean_name.toLowerCase().includes(q) ||
                c.market.toLowerCase().includes(q)
            );
            renderMarketTable(filtered);
        });
    }
});

// 전역 window 바인딩으로 타 파일 결합 유연성 확보
window.marketData = marketData;
window.renderMarketTable = renderMarketTable;
window.loadMarket = loadMarket;
window.loadSymbols = loadSymbols;
window.initMarketTabs = initMarketTabs;
