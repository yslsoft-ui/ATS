/**
 * Upbit Terminal Portfolio View Renderer
 * DOM 업데이트 및 테이블 렌더링 전담 모듈
 */

const PortfolioView = {
    /**
     * 포트폴리오 상단 메트릭 요약을 업데이트합니다.
     */
    updateMetrics(totalValue, roi, cash) {
        const totalValEl = document.getElementById('port-total-value');
        const cashEl = document.getElementById('port-cash');
        const roiEl = document.getElementById('port-total-roi');

        if (totalValEl) totalValEl.innerText = Math.round(totalValue).toLocaleString();
        if (cashEl) cashEl.innerText = Math.round(cash).toLocaleString();
        if (roiEl) {
            roiEl.innerText = `${roi >= 0 ? '+' : ''}${roi}%`;
            roiEl.className = `value ${roi >= 0 ? 'bull' : 'bear'}`;
        }
    },

    /**
     * 보유 종목 테이블을 렌더링합니다.
     */
    renderPositionsTable(tbodyId, positions, isBacktest = false, marketData = []) {
        const tbody = document.getElementById(tbodyId);
        if (!tbody) return;

        tbody.innerHTML = '';
        if (positions.length === 0) {
            tbody.innerHTML = '<tr><td colspan="4" style="text-align:center;padding:20px;">보유 종목이 없습니다.</td></tr>';
            return;
        }

        positions.forEach(pos => {
            const tr = document.createElement('tr');
            let currentPrice = pos.avg_price;
            let profitRate = 0;
            
            if (isBacktest) {
                currentPrice = pos.current_price || pos.avg_price;
                profitRate = pos.avg_price > 0 ? ((currentPrice - pos.avg_price) / pos.avg_price * 100).toFixed(4) : 0;
            } else {
                const coin = marketData.find(c => c.market === pos.symbol);
                currentPrice = coin ? coin.trade_price : pos.avg_price;
                profitRate = pos.avg_price > 0 ? ((currentPrice - pos.avg_price) / pos.avg_price * 100).toFixed(2) : 0;
            }
            
            const rateClass = profitRate >= 0 ? 'bull' : 'bear';
            const exBadge = pos.exchange ? `<span class="ctx-badge" style="font-size: 0.65rem; padding: 2px 4px; margin-left: 5px; vertical-align: middle; background: rgba(148, 163, 184, 0.15);">${pos.exchange.toUpperCase()}</span>` : '';

            const displaySymbol = pos.symbol.replace(/^(KRW-|UPB-|KIS-)/, '');
            const coinInfo = marketData.find(c => c.market === displaySymbol && (!pos.exchange || c.exchange === pos.exchange));
            const koreanName = (coinInfo && coinInfo.korean_name) || pos.korean_name || '';
            const tooltipAttr = koreanName ? `title="${koreanName}"` : '';
            const tooltipStyle = koreanName ? 'cursor:help; border-bottom: 1px dashed rgba(148,163,184,0.4);' : '';

            tr.innerHTML = `
                <td>
                    <strong ${tooltipAttr} style="${tooltipStyle}">${displaySymbol}</strong>
                    ${exBadge}
                </td>
                <td class="num">${pos.quantity.toFixed(4)}</td>
                <td class="num">${pos.avg_price.toLocaleString()}</td>
                <td class="num ${rateClass}">${profitRate}%</td>
            `;
            
            // 행 클릭 시 상세 모달 열기
            tr.onclick = () => {
                if (typeof showAssetDetails === 'function') {
                    showAssetDetails(pos.exchange || (pos.symbol.includes('KIS') ? 'kis' : 'upbit'), pos.symbol);
                }
            };
            
            tbody.appendChild(tr);
        });
    },

    /**
     * 최근 체결 내역 테이블을 렌더링합니다.
     */
    renderHistoryTable(tbodyId, history, isBacktest = false) {
        const tbody = document.getElementById(tbodyId);
        if (!tbody) return;

        tbody.innerHTML = '';
        
        // 백테스트 시에는 하단 상세 탭이 따로 있으므로 상단은 15개 요약
        const recentHistory = isBacktest ? history.slice(0, 15) : history;
        if (recentHistory.length === 0) {
            tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;padding:20px;">거래 내역이 없습니다.</td></tr>';
            return;
        }

        recentHistory.forEach(h => {
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
            tbody.appendChild(tr);
        });
    },

    /**
     * 자산 상세 모달 내 데이터를 갱신합니다.
     */
    updateModalContent(portfolioData, exchange, symbol, marketData = []) {
        if (!portfolioData) return;
        
        const pos = portfolioData.positions.find(p => p.exchange === exchange && p.symbol === symbol);
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
            const assetHistory = portfolioData.history.filter(h => h.symbol === symbol).reverse();
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
    },

    /**
     * 업비트 API를 통해 받아온 실제 잔고 정보를 렌더링합니다.
     */
    renderRealAssetsTable(tbodyId, data, totalValueEl, assetCountEl, onOrderClick, onHistoryClick, onAssetDblClick) {
        const tbody = document.getElementById(tbodyId);
        if (!tbody) return;

        if (!data || !data.assets) {
            tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;padding:20px;color:rgba(255,255,255,0.4);;">자산 내역이 비어있거나 키를 확인하세요.</td></tr>';
            return;
        }

        // 헤더 메트릭스 업데이트
        if (totalValueEl) totalValueEl.innerText = `${data.formatted_total_value} 원`;
        if (assetCountEl) assetCountEl.innerText = `${data.assets.length} 개 종목`;
        
        tbody.innerHTML = '';
        
        if (data.assets.length === 0) {
            tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;padding:20px;">보유 자산이 없습니다.</td></tr>';
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

            const isOrderDisabled = asset.currency === 'KRW' || asset.current_price <= 0;
            const actionsHtml = `
                <div class="real-asset-actions">
                    <button class="btn-action-order" ${isOrderDisabled ? 'disabled' : ''}>주문</button>
                    <button class="btn-action-history">이력</button>
                </div>
            `;
            
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
                <td>${actionsHtml}</td>
            `;
            
            // 주문 버튼 클릭 리스너
            const orderBtn = tr.querySelector('.btn-action-order');
            if (orderBtn) {
                orderBtn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    if (typeof onOrderClick === 'function') {
                        onOrderClick(asset);
                    }
                });
            }

            // 이력 버튼 클릭 리스너
            const historyBtn = tr.querySelector('.btn-action-history');
            if (historyBtn) {
                historyBtn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    if (typeof onHistoryClick === 'function') {
                        onHistoryClick(asset);
                    }
                });
            }

            // 더블 클릭 시 차트 연동 트리거 호출
            tr.addEventListener('dblclick', (e) => {
                e.stopPropagation(); // 단순 클릭 이벤트 전파 차단
                if (asset.currency !== 'KRW' && typeof onAssetDblClick === 'function') {
                    onAssetDblClick(asset);
                }
            });
            
            tbody.appendChild(tr);
        });
    },

    /**
     * 거래소별 요약 성과 현황을 렌더링합니다.
     */
    renderExchangeSummary(tbodyId, res, onExchangeClick) {
        const tbody = document.getElementById(tbodyId);
        if (!tbody) return;
        tbody.innerHTML = '';

        const results = res ? (res.results || []) : [];
        const exInitialCashMap = res ? (res.exchange_initial_cash || {}) : {};

        if (results.length === 0 && Object.keys(exInitialCashMap).length === 0) {
            tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;padding:20px;color:#64748B;">매매 거래가 발생한 종목이 없습니다.</td></tr>';
            const detailTbody = document.getElementById('port-history-detail-tbody');
            if (detailTbody) {
                detailTbody.innerHTML = '<tr><td colspan="8" style="text-align:center;padding:20px;">거래 내역이 없습니다.</td></tr>';
            }
            return;
        }

        const exchangeSummary = {};
        
        Object.entries(exInitialCashMap).forEach(([ex, cashVal]) => {
            const exKey = ex.toLowerCase();
            exchangeSummary[exKey] = {
                exchange: exKey,
                symbolCount: 0,
                tradeCount: 0,
                fee: 0,
                profit: 0,
                initialCash: cashVal
            };
        });

        results.forEach(item => {
            const exKey = item.exchange.toLowerCase();
            if (!exchangeSummary[exKey]) {
                exchangeSummary[exKey] = {
                    exchange: exKey,
                    symbolCount: 0,
                    tradeCount: 0,
                    fee: 0,
                    profit: 0,
                    initialCash: item.initial_cash || 0
                };
            }
            
            const trades = item.trades || [];
            const finalPrice = item.finalPrice || (trades.length > 0 ? trades[trades.length - 1].price : 0);

            let currentQty = 0;
            let feeSum = 0;
            let sellSum = 0;
            let buySum = 0;

            trades.forEach(t => {
                feeSum += t.fee || 0;
                if (t.side === 'BUY') {
                    currentQty += t.quantity;
                    buySum += t.price * t.quantity;
                } else {
                    currentQty -= t.quantity;
                    sellSum += t.price * t.quantity;
                    if (currentQty <= 0) currentQty = 0;
                }
            });

            const valuation = currentQty * finalPrice;
            const profit = sellSum + valuation - buySum - feeSum;

            exchangeSummary[exKey].symbolCount += 1;
            exchangeSummary[exKey].tradeCount += trades.length;
            exchangeSummary[exKey].fee += feeSum;
            exchangeSummary[exKey].profit += profit;
        });

        let totalInitial = 0;
        let totalFinal = 0;
        let totalSymbols = 0;
        let totalTrades = 0;
        let totalFees = 0;
        let totalProfit = 0;

        Object.values(exchangeSummary).forEach((sum, idx) => {
            const tr = document.createElement('tr');
            tr.style.cursor = 'pointer';
            tr.id = `port-ex-row-${sum.exchange}`;
            
            const profitClass = sum.profit >= 0 ? 'bull' : 'bear';
            const profitText = (sum.profit >= 0 ? '+' : '') + Math.round(sum.profit).toLocaleString() + " 원";
            
            const finalValue = sum.initialCash + sum.profit;

            totalInitial += sum.initialCash;
            totalFinal += finalValue;
            totalSymbols += sum.symbolCount;
            totalTrades += sum.tradeCount;
            totalFees += sum.fee;
            totalProfit += sum.profit;

            tr.innerHTML = `
                <td><strong>${sum.exchange.toUpperCase()}</strong></td>
                <td class="num">${Math.round(sum.initialCash).toLocaleString()} 원</td>
                <td class="num">${Math.round(finalValue).toLocaleString()} 원</td>
                <td class="num">${sum.symbolCount} 개</td>
                <td class="num">${sum.tradeCount} 건</td>
                <td class="num">${Math.round(sum.fee).toLocaleString()} 원</td>
                <td class="num ${profitClass}">${profitText}</td>
            `;

            tr.onclick = () => {
                document.querySelectorAll('#port-exchanges-table tbody tr').forEach(r => r.classList.remove('selected'));
                tr.classList.add('selected');
                if (typeof onExchangeClick === 'function') {
                    onExchangeClick(sum.exchange);
                }
            };

            // 첫 번째 거래소 자동 선택 처리 (직접 클래스 주입)
            if (idx === 0) {
                tr.classList.add('selected');
                if (typeof onExchangeClick === 'function') {
                    setTimeout(() => onExchangeClick(sum.exchange), 0);
                }
            }

            tbody.appendChild(tr);
        });

        if (Object.keys(exchangeSummary).length > 0) {
            const totalTr = document.createElement('tr');
            totalTr.style.background = 'rgba(148, 163, 184, 0.08)';
            totalTr.style.fontWeight = 'bold';
            totalTr.style.borderTop = '2px solid rgba(148, 163, 184, 0.2)';
            
            const totProfitClass = totalProfit >= 0 ? 'bull' : 'bear';
            const totProfitText = (totalProfit >= 0 ? '+' : '') + Math.round(totalProfit).toLocaleString() + " 원";

            totalTr.innerHTML = `
                <td><strong>합계 (TOTAL)</strong></td>
                <td class="num">${Math.round(totalInitial).toLocaleString()} 원</td>
                <td class="num">${Math.round(totalFinal).toLocaleString()} 원</td>
                <td class="num">${totalSymbols} 개</td>
                <td class="num">${totalTrades} 건</td>
                <td class="num">${Math.round(totalFees).toLocaleString()} 원</td>
                <td class="num ${totProfitClass}">${totProfitText}</td>
            `;
            tbody.appendChild(totalTr);
        }
    },

    /**
     * 특정 거래소의 종목별 상세 현황 테이블을 렌더링합니다.
     */
    renderSymbolDetailTable(tbodyId, titleId, exchangeName, res, tfootId, onSymbolClick) {
        const titleEl = document.getElementById(titleId);
        const tbody = document.getElementById(tbodyId);
        if (!tbody || !titleEl) return;
        
        titleEl.innerText = `${exchangeName.toUpperCase()} 상세 종목 현황`;
        tbody.innerHTML = '';
        
        const results = res ? (res.results || []) : [];
        const exchangeItems = results.filter(r => r.exchange.toLowerCase() === exchangeName.toLowerCase());

        const processedItems = exchangeItems.map(item => {
            const trades = item.trades || [];
            const finalPrice = item.finalPrice || (trades.length > 0 ? trades[trades.length - 1].price : 0);

            let currentQty = 0;
            let avgPrice = 0;
            let totalCost = 0;
            let feeSum = 0;
            let sellSum = 0;
            let buySum = 0;

            trades.forEach(t => {
                feeSum += t.fee || 0;
                if (t.side === 'BUY') {
                    totalCost += t.price * t.quantity;
                    currentQty += t.quantity;
                    buySum += t.price * t.quantity;
                    if (currentQty > 0) {
                        avgPrice = totalCost / currentQty;
                    }
                } else {
                    currentQty -= t.quantity;
                    sellSum += t.price * t.quantity;
                    if (currentQty <= 0) {
                        currentQty = 0;
                        avgPrice = 0;
                        totalCost = 0;
                    }
                }
            });

            const valuation = currentQty * finalPrice;
            const profit = sellSum + valuation - buySum - feeSum;
            const investCash = avgPrice * currentQty;
            
            let profitRate = 0;
            const buyTrades = trades.filter(t => t.side === 'BUY');
            const buyCount = buyTrades.length;
            if (buyCount > 0) {
                const avgBuyVal = buySum / buyCount;
                profitRate = avgBuyVal > 0 ? (profit / avgBuyVal * 100) : 0;
            }

            return {
                ...item,
                currentQty,
                avgPrice,
                finalPrice,
                profitRate,
                profit,
                investCash,
                valuation,
                tradeCount: trades.length,
                fee: feeSum,
                buySum,
                sellSum
            };
        });

        processedItems.sort((a, b) => b.profit - a.profit);

        const tfoot = document.getElementById(tfootId);
        if (tfoot) {
            tfoot.innerHTML = '';
            tfoot.style.display = 'none';
        }

        if (processedItems.length === 0) {
            tbody.innerHTML = '<tr><td colspan="10" style="text-align:center;padding:20px;color:#64748B;">매매 거래가 발생한 종목이 없습니다.</td></tr>';
            return;
        }

        let totTradeCount = 0;
        let totBuySum = 0;
        let totSellSum = 0;
        let totValuation = 0;
        let totFee = 0;
        let totProfit = 0;
        let totAvgBuyVal = 0;

        processedItems.forEach((item, idx) => {
            totTradeCount += item.tradeCount || 0;
            totBuySum += item.buySum || 0;
            totSellSum += item.sellSum || 0;
            totValuation += item.valuation || 0;
            totFee += item.fee || 0;
            totProfit += item.profit || 0;

            const buyTrades = item.trades ? item.trades.filter(t => t.side === 'BUY') : [];
            const buyCount = buyTrades.length;
            if (buyCount > 0) {
                totAvgBuyVal += (item.buySum / buyCount);
            }

            const tr = document.createElement('tr');
            tr.style.cursor = 'pointer';
            tr.id = `port-pos-row-${item.exchange}-${item.symbol}`;
            
            const rateClass = item.profitRate >= 0 ? 'bull' : 'bear';
            const profitClass = item.profit >= 0 ? 'bull' : 'bear';
            
            const rateText = item.tradeCount > 0 ? `${item.profitRate.toFixed(4)}%` : '-';
            const profitText = (item.profit >= 0 ? '+' : '') + Math.round(item.profit).toLocaleString() + " 원";

            const korName = item.korean_name && item.korean_name !== item.symbol ? item.korean_name : '';
            const symbolTooltip = korName ? `title="${korName}"` : '';

            tr.innerHTML = `
                <td><strong ${symbolTooltip} style="${korName ? 'cursor:help; border-bottom: 1px dashed rgba(148,163,184,0.4);' : ''}">${item.symbol}</strong></td>
                <td class="num">${item.tradeCount} 건</td>
                <td class="num">${Math.round(item.buySum).toLocaleString()} 원</td>
                <td class="num">${Math.round(item.sellSum).toLocaleString()} 원</td>
                <td class="num">${item.currentQty.toFixed(4)}</td>
                <td class="num">${PortfolioAdapter.formatPricePort(item.finalPrice)}</td>
                <td class="num">${Math.round(item.valuation).toLocaleString()} 원</td>
                <td class="num">${Math.round(item.fee).toLocaleString()} 원</td>
                <td class="num ${profitClass}">${profitText}</td>
                <td class="num ${rateClass}">${rateText}</td>
            `;

            tr.onclick = () => {
                document.querySelectorAll('#port-symbols-table tbody tr').forEach(r => r.classList.remove('selected'));
                tr.classList.add('selected');
                if (typeof onSymbolClick === 'function') {
                    onSymbolClick(item);
                }
            };

            // 첫 번째 종목 자동 선택 처리 (직접 클래스 주입)
            if (idx === 0) {
                tr.classList.add('selected');
                if (typeof onSymbolClick === 'function') {
                    setTimeout(() => onSymbolClick(item), 0);
                }
            }

            tbody.appendChild(tr);
        });

        if (tfoot && processedItems.length > 0) {
            tfoot.style.display = 'table-footer-group';
            const tr = document.createElement('tr');
            
            let totProfitRate = 0;
            if (totAvgBuyVal > 0) {
                totProfitRate = (totProfit / totAvgBuyVal * 100);
            }

            const totProfitClass = totProfit >= 0 ? 'bull' : 'bear';
            const totProfitText = (totProfit >= 0 ? '+' : '') + Math.round(totProfit).toLocaleString() + " 원";
            const totRateClass = totProfitRate >= 0 ? 'bull' : 'bear';
            const totRateText = totAvgBuyVal > 0 ? `${totProfitRate.toFixed(4)}%` : '-';

            tr.innerHTML = `
                <td><strong>합계 (TOTAL)</strong></td>
                <td class="num">${totTradeCount} 건</td>
                <td class="num">${Math.round(totBuySum).toLocaleString()} 원</td>
                <td class="num">${Math.round(totSellSum).toLocaleString()} 원</td>
                <td class="num">-</td>
                <td class="num">-</td>
                <td class="num">${Math.round(totValuation).toLocaleString()} 원</td>
                <td class="num">${Math.round(totFee).toLocaleString()} 원</td>
                <td class="num ${totProfitClass}">${totProfitText}</td>
                <td class="num ${totRateClass}">${totRateText}</td>
            `;
            tfoot.appendChild(tr);
        }
    },

    /**
     * 특정 종목의 백테스트 전체 거래 내역을 렌더링합니다.
     */
    renderHistoryTablePort(tbodyId, item) {
        const histTbody = document.getElementById(tbodyId);
        if (!histTbody) return;
        histTbody.innerHTML = '';

        const trades = item.trades || [];
        if (trades.length === 0) {
            histTbody.innerHTML = '<tr><td colspan="8" style="text-align:center;padding:20px;">거래 내역이 없습니다.</td></tr>';
            return;
        }

        const sortedTrades = [...trades].reverse();
        sortedTrades.forEach(t => {
            const hTr = document.createElement('tr');
            const dateStr = PortfolioAdapter.formatTimestampPort(t.timestamp);

            hTr.innerHTML = `
                <td>${dateStr}</td>
                <td><strong>${item.symbol}</strong> <span style="font-size:0.7rem; color:#64748B;">(${item.exchange})</span></td>
                <td class="${t.side === 'BUY' ? 'bull' : 'bear'}">${t.side}</td>
                <td class="num">${PortfolioAdapter.formatPricePort(t.price)}</td>
                <td class="num">${t.quantity.toFixed(4)}</td>
                <td class="num">${PortfolioAdapter.formatPricePort(t.price * t.quantity)}</td>
                <td class="num">${Math.round(t.fee).toLocaleString()} 원</td>
                <td>${t.reason || '-'}</td>
            `;
            histTbody.appendChild(hTr);
        });
    }
};

if (typeof module !== 'undefined' && module.exports) {
    module.exports = PortfolioView;
} else {
    window.PortfolioView = PortfolioView;
}
