/**
 * Upbit Terminal Portfolio Chart Renderer
 * 자산 비중 SVG 도넛 차트 및 인터랙션 구현
 */

const PortfolioChart = {
    /**
     * 포트폴리오 자산 비중 차트를 렌더링합니다.
     * @param {string} containerId - 차트를 주입할 부모 엘리먼트 ID
     * @param {object} portfolioData - 포트폴리오 데이터
     * @param {boolean} isBacktest - 백테스트 여부
     * @param {array} marketData - 실시간 종목 정보 (현재가 매핑용)
     */
    render(containerId, portfolioData, isBacktest = false, marketData = []) {
        const mainContainer = document.getElementById(containerId);
        if (!mainContainer) return;

        mainContainer.innerHTML = ''; // 초기화
        
        // 가로 방향 3열 배치 스타일 지정
        mainContainer.style.display = 'flex';
        mainContainer.style.flexDirection = 'row';
        mainContainer.style.flexWrap = 'wrap';
        mainContainer.style.gap = '15px';
        mainContainer.style.justifyContent = 'flex-start';

        // Adapter를 이용해 데이터를 그룹화
        const exchangeGroups = PortfolioAdapter.groupAssetsForAllocation(portfolioData, isBacktest, marketData);
        const exKeys = Object.keys(exchangeGroups);

        if (exKeys.length === 0) {
            const div = document.createElement('div');
            div.style.textAlign = 'center';
            div.style.padding = '20px';
            div.style.color = '#64748B';
            div.style.width = '100%';
            div.innerText = '보유 자산이 없습니다.';
            mainContainer.appendChild(div);
            return;
        }

        // 각 거래소별로 컨테이너를 생성하여 차트를 그립니다.
        exKeys.forEach(exKey => {
            const group = exchangeGroups[exKey];
            if (group.totalValue <= 0) return;

            // 거래소별 서브 컨테이너
            const wrapper = document.createElement('div');
            wrapper.className = 'allocation-content';
            wrapper.style.display = 'flex';
            wrapper.style.justifyContent = 'center';
            wrapper.style.alignItems = 'center';
            wrapper.style.padding = '5px 0';

            const chartBox = document.createElement('div');
            chartBox.className = 'chart-box';
            chartBox.style.width = '100%';
            chartBox.style.display = 'flex';
            chartBox.style.justifyContent = 'center';
            
            const chartContainer = document.createElement('div');
            chartContainer.style.width = '100%';
            chartContainer.style.maxWidth = '180px'; // 3열 가로 배치를 위한 최적 사이즈
            chartContainer.style.aspectRatio = '1 / 1';
            
            chartBox.appendChild(chartContainer);
            wrapper.appendChild(chartBox);
            
            // 거래소 배지 헤더
            const header = document.createElement('div');
            header.style.marginBottom = '6px';
            header.style.textAlign = 'center';
            header.innerHTML = `<span class="ctx-badge" style="background: rgba(148,163,184,0.12); font-size: 0.75rem; border: 1px solid rgba(148,163,184,0.1);">${exKey.toUpperCase()}</span>`;
            
            const groupWrapper = document.createElement('div');
            groupWrapper.className = 'allocation-group-wrapper';
            groupWrapper.style.flex = '1 1 calc(33.3% - 15px)';
            groupWrapper.style.minWidth = '160px';
            groupWrapper.style.padding = '12px 10px';
            groupWrapper.style.background = 'rgba(30, 41, 59, 0.4)'; // Slate Surface 색상
            groupWrapper.style.borderRadius = '8px';
            groupWrapper.style.border = '1px solid rgba(148, 163, 184, 0.08)';
            groupWrapper.style.boxSizing = 'border-box';
            
            groupWrapper.appendChild(header);
            groupWrapper.appendChild(wrapper);
            mainContainer.appendChild(groupWrapper);

            // SVG 렌더링
            this.createAllocationSvg(group.assets, group.totalValue, chartContainer);
        });
    },

    /**
     * 순수 SVG 렌더링 어댑터입니다. 주어진 DOM 컨테이너에 차트를 주입합니다.
     */
    createAllocationSvg(assets, total, chartContainer) {
        if (total <= 0) return;

        const size = 200;
        const center = size / 2;
        const radius = 82;
        const strokeWidth = 24;
        const circumference = 2 * Math.PI * radius;
        
        let accumulatedPercent = 0;

        const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
        svg.setAttribute("viewBox", `0 0 ${size} ${size}`);
        svg.style.width = "100%";
        svg.style.height = "100%";

        const centerText1 = document.createElementNS("http://www.w3.org/2000/svg", "text");
        centerText1.setAttribute("x", "50%");
        centerText1.setAttribute("y", "38%");
        centerText1.setAttribute("text-anchor", "middle");
        centerText1.setAttribute("fill", "#94A3B8");
        centerText1.setAttribute("font-size", "10px");
        centerText1.textContent = "총 보유 자산";

        const centerText2 = document.createElementNS("http://www.w3.org/2000/svg", "text");
        centerText2.setAttribute("x", "50%");
        centerText2.setAttribute("y", "54%");
        centerText2.setAttribute("text-anchor", "middle");
        centerText2.setAttribute("fill", "#F8FAFC");
        centerText2.setAttribute("font-size", "14px");
        centerText2.setAttribute("font-weight", "bold");
        centerText2.textContent = PortfolioAdapter.formatKoreanAmount(total);

        const centerText3 = document.createElementNS("http://www.w3.org/2000/svg", "text");
        centerText3.setAttribute("x", "50%");
        centerText3.setAttribute("y", "68%");
        centerText3.setAttribute("text-anchor", "middle");
        centerText3.setAttribute("fill", "#64748B");
        centerText3.setAttribute("font-size", "10px");
        centerText3.textContent = "PORTFOLIO";

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
            circle.style.transition = "stroke-width 0.2s ease, stroke 0.2s ease";
            
            circle.style.cursor = 'pointer';
            circle.onclick = () => {
                if (asset.symbol && typeof showAssetDetails === 'function') {
                    showAssetDetails(asset.symbol.includes('KIS') ? 'kis' : 'upbit', asset.symbol);
                }
            };
            
            circle.onmouseover = () => {
                circle.setAttribute("stroke-width", strokeWidth + 6);
                
                // 1. 상단: 한글 자산명 (없으면 영문 레이블)
                const displayName = asset.koreanName || asset.label;
                centerText1.textContent = displayName;
                centerText1.setAttribute("fill", asset.color);
                
                // 2. 중앙: 자산 개별 금액
                centerText2.textContent = PortfolioAdapter.formatKoreanAmount(asset.value);
                
                // 3. 하단: 영문 심볼 + 비율
                centerText3.textContent = asset.symbol ? `${asset.label} (${(percent * 100).toFixed(1)}%)` : `CASH (${(percent * 100).toFixed(1)}%)`;
                centerText3.setAttribute("fill", "rgba(148, 163, 184, 0.8)");
            };
            
            circle.onmouseout = () => {
                circle.setAttribute("stroke-width", strokeWidth);
                
                // 기본 상태 복원
                centerText1.textContent = "총 보유 자산";
                centerText1.setAttribute("fill", "#94A3B8");
                
                centerText2.textContent = PortfolioAdapter.formatKoreanAmount(total);
                
                centerText3.textContent = "PORTFOLIO";
                centerText3.setAttribute("fill", "#64748B");
            };

            const anim = document.createElementNS("http://www.w3.org/2000/svg", "animate");
            anim.setAttribute("attributeName", "stroke-dashoffset");
            anim.setAttribute("from", circumference);
            anim.setAttribute("to", -accumulatedPercent * circumference);
            anim.setAttribute("dur", "0.6s");
            anim.setAttribute("fill", "freeze");
            circle.appendChild(anim);

            svg.appendChild(circle);
            accumulatedPercent += percent;
        });

        svg.appendChild(centerText1);
        svg.appendChild(centerText2);
        svg.appendChild(centerText3);
        chartContainer.appendChild(svg);
    }
};

if (typeof module !== 'undefined' && module.exports) {
    module.exports = PortfolioChart;
} else {
    window.PortfolioChart = PortfolioChart;
}
