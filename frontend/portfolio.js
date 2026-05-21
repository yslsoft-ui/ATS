/**
 * Upbit Terminal 포트폴리오(Portfolio) 및 실자산 관리 모듈
 */

const ASSET_COLORS = [
    '#FF6384', '#36A2EB', '#FFCE56', '#4BC0C0', '#9966FF', 
    '#FF9F40', '#C9CBCF', '#7BC225', '#FF4500', '#1E90FF'
];

/**
 * 서버에서 관리 중인 다중 포트폴리오 목록을 불러와 셀렉트 박스에 설정합니다.
 */
async function loadPortfolioList() {
    const select = document.getElementById('portfolio-select');
    if (!select) return;

    try {
        const portfolios = await APIClient.fetchPortfolioList();
        
        select.innerHTML = '';
        portfolios.forEach(p => {
            const opt = document.createElement('option');
            opt.value = p.id;
            opt.textContent = p.name;
            if (p.id === state.currentPortfolioId) opt.selected = true;
            select.appendChild(opt);
        });
    } catch (e) {
        console.error("Portfolio list load failed", e);
    }
}

/**
 * 특정 포트폴리오의 실시간 상태(캐시, 보유 종목 목록, 거래 내역 등)를 불러와 화면에 업데이트합니다.
 */
async function loadPortfolio() {
    try {
        const data = await APIClient.fetchPortfolio(state.currentPortfolioId);
        state.currentPortfolioData = data; // 전역 저장
        
        // 요약 정보 업데이트
        document.getElementById('port-total-value').innerText = data.total_value.toLocaleString();
        document.getElementById('port-cash').innerText = data.cash.toLocaleString();
        
        // 실제 원금을 기반으로 수익률 계산
        const initialValue = data.initial_cash || 10000000; // 원금이 없으면 기본값 1000만
        const totalRoi = ((data.total_value - initialValue) / initialValue * 100).toFixed(2);
        const roiEl = document.getElementById('port-total-roi');
        roiEl.innerText = `${totalRoi}%`;
        roiEl.className = `value ${totalRoi >= 0 ? 'bull' : 'bear'}`;

        // 포지션 테이블 업데이트
        const posTbody = document.getElementById('positions-tbody');
        posTbody.innerHTML = '';
        if (data.positions.length === 0) {
            posTbody.innerHTML = '<tr><td colspan="4" style="text-align:center;padding:20px;">보유 종목이 없습니다.</td></tr>';
        } else {
            data.positions.forEach(pos => {
                const tr = document.createElement('tr');
                const coin = marketData.find(c => c.market === pos.symbol);
                const currentPrice = coin ? coin.trade_price : pos.avg_price;
                const profitRate = ((currentPrice - pos.avg_price) / pos.avg_price * 100).toFixed(2);
                const rateClass = profitRate >= 0 ? 'bull' : 'bear';

                tr.innerHTML = `
                    <td><strong>${pos.symbol.replace(/^(KRW-|UPB-|KIS-)/, '')}</strong></td>
                    <td class="num">${pos.quantity.toFixed(4)}</td>
                    <td class="num">${pos.avg_price.toLocaleString()}</td>
                    <td class="num ${rateClass}">${profitRate}%</td>
                `;
                
                // 행 클릭 시 상세 모달 열기
                tr.onclick = () => showAssetDetails(pos.exchange, pos.symbol);
                
                posTbody.appendChild(tr);
            });
        }

        // 히스토리 테이블 업데이트
        const histTbody = document.getElementById('port-history-tbody');
        histTbody.innerHTML = '';
        
        if (data.history.length === 0) {
            histTbody.innerHTML = '<tr><td colspan="5" style="text-align:center;padding:20px;">거래 내역이 없습니다.</td></tr>';
        } else {
            const sortedHistory = [...data.history].reverse();
            sortedHistory.forEach(h => {
                const tr = document.createElement('tr');
                
                // Context 데이터를 배지로 변환
                let contextHtml = '';
                if (h.context) {
                    const ctx = typeof h.context === 'string' ? JSON.parse(h.context) : h.context;
                    contextHtml = Object.entries(ctx).map(([k, v]) => 
                        `<span class="ctx-badge">${k}: ${v}</span>`
                    ).join('');
                }

                tr.innerHTML = `
                    <td>${new Date(h.timestamp * 1000).toLocaleTimeString()}</td>
                    <td>${h.symbol.replace(/^(KRW-|UPB-|KIS-)/, '')}</td>
                    <td class="${h.side === 'BUY' ? 'bull' : 'bear'}">${h.side}</td>
                    <td class="num">${h.price.toLocaleString()}</td>
                    <td class="num">${h.quantity.toFixed(4)}</td>
                    <td>
                        <div class="reason-cell">
                            <span class="reason-text">${h.reason || '-'}</span>
                            <div class="context-badges">${contextHtml}</div>
                        </div>
                    </td>
                `;
                histTbody.appendChild(tr);
            });
        }

        // 자산 비중 차트 업데이트
        renderAllocationChart(data);

        // 만약 상세 모달이 열려있다면 내용 갱신
        if (state.activeAssetDetail) {
            updateModalContent(state.activeAssetDetail.exchange, state.activeAssetDetail.symbol);
        }

        // --- 다중 포트폴리오 실시간 시세 구독 핫스왑 ---
        if (state.ws && state.ws.readyState === WebSocket.OPEN) {
            const activeSymbols = new Set([`${state.currentExchange}:${state.currentSymbol}`]);
            
            data.positions.forEach(pos => {
                const exch = pos.exchange || 'upbit';
                activeSymbols.add(`${exch}:${pos.symbol}`);
            });

            activeSymbols.forEach(token => {
                const [exch, sym] = token.split(':');
                state.ws.send(JSON.stringify({ subscribe: sym, exchange: exch }));
            });
        }

    } catch (e) {
        console.error("Portfolio load failed", e);
    }
}

/**
 * SVG 도넛 차트를 활용하여 포트폴리오의 실시간 자산 비중을 렌더링합니다.
 * @param {object} data - 포트폴리오 데이터
 */
function renderAllocationChart(data) {
    const chartContainer = document.getElementById('portfolio-pie-chart');
    const legendContainer = document.getElementById('portfolio-legend');
    if (!chartContainer || !legendContainer) return;

    const assets = [];
    
    // 현금 추가
    if (data.cash > 0) {
        assets.push({ 
            symbol: null, 
            label: 'CASH', 
            koreanName: '현금',
            value: data.cash, 
            color: '#444' 
        });
    }

    // 종목 추가
    data.positions.forEach((pos, idx) => {
        const coin = (marketData || []).find(c => c.market === pos.symbol);
        const currentPrice = coin ? coin.trade_price : pos.avg_price;
        const totalValue = pos.quantity * currentPrice;
        assets.push({ 
            symbol: pos.symbol,
            label: pos.symbol.replace(/^(KRW-|UPB-|KIS-)/, ''), 
            koreanName: coin ? coin.korean_name : pos.symbol.replace(/^(KRW-|UPB-|KIS-)/, ''),
            value: totalValue,
            color: ASSET_COLORS[idx % ASSET_COLORS.length]
        });
    });

    assets.sort((a, b) => b.value - a.value);

    const total = data.total_value;
    chartContainer.innerHTML = '';
    legendContainer.innerHTML = '';

    if (total <= 0) return;

    const size = 200;
    const center = size / 2;
    const radius = 80;
    const strokeWidth = 30;
    const circumference = 2 * Math.PI * radius;
    
    let accumulatedPercent = 0;

    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", `0 0 ${size} ${size}`);
    svg.style.width = "100%";
    svg.style.height = "100%";

    const centerText = document.createElementNS("http://www.w3.org/2000/svg", "text");
    centerText.setAttribute("x", "50%");
    centerText.setAttribute("y", "48%");
    centerText.setAttribute("text-anchor", "middle");
    centerText.setAttribute("fill", "white");
    centerText.setAttribute("font-size", "14px");
    centerText.setAttribute("font-weight", "bold");
    centerText.textContent = "자산 비중";

    const centerSubText = document.createElementNS("http://www.w3.org/2000/svg", "text");
    centerSubText.setAttribute("x", "50%");
    centerSubText.setAttribute("y", "62%");
    centerSubText.setAttribute("text-anchor", "middle");
    centerSubText.setAttribute("fill", "rgba(255,255,255,0.6)");
    centerSubText.setAttribute("font-size", "11px");
    centerSubText.textContent = "Total Assets";

    assets.forEach(asset => {
        const percent = (asset.value / total);
        if (percent < 0.001) return;

        const circle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
        circle.setAttribute("cx", center);
        circle.setAttribute("cy", center);
        circle.setAttribute("r", radius);
        circle.setAttribute("fill", "transparent");
        circle.setAttribute("stroke", asset.color);
        circle.setAttribute("stroke-width", strokeWidth);
        circle.setAttribute("stroke-dasharray", `${percent * circumference} ${circumference}`);
        circle.setAttribute("stroke-dashoffset", -accumulatedPercent * circumference);
        circle.setAttribute("transform", `rotate(-90 ${center} ${center})`);
        circle.style.transition = "stroke-width 0.3s ease, stroke 0.3s ease";
        
        circle.style.cursor = 'pointer';
        circle.onclick = () => {
            if (asset.symbol) showAssetDetails(asset.symbol.includes('KIS') ? 'kis' : 'upbit', asset.symbol);
        };
        
        circle.onmouseover = () => {
            circle.setAttribute("stroke-width", strokeWidth + 10);
            centerText.textContent = asset.koreanName;
            centerSubText.textContent = `${asset.label} (${(percent * 100).toFixed(1)}%)`;
            centerText.setAttribute("fill", asset.color);
        };
        
        circle.onmouseout = () => {
            circle.setAttribute("stroke-width", strokeWidth);
            centerText.textContent = "자산 비중";
            centerSubText.textContent = "Total Assets";
            centerText.setAttribute("fill", "white");
        };

        const anim = document.createElementNS("http://www.w3.org/2000/svg", "animate");
        anim.setAttribute("attributeName", "stroke-dashoffset");
        anim.setAttribute("from", circumference);
        anim.setAttribute("to", -accumulatedPercent * circumference);
        anim.setAttribute("dur", "0.8s");
        anim.setAttribute("fill", "freeze");
        circle.appendChild(anim);

        svg.appendChild(circle);

        // 범례(Legend) 추가
        const legendItem = document.createElement('div');
        legendItem.className = 'legend-item';
        legendItem.onmouseover = () => circle.onmouseover();
        legendItem.onmouseout = () => circle.onmouseout();
        
        if (asset.symbol) {
            legendItem.style.cursor = 'pointer';
            legendItem.onclick = () => showAssetDetails(asset.symbol.includes('KIS') ? 'kis' : 'upbit', asset.symbol);
        }
        
        legendItem.innerHTML = `
            <div class="legend-color" style="background: ${asset.color}"></div>
            <div class="legend-info">
                <span class="legend-name">${asset.koreanName} <small style="color:rgba(255,255,255,0.4); font-size: 0.8em;">${asset.label}</small></span>
                <span class="legend-value">${(percent * 100).toFixed(1)}% (${Math.round(asset.value).toLocaleString()}원)</span>
            </div>
        `;
        legendContainer.appendChild(legendItem);

        accumulatedPercent += percent;
    });

    svg.appendChild(centerText);
    svg.appendChild(centerSubText);
    chartContainer.appendChild(svg);
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
 * @param {string} exchange - 거래소 고유 ID
 * @param {string} symbol - 종목 코드
 */
function showAssetDetails(exchange, symbol) {
    state.activeAssetDetail = { exchange, symbol };
    updateModalContent(exchange, symbol);
    const modal = document.getElementById('asset-modal');
    if (modal) {
        modal.style.display = 'flex';
        modal.onclick = (e) => {
            if (e.target === modal) closeAssetModal();
        };
    }
}

/**
 * 자산 상세 모달 내 데이터를 갱신합니다.
 */
function updateModalContent(exchange, symbol) {
    if (!state.currentPortfolioData) return;
    const data = state.currentPortfolioData;
    const pos = data.positions.find(p => p.exchange === exchange && p.symbol === symbol);
    if (!pos) return;

    const coin = marketData.find(c => c.exchange === exchange && c.market === symbol);
    const currentPrice = coin ? coin.trade_price : pos.avg_price;
    const profitRate = ((currentPrice - pos.avg_price) / pos.avg_price * 100).toFixed(2);
    const pnl = (currentPrice - pos.avg_price) * pos.quantity;

    // 헤더 업데이트
    document.getElementById('modal-asset-symbol').innerText = symbol;
    document.getElementById('modal-asset-name').innerText = coin ? coin.korean_name : '';

    // 지표 업데이트
    document.getElementById('modal-asset-qty').innerText = pos.quantity.toFixed(4);
    document.getElementById('modal-asset-avg').innerText = pos.avg_price.toLocaleString();
    
    const roiEl = document.getElementById('modal-asset-roi');
    roiEl.innerText = `${profitRate}%`;
    roiEl.className = `value ${profitRate >= 0 ? 'bull' : 'bear'}`;
    
    const pnlEl = document.getElementById('modal-asset-pnl');
    pnlEl.innerText = pnl.toLocaleString();
    pnlEl.className = `value ${pnl >= 0 ? 'bull' : 'bear'}`;

    // 해당 종목 거래 내역 필터링
    const modalTbody = document.getElementById('modal-history-tbody');
    if (modalTbody) {
        modalTbody.innerHTML = '';
        const assetHistory = data.history.filter(h => h.symbol === symbol).reverse();
        if (assetHistory.length === 0) {
            modalTbody.innerHTML = '<tr><td colspan="6" style="text-align:center;padding:20px;">거래 내역이 없습니다.</td></tr>';
        } else {
            assetHistory.forEach(h => {
                const tr = document.createElement('tr');
                let contextHtml = '';
                if (h.context) {
                    const ctx = typeof h.context === 'string' ? JSON.parse(h.context) : h.context;
                    contextHtml = Object.entries(ctx).map(([k, v]) => 
                        `<span class="ctx-badge">${k}: ${v}</span>`
                    ).join('');
                }

                tr.innerHTML = `
                    <td>${new Date(h.timestamp * 1000).toLocaleTimeString()}</td>
                    <td class="${h.side === 'BUY' ? 'bull' : 'bear'}">${h.side}</td>
                    <td class="num">${h.price.toLocaleString()}</td>
                    <td class="num">${h.quantity.toFixed(4)}</td>
                    <td>
                        <div class="reason-cell">
                            <span class="reason-text">${h.reason || '-'}</span>
                            <div class="context-badges">${contextHtml}</div>
                        </div>
                    </td>
                    <td class="num">${(h.price * h.quantity).toLocaleString()}</td>
                `;
                modalTbody.appendChild(tr);
            });
        }
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
        if (!data || !data.assets) {
            tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;padding:20px;color:rgba(255,255,255,0.4);">자산 내역이 비어있거나 키를 확인하세요.</td></tr>';
            return;
        }
        
        // 헤더 메트릭스 업데이트
        if (totalValueEl) totalValueEl.innerText = `${data.formatted_total_value} 원`;
        if (assetCountEl) assetCountEl.innerText = `${data.assets.length} 개 종목`;
        
        tbody.innerHTML = '';
        
        if (data.assets.length === 0) {
            tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;padding:20px;">보유 자산이 없습니다.</td></tr>';
            return;
        }
        
        data.assets.forEach(asset => {
            const tr = document.createElement('tr');
            tr.className = 'market-row';
            
            // 평가 수익률 연산
            let roiHtml = '-';
            if (asset.avg_buy_price > 0 && asset.currency !== 'KRW') {
                const roi = ((asset.current_price - asset.avg_buy_price) / asset.avg_buy_price * 100).toFixed(2);
                roiHtml = `<span class="${roi >= 0 ? 'bull' : 'bear'}">${roi >= 0 ? '+' : ''}${roi}%</span>`;
            }
            
            // 수량 정밀도 처리
            const balanceStr = asset.currency === 'KRW' 
                ? Math.floor(asset.balance).toLocaleString() 
                : asset.balance.toFixed(4);
                
            // 게이지 비주얼 바 렌더링
            const barHtml = `
                <div class="progress-bar-container">
                    <div class="progress-bar-fill" style="width: ${asset.percent}%"></div>
                    <span class="progress-bar-text">${asset.percent}%</span>
                </div>
            `;
            
            // 코인 로고 아이콘 URL 및 Fallback SVG 처리
            let iconHtml = '';
            if (asset.currency === 'KRW') {
                iconHtml = `<img src="data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' width='24' height='24'><circle cx='12' cy='12' r='10' fill='%234caf50'/><text x='50%' y='62%' font-size='10' font-family='sans-serif' font-weight='bold' fill='white' text-anchor='middle'>₩</text></svg>" style="width:24px; height:24px; border-radius:50%; flex-shrink:0;">`;
            } else {
                const iconUrl = `https://static.upbit.com/logos/${asset.currency}.png`;
                iconHtml = `<img src="${iconUrl}" style="width:24px; height:24px; border-radius:50%; background:#1E293B; flex-shrink:0;" onerror="this.onerror=null; this.src='data:image/svg+xml;utf8,<svg xmlns=\\'http://www.w3.org/2000/svg\\' viewBox=\\'0 0 24 24\\' width=\\'24\\' height=\\'24\\'><circle cx=\\'12\\' cy=\\'12\\' r=\\'10\\' fill=\\'%231E293B\\' stroke=\\'%234b5563\\' stroke-width=\\'1\\'/><text x=\\'50%\\' y=\\'62%\\' font-size=\\'9\\' font-family=\\'sans-serif\\' font-weight=\\'bold\\' fill=\\'%2394A3B8\\' text-anchor=\\'middle\\'>${asset.currency.slice(0, 3)}</text></svg>';">`;
            }
            
            tr.innerHTML = `
                <td>
                    <div style="display:flex; align-items:center; gap: 10px;">
                        ${iconHtml}
                        <div style="display:flex; flex-direction:column; line-height:1.2;">
                            <span style="font-weight:bold; color:#F8FAFC; font-size:0.9rem;">${asset.korean_name}</span>
                            <span style="font-size:0.72rem; color:#94A3B8; font-family:'Roboto Mono', monospace;">${asset.currency}</span>
                        </div>
                    </div>
                </td>
                <td class="num" style="text-align:right; font-family:'Roboto Mono', monospace;">${balanceStr}</td>
                <td class="num" style="text-align:right;">${asset.avg_buy_price > 0 ? (asset.avg_buy_price >= 100 ? Math.floor(asset.avg_buy_price).toLocaleString() : asset.avg_buy_price.toLocaleString()) : '-'}</td>
                <td class="num" style="text-align:right;">${asset.current_price > 0 ? (asset.current_price >= 100 ? Math.floor(asset.current_price).toLocaleString() : asset.current_price.toLocaleString()) : '-'}</td>
                <td class="num" style="text-align:right; font-weight:bold; color:#F8FAFC;">${asset.formatted_eval_value} 원</td>
                <td>${barHtml}</td>
            `;
            
            // 더블 클릭 시 해당 코인 차트 뷰로 즉시 연동
            tr.addEventListener('dblclick', () => {
                if (asset.currency === 'KRW') return;
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
            });
            
            tbody.appendChild(tr);
        });
    } catch (e) {
        tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;padding:20px;color:#FF4B4B;">&#x26A0;&#xFE0F; 자산 조회 실패 (API 키 권한 또는 인터넷 연결 상태를 확인하세요)</td></tr>';
        console.error("Asset load failed", e);
    }
}

// 다중 포트폴리오 선택기 변경 시 이벤트 리스너 정의
document.addEventListener('DOMContentLoaded', () => {
    const portfolioSelect = document.getElementById('portfolio-select');
    if (portfolioSelect) {
        portfolioSelect.addEventListener('change', (e) => {
            state.currentPortfolioId = e.target.value;
        });
    }
});

// 전역 window 바인딩으로 타 JS 파일 및 HTML 인라인 호출 지원
window.loadPortfolioList = loadPortfolioList;
window.loadPortfolio = loadPortfolio;
window.renderAllocationChart = renderAllocationChart;
window.executePanicSell = executePanicSell;
window.showAssetDetails = showAssetDetails;
window.updateModalContent = updateModalContent;
window.closeAssetModal = closeAssetModal;
window.loadRealAssets = loadRealAssets;
