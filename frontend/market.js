/**
 * Upbit Terminal 마켓(Market) 관리 모듈
 */

// 전역 마켓 데이터 적재 변수
let marketData = [];
let allSymbolsCache = [];

// 최종 수집 시각 (Date 객체)
let lastMarketFetchedAt = null;

// 경과 시간 갱신 인터벌 ID
let elapsedInterval = null;

/**
 * Date 객체를 받아 현재 시간과의 차이를 'X분 Y초 전' 형식으로 반환합니다.
 */
function formatElapsedTime(date) {
    if (!date) return '';
    const diffSec = Math.floor((Date.now() - date.getTime()) / 1000);
    if (diffSec < 0) return '방금 전';
    if (diffSec < 60) return `${diffSec}초 전`;
    const min = Math.floor(diffSec / 60);
    const sec = diffSec % 60;
    if (sec === 0) return `${min}분 전`;
    return `${min}분 ${sec}초 전`;
}

/**
 * 경과 시간 표시를 1초마다 갱신합니다.
 */
function startElapsedTimer() {
    if (elapsedInterval) clearInterval(elapsedInterval);
    elapsedInterval = setInterval(() => {
        const el = document.getElementById('market-elapsed');
        if (el) el.innerText = lastMarketFetchedAt ? `(${formatElapsedTime(lastMarketFetchedAt)})` : '';
    }, 1000);
}

/**
 * 마켓 데이터를 테이블 포맷에 맞춰 화면에 렌더링합니다.
 * @param {Array} data - 마켓 데이터 배열
 */
function renderMarketTable(data) {
    const thead = document.querySelector('#market-table thead');
    const tbody = document.getElementById('market-tbody');
    if (!tbody) return;
    tbody.innerHTML = '';
    
    // thead 동적 구성
    if (thead) {
        if (state.currentMarketTab === 'kis') {
            thead.innerHTML = `
                <tr>
                    <th style="width: 65px; text-align: center;">수집</th>
                    <th style="width: 50px; text-align: center;">#</th>
                    <th>종목</th>
                    <th style="text-align:right">현재가</th>
                    <th style="text-align:right">변동률(24h)</th>
                    <th style="text-align:right">변동액(24h)</th>
                    <th style="text-align:right">고가</th>
                    <th style="text-align:right">저가</th>
                    <th style="text-align:right">거래대금(24h)</th>
                </tr>
            `;
        } else {
            thead.innerHTML = `
                <tr>
                    <th>#</th>
                    <th>종목</th>
                    <th style="text-align:right">현재가</th>
                    <th style="text-align:right">변동률(24h)</th>
                    <th style="text-align:right">변동액(24h)</th>
                    <th style="text-align:right">고가</th>
                    <th style="text-align:right">저가</th>
                    <th style="text-align:right">거래대금(24h)</th>
                </tr>
            `;
        }
    }
    
    // 현재 선택된 탭에 맞는 거래소 데이터만 필터링
    const filteredByExch = data.filter(c => c.exchange === state.currentMarketTab);
    
    filteredByExch.forEach((coin, idx) => {
        const ticker = coin.market;
        const exchange = coin.exchange || 'upbit';
        let iconUrl = '';

        if (exchange === 'upbit') {
            iconUrl = `https://static.upbit.com/logos/${ticker}.png`;
        } else if (exchange === 'bithumb') {
            iconUrl = `https://static.upbit.com/logos/${ticker.toUpperCase()}.png`;
        }

        const fallbackSvg = `data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' width='24' height='24'><circle cx='12' cy='12' r='10' fill='%231E293B' stroke='%234b5563' stroke-width='1'/><text x='50%' y='62%' font-size='9' font-family='sans-serif' font-weight='bold' fill='%2394A3B8' text-anchor='middle'>${ticker.slice(0, 3)}</text></svg>`;

        const isCollected = coin.is_collected !== false; // undefined이거나 true면 수집 중
        const checked = isCollected ? 'checked' : '';

        const rate = (coin.signed_change_rate || 0) * 100;
        const rateClass = rate >= 0 ? 'bull' : 'bear';
        const rateStr = (rate >= 0 ? '+' : '') + rate.toFixed(2) + '%';

        // 현재가 소수점 자릿수와 변동액 소수점 자릿수 동기화
        const decimals = coin.trade_price < 1 ? 4 : (coin.trade_price < 1000 ? 2 : 0);

        // 백엔드에서 제공된 전일 대비 변동 금액 직접 사용
        const changePrice = coin.change_price || 0;
        const changeSign = rate >= 0 ? '▲' : '▼';
        const changePriceStr = changeSign + ' ' + formatPrice(Math.abs(changePrice), decimals);

        const tr = document.createElement('tr');
        tr.className = 'market-row';

        const tradePriceText = !isCollected ? '-' : formatPrice(coin.trade_price, decimals);
        const rateText = !isCollected ? '-' : rateStr;
        const changePriceText = !isCollected ? '-' : changePriceStr;
        const highPriceText = !isCollected ? '-' : formatPrice(coin.high_price);
        const lowPriceText = !isCollected ? '-' : formatPrice(coin.low_price);
        const volumeText = !isCollected ? '-' : formatVolume(coin.acc_trade_price_24h);

        let trHtml = '';
        if (state.currentMarketTab === 'kis') {
            trHtml += `
                <td style="text-align: center; width: 65px;" class="checkbox-cell">
                    <div class="collect-checkbox-wrapper">
                        <input type="checkbox" class="collect-checkbox" data-code="${ticker}" data-name="${coin.korean_name}" ${checked}>
                    </div>
                </td>
            `;
        }

        trHtml += `
            <td class="rank">${idx + 1}</td>
            <td class="coin-cell">
                <img src="${iconUrl}" alt="${ticker}" class="coin-icon">
                <div class="coin-names">
                    <span class="coin-kr">${coin.korean_name}</span>
                    <span class="coin-code">${ticker}</span>
                </div>
            </td>
            <td class="num">${tradePriceText}</td>
            <td class="num ${!isCollected ? '' : rateClass}">${rateText}</td>
            <td class="num ${!isCollected ? '' : rateClass}">${changePriceText}</td>
            <td class="num">${highPriceText}</td>
            <td class="num">${lowPriceText}</td>
            <td class="num secondary">${volumeText}</td>
        `;
        tr.innerHTML = trHtml;

        // 이미지 로드 에러 시 안전하게 SVG 텍스트 대체
        const img = tr.querySelector('.coin-icon');
        if (img) {
            img.onerror = () => {
                img.onerror = null;
                img.src = fallbackSvg;
            };
        }

        // 클릭 시 모니터링 페이지로 전환
        tr.addEventListener('click', () => {
            if (state.currentMarketTab === 'kis' && !isCollected) {
                showToast("수집 중이 아닌 종목은 모니터링할 수 없습니다. 수집을 먼저 시작하십시오.", "warning");
                return;
            }
            Store.update({
                currentExchange: coin.exchange || 'upbit',
                currentSymbol: coin.market
            });

            // 모니터링 메뉴로 전환
            ViewRouter.navigateTo('monitoring-view');
        });

        // 체크박스 영역 클릭 시 tr 클릭 이벤트 전파 차단
        const checkboxCell = tr.querySelector('.checkbox-cell');
        if (checkboxCell) {
            checkboxCell.addEventListener('click', (e) => {
                e.stopPropagation();
            });
        }

        const checkbox = tr.querySelector('.collect-checkbox');
        if (checkbox) {
            checkbox.addEventListener('click', (e) => {
                e.stopPropagation();
            });
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

                    // 약간의 딜레이 후 강제 리로드하여 화면 갱신
                    setTimeout(() => {
                        loadMarket(true);
                    }, 500);

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
    
    const countEl = document.getElementById('market-count');
    if (countEl) countEl.innerText = `${filteredByExch.length} 종목`;
}

/**
 * 서버에서 전체 마켓 데이터를 비동기로 로드하고 테이블에 렌더링합니다.
 */
async function loadMarket(force = false) {
    const tbody = document.getElementById('market-tbody');
    if (!tbody) return;

    // 5초 캐시 가드 (새로고침 버튼 클릭이나 강제 갱신이 아니면 캐시 재사용)
    if (!force && lastMarketFetchedAt && (Date.now() - lastMarketFetchedAt.getTime() < 5000)) {
        renderMarketTable(marketData);
        return;
    }

    tbody.innerHTML = '<tr><td colspan="8" style="text-align:center;padding:30px;">&#x23F3; 데이터 로딩 중...</td></tr>';
    try {
        const res = await APIClient.fetchMarketData();
        marketData = res.tickers || [];
        window.marketData = marketData; // 타 모듈(app.js 등)에서 참조 가능하도록 전역 노출

        const countEl = document.getElementById('market-count');
        if (countEl) countEl.innerText = `${marketData.length}종목`;
        
        // 레이턴시 정보 및 최종 갱신 시간 렌더링
        const updateLatency = (id, val) => {
            const el = document.getElementById(id);
            if (el) el.innerText = val !== undefined ? val : '-';
        };
        const lat = res.latency || {};
        updateLatency('latency-upbit', lat.upbit);
        updateLatency('latency-bithumb', lat.bithumb);
        updateLatency('latency-kis', lat.kis);
        
        const timeEl = document.getElementById('market-last-updated');
        if (timeEl) timeEl.innerText = res.timestamp || '-';

        // 경과 시간 실시간 갱신
        if (res.timestamp) {
            // 서버 타임스탬프 'YYYY-MM-DD HH:MM:SS' → Date 객체 (로컬 시각으로 파싱)
            lastMarketFetchedAt = new Date(res.timestamp.replace(' ', 'T'));
            const elEl = document.getElementById('market-elapsed');
            if (elEl) elEl.innerText = `(${formatElapsedTime(lastMarketFetchedAt)})`;
            startElapsedTimer();
        }

        renderMarketTable(marketData);
        // 초기 로드시 헤더 정보 업데이트
        updateHeaderInfo(state.currentExchange, state.currentSymbol);
    } catch (e) {
        tbody.innerHTML = '<tr><td colspan="8" style="text-align:center;">&#x26A0;&#xFE0F; 데이터 로드 실패</td></tr>';
    }
}



/**
 * 서버에서 거래 가능한 심볼 목록을 가져와 모니터링 드롭다운 메뉴를 초기화합니다.
 */
async function loadSymbols() {
    try {
        const symbols = await APIClient.fetchSymbols();
        allSymbolsCache = symbols || [];
        
        // 한글 종목명 매핑은 select 드롭다운 존재 유무와 관계없이 항상 수행
        symbols.forEach(symObj => {
            if (window.state) {
                if (!window.state.symbolNames) window.state.symbolNames = {};
                window.state.symbolNames[`${symObj.exchange_id}:${symObj.symbol}`] = symObj.name;
            }
        });

        const select = document.getElementById('symbol-select');
        if (select) {
            select.innerHTML = '';
            symbols.forEach(symObj => {
                const opt = document.createElement('option');
                opt.value = `${symObj.exchange_id}:${symObj.symbol}`;
                opt.textContent = symObj.name || symObj.symbol;
                if (symObj.symbol === state.currentSymbol && symObj.exchange_id === state.currentExchange) opt.selected = true;
                select.appendChild(opt);
            });
        }
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

// 마켓 검색 및 수동 새로고침 실시간 이벤트 리스너 정의
document.addEventListener('DOMContentLoaded', () => {
    if (typeof initMarketTabs === 'function') initMarketTabs();
    const searchInput = document.getElementById('market-search');
    if (searchInput) {
        searchInput.addEventListener('input', (e) => {
            const q = e.target.value.toLowerCase();
            if (!q) { 
                renderMarketTable(marketData); 
                return; 
            }
            let filtered = marketData.filter(c =>
                c.korean_name.toLowerCase().includes(q) ||
                c.market.toLowerCase().includes(q)
            );
            
            // KIS 탭이고 검색어가 입력되었을 때, 미수집 종목도 검색 결과에 포함
            if (state.currentMarketTab === 'kis') {
                const activeCodes = new Set(marketData.filter(c => c.exchange === 'kis').map(c => c.market));
                const nonActiveKisSymbols = allSymbolsCache.filter(symObj => 
                    symObj.exchange_id === 'kis' && 
                    !activeCodes.has(symObj.symbol) &&
                    (symObj.name.toLowerCase().includes(q) || symObj.symbol.toLowerCase().includes(q))
                );
                
                const extraItems = nonActiveKisSymbols.map(symObj => ({
                    exchange: 'kis',
                    market: symObj.symbol,
                    korean_name: symObj.name,
                    trade_price: 0,
                    signed_change_rate: 0,
                    change_price: 0,
                    high_price: 0,
                    low_price: 0,
                    acc_trade_price_24h: 0,
                    is_collected: false // 비활성화 마킹
                }));
                
                filtered = [...filtered, ...extraItems];
            }
            
            renderMarketTable(filtered);
        });
    }

    const refreshBtn = document.getElementById('btn-market-refresh');
    if (refreshBtn) {
        refreshBtn.addEventListener('click', () => loadMarket(true));
    }

});


// 전역 window 바인딩으로 타 파일 결합 유연성 확보
window.marketData = marketData;
window.renderMarketTable = renderMarketTable;
window.loadMarket = loadMarket;
window.loadSymbols = loadSymbols;
window.initMarketTabs = initMarketTabs;

if (typeof ViewRouter !== 'undefined') {
    ViewRouter.registerRoute('market-view', () => {
        if (typeof exitExplorerMode === 'function') exitExplorerMode();
        loadMarket();
    });
}

