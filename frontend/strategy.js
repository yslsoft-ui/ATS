/**
 * 매매 전략 관리 및 실시간 분석 모니터링 관련 기능 구현 모듈
 * (전략 버전 제어, 롤백, AI 제안 승인 콘솔 UI 포함)
 */

// 실데이터 API 연결 전 Mock UI 테스트용 플래그
const USE_MOCK = false;

// 모의 데이터 셋
const MOCK_STRATEGIES_DETAIL = {
    "rsistrategy": {
        "strategy_id": "rsistrategy",
        "name": "RSI 역추세 매매 전략",
        "enabled": true,
        "current_version_id": 3,
        "current_params": {"rsi_window": 14, "buy_threshold": 30.0, "sell_threshold": 70.0},
        "rollback_source_version": 2,
        "applied_at": 1718020900000,
        "description": "RSI 지표가 과매도/과매수 구간에 진입 시 반대 방향으로 추종 매매를 수행합니다."
    }
};

const MOCK_PROPOSALS = [
    {
        "id": 101,
        "strategy_id": "rsistrategy",
        "status": "APPLIED",
        "outcome": "ROLLED_BACK",
        "original_params": {"rsi_window": 14, "buy_threshold": 30.0, "sell_threshold": 70.0},
        "proposed_params": {"rsi_window": 14, "buy_threshold": 35.0, "sell_threshold": 65.0},
        "evaluation_metrics": {"expected_roi": 12.4, "risk_score": 2.1},
        "post_metrics": {"actual_roi": -4.2, "trade_count": 8},
        "mutation_trace": {"buy_threshold": [30.0, 35.0], "sell_threshold": [70.0, 65.0]},
        "confidence_score": 85,
        "created_at": 1718020000000,
        "updated_at": 1718020500000,
        "applied_at": 1718020500000,
        "rolled_back_at": 1718020900000
    },
    {
        "id": 102,
        "strategy_id": "rsistrategy",
        "status": "PENDING",
        "outcome": "RUNNING",
        "original_params": {"rsi_window": 14, "buy_threshold": 30.0, "sell_threshold": 70.0},
        "proposed_params": {"rsi_window": 16, "buy_threshold": 30.0, "sell_threshold": 70.0},
        "evaluation_metrics": {"expected_roi": 8.7, "risk_score": 1.1},
        "post_metrics": null,
        "mutation_trace": {"rsi_window": [14, 16]},
        "confidence_score": 75,
        "created_at": 1718021000000,
        "updated_at": 1718021000000,
        "applied_at": null,
        "rolled_back_at": null
    }
];

const MOCK_SNAPSHOTS = [
    {"timestamp": 1718018000000, "roi": 0.0, "version_id": 1, "snapshot_type": "STARTUP"},
    {"timestamp": 1718018500000, "roi": 1.2, "version_id": 1, "snapshot_type": "PERIODIC"},
    {"timestamp": 1718019000000, "roi": 2.5, "version_id": 1, "snapshot_type": "PERIODIC"},
    {"timestamp": 1718019500000, "roi": 3.8, "version_id": 1, "snapshot_type": "PERIODIC"},
    {"timestamp": 1718020500000, "roi": 5.4, "version_id": 1, "snapshot_type": "PARAMETER_CHANGE"},
    {"timestamp": 1718020600000, "roi": 3.2, "version_id": 2, "snapshot_type": "PERIODIC"},
    {"timestamp": 1718020700000, "roi": 1.8, "version_id": 2, "snapshot_type": "PERIODIC"},
    {"timestamp": 1718020800000, "roi": 0.5, "version_id": 2, "snapshot_type": "PERIODIC"},
    {"timestamp": 1718020900000, "roi": -1.2, "version_id": 2, "snapshot_type": "ROLLBACK"},
    {"timestamp": 1718021000000, "roi": 0.8, "version_id": 3, "snapshot_type": "PERIODIC"},
    {"timestamp": 1718021100000, "roi": 2.1, "version_id": 3, "snapshot_type": "PERIODIC"}
];

const MOCK_HISTORY = [
    {
        "version_id": 1,
        "parent_version_id": null,
        "changed_by": "AUTO",
        "change_reason": "STARTUP_RESTORE",
        "new_params": {"rsi_window": 14, "buy_threshold": 30.0, "sell_threshold": 70.0},
        "old_params": null,
        "created_at": 1718018000000
    },
    {
        "version_id": 2,
        "parent_version_id": 1,
        "changed_by": "USER",
        "change_reason": "PROPOSAL_APPLY",
        "new_params": {"rsi_window": 14, "buy_threshold": 35.0, "sell_threshold": 65.0},
        "old_params": {"rsi_window": 14, "buy_threshold": 30.0, "sell_threshold": 70.0},
        "created_at": 1718020500000
    },
    {
        "version_id": 3,
        "parent_version_id": 1,
        "changed_by": "USER",
        "change_reason": "ROLLBACK",
        "new_params": {"rsi_window": 14, "buy_threshold": 30.0, "sell_threshold": 70.0},
        "old_params": {"rsi_window": 14, "buy_threshold": 35.0, "sell_threshold": 65.0},
        "created_at": 1718020900000
    }
];

// 현재 뷰에서 선택된 전략 ID
let selectedStrategyId = null;
// 현재 전략의 전체 제안 목록 캐시
let cachedProposals = [];

/**
 * 전체 전략 정보를 API로 가져와 좌측 사이드바에 렌더링합니다.
 */
async function loadStrategies() {
    const listEl = document.getElementById('strategy-list');
    if (!listEl) return;

    try {
        const strategies = await APIClient.fetchStrategies();
        renderStrategyCards(strategies);
    } catch (e) {
        listEl.innerHTML = '<p class="status-text">전략 정보를 불러오는데 실패했습니다.</p>';
    }
}

/**
 * 수집한 전략 정보를 바탕으로 카드 리스트를 렌더링합니다.
 */
function renderStrategyCards(strategies) {
    const listEl = document.getElementById('strategy-list');
    if (!listEl) return;
    listEl.innerHTML = '';

    // 정렬 순서 정의
    const typeOrder = { "ENTRY": 1, "BOTH": 2, "EXIT": 3 };
    strategies.sort((a, b) => (typeOrder[a.type] || 99) - (typeOrder[b.type] || 99));

    strategies.forEach(s => {
        const card = document.createElement('div');
        const isEnabled = s.enabled !== false;
        
        // 선택된 전략 강조
        const isSelected = selectedStrategyId === s.id;
        
        card.className = `strategy-item ${isEnabled ? '' : 'disabled'} ${isSelected ? 'active-selection' : ''}`;
        card.style.opacity = isEnabled ? '1' : '0.6';
        card.style.padding = '12px';
        card.style.background = isSelected ? 'rgba(99, 102, 241, 0.15)' : 'rgba(30, 41, 59, 0.4)';
        card.style.border = isSelected ? '1px solid #6366F1' : '1px solid rgba(148, 163, 184, 0.1)';
        card.style.borderRadius = '6px';
        card.style.cursor = 'pointer';
        card.style.transition = 'all 0.2s ease';

        const typeColors = { "ENTRY": "#4A90E2", "EXIT": "#FF4B4B", "BOTH": "#F5A623" };
        const typeLabel = s.type === "ENTRY" ? "매수" : (s.type === "EXIT" ? "매도" : "공용");

        card.innerHTML = `
            <div onclick="selectStrategy('${s.id}')" style="display: flex; flex-direction: column; gap: 4px;">
                <div style="display: flex; justify-content: space-between; align-items: center; font-weight: bold; color: #F8FAFC;">
                    <span>${s.name}</span>
                    <span class="badge" style="font-size: 0.65rem; background: ${isEnabled ? '#1a472a' : '#471a1a'}; color: ${isEnabled ? '#4caf50' : '#FF4B4B'};">${isEnabled ? '활성' : '비활성'}</span>
                </div>
                <div style="display: flex; justify-content: space-between; align-items: center; margin-top: 4px;">
                    <span class="type-badge" style="background: ${typeColors[s.type] || '#666'}; font-size: 0.6rem; padding: 1px 5px; border-radius: 4px; color: white;">${typeLabel}</span>
                    <span style="color: #64748B; font-size: 0.75rem; font-family: monospace;">${s.id}</span>
                </div>
            </div>
        `;
        listEl.appendChild(card);
    });

    // 최초 1회, 전략이 선택되지 않은 경우 첫 번째 전략을 자동으로 로드
    if (!selectedStrategyId && strategies.length > 0) {
        selectStrategy(strategies[0].id);
    }
}

/**
 * 우측 상세 운영 대시보드를 로드하고 렌더링합니다.
 */
async function selectStrategy(strategyId) {
    selectedStrategyId = strategyId;
    
    // 활성화 표시를 위해 사이드바 다시 렌더링
    await loadStrategiesOnly();

    const panel = document.getElementById('strategy-detail-panel');
    if (!panel) return;
    panel.style.display = 'flex';

    try {
        let detail, proposals, snapshots, history;

        // [1단계: READ ONLY REAL DATA 연결]
        // Strategy Detail, Snapshots, History는 실제 백엔드 API 실측 데이터 연동 (실패 시 Mock으로 자동 Fallback)
        try {
            detail = await APIClient.fetchStrategyDetail(strategyId);
            snapshots = await APIClient.fetchStrategySnapshots(strategyId);
            history = await APIClient.fetchStrategyHistory(strategyId);
        } catch (apiError) {
            console.warn("실제 Read-Only API 페치 실패, 모의 데이터로 Fallback합니다:", apiError);
            detail = MOCK_STRATEGIES_DETAIL[strategyId] || {
                "strategy_id": strategyId,
                "name": strategyId.toUpperCase(),
                "enabled": false,
                "current_version_id": 1,
                "current_params": {},
                "rollback_source_version": null,
                "applied_at": Date.now(),
                "description": "실제 상세 정보를 불러올 수 없어 임시 반환한 정보입니다."
            };
            snapshots = MOCK_SNAPSHOTS;
            history = MOCK_HISTORY;
        }

        // 제안(Proposals)도 API 우선 -> Mock Fallback 구조 적용 (전체 조회를 위해 includePruned=true 전달)
        try {
            cachedProposals = await APIClient.fetchProposals(strategyId, true);
        } catch (apiError) {
            console.warn("실제 Proposals API 페치 실패, 모의 데이터로 Fallback합니다:", apiError);
            cachedProposals = MOCK_PROPOSALS.filter(p => p.strategy_id === strategyId);
        }

        // 1. 전략 요약 정보 렌더링
        renderStrategySummary(detail);

        // 2. Plotly 시계열 ROI 차트 렌더링
        renderStrategyRoiChart(snapshots);

        // 3. AI 제안 카드 렌더링 (기본값인 PENDING 필터 선택에 맞추어 필터링 렌더링)
        const filterSelect = document.getElementById('proposal-status-filter');
        if (filterSelect) {
            filterSelect.value = 'PENDING';
        }
        filterProposals();

        // 4. 버전 히스토리 및 롤백 테이블 렌더링
        renderVersionHistoryTable(history, detail.current_version_id);

        // 5. AI 건강 지표 패널 로드
        loadAIHealthData(strategyId);

    } catch (e) {
        console.error("Failed to select strategy detail: ", e);
    }
}

/**
 * 사이드바 갱신 시 무한루프 방지를 위해 전략 리스트만 다시 페치
 */
async function loadStrategiesOnly() {
    try {
        const strategies = await APIClient.fetchStrategies();
        const listEl = document.getElementById('strategy-list');
        if (!listEl) return;
        listEl.innerHTML = '';
        
        const typeOrder = { "ENTRY": 1, "BOTH": 2, "EXIT": 3 };
        strategies.sort((a, b) => (typeOrder[a.type] || 99) - (typeOrder[b.type] || 99));

        strategies.forEach(s => {
            const card = document.createElement('div');
            const isEnabled = s.enabled !== false;
            const isSelected = selectedStrategyId === s.id;
            
            card.className = `strategy-item ${isEnabled ? '' : 'disabled'} ${isSelected ? 'active-selection' : ''}`;
            card.style.opacity = isEnabled ? '1' : '0.6';
            card.style.padding = '12px';
            card.style.background = isSelected ? 'rgba(99, 102, 241, 0.15)' : 'rgba(30, 41, 59, 0.4)';
            card.style.border = isSelected ? '1px solid #6366F1' : '1px solid rgba(148, 163, 184, 0.1)';
            card.style.borderRadius = '6px';
            card.style.cursor = 'pointer';
            card.style.transition = 'all 0.2s ease';

            const typeColors = { "ENTRY": "#4A90E2", "EXIT": "#FF4B4B", "BOTH": "#F5A623" };
            const typeLabel = s.type === "ENTRY" ? "매수" : (s.type === "EXIT" ? "매도" : "공용");

            card.innerHTML = `
                <div onclick="selectStrategy('${s.id}')" style="display: flex; flex-direction: column; gap: 4px;">
                    <div style="display: flex; justify-content: space-between; align-items: center; font-weight: bold; color: #F8FAFC;">
                        <span>${s.name}</span>
                        <span class="badge" style="font-size: 0.65rem; background: ${isEnabled ? '#1a472a' : '#471a1a'}; color: ${isEnabled ? '#4caf50' : '#FF4B4B'};">${isEnabled ? '활성' : '비활성'}</span>
                    </div>
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-top: 4px;">
                        <span class="type-badge" style="background: ${typeColors[s.type] || '#666'}; font-size: 0.6rem; padding: 1px 5px; border-radius: 4px; color: white;">${typeLabel}</span>
                        <span style="color: #64748B; font-size: 0.75rem; font-family: monospace;">${s.id}</span>
                    </div>
                </div>
            `;
            listEl.appendChild(card);
        });
    } catch(e) {}
}

/**
 * 전략 정보 요약 렌더링
 */
function renderStrategySummary(detail) {
    document.getElementById('detail-strategy-name').innerText = detail.name;
    
    const statusEl = document.getElementById('detail-strategy-status');
    if (detail.enabled) {
        statusEl.innerText = '가동 중';
        statusEl.style.background = 'rgba(16, 185, 129, 0.2)';
        statusEl.style.color = '#10B981';
    } else {
        statusEl.innerText = '사용 중단';
        statusEl.style.background = 'rgba(239, 68, 68, 0.2)';
        statusEl.style.color = '#EF4444';
    }

    document.getElementById('detail-current-version').innerText = `V${detail.current_version_id}`;
    document.getElementById('detail-applied-at').innerText = formatTimestamp(detail.applied_at);
    document.getElementById('detail-rollback-source').innerText = detail.rollback_source_version ? `V${detail.rollback_source_version}` : 'None';
    document.getElementById('detail-current-params').innerText = JSON.stringify(detail.current_params, null, 2);
}

/**
 * Plotly 버전 오버레이 ROI 시계열 차트 렌더링
 */
function renderStrategyRoiChart(snapshots) {
    const chartDiv = document.getElementById('strategy-roi-chart');
    if (!chartDiv) return;

    if (typeof Plotly === 'undefined') {
        chartDiv.innerHTML = '<div style="display:flex; justify-content:center; align-items:center; height:100%; color:#64748B;">Plotly 라이브러리가 존재하지 않습니다.</div>';
        return;
    }

    if (!snapshots || snapshots.length === 0) {
        chartDiv.innerHTML = '<div style="display:flex; justify-content:center; align-items:center; height:100%; color:#64748B;">성과 스냅샷 데이터가 없습니다. (수집 대기 중)</div>';
        return;
    }

    // 버전별 선 색상 배색
    const versionColors = {
        1: '#F59E0B', // Amber
        2: '#0072FF', // Bear Blue
        3: '#10B981', // Emerald Green
        4: '#EF4444', // Red
        5: '#EC4899', // Pink
    };

    const traces = [];
    const shapes = [];
    const annotations = [];

    // 버전 ID 추출 및 정렬
    const versions = [...new Set(snapshots.map(s => s.version_id))].sort((a, b) => a - b);

    versions.forEach(vId => {
        const vSnaps = snapshots.filter(s => s.version_id === vId).sort((a, b) => a.timestamp - b.timestamp);
        if (vSnaps.length === 0) return;

        // 라인이 끊겨 보이지 않게 바로 이전 버전의 마지막 포인트를 현재 버전 시작 지점에 이어줌 (연속성 확보)
        const firstTime = vSnaps[0].timestamp;
        const prevVersionSnaps = snapshots.filter(s => s.version_id < vId).sort((a, b) => a.timestamp - b.timestamp);
        if (prevVersionSnaps.length > 0) {
            const lastPrev = prevVersionSnaps[prevVersionSnaps.length - 1];
            vSnaps.unshift({
                ...lastPrev,
                version_id: vId, // 현재 버전 라인에 그리기 위해 속성만 임시 오버레이
                timestamp: firstTime - 1 // 1ms 전에 위치시켜 자연스럽게 이음
            });
        }

        const xData = vSnaps.map(s => new Date(s.timestamp));
        const yData = vSnaps.map(s => s.roi);
        const textData = vSnaps.map(s => `버전: V${s.version_id}<br>타입: ${s.snapshot_type}<br>ROI: ${s.roi.toFixed(3)}%<br>거래 건수: ${s.trade_count || 0}`);

        traces.push({
            x: xData,
            y: yData,
            type: 'scatter',
            mode: 'lines+markers',
            name: `버전 ${vId}`,
            line: {
                color: versionColors[vId] || '#94A3B8',
                width: 3.5,
                shape: 'linear'
            },
            marker: {
                size: 5,
                color: versionColors[vId] || '#94A3B8'
            },
            text: textData,
            hoverinfo: 'text+x'
        });
    });

    // 롤백 및 변경 이벤트 발생 시점 세로축 점선(Marker) 그리기
    snapshots.forEach(s => {
        if (s.snapshot_type === 'PARAMETER_CHANGE' || s.snapshot_type === 'ROLLBACK') {
            const time = new Date(s.timestamp);
            const isRollback = s.snapshot_type === 'ROLLBACK';
            
            shapes.push({
                type: 'line',
                x0: time,
                x1: time,
                yref: 'paper',
                y0: 0,
                y1: 1,
                line: {
                    color: isRollback ? '#EF4444' : '#6366F1',
                    width: 1.5,
                    dash: 'dash'
                }
            });

            annotations.push({
                x: time,
                y: s.roi,
                xref: 'x',
                yref: 'y',
                text: isRollback ? '⏪ 롤백' : '⚡ 적용',
                showarrow: true,
                arrowhead: 2,
                ax: 0,
                ay: -25,
                font: {
                    color: '#F8FAFC',
                    size: 9,
                    family: 'Pretendard, Inter'
                },
                bgcolor: isRollback ? '#EF4444' : '#6366F1',
                bordercolor: '#1E293B',
                borderwidth: 1,
                borderpad: 3,
                opacity: 0.95
            });
        }
    });

    const layout = {
        paper_bgcolor: 'rgba(30, 41, 59, 0.0)',
        plot_bgcolor: '#0F172A',
        margin: { t: 15, r: 15, l: 45, b: 35 },
        xaxis: {
            gridcolor: 'rgba(148, 163, 184, 0.08)',
            tickfont: { color: '#94A3B8', size: 9, family: 'Roboto Mono' },
            type: 'date',
            zeroline: false
        },
        yaxis: {
            gridcolor: 'rgba(148, 163, 184, 0.08)',
            tickfont: { color: '#94A3B8', size: 9, family: 'Roboto Mono' },
            zeroline: true,
            zerolinecolor: 'rgba(148, 163, 184, 0.2)',
            title: {
                text: 'ROI (%)',
                font: { color: '#94A3B8', size: 10, family: 'Pretendard' }
            }
        },
        showlegend: true,
        legend: {
            font: { color: '#94A3B8', size: 9, family: 'Pretendard' },
            orientation: 'h',
            y: 1.1,
            x: 0.01,
            bgcolor: 'transparent'
        },
        shapes: shapes,
        annotations: annotations
    };

    Plotly.newPlot(chartDiv, traces, layout, { displayModeBar: false, responsive: true });
}

/**
 * AI 제안 카드 렌더링
 */
function renderProposalCards(proposals) {
    const container = document.getElementById('proposal-cards-container');
    if (!container) return;

    if (!proposals || proposals.length === 0) {
        container.innerHTML = '<p style="color: #64748B; font-size: 0.85rem; width: 100%; text-align: center; padding: 15px 0;">조건을 만족하는 AI 의사결정 제안이 없습니다.</p>';
        return;
    }

    container.innerHTML = '';
    proposals.forEach(p => {
        const card = document.createElement('div');
        card.className = 'proposal-card-item';
        card.style.flex = '1 1 calc(50% - 15px)';
        card.style.minWidth = '280px';
        card.style.background = '#0F172A';
        card.style.border = '1px solid rgba(99, 102, 241, 0.3)';
        card.style.borderRadius = '8px';
        card.style.padding = '15px';
        card.style.display = 'flex';
        card.style.flexDirection = 'column';
        card.style.gap = '10px';
        card.style.boxSizing = 'border-box';
        card.style.position = 'relative';
        card.style.overflow = 'hidden';

        // 신뢰도 점수에 따른 보더 탑 디자인 효과
        const scoreColor = p.confidence_score >= 80 ? '#10B981' : (p.confidence_score >= 60 ? '#F59E0B' : '#EF4444');
        const scoreBadge = `<span class="ctx-badge" style="background: rgba(${p.confidence_score >= 80 ? '16,185,129' : '245,158,11'}, 0.15); color: ${scoreColor}; font-weight: bold;">신뢰도: ${p.confidence_score}점</span>`;

        // 변이 항목 가독성 처리
        let mutationHtml = '';
        if (p.mutation_trace) {
            mutationHtml = Object.entries(p.mutation_trace).map(([key, vals]) => {
                return `<div style="font-family: monospace; font-size: 0.78rem; background: rgba(148,163,184,0.08); padding: 4px 8px; border-radius: 4px; display:flex; justify-content:space-between;">
                    <span style="color:#94A3B8;">${key}</span>
                    <span style="color:#38BDF8;">${vals[0]} ➔ <strong style="color:#10B981;">${vals[1]}</strong></span>
                </div>`;
            }).join('');
        }

        // 지표 필드 매칭 (Mock vs Real)
        const expectedRoi = p.evaluation_metrics ? p.evaluation_metrics.expected_roi : (p.metrics ? (p.metrics.expected_roi || p.metrics.roi_7d || 0.0) : 0.0);
        const riskScore = p.evaluation_metrics ? (p.evaluation_metrics.risk_score || '낮음') : (p.metrics ? (p.metrics.mdd || '보통') : '보통');

        // 상태별 액션 영역 분기
        let actionButtonsHtml = '';
        if (p.status === 'PENDING') {
            actionButtonsHtml = `
                <button class="btn sm" onclick="deferProposal(${p.id})" style="background:#475569; color:#94A3B8; border:none; padding:4px 12px; font-size:0.75rem;">보류</button>
                <button class="btn success sm" onclick="approveProposal(${p.id})" style="padding:4px 16px; font-size:0.75rem;">승인 및 적용</button>
            `;
        } else if (p.status === 'APPLIED') {
            const isAuto = p.changed_by === 'AUTO' || (p.metrics && p.metrics.applied_by === 'AUTO');
            actionButtonsHtml = `
                <span class="ctx-badge" style="background:${isAuto ? 'rgba(139,92,246,0.15)' : 'rgba(16,185,129,0.15)'}; color:${isAuto ? '#a78bfa' : '#10B981'}; border:1px solid ${isAuto ? 'rgba(139,92,246,0.2)' : 'rgba(16,185,129,0.2)'}; font-weight:bold; font-size:0.75rem; padding: 3px 10px;">
                    ${isAuto ? '⚡ 자동 적용 완료' : '✓ 수동 적용 완료'}
                </span>
            `;
        } else if (p.status === 'PRUNED') {
            actionButtonsHtml = `
                <span class="ctx-badge" style="background:rgba(239,68,68,0.1); color:#ef4444; border:1px solid rgba(239,68,68,0.15); font-size:0.75rem; padding: 3px 10px;">
                    ✗ 기준 미달 자동 폐기 (Pruned)
                </span>
            `;
        } else if (p.status === 'DEFERRED') {
            actionButtonsHtml = `
                <span class="ctx-badge" style="background:rgba(148,163,184,0.1); color:#94a3b8; border:1px solid rgba(148,163,184,0.15); font-size:0.75rem; padding: 3px 10px;">
                    보류 상태 (Deferred)
                </span>
            `;
        } else {
            actionButtonsHtml = `
                <span class="ctx-badge" style="background:rgba(148,163,184,0.1); color:#94a3b8; font-size:0.75rem; padding: 3px 10px;">
                    ${p.status}
                </span>
            `;
        }

        // Audit Log 분석 근거 및 Counterfactual ROI 마크업 구성
        let auditHtml = '';
        if (p.audit_log_json && Object.keys(p.audit_log_json).length > 0) {
            const audit = p.audit_log_json;
            const limitTriggered = audit.performance_limit_triggered ? '<strong style="color:#ef4444;">성능하한차단(승률/PF)</strong>' : '';
            auditHtml = `
                <div style="margin-top: 8px; font-size: 0.75rem; background: rgba(239,68,68,0.05); border: 1px solid rgba(239,68,68,0.12); padding: 8px; border-radius: 4px; color: #94A3B8;">
                    <div style="color: #f87171; font-weight: bold; margin-bottom: 4px;">🤖 AI 폐기 분석 근거:</div>
                    <div style="display: flex; flex-direction: column; gap: 3px; font-family: monospace;">
                        <div>• 기본 점수: ${audit.base_score}점</div>
                        <div>• 국면 조정: ${audit.regime_weight >= 0 ? '+' : ''}${audit.regime_weight}점</div>
                        <div>• 롤백 감점: -${audit.rollback_penalty}점</div>
                        <div>• 다양성 감점: -${audit.diversity_penalty}점 (최소거리: ${audit.min_distance_observed !== null ? audit.min_distance_observed : '없음'})</div>
                        ${limitTriggered ? `<div>• 제한 트리거: ${limitTriggered}</div>` : ''}
                    </div>
                </div>
            `;
        }

        let counterfactualHtml = '';
        if ((p.status === 'PRUNED' || p.status === 'DEFERRED') && p.is_counterfactual_tracked > 0) {
            const roiVal = p.counterfactual_roi !== undefined ? p.counterfactual_roi : 0.0;
            const roiColor = roiVal >= 0 ? '#10B981' : '#EF4444';
            counterfactualHtml = `
                <div style="margin-top: 8px; font-size: 0.78rem; background: rgba(139,92,246,0.08); padding: 6px 10px; border-radius: 4px; border: 1px dashed rgba(139,92,246,0.25); display: flex; justify-content: space-between; align-items: center;">
                    <span style="color:#c084fc; font-weight: bold;">🔬 반사실적 가상 실적 (Shadow ROI):</span>
                    <strong style="color:${roiColor}; font-family: monospace;">${roiVal >= 0 ? '+' : ''}${roiVal.toFixed(2)}%</strong>
                </div>
            `;
        }

        const pathHash = p.decision_path_hash ? `<div style="font-family: monospace; font-size: 0.7rem; color: #64748B; margin-top: 2px;">Hash: ${p.decision_path_hash}</div>` : '';

        card.innerHTML = `
            <div style="display:flex; justify-content:space-between; align-items:center; border-bottom: 1px solid rgba(148,163,184,0.1); padding-bottom: 6px;">
                <div>
                    <span style="color:#818CF8; font-weight:bold; font-size: 0.85rem;">제안 번호: #${p.id}</span>
                    ${pathHash}
                </div>
                ${scoreBadge}
            </div>
            
            <div style="display:flex; flex-direction:column; gap:6px; font-size:0.8rem; margin-top: 8px;">
                <div><span style="color:#94A3B8;">예상 ROI (백테스트):</span> <strong style="color:#10B981; font-family: monospace; margin-left: 5px;">+${expectedRoi}%</strong></div>
                <div><span style="color:#94A3B8;">위험도 위험 점수 (MDD):</span> <strong style="color:#e0e0e0; font-family: monospace; margin-left: 5px;">${riskScore}</strong></div>
            </div>

            <div style="display:flex; flex-direction:column; gap:4px; margin-top:5px;">
                <span style="color:#94A3B8; font-size: 0.75rem; font-weight:bold;">🔄 파라미터 변이 상세:</span>
                ${mutationHtml}
            </div>

            ${auditHtml}
            ${counterfactualHtml}

            <div style="display:flex; gap:10px; margin-top: 10px; justify-content: flex-end; border-top: 1px solid rgba(148,163,184,0.05); padding-top: 10px;">
                ${actionButtonsHtml}
            </div>
        `;
        container.appendChild(card);
    });
}

/**
 * 대시보드 내 셀렉트 값에 따라 cachedProposals 제안 카드를 정교하게 필터링하여 다시 그립니다.
 */
function filterProposals() {
    const filterEl = document.getElementById('proposal-status-filter');
    if (!filterEl) return;

    const filterVal = filterEl.value;
    let filtered = [];

    if (filterVal === 'PENDING') {
        filtered = cachedProposals.filter(p => p.status === 'PENDING');
    } else if (filterVal === 'APPLIED') {
        filtered = cachedProposals.filter(p => p.status === 'APPLIED');
    } else if (filterVal === 'PRUNED_DEFERRED') {
        filtered = cachedProposals.filter(p => p.status === 'PRUNED' || p.status === 'DEFERRED');
    }

    renderProposalCards(filtered);
}

/**
 * 버전 변경 이력 및 원클릭 롤백 테이블 렌더링
 */
function renderVersionHistoryTable(history, currentVersionId) {
    const tbody = document.getElementById('version-history-tbody');
    if (!tbody) return;

    if (!history || history.length === 0) {
        tbody.innerHTML = '<tr><td colspan="8" style="text-align:center; padding:20px; color:#64748B;">파라미터 변경 이력이 존재하지 않습니다.</td></tr>';
        return;
    }

    tbody.innerHTML = '';
    history.forEach(h => {
        const tr = document.createElement('tr');
        const isCurrent = h.version_id === currentVersionId;
        if (isCurrent) {
            tr.style.background = 'rgba(16, 185, 129, 0.05)';
        }

        const formattedNew = `<pre style="font-family: monospace; font-size: 0.75rem; color: #10B981; margin:0;">${JSON.stringify(h.new_params)}</pre>`;
        const formattedOld = h.old_params ? `<pre style="font-family: monospace; font-size: 0.75rem; color: #94A3B8; margin:0;">${JSON.stringify(h.old_params)}</pre>` : '<span style="color:#475569;">Initial</span>';
        
        let reasonBadge = '';
        if (h.change_reason === 'PROPOSAL_APPLY') {
            if (h.changed_by === 'AUTO') {
                reasonBadge = '<span class="ctx-badge" style="background:rgba(139,92,246,0.15); color:#a78bfa; font-size:0.72rem; border:1px solid rgba(139,92,246,0.2)">AI 자동 적용</span>';
            } else {
                reasonBadge = '<span class="ctx-badge" style="background:rgba(99,102,241,0.15); color:#818cf8; font-size:0.72rem; border:1px solid rgba(99,102,241,0.15)">AI 제안 적용</span>';
            }
        } else if (h.change_reason === 'ROLLBACK') {
            reasonBadge = '<span class="ctx-badge" style="background:rgba(239,68,68,0.15); color:#f87171; font-size:0.72rem; border:1px solid rgba(239,68,68,0.15)">원클릭 롤백</span>';
        } else {
            reasonBadge = `<span class="ctx-badge" style="background:rgba(148,163,184,0.15); color:#cbd5e1; font-size:0.72rem; border:1px solid rgba(148,163,184,0.15)">${h.change_reason}</span>`;
        }

        let changedByBadge = '';
        if (h.changed_by === 'USER') {
            changedByBadge = '<span class="badge" style="background: #1E293B; color: #94A3B8; font-size: 0.7rem; border: 1px solid rgba(148, 163, 184, 0.1);">USER</span>';
        } else if (h.changed_by === 'AUTO') {
            changedByBadge = '<span class="badge" style="background: rgba(139, 92, 246, 0.2); color: #A78BFA; font-size: 0.7rem; border: 1px solid rgba(139, 92, 246, 0.4); font-weight: bold;">AUTO (자동)</span>';
        } else {
            changedByBadge = `<span class="badge" style="background: #0F172A; color: #64748B; font-size: 0.7rem; border: 1px solid rgba(148, 163, 184, 0.05);">${h.changed_by}</span>`;
        }

        tr.innerHTML = `
            <td style="font-family: monospace; font-weight: bold; color: ${isCurrent ? '#10B981' : '#F8FAFC'};">V${h.version_id} ${isCurrent ? '★' : ''}</td>
            <td style="font-family: monospace; color: #94A3B8;">${h.parent_version_id ? `V${h.parent_version_id}` : '-'}</td>
            <td>${changedByBadge}</td>
            <td>${reasonBadge}</td>
            <td>${formattedNew}</td>
            <td>${formattedOld}</td>
            <td style="font-family: monospace; font-size: 0.75rem; color:#94A3B8;">${formatTimestamp(h.created_at)}</td>
            <td style="text-align: center;">
                ${isCurrent ? 
                    '<span style="color:#64748B; font-size:0.78rem;">활성 중</span>' : 
                    `<button class="btn danger sm" onclick="executeRollback('${h.strategy_id}', ${h.version_id})" style="padding: 2px 8px; font-size: 0.7rem; background:#EF4444; border:none; color:white;">복구</button>`
                }
            </td>
        `;
        tbody.appendChild(tr);
    });
}

/**
 * 제안 승인 API 요청
 */
async function approveProposal(proposalId) {
    if (!confirm(`제안 #${proposalId}을 승인하고 실시간 전략 파라미터로 즉시 적용하시겠습니까?`)) {
        return;
    }

    try {
        // API 우선 시도 (Server Authoritative)
        const res = await APIClient.approveProposal(proposalId);
        alert(`제안 승인 및 V${res.new_version_id || res.version_id || res.version} 갱신 완료!`);
        selectStrategy(selectedStrategyId);
    } catch(e) {
        console.warn("제안 승인 API 호출 실패, 모의 데이터 적용으로 Fallback을 시도합니다:", e);
        try {
            // Mock 데이터 갱신 모사
            const prop = MOCK_PROPOSALS.find(p => p.id === proposalId);
            if (prop) {
                prop.status = 'APPLIED';
                prop.outcome = 'RUNNING';
                prop.applied_at = Date.now();
                
                const detail = MOCK_STRATEGIES_DETAIL[selectedStrategyId];
                if (detail) {
                    detail.rollback_source_version = null;
                    detail.current_version_id = detail.current_version_id + 1;
                    detail.current_params = prop.proposed_params;
                    detail.applied_at = Date.now();
                }
                
                MOCK_HISTORY.push({
                    "version_id": detail.current_version_id,
                    "parent_version_id": detail.current_version_id - 1,
                    "changed_by": "USER",
                    "change_reason": "PROPOSAL_APPLY",
                    "new_params": prop.proposed_params,
                    "old_params": prop.original_params,
                    "created_at": Date.now()
                });
                
                MOCK_SNAPSHOTS.push({
                    "timestamp": Date.now(),
                    "roi": 0.5,
                    "version_id": detail.current_version_id,
                    "snapshot_type": "PARAMETER_CHANGE"
                });
                alert("제안이 성공적으로 적용되었습니다. (Mock Fallback)");
                selectStrategy(selectedStrategyId);
            } else {
                throw new Error("대상 Mock 제안 데이터를 찾을 수 없습니다.");
            }
        } catch (mockErr) {
            alert(`제안 적용 실패: ${e.message || e}`);
        }
    }
}

/**
 * 제안 보류 요청
 */
async function deferProposal(proposalId) {
    if (!confirm(`제안 #${proposalId}을 보류 상태로 변경하시겠습니까?`)) {
        return;
    }
    
    // 단순 PENDING 상태 유지 또는 보류 처리 UI 모사
    alert("제안이 보류 처리되었습니다.");
}

/**
 * 원클릭 롤백 API 요청
 */
async function executeRollback(strategyId, targetVersionId) {
    if (!confirm(`전략 ${strategyId.toUpperCase()} 설정을 V${targetVersionId} 파라미터로 롤백 복구하시겠습니까?`)) {
        return;
    }

    try {
        // API 우선 시도
        const res = await APIClient.rollbackStrategy(strategyId, targetVersionId);
        alert(`전략 롤백 복구 완료! (신규 롤백 버전: V${res.new_version_id})`);
        selectStrategy(strategyId);
    } catch(e) {
        console.warn("전략 롤백 API 호출 실패, 모의 데이터 적용으로 Fallback을 시도합니다:", e);
        try {
            const detail = MOCK_STRATEGIES_DETAIL[strategyId];
            const targetHist = MOCK_HISTORY.find(h => h.version_id === targetVersionId);
            
            if (detail && targetHist) {
                const prevVer = detail.current_version_id;
                detail.rollback_source_version = prevVer;
                detail.current_version_id = prevVer + 1;
                detail.current_params = targetHist.new_params;
                detail.applied_at = Date.now();
                
                MOCK_HISTORY.push({
                    "version_id": detail.current_version_id,
                    "parent_version_id": targetVersionId,
                    "changed_by": "USER",
                    "change_reason": "ROLLBACK",
                    "new_params": targetHist.new_params,
                    "old_params": targetHist.old_params,
                    "created_at": Date.now()
                });
                
                MOCK_SNAPSHOTS.push({
                    "timestamp": Date.now(),
                    "roi": -1.2,
                    "version_id": detail.current_version_id,
                    "snapshot_type": "ROLLBACK"
                });
                alert("V" + targetVersionId + " 설정으로 안전 롤백을 완료하였습니다. (Mock Fallback)");
                selectStrategy(strategyId);
            } else {
                throw new Error("대상 Mock 버전/히스토리 데이터를 찾을 수 없습니다.");
            }
        } catch (mockErr) {
            alert(`롤백 복구 실패: ${e.message || e}`);
        }
    }
}

/**
 * 수집한 전략 정보를 바탕으로 카드 리스트 및 입력 폼을 렌더링합니다. (설정 탭 전용)
 */
function renderStrategyCardsInSettings(strategies) {
    const container = document.getElementById('settings-strategy-container');
    if (!container) return;
    container.innerHTML = '';

    strategies.forEach(s => {
        const card = document.createElement('div');
        let paramsHtml = '';
        for (const [key, info] of Object.entries(s.params)) {
            const inputType = info.type === 'str' ? 'text' : 'number';
            const stepAttr = info.type === 'float' ? 'step="any"' : (info.type === 'int' ? 'step="1"' : '');
            paramsHtml += `
                <div class="param-group">
                    <div class="param-row">
                        <label>${key}</label>
                        <input type="${inputType}" ${stepAttr} class="dark-input param-input" 
                               data-strategy="${s.id}" data-key="${key}" data-type="${info.type}"
                               value="${info.current !== undefined ? info.current : info.default}">
                    </div>
                    <div class="param-desc">${info.description}</div>
                </div>
            `;
        }

        const isEnabled = s.enabled !== false;
        card.className = `strategy-item ${isEnabled ? '' : 'disabled'}`;
        card.style.opacity = isEnabled ? '1' : '0.5';

        const typeColors = { "ENTRY": "#4A90E2", "EXIT": "#FF4B4B", "BOTH": "#F5A623" };
        const typeLabel = s.type === "ENTRY" ? "매수" : (s.type === "EXIT" ? "매도" : "공용");

        card.innerHTML = `
            <h4>
                <div style="display: flex; flex-direction: column;">
                    <span data-id="${s.id}">${s.name}</span>
                    <span class="type-badge" style="background: ${typeColors[s.type] || '#666'}; font-size: 0.6rem; padding: 2px 6px; border-radius: 10px; width: fit-content; margin-top: 4px; color: white;">${typeLabel}</span>
                </div>
                <div style="display: flex; align-items: center; gap: 5px;">
                    <span class="badge" style="font-size: 0.7rem; background: ${isEnabled ? '#1a472a' : '#471a1a'}; color: ${isEnabled ? '#4caf50' : '#FF4B4B'};">${isEnabled ? '활성' : '비활성'}</span>
                    <button class="btn sm ${isEnabled ? 'danger' : 'primary'}" onclick="toggleStrategyStatus('${s.id}', ${isEnabled})" style="padding: 2px 8px; font-size: 0.7rem;">
                        ${isEnabled ? '사용 안함' : '사용함'}
                    </button>
                </div>
            </h4>
            <div class="desc">${s.description}</div>
            <div class="strategy-params">
                ${paramsHtml}
            </div>
            <div class="strategy-actions" style="margin-top: 15px;">
                <button class="btn primary sm" onclick="saveStrategyParams('${s.id}')" style="width: 100%;">설정 저장</button>
            </div>
        `;
        container.appendChild(card);
    });
}

/**
 * 웹소켓으로 수신된 실시간 전략 연산 상태를 화면에 반영합니다. (사이드바 컴팩트 형태 등)
 */
function updateStrategyStatusUI(status) {
    const strategyId = status.strategy_id;
    const statusBadge = document.getElementById('sidebar-strategy-status');
    if (statusBadge) {
        statusBadge.className = 'status-indicator status-on';
    }
}

/**
 * 수정한 전략 파라미터 설정을 API를 통해 백엔드에 보관합니다.
 */
async function saveStrategyParams(strategyId) {
    const inputs = document.querySelectorAll(`.param-input[data-strategy="${strategyId}"]`);
    const params = {};
    inputs.forEach(input => {
        const type = input.dataset.type;
        if (type === 'str') {
            params[input.dataset.key] = input.value;
        } else if (type === 'int') {
            params[input.dataset.key] = parseInt(input.value) || 0;
        } else {
            params[input.dataset.key] = parseFloat(input.value) || 0;
        }
    });

    try {
        await APIClient.saveStrategyParams(strategyId, params);
        alert(`${strategyId} 전략 설정이 저장되었습니다.`);
        selectStrategy(selectedStrategyId);
    } catch (e) {
        alert("설정 저장 실패");
    }
}

/**
 * 특정 전략의 실행 및 사용 유무를 API로 전환합니다.
 */
async function toggleStrategyStatus(strategyId, currentEnabled) {
    try {
        await APIClient.toggleStrategyStatus(strategyId, currentEnabled);
        selectStrategy(selectedStrategyId);
    } catch (e) {
        alert("상태 변경 실패");
    }
}

// 전역 window 바인딩
window.loadStrategies = loadStrategies;
window.selectStrategy = selectStrategy;
window.saveStrategyParams = saveStrategyParams;
window.toggleStrategyStatus = toggleStrategyStatus;
window.updateStrategyStatusUI = updateStrategyStatusUI;
window.approveProposal = approveProposal;
window.deferProposal = deferProposal;
window.executeRollback = executeRollback;
window.refreshAIHealth = refreshAIHealth;

// ─────────────────────────────────────────────
// Step 4: AI 건강 지표 패널 렌더링
// ─────────────────────────────────────────────

/**
 * 선택된 전략의 AI 건강 지표를 API에서 불러와 패널에 렌더링합니다.
 */
async function loadAIHealthData(strategyId) {
    try {
        const [diversityData, cfData] = await Promise.all([
            fetch(`/api/intelligence/diversity${strategyId ? '?strategy_id=' + strategyId : ''}`).then(r => r.json()),
            fetch(`/api/intelligence/counterfactual-summary${strategyId ? '?strategy_id=' + strategyId : ''}`).then(r => r.json()),
        ]);
        renderAIHealthPanel(diversityData, cfData);
    } catch (e) {
        console.warn('[AIHealth] 데이터 로드 실패:', e);
        const cfList = document.getElementById('ai-counterfactual-list');
        if (cfList) cfList.innerHTML = '<p style="color:#64748B;font-size:0.8rem;">데이터를 불러올 수 없습니다.</p>';
    }
}

/**
 * 새로고침 버튼 핸들러
 */
function refreshAIHealth() {
    if (selectedStrategyId) loadAIHealthData(selectedStrategyId);
}

/**
 * AI 건강 지표 패널의 모든 UI 요소를 갱신합니다.
 */
function renderAIHealthPanel(diversityData, cfData) {
    // 1. Entropy 게이지
    const entropy = diversityData.entropy ?? 0;
    const entropyEl = document.getElementById('ai-entropy-value');
    const entropyBar = document.getElementById('ai-entropy-bar');
    const entropyLabel = document.getElementById('ai-entropy-label');
    const alertBadge = document.getElementById('ai-health-alert-badge');

    if (entropyEl) entropyEl.textContent = entropy.toFixed(2);
    if (entropyBar) {
        const pct = Math.round(entropy * 100);
        entropyBar.style.width = pct + '%';
        if (entropy < 0.3) {
            entropyBar.style.background = '#EF4444';   // 빨강 — 수렴 위험
            entropyEl.style.color = '#EF4444';
            if (entropyLabel) entropyLabel.textContent = '⚠ 수렴 위험 (< 0.3)';
        } else if (entropy < 0.5) {
            entropyBar.style.background = '#F59E0B';   // 노랑 — 주의
            entropyEl.style.color = '#F59E0B';
            if (entropyLabel) entropyLabel.textContent = '⚡ 주의 (0.3 ~ 0.5)';
        } else {
            entropyBar.style.background = '#10B981';   // 초록 — 정상
            entropyEl.style.color = '#10B981';
            if (entropyLabel) entropyLabel.textContent = '✅ 정상 (≥ 0.5)';
        }
    }
    if (alertBadge) alertBadge.style.display = diversityData.convergence_alert ? 'inline-block' : 'none';

    // 2. λ 보정 신호
    const boost = diversityData.combined_boost ?? {};
    const lambdaEl = document.getElementById('ai-lambda-boost');
    const alertLevelEl = document.getElementById('ai-alert-level');
    const thresholdDeltaEl = document.getElementById('ai-threshold-delta');

    if (lambdaEl) lambdaEl.textContent = '×' + (boost.lambda_boost ?? '—');
    if (alertLevelEl) {
        const level = boost.alert_level ?? 'NONE';
        alertLevelEl.textContent = level;
        const colors = { HIGH: '#EF4444', MEDIUM: '#F59E0B', NONE: '#64748B' };
        const bgs = { HIGH: 'rgba(239,68,68,0.15)', MEDIUM: 'rgba(245,158,11,0.15)', NONE: 'rgba(100,116,139,0.2)' };
        alertLevelEl.style.color = colors[level] || '#64748B';
        alertLevelEl.style.background = bgs[level] || 'rgba(100,116,139,0.2)';
    }
    if (thresholdDeltaEl) {
        const delta = boost.diversity_threshold_delta ?? 0;
        thresholdDeltaEl.textContent = `다양성 임계치 조정: +${delta.toFixed(2)}`;
    }

    // 3. Pruning Accuracy
    const pa = diversityData.pruning_accuracy ?? {};
    const outperformEl = document.getElementById('ai-outperform-rate');
    const countEl = document.getElementById('ai-pruning-counts');
    const biasAlertEl = document.getElementById('ai-bias-alert');

    if (outperformEl) {
        const rate = ((pa.outperform_rate ?? 0) * 100).toFixed(1);
        outperformEl.textContent = rate + '%';
        outperformEl.style.color = pa.bias_alert ? '#EF4444' : '#D946EF';
    }
    if (countEl) countEl.textContent = `추적 완료: ${pa.total_tracked ?? 0}건 / 오판: ${pa.outperformed_count ?? 0}건`;
    if (biasAlertEl) biasAlertEl.style.display = pa.bias_alert ? 'block' : 'none';

    // 3.5. Replay Correction (비동기 랭킹 보정) 상태 바인딩
    const rs = diversityData.replay_status ?? {};
    const driftEl = document.getElementById('ai-replay-drift');
    const replayStatusEl = document.getElementById('ai-replay-status');
    const correctedAtEl = document.getElementById('ai-replay-corrected-at');
    const blockReasonEl = document.getElementById('ai-replay-block-reason');

    if (driftEl) {
        const driftVal = rs.rank_drift ?? 0.0;
        driftEl.textContent = driftVal.toFixed(4);
    }
    if (replayStatusEl) {
        const isBlocked = rs.correction_active ?? false;
        
        if (isBlocked) {
            replayStatusEl.textContent = '⚠ 승격 차단 (보정 중)';
            replayStatusEl.style.color = '#EF4444';
            replayStatusEl.style.background = 'rgba(239, 68, 68, 0.15)';
            if (blockReasonEl && rs.promotion_block_reason) {
                blockReasonEl.textContent = `차단 사유: ${rs.promotion_block_reason}`;
                blockReasonEl.style.display = 'block';
            } else if (blockReasonEl) {
                blockReasonEl.style.display = 'none';
            }
        } else {
            replayStatusEl.textContent = '정상 (대기)';
            replayStatusEl.style.color = '#10B981';
            replayStatusEl.style.background = 'rgba(16, 185, 129, 0.15)';
            if (blockReasonEl) {
                blockReasonEl.style.display = 'none';
            }
        }
    }
    if (correctedAtEl) {
        const ts = rs.last_replay_corrected_at;
        if (ts && ts > 0) {
            correctedAtEl.textContent = `최종 보정: ${formatTimestamp(ts * 1000)}`;
        } else {
            correctedAtEl.textContent = '최종 보정: 없음';
        }
    }

    // 4. Entropy 시계열 Plotly 미니차트
    const chartEl = document.getElementById('ai-entropy-chart');
    if (chartEl && diversityData.decision_drift?.entropy_timeline?.length) {
        const tl = diversityData.decision_drift.entropy_timeline;
        const xs = tl.map(d => new Date(d.ts).toLocaleDateString('ko-KR', { month: 'short', day: 'numeric' }));
        const ys = tl.map(d => d.entropy);
        Plotly.newPlot(chartEl, [{
            x: xs, y: ys, type: 'scatter', mode: 'lines+markers',
            line: { color: '#38BDF8', width: 2 },
            marker: { size: 5, color: ys.map(v => v < 0.3 ? '#EF4444' : v < 0.5 ? '#F59E0B' : '#10B981') },
            fill: 'tozeroy', fillcolor: 'rgba(56,189,248,0.07)',
            hovertemplate: '%{y:.3f}<extra></extra>',
        }], {
            paper_bgcolor: 'transparent', plot_bgcolor: 'transparent',
            margin: { l: 28, r: 8, t: 8, b: 28 },
            xaxis: { color: '#64748B', tickfont: { size: 10 }, gridcolor: 'rgba(148,163,184,0.08)' },
            yaxis: { color: '#64748B', tickfont: { size: 10 }, gridcolor: 'rgba(148,163,184,0.08)', range: [0, 1.05] },
            shapes: [{ type: 'line', x0: 0, x1: 1, xref: 'paper', y0: 0.3, y1: 0.3,
                line: { color: '#EF4444', width: 1, dash: 'dot' } }],
        }, { displayModeBar: false, responsive: true });
    }

    // 5. Counterfactual 목록
    const cfListEl = document.getElementById('ai-counterfactual-list');
    if (!cfListEl) return;
    if (!cfData?.items?.length) {
        cfListEl.innerHTML = '<p style="color:#64748B;font-size:0.8rem;">추적 중인 반사실적 제안이 없습니다.</p>';
    } else {
        cfListEl.innerHTML = cfData.items.map(item => {
            const tracked = item.is_tracked === 2 ? '완료' : '추적중';
            const roiColor = item.counterfactual_roi > 0 ? '#FF4B4B' : '#64748B';
            const trackedColor = item.is_tracked === 2 ? '#10B981' : '#F59E0B';
            return `
                <div style="display:flex;align-items:center;gap:10px;padding:8px 10px;background:#1E293B;border:1px solid #334155;border-radius:5px;font-size:0.78rem;">
                    <span style="color:#94A3B8;min-width:24px;">#${item.proposal_id}</span>
                    <span style="flex:1;color:#F8FAFC;font-family:'Roboto Mono',monospace;">신뢰도: ${item.confidence_score}점</span>
                    <span style="color:${roiColor};font-weight:700;font-family:'Roboto Mono',monospace;">가상ROI: ${item.counterfactual_roi > 0 ? '+' : ''}${item.counterfactual_roi.toFixed(2)}%</span>
                    <span style="color:#94A3B8;">MDD: ${item.counterfactual_mdd.toFixed(2)}%</span>
                    <span style="color:${trackedColor};font-size:0.7rem;padding:1px 5px;border-radius:3px;background:rgba(0,0,0,0.2);">${tracked}</span>
                    <span style="color:#64748B;font-size:0.7rem;">${item.days_observed}일 관찰</span>
                </div>
            `;
        }).join('');
    }

    // 6. Mutation Graph 캐싱 및 호출
    if (diversityData.mutation_graph) {
        lastMutationGraphData = diversityData.mutation_graph;
        const selectEl = document.getElementById('mutation-graph-param-select');
        if (selectEl && (!selectEl.options || selectEl.options.length === 0)) {
            const trends = diversityData.mutation_graph.param_trend || {};
            const params = Object.keys(trends);
            selectEl.innerHTML = params.map(p => `<option value="${p}">${p}</option>`).join('');
        }
        renderMutationGraph();
    }
}

/**
 * Plotly 3-Layer ScatterGL 파라미터 변이 계보 차트 렌더링
 */
function renderMutationGraph() {
    const chartDiv = document.getElementById('ai-mutation-chart');
    const metaDiv = document.getElementById('ai-mutation-meta-info');
    if (!chartDiv || !lastMutationGraphData) return;

    const selectEl = document.getElementById('mutation-graph-param-select');
    if (!selectEl || selectEl.options.length === 0) {
        chartDiv.innerHTML = '<div style="display:flex; justify-content:center; align-items:center; height:100%; color:#64748B;">표시 가능한 파라미터가 없습니다.</div>';
        return;
    }

    const selectedParam = selectEl.value;
    const collapsePruned = document.getElementById('ai-mutation-collapse-pruned')?.checked ?? false;
    const highlightBest = document.getElementById('ai-mutation-highlight-best')?.checked ?? true;

    const rawNodes = lastMutationGraphData.nodes || [];
    const rawEdges = lastMutationGraphData.edges || [];
    const bestPathHashes = lastMutationGraphData.best_path_nodes || [];
    const meta = lastMutationGraphData.graph_meta || {};

    // 1. 노드 필터링
    let filteredNodes = rawNodes;
    if (collapsePruned) {
        filteredNodes = rawNodes.filter(n => n.status !== 'PRUNED');
    }
    const filteredNodeHashes = new Set(filteredNodes.map(n => n.hash));

    // 2. 에지 필터링
    let filteredEdges = rawEdges.filter(e => filteredNodeHashes.has(e.from) && filteredNodeHashes.has(e.to));

    // 3. 노드 2D 좌표 맵 구성 (X: Time, Y: Parameter value)
    const nodeMap = {};
    filteredNodes.forEach(n => {
        const val = n.proposed_params[selectedParam];
        nodeMap[n.hash] = {
            x: new Date(n.created_at),
            y: typeof val === 'number' ? val : 0.0,
            node: n
        };
    });

    const traces = [];

    // Layer 1: Edges (배경 연결선)
    if (filteredEdges.length > 0) {
        const edgeX = [];
        const edgeY = [];
        filteredEdges.forEach(e => {
            const fromNode = nodeMap[e.from];
            const toNode = nodeMap[e.to];
            if (fromNode && toNode) {
                edgeX.push(fromNode.x, toNode.x, null);
                edgeY.push(fromNode.y, toNode.y, null);
            }
        });

        const nodeCount = filteredNodes.length;
        const opacity = Math.min(0.2, Math.max(0.05, 1.0 / Math.log(nodeCount + 2)));

        traces.push({
            x: edgeX,
            y: edgeY,
            type: 'scattergl',
            mode: 'lines',
            name: '변이 경로',
            line: {
                color: `rgba(148, 163, 184, ${opacity})`,
                width: 1
            },
            hoverinfo: 'skip',
            showlegend: false
        });
    }

    // Layer 2: Nodes (상태별 마커)
    if (filteredNodes.length > 0) {
        const nodeX = [];
        const nodeY = [];
        const nodeColors = [];
        const nodeSizes = [];
        const nodeTexts = [];

        const colorsMap = {
            'APPLIED': '#10B981', // 초록
            'PENDING': '#F59E0B', // 노랑
            'PRUNED': '#64748B',  // 회색
            'DEFERRED': '#D946EF', // 주황
            'ROLLED_BACK': '#EF4444' // 빨강
        };

        filteredNodes.forEach(n => {
            const pt = nodeMap[n.hash];
            if (!pt) return;

            nodeX.push(pt.x);
            nodeY.push(pt.y);
            nodeColors.push(colorsMap[n.status] || '#cbd5e1');
            nodeSizes.push(Math.max(6, Math.min(18, (n.score - 40) * 0.2 + 6)));

            const origVal = n.original_params[selectedParam] !== undefined ? n.original_params[selectedParam] : '-';
            const proposedVal = n.proposed_params[selectedParam] !== undefined ? n.proposed_params[selectedParam] : '-';
            const delta = (typeof origVal === 'number' && typeof proposedVal === 'number') 
                ? `${(proposedVal - origVal) >= 0 ? '+' : ''}${(proposedVal - origVal).toFixed(4)}` 
                : '-';

            nodeTexts.push(`
                <b>제안 #${n.id}</b> (${n.status})<br>
                시간: ${formatTimestamp(n.created_at)}<br>
                신뢰도: ${n.score}점<br>
                예상 ROI: +${n.expected_roi.toFixed(2)}%<br>
                Shadow ROI: ${n.counterfactual_roi.toFixed(2)}%<br>
                파라미터 [${selectedParam}]: ${origVal} ➔ ${proposedVal} (Δ: ${delta})
            `);
        });

        traces.push({
            x: nodeX,
            y: nodeY,
            type: 'scattergl',
            mode: 'markers',
            name: '제안 노드',
            marker: {
                color: nodeColors,
                size: nodeSizes,
                line: {
                    color: '#0F172A',
                    width: 1
                }
            },
            text: nodeTexts,
            hoverinfo: 'text'
        });
    }

    // Layer 3: Best Path Highlight (최적 경로 에메랄드색 오버레이)
    if (highlightBest && bestPathHashes.length > 1) {
        const pathX = [];
        const pathY = [];
        bestPathHashes.forEach(h => {
            const pt = nodeMap[h];
            if (pt) {
                pathX.push(pt.x);
                pathY.push(pt.y);
            }
        });

        if (pathX.length > 0) {
            traces.push({
                x: pathX,
                y: pathY,
                type: 'scattergl',
                mode: 'lines+markers',
                name: '최적 ROI 경로',
                line: {
                    color: '#10B981',
                    width: 2.5
                },
                marker: {
                    size: 7,
                    color: '#10B981',
                    line: {
                        color: '#0F172A',
                        width: 1
                    }
                },
                hoverinfo: 'skip'
            });
        }
    }

    const layout = {
        paper_bgcolor: 'transparent',
        plot_bgcolor: '#0F172A',
        margin: { l: 45, r: 15, t: 15, b: 35 },
        xaxis: {
            color: '#64748B',
            tickfont: { size: 9, family: 'Roboto Mono' },
            gridcolor: 'rgba(148, 163, 184, 0.05)',
            type: 'date',
            zeroline: false
        },
        yaxis: {
            color: '#64748B',
            tickfont: { size: 9, family: 'Roboto Mono' },
            gridcolor: 'rgba(148, 163, 184, 0.05)',
            zeroline: false,
            title: {
                text: selectedParam,
                font: { color: '#94A3B8', size: 10, family: 'Pretendard' }
            }
        },
        showlegend: false
    };

    Plotly.newPlot(chartDiv, traces, layout, { displayModeBar: false, responsive: true });

    // Meta 정보 업데이트
    if (metaDiv && meta.node_count !== undefined) {
        metaDiv.innerHTML = `
            <span>노드: ${meta.node_count} | 에지: ${meta.edge_count} | 최대깊이: ${meta.max_depth}</span>
            <span>분기수: ${meta.branching_factor} | 밀도: ${meta.density}</span>
        `;
    }
}
window.renderMutationGraph = renderMutationGraph;

if (typeof ViewRouter !== 'undefined') {
    ViewRouter.registerRoute('strategy-view', () => {
        loadStrategies();
    });
}

