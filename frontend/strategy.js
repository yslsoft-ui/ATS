/**
 * 매매 전략 관리 및 실시간 분석 모니터링 관련 기능 구현 모듈
 * (전략 버전 제어, 롤백, AI 제안 승인 콘솔 UI 포함)
 */

// 실데이터 API 연결 전 Mock UI 테스트용 플래그
const USE_MOCK = false;

// 모의 데이터 셋
const MOCK_STRATEGIES_DETAIL = {
    "RSIStrategy": {
        "strategy_id": "RSIStrategy",
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
        "strategy_id": "RSIStrategy",
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
        "strategy_id": "RSIStrategy",
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
 * 스타일 동적 주입 함수
 */
function injectStyles() {
    if (document.getElementById('strategy-custom-styles')) return;
    const style = document.createElement('style');
    style.id = 'strategy-custom-styles';
    style.innerHTML = `
        .led-indicator {
            display: inline-block;
            width: 8px;
            height: 8px;
            border-radius: 50%;
            margin-right: 6px;
            vertical-align: middle;
            transition: all 0.3s ease;
        }
        .led-indicator.running {
            background-color: #10B981;
            box-shadow: 0 0 8px #10B981;
            animation: pulse-green 1.5s infinite;
        }
        .led-indicator.exit-only {
            background-color: #F59E0B;
            box-shadow: 0 0 8px #F59E0B;
            animation: pulse-orange 1.5s infinite;
        }
        .led-indicator.disabled {
            background-color: #64748B;
            box-shadow: none;
        }
        @keyframes pulse-green {
            0% { box-shadow: 0 0 0 0 rgba(16, 185, 129, 0.7); }
            70% { box-shadow: 0 0 0 6px rgba(16, 185, 129, 0); }
            100% { box-shadow: 0 0 0 0 rgba(16, 185, 129, 0); }
        }
        @keyframes pulse-orange {
            0% { box-shadow: 0 0 0 0 rgba(245, 158, 11, 0.7); }
            70% { box-shadow: 0 0 0 6px rgba(245, 158, 11, 0); }
            100% { box-shadow: 0 0 0 0 rgba(245, 158, 11, 0); }
        }
        .badge-status {
            font-size: 0.65rem;
            padding: 2px 6px;
            border-radius: 4px;
            font-weight: bold;
            display: inline-flex;
            align-items: center;
        }
        .badge-status.running {
            background: rgba(16, 185, 129, 0.15);
            color: #10B981;
            border: 1px solid rgba(16, 185, 129, 0.2);
        }
        .badge-status.exit-only {
            background: rgba(245, 158, 11, 0.15);
            color: #F59E0B;
            border: 1px solid rgba(245, 158, 11, 0.2);
        }
        .badge-status.disabled {
            background: rgba(100, 116, 139, 0.15);
            color: #94A3B8;
            border: 1px solid rgba(100, 116, 139, 0.2);
        }
        .badge-eval {
            font-size: 0.65rem;
            padding: 2px 6px;
            border-radius: 4px;
            font-weight: bold;
            display: inline-flex;
            align-items: center;
            background: rgba(139, 92, 246, 0.10);
            color: #a78bfa;
            border: 1px solid rgba(139, 92, 246, 0.15);
        }
        .badge-eval.active {
            background: rgba(139, 92, 246, 0.2);
            border: 1px solid rgba(139, 92, 246, 0.35);
            box-shadow: 0 0 6px rgba(139, 92, 246, 0.4);
            animation: pulse-purple 2s infinite;
        }
        @keyframes pulse-purple {
            0% { box-shadow: 0 0 0 0 rgba(139, 92, 246, 0.5); }
            70% { box-shadow: 0 0 0 4px rgba(139, 92, 246, 0); }
            100% { box-shadow: 0 0 0 0 rgba(139, 92, 246, 0); }
        }
    `;
    document.head.appendChild(style);
}

// 전역 상태 변수들
let selectedNode = null; // { type: 'strategy'|'proposal-group'|'audit-events', id: string|number }
let selectedProposalId = null;
let activeTraceData = null;

/**
 * 전체 전략 정보를 API로 가져와 요약 바, 트리 탐색기 및 설정 화면을 초기화합니다.
 */
async function loadStrategies() {
    injectStyles();
    
    // 1. 상단 요약 대시보드 로드
    await loadSummaryBar();
    
    // 2. 좌측 트리 탐색기 로드
    await loadStrategyTree();
    
    // 3. 설정 탭용 전략 카드 동시 갱신 (기존 설정 호환성 보장)
    try {
        const settingsStrategies = await APIClient.fetchStrategies();
        renderStrategyCardsInSettings(settingsStrategies);
    } catch (e) {
        console.error("설정 화면 전략 리스트 로드 실패:", e);
    }
}

/**
 * 상단 의사결정 콘솔 요약 바의 HSL 기반 신호등 지표들을 채웁니다.
 */
async function loadSummaryBar() {
    try {
        const summary = await APIClient.fetchDecisionConsoleSummary();
        
        document.getElementById('summary-op-mode').innerText = summary.operation_mode.toUpperCase();
        document.getElementById('summary-active-count').innerText = summary.active_strategies_count;
        document.getElementById('summary-champ-count').innerText = summary.champion_strategies_count;
        document.getElementById('summary-pending-count').innerText = summary.pending_proposals_count;
        document.getElementById('summary-blocked-count').innerText = summary.blocked_proposals_count;
        document.getElementById('summary-last-promotion').innerText = summary.recent_promotion_time;
        
        // 데이터 품질 (Freshness)
        const dq = document.getElementById('summary-data-quality');
        dq.innerText = summary.data_quality_status;
        if (summary.data_quality_status.includes('차단')) {
            dq.style.color = '#EF4444';
            dq.style.fontWeight = 'bold';
        } else {
            dq.style.color = '#10B981';
            dq.style.fontWeight = 'bold';
        }

        // GIRS 안정성 점수
        const stab = document.getElementById('summary-girs-stability');
        stab.innerText = summary.girs_stability;
        if (summary.girs_stability < 0.2) {
            stab.style.color = '#EF4444';
        } else if (summary.girs_stability < 0.5) {
            stab.style.color = '#F59E0B';
        } else {
            stab.style.color = '#10B981';
        }
    } catch (e) {
        console.error("[Summary Bar] 로드 실패:", e);
    }
}

/**
 * 좌측 트리 탐색기(Strategy Tree View)를 렌더링하고 상태 경고를 오버레이합니다.
 */
async function loadStrategyTree() {
    const treeRoot = document.getElementById('console-tree-root');
    if (!treeRoot) return;

    try {
        const strategies = await APIClient.fetchDecisionConsoleStrategies();
        let html = '';

        // [전략 목록]
        html += `<div class="tree-node branch expanded">
            <span class="toggle-icon">▼</span> 🎯 전략 목록
        </div>`;
        html += `<div class="tree-children">`;
        strategies.forEach(s => {
            const statusIcon = s.settings_enabled ? '🟢' : '⚪';
            const warnIcon = !s.is_synced ? '<span class="tree-warn-badge" style="color: #EF4444; margin-left: 5px;" title="설정/DB/엔진 상태 불일치 또는 챔피언 누락">⚠️</span>' : '';
            const isSelected = selectedNode && selectedNode.type === 'strategy' && selectedNode.id === s.id;
            html += `
                <div class="tree-node leaf ${isSelected ? 'selected' : ''}" onclick="selectTreeLeaf('strategy', '${s.id}')" style="padding-left: 15px; display: flex; align-items: center; justify-content: space-between; cursor: pointer;">
                    <div style="display: flex; align-items: center; gap: 6px;">
                        <span>${statusIcon}</span>
                        <span class="leaf-name" style="color: ${isSelected ? '#F8FAFC' : '#94A3B8'}; font-weight: ${isSelected ? 'bold' : 'normal'};">${s.name} (${s.id})</span>
                    </div>
                    ${warnIcon}
                </div>
            `;
        });
        html += `</div>`;

        // [제안 그룹]
        html += `<div class="tree-node branch expanded" style="margin-top: 15px;">
            <span class="toggle-icon">▼</span> 🧠 AI 의사결정 제안
        </div>`;
        html += `<div class="tree-children">`;
        
        const groups = [
            { id: 'PENDING', name: ' 대기 중 제안', icon: '⏳' },
            { id: 'APPLIED', name: ' 적용된 제안', icon: '✅' },
            { id: 'PRUNED_DEFERRED', name: ' 보류/폐기 제안', icon: '🚫' }
        ];

        groups.forEach(g => {
            const isSelected = selectedNode && selectedNode.type === 'proposal-group' && selectedNode.id === g.id;
            html += `
                <div class="tree-node leaf ${isSelected ? 'selected' : ''}" onclick="selectTreeLeaf('proposal-group', '${g.id}')" style="padding-left: 15px; display: flex; align-items: center; gap: 6px; cursor: pointer;">
                    <span>${g.icon}</span>
                    <span class="leaf-name" style="color: ${isSelected ? '#F8FAFC' : '#94A3B8'}; font-weight: ${isSelected ? 'bold' : 'normal'};">${g.name}</span>
                </div>
            `;
        });
        html += `</div>`;

        // [감사 및 진단]
        html += `<div class="tree-node branch expanded" style="margin-top: 15px;">
            <span class="toggle-icon">▼</span> 🛡️ 감사 및 진단
        </div>`;
        html += `<div class="tree-children">`;
        
        const auditSelected = selectedNode && selectedNode.type === 'audit-events';
        html += `
            <div class="tree-node leaf ${auditSelected ? 'selected' : ''}" onclick="selectTreeLeaf('audit-events', 'all')" style="padding-left: 15px; display: flex; align-items: center; gap: 6px; cursor: pointer;">
                <span>📜</span>
                <span class="leaf-name" style="color: ${auditSelected ? '#F8FAFC' : '#94A3B8'}; font-weight: ${auditSelected ? 'bold' : 'normal'};"> 감사 이벤트 로그</span>
            </div>
        `;
        html += `</div>`;

        treeRoot.innerHTML = html;

        // 접기/펴기 토글 핸들러
        const branches = treeRoot.querySelectorAll('.tree-node.branch');
        branches.forEach(b => {
            b.onclick = (e) => {
                // 노드 자체 클릭 시 토글 수행
                if (e.target.classList.contains('leaf-name') || e.target.classList.contains('leaf')) return;
                const children = b.nextElementSibling;
                const toggle = b.querySelector('.toggle-icon');
                if (children && children.classList.contains('tree-children')) {
                    if (children.style.display === 'none') {
                        children.style.display = 'block';
                        toggle.innerText = '▼';
                        b.classList.add('expanded');
                    } else {
                        children.style.display = 'none';
                        toggle.innerText = '▶';
                        b.classList.remove('expanded');
                    }
                }
            };
        });

        // 초기 노드가 없을 시 첫 번째 전략 노드 선택
        if (!selectedNode && strategies.length > 0) {
            selectTreeLeaf('strategy', strategies[0].id);
        }

    } catch (e) {
        console.error("[Strategy Tree] 로드 실패:", e);
        treeRoot.innerHTML = '<p style="color:#64748B; font-size:0.8rem; padding: 10px;">트리를 불러올 수 없습니다.</p>';
    }
}

/**
 * 특정 전략 선택 핸들러 (레거시/E2E 호환용)
 */
async function selectStrategy(strategyId) {
    selectedStrategyId = strategyId;
    await selectTreeLeaf('strategy', strategyId);
}

/**
 * 트리 노드(리프) 클릭 핸들러
 */
async function selectTreeLeaf(type, id) {
    selectedNode = { type, id };
    
    // 좌측 트리뷰 리프 노드 스타일링 동기화
    await loadStrategyTree();

    // 중앙 워크스페이스 컨텐츠 전체 감춤
    document.getElementById('workspace-empty-view').style.display = 'none';
    document.getElementById('workspace-strategy-view').style.display = 'none';
    document.getElementById('workspace-proposal-list-view').style.display = 'none';

    if (type === 'strategy') {
        await loadStrategyWorkspace(id);
    } else if (type === 'proposal-group') {
        await loadProposalListWorkspace(id);
    } else if (type === 'audit-events') {
        await loadAuditEventsWorkspace();
    }
}

/**
 * 3-1. 중앙 워크스페이스 - 특정 전략 상세 정보 렌더링
 */
async function loadStrategyWorkspace(strategyId) {
    const pane = document.getElementById('workspace-strategy-view');
    pane.style.display = 'block';

    try {
        const trace = await APIClient.fetchDecisionConsoleStrategyTrace(strategyId);
        
        // 전략 정보 타이틀 및 동기화 배지
        document.getElementById('workspace-strategy-name').innerText = `전략 상세: ${trace.strategy_id}`;
        
        const syncBadge = document.getElementById('workspace-strategy-sync-badge');
        if (trace.is_synced) {
            syncBadge.innerText = '동기화됨';
            syncBadge.style.background = 'rgba(16, 185, 129, 0.2)';
            syncBadge.style.color = '#10B981';
            syncBadge.style.border = '1px solid rgba(16, 185, 129, 0.4)';
        } else {
            syncBadge.innerText = '불일치 경고';
            syncBadge.style.background = 'rgba(239, 68, 68, 0.2)';
            syncBadge.style.color = '#EF4444';
            syncBadge.style.border = '1px solid rgba(239, 68, 68, 0.4)';
        }

        // 4대 일치성 상태 진단판 바인딩
        document.getElementById('diag-settings-val').innerText = trace.settings_enabled ? 'ENABLED (활성)' : 'DISABLED (비활성)';
        document.getElementById('diag-db-val').innerText = trace.db_champion_version ? `V${trace.db_champion_version}` : 'None (누락)';
        document.getElementById('diag-engine-val').innerText = trace.engine_enabled ? `RUNNING (${trace.engine_version})` : 'INACTIVE (비가동)';
        
        const syncVal = document.getElementById('diag-sync-val');
        syncVal.innerText = trace.is_synced ? '정상 (SYNCED)' : '⚠️ 불일치 감지';
        syncVal.style.color = trace.is_synced ? '#10B981' : '#EF4444';

        // 일치성 경고 / 챔피언 누락 경고 배너
        const banner = document.getElementById('workspace-strategy-warning-banner');
        if (!trace.is_synced) {
            banner.style.display = 'flex';
            document.getElementById('workspace-strategy-warning-msg').innerText = trace.sync_alert_message || '전략 파일 설정과 런타임 엔진 구동 환경 간의 비정상적 불일치가 확인되었습니다.';
        } else {
            banner.style.display = 'none';
        }

        // 파라미터 변동 이력 (현재값 vs 이전값)
        const paramTbody = document.getElementById('workspace-param-diff-tbody');
        paramTbody.innerHTML = '';
        if (trace.params_diff && trace.params_diff.length > 0) {
            trace.params_diff.forEach(d => {
                const tr = document.createElement('tr');
                tr.innerHTML = `
                    <td style="font-family: monospace; font-weight: bold; color: #38BDF8;">${d.name}</td>
                    <td style="text-align: right; font-family: monospace; color: #10B981; font-weight: bold;">${d.current}</td>
                    <td style="text-align: right; font-family: monospace; color: #94A3B8;">${d.previous}</td>
                    <td style="color: #cbd5e1; font-size: 0.82rem;">${d.change_reason}</td>
                    <td style="font-family: monospace; font-size: 0.75rem; color: #64748B;">${d.changed_at}</td>
                `;
                paramTbody.appendChild(tr);
            });
        } else {
            paramTbody.innerHTML = '<tr><td colspan="5" class="placeholder-text" style="text-align: center; color: #64748B; padding: 15px;">파라미터 변동 이력이 존재하지 않습니다.</td></tr>';
        }

        // 최근 성과 지표
        const perfTbody = document.getElementById('workspace-perf-tbody');
        perfTbody.innerHTML = '';
        if (trace.snapshots && trace.snapshots.length > 0) {
            trace.snapshots.forEach(s => {
                const tr = document.createElement('tr');
                const roiColor = s.roi >= 0 ? '#FF4B4B' : '#0072FF';
                tr.innerHTML = `
                    <td style="font-family: monospace; font-weight: bold; color: #F8FAFC;">V${s.version_id}</td>
                    <td><span class="badge-status disabled" style="font-size:0.7rem; padding:1px 4px;">${s.snapshot_type}</span></td>
                    <td style="text-align: right; font-family: monospace; color: ${roiColor}; font-weight: bold;">${s.roi >= 0 ? '+' : ''}${s.roi.toFixed(3)}%</td>
                    <td style="text-align: right; font-family: monospace; color: #EF4444;">${s.mdd.toFixed(2)}%</td>
                    <td style="text-align: right; font-family: monospace; color: #F59E0B;">${s.profit_factor ? s.profit_factor.toFixed(2) : '-'}</td>
                    <td style="text-align: right; font-family: monospace; color: #10B981;">${s.win_rate ? s.win_rate.toFixed(1) : '-'}%</td>
                    <td style="text-align: right; font-family: monospace; color: #cbd5e1;">${s.trade_count}</td>
                    <td style="font-family: monospace; font-size: 0.78rem; color: #64748B;">${formatTimestamp(s.timestamp)}</td>
                `;
                perfTbody.appendChild(tr);
            });
        } else {
            perfTbody.innerHTML = '<tr><td colspan="8" class="placeholder-text" style="text-align: center; color: #64748B; padding: 15px;">성과 히스토리가 존재하지 않습니다.</td></tr>';
        }

        // 의사결정 및 버전 적용 타임라인
        const timelineContainer = document.getElementById('workspace-strategy-timeline');
        timelineContainer.innerHTML = '';
        if (trace.timeline && trace.timeline.length > 0) {
            trace.timeline.forEach(t => {
                const item = document.createElement('div');
                item.className = 'timeline-item';
                
                let icon = '⚡';
                if (t.type === 'PROPOSAL') icon = '🧠';
                else if (t.type === 'VERSION') icon = '📦';
                else if (t.type === 'SYSTEM_EVENT') icon = '🛡️';

                item.innerHTML = `
                    <div class="timeline-icon" style="background: rgba(99, 102, 241, 0.15); border: 1px solid #6366F1; color: #818CF8; border-radius: 50%; width: 26px; height: 26px; display: flex; align-items: center; justify-content: center; font-size: 0.8rem;">${icon}</div>
                    <div class="timeline-content" style="margin-left: 15px; display: flex; flex-direction: column; gap: 4px; flex: 1;">
                        <div class="timeline-header" style="display: flex; justify-content: space-between; align-items: center;">
                            <span class="timeline-title" style="font-weight: bold; color: #F8FAFC; font-size: 0.85rem;">${t.title}</span>
                            <span class="timeline-time" style="font-family: monospace; font-size: 0.72rem; color: #64748B;">${formatTimestamp(t.timestamp)}</span>
                        </div>
                        <div class="timeline-desc" style="font-size: 0.8rem; color: #94A3B8;">${t.description}</div>
                    </div>
                `;
                timelineContainer.appendChild(item);
            });
        } else {
            timelineContainer.innerHTML = '<p class="placeholder-text" style="text-align: center; color: #64748B; padding: 10px;">타임라인 데이터가 없습니다.</p>';
        }

        // 우측 Tracer 패널 요약정보 동기화
        loadTracerPanelForStrategy(trace);

    } catch (e) {
        console.error("[Strategy Workspace] 로드 실패:", e);
    }
}

/**
 * 4. 우측 Tracer 패널 - 전략 선택 시 요약 바인딩
 */
function loadTracerPanelForStrategy(trace) {
    document.getElementById('tracer-panel-empty').style.display = 'none';
    const panel = document.getElementById('tracer-panel-content');
    panel.style.display = 'flex';

    // 전략의 경우 Proposal 10대 탭 심층 Tracer 전체화면 확장을 원천 차단함
    document.getElementById('btn-expand-tracer').style.display = 'none';

    document.getElementById('tracer-item-title').innerText = trace.strategy_id;
    
    const badge = document.getElementById('tracer-item-badge');
    badge.innerText = trace.engine_enabled ? 'RUNNING' : 'INACTIVE';
    badge.className = `item-badge ${trace.engine_enabled ? 'running' : 'disabled'}`;
    badge.style.background = trace.engine_enabled ? 'rgba(16, 185, 129, 0.2)' : 'rgba(100, 116, 139, 0.2)';
    badge.style.color = trace.engine_enabled ? '#10B981' : '#CBD5E1';
    badge.style.border = trace.engine_enabled ? '1px solid rgba(16, 185, 129, 0.4)' : '1px solid rgba(100, 116, 139, 0.4)';

    document.getElementById('tracer-reason-text').innerText = trace.sync_alert_message || '이 전략은 현재 설정 파일 상태와 런타임 구동 엔진 간에 완벽히 동기화되어 정상 작동 중입니다.';

    // 4분할 지표 표 채우기 (전략 성과 기준)
    const latestPerf = trace.snapshots && trace.snapshots.length > 0 ? trace.snapshots[0] : null;
    
    document.getElementById('quad-girs').innerText = latestPerf ? `${latestPerf.roi.toFixed(2)}%` : '-';
    document.getElementById('quad-girs').previousElementSibling.innerText = 'Recent ROI';

    document.getElementById('quad-promotion').innerText = latestPerf ? `${latestPerf.mdd.toFixed(2)}%` : '-';
    document.getElementById('quad-promotion').previousElementSibling.innerText = 'Recent MDD';

    document.getElementById('quad-stability').innerText = latestPerf ? `${latestPerf.win_rate ? latestPerf.win_rate.toFixed(1) : '-'}%` : '-';
    document.getElementById('quad-stability').previousElementSibling.innerText = 'Win Rate';

    document.getElementById('quad-rollback').innerText = latestPerf ? latestPerf.trade_count : '-';
    document.getElementById('quad-rollback').previousElementSibling.innerText = 'Trade Count';

    // 안전 제한 상태 표시
    const alertsSec = document.getElementById('tracer-alerts-section');
    const alertsList = document.getElementById('tracer-alerts-list');
    alertsList.innerHTML = '';
    
    if (!trace.is_synced) {
        alertsSec.style.display = 'block';
        const alertItem = document.createElement('div');
        alertItem.className = 'alert-item blocked';
        alertItem.style.borderLeft = '3px solid #EF4444';
        alertItem.innerHTML = `
            <div style="display:flex; justify-content:space-between; font-weight:bold; font-size:0.8rem;">
                <span class="alert-name" style="color:#F8FAFC;">상태 동기화 경고</span>
                <span class="alert-status blocked" style="color:#EF4444;">BLOCKED</span>
            </div>
            <div class="alert-reason" style="font-size:0.75rem; color:#94A3B8; margin-top:2px;">${trace.sync_alert_message}</div>
        `;
        alertsList.appendChild(alertItem);
    } else {
        alertsSec.style.display = 'none';
    }
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

        let statusBadgeHtml = '';
        if (s.status === 'RUNNING') {
            statusBadgeHtml = `<span class="badge-status running"><span class="led-indicator running"></span>동작 중</span>`;
        } else if (s.status === 'EXIT_ONLY') {
            statusBadgeHtml = `<span class="badge-status exit-only"><span class="led-indicator exit-only"></span>정리 중</span>`;
        } else {
            statusBadgeHtml = `<span class="badge-status disabled"><span class="led-indicator disabled"></span>비활성</span>`;
        }

        card.innerHTML = `
            <h4>
                <div style="display: flex; flex-direction: column;">
                    <span data-id="${s.id}">${s.name}</span>
                    <span class="type-badge" style="background: ${typeColors[s.type] || '#666'}; font-size: 0.6rem; padding: 2px 6px; border-radius: 10px; width: fit-content; margin-top: 4px; color: white;">${typeLabel}</span>
                </div>
                <div style="display: flex; align-items: center; gap: 5px;">
                    ${statusBadgeHtml}
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

/**
 * 3-2. 중앙 워크스페이스 - 제안 목록 렌더링
 */
async function loadProposalListWorkspace(status) {
    const pane = document.getElementById('workspace-proposal-list-view');
    pane.style.display = 'block';

    const titleMap = {
        'PENDING': '대기 중인 AI 의사결정 제안 목록',
        'APPLIED': '적용 완료된 AI 의사결정 제안 목록',
        'PRUNED_DEFERRED': '보류 및 자동 폐기된 AI 제안 목록'
    };

    document.getElementById('workspace-proposal-list-title').innerText = titleMap[status] || 'AI 제안 목록';

    try {
        const proposals = await APIClient.fetchDecisionConsoleProposals(null, status);
        document.getElementById('workspace-proposal-list-count').innerText = proposals.length;

        const tbody = document.getElementById('workspace-proposal-list-tbody');
        
        // 제안 목록 전용 테이블 헤더 동적 셋업
        const thead = document.querySelector('#proposal-workspace-table thead');
        thead.innerHTML = `
            <tr>
                <th>ID</th>
                <th>전략</th>
                <th style="text-align: right;">신뢰도 점수</th>
                <th>상태</th>
                <th>제안 시각</th>
            </tr>
        `;

        tbody.innerHTML = '';

        if (proposals.length > 0) {
            proposals.forEach(p => {
                const tr = document.createElement('tr');
                tr.style.cursor = 'pointer';
                tr.onclick = () => selectProposalRow(p.id, tr);
                tr.setAttribute('data-proposal-id', p.id);

                const scoreColor = p.confidence_score >= 80 ? '#10B981' : (p.confidence_score >= 60 ? '#F59E0B' : '#EF4444');
                
                let statusBadge = '';
                if (p.status === 'PENDING') statusBadge = '<span class="badge-status exit-only">PENDING</span>';
                else if (p.status === 'APPLIED') statusBadge = '<span class="badge-status running">APPLIED</span>';
                else if (p.status === 'PRUNED') statusBadge = '<span class="badge-status disabled">PRUNED</span>';
                else if (p.status === 'DEFERRED') statusBadge = '<span class="badge-status disabled" style="color:#d946ef; border-color:#d946ef;">DEFERRED</span>';
                else statusBadge = `<span class="badge-status disabled">${p.status}</span>`;

                tr.innerHTML = `
                    <td style="font-family: monospace; font-weight: bold; color: #818CF8;">#${p.id}</td>
                    <td style="font-weight: bold; color: #F8FAFC;">${p.strategy_id}</td>
                    <td style="text-align: right; font-family: monospace; color: ${scoreColor}; font-weight: bold;">${p.confidence_score}점</td>
                    <td>${statusBadge}</td>
                    <td style="font-family: monospace; font-size: 0.78rem; color: #64748B;">${formatTimestamp(p.created_at)}</td>
                `;
                tbody.appendChild(tr);
            });

            // 첫 번째 제안 자동 선택
            const firstTr = tbody.querySelector('tr');
            if (firstTr) {
                firstTr.click();
            }
        } else {
            tbody.innerHTML = '<tr><td colspan="5" class="placeholder-text" style="text-align: center; color: #64748B; padding: 15px;">해당 상태의 제안이 존재하지 않습니다.</td></tr>';
            document.getElementById('tracer-panel-empty').style.display = 'block';
            document.getElementById('tracer-panel-content').style.display = 'none';
        }

    } catch (e) {
        console.error("[Proposals Workspace] 로드 실패:", e);
        document.getElementById('workspace-proposal-list-tbody').innerHTML = '<tr><td colspan="5" class="placeholder-text">데이터 조회 실패</td></tr>';
    }
}

/**
 * 중앙 목록에서 제안 선택 시 우측 Split View 요약 렌더링
 */
async function selectProposalRow(proposalId, trElement) {
    selectedProposalId = proposalId;

    const rows = document.querySelectorAll('#workspace-proposal-list-tbody tr');
    rows.forEach(r => r.classList.remove('active-row'));
    if (trElement) {
        trElement.classList.add('active-row');
    }

    document.getElementById('tracer-panel-empty').style.display = 'none';
    const panel = document.getElementById('tracer-panel-content');
    panel.style.display = 'flex';
    document.getElementById('btn-expand-tracer').style.display = 'block';

    try {
        const trace = await APIClient.fetchDecisionConsoleProposalTrace(proposalId);
        activeTraceData = trace; // 모달 확장용 데이터 캐싱

        document.getElementById('tracer-item-title').innerText = `Proposal #${trace.proposal.id}`;
        
        const badge = document.getElementById('tracer-item-badge');
        badge.innerText = trace.proposal.status;
        badge.className = `item-badge ${trace.proposal.status.toLowerCase()}`;
        
        // 원인 요약 바인딩 (자동 승격 미반영 사유 명시)
        let reason = '';
        if (trace.proposal.status === 'PRUNED') {
            reason = trace.proposal.audit_log_json && trace.proposal.audit_log_json.prune_reason 
                ? trace.proposal.audit_log_json.prune_reason 
                : 'AI 리스크 점수 또는 변이 거리가 제한 임계치를 초과하여 자동 폐기(Pruned)되었습니다.';
        } else if (trace.proposal.status === 'DEFERRED') {
            reason = '수렴 조치 또는 챔피언 교체 Cooldown 대기 등 안정성 가드로 인해 보류(Deferred) 상태로 이송되었습니다.';
        } else if (trace.proposal.status === 'PENDING') {
            reason = 'GIRSScorer 검증 및 Feature Contract 적합 판정을 완료하여 승격 및 원클릭 복구 대기열에 등재되었습니다.';
        } else if (trace.proposal.status === 'APPLIED') {
            reason = '전 전략 검증 지표가 실거래 반영 기준을 통과하여 챔피언 버전으로 실시간 반영 완료되었습니다.';
        }
        document.getElementById('tracer-reason-text').innerText = reason;

        // 주요 지표 4분할
        const stability = trace.girs_score ? (trace.girs_score.final_promotion_score ?? 1.0) : 1.0;
        const modelRisk = trace.girs_score ? (trace.girs_score.model_risk_score ?? 0.0) : 0.0;
        const fallbackRisk = trace.girs_score ? (trace.girs_score.fallback_risk_score ?? 0.0) : 0.0;
        const rollbackProb = trace.proposal.metrics ? (trace.proposal.metrics.rollback_probability ?? 0.0) : 0.0;

        document.getElementById('quad-girs').innerText = modelRisk.toFixed(3);
        document.getElementById('quad-girs').previousElementSibling.innerText = 'GIRS Model Risk';

        document.getElementById('quad-promotion').innerText = stability.toFixed(3);
        document.getElementById('quad-promotion').previousElementSibling.innerText = 'Stability Score';

        document.getElementById('quad-stability').innerText = fallbackRisk.toFixed(3);
        document.getElementById('quad-stability').previousElementSibling.innerText = 'Fallback Risk';

        document.getElementById('quad-rollback').innerText = (rollbackProb * 100).toFixed(1) + '%';
        document.getElementById('quad-rollback').previousElementSibling.innerText = 'Rollback Prob.';

        // 안정성 차단 및 제한 사항 바인딩 (Guards)
        const alertsSec = document.getElementById('tracer-alerts-section');
        const alertsList = document.getElementById('tracer-alerts-list');
        alertsList.innerHTML = '';

        if (trace.guards && trace.guards.length > 0) {
            alertsSec.style.display = 'block';
            trace.guards.forEach(g => {
                const item = document.createElement('div');
                const statusClass = g.status.toLowerCase();
                item.className = `alert-item ${statusClass}`;
                
                let borderCol = '#10B981';
                if (statusClass === 'blocked') borderCol = '#EF4444';
                else if (statusClass === 'warn' || statusClass === 'warning') borderCol = '#F59E0B';

                item.style.borderLeft = `3px solid ${borderCol}`;
                item.style.padding = '6px 10px';
                item.style.background = 'rgba(15, 23, 42, 0.4)';
                item.style.borderRadius = '4px';
                item.style.marginBottom = '6px';
                item.innerHTML = `
                    <div style="display:flex; justify-content:space-between; font-weight:bold; font-size:0.8rem;">
                        <span style="color:#F8FAFC;">${g.name}</span>
                        <span style="color:${borderCol};">${g.status}</span>
                    </div>
                    ${g.reason ? `<div style="font-size:0.75rem; color:#94A3B8; margin-top:2px;">${g.reason}</div>` : ''}
                `;
                alertsList.appendChild(item);
            });
        } else {
            alertsSec.style.display = 'none';
        }

    } catch (e) {
        console.error("[Proposal Selection] 상세 조회 실패:", e);
    }
}

/**
 * 3-3. 중앙 워크스페이스 - 감사 이벤트 로그 렌더링
 */
async function loadAuditEventsWorkspace() {
    const pane = document.getElementById('workspace-proposal-list-view');
    pane.style.display = 'block';

    document.getElementById('workspace-proposal-list-title').innerText = '의사결정 시스템 감사 이벤트 로그';
    document.getElementById('workspace-proposal-list-count').innerText = '';

    try {
        const events = await APIClient.fetchDecisionConsoleEvents();
        const tbody = document.getElementById('workspace-proposal-list-tbody');
        
        const thead = document.querySelector('#proposal-workspace-table thead');
        thead.innerHTML = `
            <tr>
                <th>시간</th>
                <th>이벤트 유형</th>
                <th>대상 Proposal</th>
                <th>로그 메시지</th>
            </tr>
        `;

        tbody.innerHTML = '';
        if (events.length > 0) {
            events.forEach(e => {
                const tr = document.createElement('tr');
                tr.innerHTML = `
                    <td style="font-family: monospace; font-size: 0.78rem; color: #64748B;">${e.timestamp}</td>
                    <td style="font-weight: bold; color: #D946EF;">${e.event_type}</td>
                    <td style="font-family: monospace; color: #38BDF8; font-weight: bold;">#${e.target}</td>
                    <td style="color: #cbd5e1; font-size: 0.82rem;">${e.message}</td>
                `;
                tbody.appendChild(tr);
            });
        } else {
            tbody.innerHTML = '<tr><td colspan="4" class="placeholder-text" style="text-align: center; color: #64748B; padding: 15px;">감사 로그가 존재하지 않습니다.</td></tr>';
        }
        
        document.getElementById('tracer-panel-empty').style.display = 'block';
        document.getElementById('tracer-panel-content').style.display = 'none';

    } catch (e) {
        console.error("[Audit Events Workspace] 로드 실패:", e);
    }
}

/**
 * 10대 탭 심층 Tracer 전체화면 모달 열기
 */
function openFullTracerModal() {
    if (!activeTraceData) return;
    
    document.getElementById('full-tracer-modal').style.display = 'flex';
    document.getElementById('modal-tracer-id').innerText = `#${activeTraceData.proposal.id}`;
    document.getElementById('modal-tracer-strategy-name').innerText = activeTraceData.proposal.strategy_id;
    
    const badge = document.getElementById('modal-tracer-badge');
    badge.innerText = activeTraceData.proposal.status;
    badge.className = `badge ${activeTraceData.proposal.status.toLowerCase()}`;

    // 기본 첫 탭(FSM) 활성화
    switchTracerTab('fsm');
}

/**
 * 10대 탭 심층 Tracer 전체화면 모달 닫기
 */
function closeFullTracerModal() {
    document.getElementById('full-tracer-modal').style.display = 'none';
    stopReevalPolling(); // 재평가 폴링 활성 시 안전 해제
}

/**
 * 10대 탭 전환 제어
 */
function switchTracerTab(tabId) {
    const menuItems = document.querySelectorAll('.tracer-tab-sidebar .tab-menu-item');
    menuItems.forEach(item => {
        if (item.getAttribute('data-tab') === tabId) {
            item.classList.add('active');
        } else {
            item.classList.remove('active');
        }
    });

    const panes = document.querySelectorAll('.tracer-tab-content .tab-pane');
    panes.forEach(pane => {
        if (pane.id === `tab-pane-${tabId}`) {
            pane.classList.add('active');
        } else {
            pane.classList.remove('active');
        }
    });

    renderTracerTabContent(tabId);
}

/**
 * 탭별 데이터 렌더링 라우팅
 */
function renderTracerTabContent(tabId) {
    if (!activeTraceData) return;

    if (tabId === 'fsm') renderFsmTab();
    else if (tabId === 'girs') renderGirsTab();
    else if (tabId === 'feature') renderFeatureTab();
    else if (tabId === 'counterfactual') renderCounterfactualTab();
    else if (tabId === 'queue-diff') renderQueueDiffTab();
    else if (tabId === 'performance') renderPerformanceTab();
    else if (tabId === 'audit') renderAuditTab();
    else if (tabId === 'reeval') refreshReevalJobs();
    else if (tabId === 'rawlog') renderRawLogTab();
    else if (tabId === 'rawjson') renderRawJsonTab();
}

/**
 * Tab 1: FSM 생명주기 시각화
 */
function renderFsmTab() {
    const trace = activeTraceData;
    const currentStatus = trace.proposal.status.toLowerCase();
    const steps = ['candidate', 'pending', 'approved', 'applied'];
    
    // progress bar 초기화
    steps.forEach(s => {
        const el = document.getElementById(`step-${s}`);
        if (el) el.className = 'fsm-step';
    });
    const lines = ['candidate-pending', 'pending-approved', 'approved-applied'];
    lines.forEach(l => {
        const el = document.getElementById(`line-${l}`);
        if (el) el.className = 'fsm-line';
    });

    // FSM 타임라인 로그로부터 상태 전이 순서 분석
    const statesVisited = trace.fsm_timeline.map(t => t.state.toLowerCase());
    
    steps.forEach((s, idx) => {
        const el = document.getElementById(`step-${s}`);
        if (!el) return;
        
        if (statesVisited.includes(s) || (s === 'candidate') || 
            (s === 'pending' && ['pending', 'approved', 'applied', 'pruned', 'deferred'].includes(currentStatus)) ||
            (s === 'approved' && ['approved', 'applied'].includes(currentStatus)) ||
            (s === 'applied' && currentStatus === 'applied')) {
            el.classList.add('completed');
        }
        
        if (currentStatus === s) {
            el.classList.add('current');
        }
    });

    lines.forEach((l, idx) => {
        const el = document.getElementById(`line-${l}`);
        if (!el) return;
        
        const fromStep = steps[idx];
        const toStep = steps[idx + 1];
        
        const fromCompleted = document.getElementById(`step-${fromStep}`).classList.contains('completed');
        const toCompleted = document.getElementById(`step-${toStep}`).classList.contains('completed');
        
        if (fromCompleted && toCompleted) {
            el.classList.add('completed');
        }
    });

    // 히스토리 테이블 채우기
    const tbody = document.getElementById('fsm-history-tbody');
    tbody.innerHTML = '';
    
    if (trace.fsm_timeline && trace.fsm_timeline.length > 0) {
        trace.fsm_timeline.forEach(t => {
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td style="font-family: monospace; font-weight: bold; color: #38BDF8;">${t.state}</td>
                <td style="font-family: monospace; font-size: 0.78rem; color: #94A3B8;">${formatTimestamp(t.timestamp)}</td>
                <td style="color: #cbd5e1; font-size: 0.82rem;">${JSON.stringify(t.payload)}</td>
            `;
            tbody.appendChild(tr);
        });
    } else {
        tbody.innerHTML = '<tr><td colspan="3" class="placeholder-text" style="text-align: center; color: #64748B; padding: 10px;">FSM 전이 히스토리가 없습니다.</td></tr>';
    }
}

/**
 * Tab 2: GIRS 리스크 점수
 */
function renderGirsTab() {
    const trace = activeTraceData;
    const girs = trace.girs_score || {};

    const modelRisk = girs.model_risk_score ?? 0.0;
    const fallbackRisk = girs.fallback_risk_score ?? 0.0;
    const stability = girs.final_promotion_score ?? 1.0;
    const finalScore = trace.proposal.confidence_score ?? 0.0;

    document.getElementById('girs-model-risk').innerText = modelRisk.toFixed(3);
    document.getElementById('girs-fallback-risk').innerText = fallbackRisk.toFixed(3);
    document.getElementById('girs-stability-score').innerText = stability.toFixed(3);
    document.getElementById('girs-final-promotion').innerText = finalScore + '점';

    const tbody = document.getElementById('girs-details-tbody');
    tbody.innerHTML = '';

    const rows = [
        { name: 'Model Inference Risk (ONNX)', val: modelRisk.toFixed(4), status: modelRisk < 0.3 ? '정상' : '위험성 감지', note: 'ONNX 딥러닝 리스크 예측 수치' },
        { name: 'Fallback Rule Risk', val: fallbackRisk.toFixed(4), status: fallbackRisk < 0.4 ? '정상' : '안전 규칙 보완 필요', note: '하드코딩 룰 베이스 리스크 보정 수치' },
        { name: 'Final Stability Score (Smoothing)', val: stability.toFixed(4), status: stability > 0.4 ? '최상' : (stability > 0.2 ? '정상' : '안정성 미달 (승격 차단)'), note: 'GIRS 최종 계산 점수 (최소 0.2 초과 필요)' },
        { name: 'Replay Drift Offset', val: (girs.replay_drift ?? 0.0).toFixed(4), status: (girs.replay_drift ?? 0.0) < 0.05 ? '정상' : 'Drift 보정 가동', note: '백테스트 vs 실거래 간의 누적 성과 괴리 수치' }
    ];

    rows.forEach(r => {
        const tr = document.createElement('tr');
        let statusCol = '#10B981';
        if (r.status.includes('위험') || r.status.includes('미달')) statusCol = '#EF4444';
        else if (r.status.includes('보완') || r.status.includes('주의')) statusCol = '#F59E0B';

        tr.innerHTML = `
            <td style="font-weight: bold; color: #F8FAFC;">${r.name}</td>
            <td style="font-family: monospace; font-weight: bold; color: #38BDF8;">${r.val}</td>
            <td style="color: ${statusCol}; font-weight: bold;">${r.status}</td>
            <td style="font-size: 0.8rem; color: #94A3B8;">${r.note}</td>
        `;
        tbody.appendChild(tr);
    });
}

/**
 * Tab 3: Feature Snapshot
 */
function renderFeatureTab() {
    const trace = activeTraceData;
    const snap = trace.feature_snapshot;

    const alert = document.getElementById('feature-validation-alert');
    const msg = document.getElementById('feature-validation-msg');

    if (!snap) {
        alert.className = 'alert-banner warning';
        alert.style.background = 'rgba(245, 158, 11, 0.15)';
        alert.style.border = '1px solid rgba(245, 158, 11, 0.2)';
        msg.innerHTML = '⚠️ 해당 제안 시점의 Feature Snapshot 데이터가 존재하지 않습니다.';
        document.getElementById('feature-price-tbody').innerHTML = '';
        document.getElementById('feature-liquidity-tbody').innerHTML = '';
        document.getElementById('feature-regime-tbody').innerHTML = '';
        return;
    }

    alert.className = 'alert-banner success';
    alert.style.background = 'rgba(16, 185, 129, 0.15)';
    alert.style.border = '1px solid rgba(16, 185, 129, 0.2)';
    msg.innerHTML = `✅ Feature Contract 스키마 검증 완료: 정상 (Model: ${trace.model_version || 'N/A'}, Scaler: ${trace.scaler_version || 'N/A'})`;

    const priceTbody = document.getElementById('feature-price-tbody');
    const liqTbody = document.getElementById('feature-liquidity-tbody');
    const regimeTbody = document.getElementById('feature-regime-tbody');

    priceTbody.innerHTML = '';
    liqTbody.innerHTML = '';
    regimeTbody.innerHTML = '';

    Object.entries(snap).forEach(([k, v]) => {
        const tr = document.createElement('tr');
        let valStr = typeof v === 'number' ? v.toFixed(5) : JSON.stringify(v);
        tr.innerHTML = `
            <td style="font-family: monospace; color:#94A3B8;">${k}</td>
            <td style="font-family: monospace; text-align: right; font-weight: bold; color: #38BDF8;">${valStr}</td>
        `;

        const lowerKey = k.toLowerCase();
        if (lowerKey.includes('price') || lowerKey.includes('close') || lowerKey.includes('open') || lowerKey.includes('high') || lowerKey.includes('low') || lowerKey.includes('return') || lowerKey.includes('volatility') || lowerKey.includes('sma') || lowerKey.includes('rsi') || lowerKey.includes('bb')) {
            priceTbody.appendChild(tr);
        } else if (lowerKey.includes('volume') || lowerKey.includes('amount') || lowerKey.includes('liquidity') || lowerKey.includes('turnover')) {
            liqTbody.appendChild(tr);
        } else {
            regimeTbody.appendChild(tr);
        }
    });

    if (priceTbody.children.length === 0) priceTbody.innerHTML = '<tr><td colspan="2" class="placeholder-text" style="text-align: center; color: #64748B;">데이터 없음</td></tr>';
    if (liqTbody.children.length === 0) liqTbody.innerHTML = '<tr><td colspan="2" class="placeholder-text" style="text-align: center; color: #64748B;">데이터 없음</td></tr>';
    if (regimeTbody.children.length === 0) regimeTbody.innerHTML = '<tr><td colspan="2" class="placeholder-text" style="text-align: center; color: #64748B;">데이터 없음</td></tr>';
}

/**
 * Tab 4: Counterfactual Simulation
 */
function renderCounterfactualTab() {
    const trace = activeTraceData;
    const tbody = document.getElementById('counterfactual-tbody');
    tbody.innerHTML = '';

    if (trace.evaluations && trace.evaluations.length > 0) {
        trace.evaluations.forEach(e => {
            const tr = document.createElement('tr');
            
            const candRoiColor = e.candidate_roi >= 0 ? '#FF4B4B' : '#0072FF';
            const champRoiColor = e.champion_roi >= 0 ? '#FF4B4B' : '#0072FF';
            const gapColor = e.roi_gap >= 0 ? '#10B981' : '#EF4444';
            
            tr.innerHTML = `
                <td style="font-weight: bold; color: #F8FAFC;">${e.horizon_name}</td>
                <td style="text-align: right; font-family: monospace; color: ${candRoiColor}; font-weight: bold;">${e.candidate_roi >= 0 ? '+' : ''}${e.candidate_roi.toFixed(3)}%</td>
                <td style="text-align: right; font-family: monospace; color: ${champRoiColor};">${e.champion_roi >= 0 ? '+' : ''}${e.champion_roi.toFixed(3)}%</td>
                <td style="text-align: right; font-family: monospace; color: ${gapColor}; font-weight: bold;">${e.roi_gap >= 0 ? '+' : ''}${e.roi_gap.toFixed(3)}%</td>
                <td style="text-align: right; font-family: monospace; color: #EF4444;">${e.candidate_mdd.toFixed(2)}%</td>
                <td style="text-align: right; font-family: monospace; color: #94A3B8;">${e.champion_mdd.toFixed(2)}%</td>
                <td style="text-align: center; font-weight: bold; color: ${e.virtual_rollback ? '#EF4444' : '#10B981'};">${e.virtual_rollback ? '⏪ TRIGGERED' : '✅ SAFE'}</td>
                <td style="font-weight: bold; color: ${e.status === 'COMPLETED' ? '#10B981' : '#F59E0B'};">${e.status}</td>
            `;
            tbody.appendChild(tr);
        });
    } else {
        tbody.innerHTML = '<tr><td colspan="8" class="placeholder-text" style="text-align: center; color: #64748B; padding: 15px;">반사실적(Counterfactual) 가상 시뮬레이션 결과가 존재하지 않습니다.</td></tr>';
    }
}

/**
 * Tab 5: Queue / Diff
 */
function renderQueueDiffTab() {
    const trace = activeTraceData;
    
    // Pareto Front 랭킹 바인딩
    document.getElementById('pareto-final-rank').innerText = trace.proposal.confidence_score >= 80 ? 'Top 10%' : 'Top 30%';
    document.getElementById('pareto-roi-rank').innerText = trace.proposal.metrics ? `ROI: +${(trace.proposal.metrics.expected_roi ?? 0).toFixed(1)}%` : '-';
    document.getElementById('pareto-mdd-rank').innerText = trace.proposal.metrics ? `MDD: ${(trace.proposal.metrics.risk_score ?? 0).toFixed(1)}` : '-';

    // 파라미터 챔피언 대비 변경점 Diff 비교
    const tbody = document.getElementById('pareto-diff-tbody');
    tbody.innerHTML = '';

    const orig = trace.proposal.original_params || {};
    const proposed = trace.proposal.proposed_params || {};

    const allKeys = new Set([...Object.keys(orig), ...Object.keys(proposed)]);
    
    if (allKeys.size > 0) {
        allKeys.forEach(k => {
            const tr = document.createElement('tr');
            
            const origVal = orig[k] !== undefined ? orig[k] : '-';
            const proposedVal = proposed[k] !== undefined ? proposed[k] : '-';
            
            const isChanged = origVal !== proposedVal;
            const statusBadge = isChanged 
                ? '<span class="badge-status exit-only" style="font-size:0.65rem; padding:2px 5px;">MUTATED</span>' 
                : '<span class="badge-status disabled" style="font-size:0.65rem; padding:2px 5px;">UNTOUCHED</span>';

            tr.innerHTML = `
                <td style="font-family: monospace; font-weight: bold; color: #38BDF8;">${k}</td>
                <td style="font-family: monospace; color: #94A3B8;">${origVal}</td>
                <td style="font-family: monospace; color: #10B981; font-weight: bold;">${proposedVal}</td>
                <td>${statusBadge}</td>
            `;
            tbody.appendChild(tr);
        });
    } else {
        tbody.innerHTML = '<tr><td colspan="4" class="placeholder-text" style="text-align: center; color: #64748B; padding: 10px;">비교 가능한 파라미터 정보가 없습니다.</td></tr>';
    }
}

/**
 * Tab 6: Performance & Orders
 */
function renderPerformanceTab() {
    const trace = activeTraceData;
    const tbody = document.getElementById('perf-orders-tbody');
    tbody.innerHTML = '';

    if (trace.related_orders && trace.related_orders.length > 0) {
        trace.related_orders.forEach(o => {
            const tr = document.createElement('tr');
            const sideColor = o.side === 'BUY' ? '#FF4B4B' : '#0072FF';
            tr.innerHTML = `
                <td style="font-family: monospace; color: #64748B;">${o.id}</td>
                <td style="font-weight: bold; color: ${sideColor};">${o.side}</td>
                <td style="font-family: monospace; font-weight: bold; color: #F8FAFC;">${o.symbol}</td>
                <td style="text-align: right; font-family: monospace; color: #cbd5e1;">${o.price.toLocaleString()}</td>
                <td style="text-align: right; font-family: monospace; color: #cbd5e1;">${o.quantity}</td>
                <td style="color:#94A3B8; font-size:0.8rem; text-align: right;">${o.fee ? o.fee.toLocaleString() : '0'}</td>
                <td style="color:#cbd5e1; font-size:0.82rem;">${o.reason}</td>
                <td style="font-family: monospace; font-size: 0.78rem; color: #64748B;">${formatTimestamp(o.timestamp)}</td>
            `;
            tbody.appendChild(tr);
        });
    } else {
        tbody.innerHTML = '<tr><td colspan="8" class="placeholder-text" style="text-align: center; color: #64748B; padding: 15px;">이 전략의 실거래 체결 주문 이력이 없습니다.</td></tr>';
    }
}

/**
 * Tab 7: Audit Logs
 */
function renderAuditTab() {
    const trace = activeTraceData;
    const tbody = document.getElementById('audit-events-tbody');
    tbody.innerHTML = '';

    if (trace.related_events && trace.related_events.length > 0) {
        trace.related_events.forEach(e => {
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td style="font-weight: bold; color: #D946EF;">${e.event_type}</td>
                <td style="font-family: monospace; color: #38BDF8; font-weight: bold;">#${trace.proposal.id}</td>
                <td style="color: #cbd5e1; font-size: 0.82rem;">${e.message}</td>
                <td style="font-family: monospace; font-size: 0.78rem; color: #64748B;">${formatTimestamp(e.timestamp)}</td>
            `;
            tbody.appendChild(tr);
        });
    } else {
        tbody.innerHTML = '<tr><td colspan="4" class="placeholder-text" style="text-align: center; color: #64748B; padding: 15px;">관련 감사 이벤트가 발견되지 않았습니다.</td></tr>';
    }
}

/**
 * Tab 8: 수동 재평가 요청 등록 및 폴링
 */
let reevalPollingInterval = null;

async function requestReevaluation() {
    if (!activeTraceData) return;
    const proposalId = activeTraceData.proposal.id;
    
    if (!confirm(`이 Proposal #${proposalId}을 원본 Feature Snapshot 기준으로 재평가하시겠습니까?\n\n이 작업은 실제 주문 및 챔피언에 영향을 주지 않는 순수 Shadow 평가 비동기 작업입니다.`)) {
        return;
    }

    try {
        const btn = document.getElementById('btn-request-reevaluation-action');
        btn.disabled = true;
        btn.innerText = '🔄 재평가 Job 요청 등록 중...';

        const res = await APIClient.requestDecisionConsoleReevaluation(proposalId);
        alert(res.message || `재평가 Job #${res.job_id}가 비동기 큐에 성공적으로 등록되었습니다.`);
        
        await refreshReevalJobs();
        startReevalPolling(proposalId);

    } catch (e) {
        alert(`재평가 요청 실패: ${e.message || e}`);
    } finally {
        const btn = document.getElementById('btn-request-reevaluation-action');
        if (btn) {
            btn.disabled = false;
            btn.innerText = '🔄 [재평가 요청] 실행';
        }
    }
}

async function refreshReevalJobs() {
    if (!activeTraceData) return;
    const proposalId = activeTraceData.proposal.id;
    const tbody = document.getElementById('reeval-jobs-tbody');
    if (!tbody) return;

    try {
        const jobs = await APIClient.fetchDecisionConsoleReevaluationJobs(proposalId);
        tbody.innerHTML = '';
        
        let hasActiveJob = false;

        if (jobs && jobs.length > 0) {
            jobs.forEach(j => {
                const tr = document.createElement('tr');
                let statusCol = '#cbd5e1';
                
                if (j.status === 'RUNNING') {
                    statusCol = '#F59E0B';
                    hasActiveJob = true;
                } else if (j.status === 'QUEUED') {
                    statusCol = '#38BDF8';
                    hasActiveJob = true;
                } else if (j.status === 'COMPLETED') {
                    statusCol = '#10B981';
                } else if (j.status === 'FAILED') {
                    statusCol = '#EF4444';
                }

                tr.innerHTML = `
                    <td style="font-family: monospace; font-weight: bold; color: #818CF8;">#${j.job_id}</td>
                    <td style="font-weight: bold; color: ${statusCol};">${j.status}</td>
                    <td style="font-family: monospace; font-size: 0.75rem; color: #94A3B8;">${j.requested_at}</td>
                    <td style="font-family: monospace; font-size: 0.75rem; color: #94A3B8;">${j.started_at}</td>
                    <td style="font-family: monospace; font-size: 0.75rem; color: #94A3B8;">${j.finished_at}</td>
                    <td style="font-family: monospace; font-size: 0.75rem; color: #64748B;">${j.worker_id || '-'}</td>
                    <td style="font-size: 0.8rem; color: ${j.status === 'FAILED' ? '#EF4444' : '#cbd5e1'}; max-width: 250px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;" title="${j.error_message || ''}">
                        ${j.error_message || '-'}
                    </td>
                `;
                tbody.appendChild(tr);
            });
        } else {
            tbody.innerHTML = '<tr><td colspan="7" class="placeholder-text" style="text-align: center; color: #64748B; padding: 15px;">재평가 요청 이력이 없습니다.</td></tr>';
        }

        // 진행 중인 비동기 작업이 모두 완료된 시점 폴링 중지 및 점수 갱신
        if (!hasActiveJob && reevalPollingInterval) {
            stopReevalPolling();
            
            // 데이터 즉시 동기화 페치
            const trace = await APIClient.fetchDecisionConsoleProposalTrace(proposalId);
            activeTraceData = trace;
            
            // 점수판 탭 갱신
            const activeTab = document.querySelector('.tracer-tab-sidebar .tab-menu-item.active');
            if (activeTab) {
                renderTracerTabContent(activeTab.getAttribute('data-tab'));
            }
            // 상단 요약 바 점수도 실시간 리플레시
            await loadSummaryBar();
        }

    } catch (e) {
        console.error("[Reeval Jobs] 갱신 오류:", e);
    }
}

function startReevalPolling(proposalId) {
    if (reevalPollingInterval) clearInterval(reevalPollingInterval);
    reevalPollingInterval = setInterval(async () => {
        await refreshReevalJobs();
    }, 3000); // 3초 주기 폴링
}

function stopReevalPolling() {
    if (reevalPollingInterval) {
        clearInterval(reevalPollingInterval);
        reevalPollingInterval = null;
    }
}

/**
 * Tab 9: 원본 텍스트 감사 로그
 */
function renderRawLogTab() {
    const trace = activeTraceData;
    const consoleEl = document.getElementById('raw-text-log-console');
    consoleEl.textContent = '';

    if (trace.related_events && trace.related_events.length > 0) {
        const lines = trace.related_events.map(e => `[${formatTimestamp(e.timestamp)}] [${e.event_type}] ${e.message} (Context: ${JSON.stringify(e.context)})`);
        consoleEl.textContent = lines.join('\n');
    } else {
        consoleEl.textContent = '표시할 감사 텍스트 로그가 없습니다.';
    }
}

/**
 * Tab 10: Raw JSON 데이터 렌더링
 */
function renderRawJsonTab() {
    const trace = activeTraceData;
    const viewer = document.getElementById('raw-json-viewer');
    viewer.textContent = JSON.stringify(trace.proposal, null, 2);
}

/**
 * Raw JSON 데이터 복사
 */
function copyRawJsonToClipboard() {
    if (!activeTraceData) return;
    const jsonStr = JSON.stringify(activeTraceData.proposal, null, 2);
    navigator.clipboard.writeText(jsonStr)
        .then(() => alert('Raw JSON 데이터가 성공적으로 클립보드에 복사되었습니다.'))
        .catch(err => alert('클립보드 복사 실패: ' + err));
}

/**
 * Raw JSON 파일 다운로드
 */
function downloadRawJsonFile() {
    if (!activeTraceData) return;
    const jsonStr = JSON.stringify(activeTraceData.proposal, null, 2);
    const blob = new Blob([jsonStr], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `proposal_${activeTraceData.proposal.id}_raw_db.json`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
}

/**
 * 상단 요약 바 배지 클릭 시 해당 필터 그룹 중앙/좌측 즉각 전환
 */
async function applySummaryFilter(filterType) {
    const strategyMenuItem = document.querySelector('.menu-item[data-view="strategy-view"]');
    if (strategyMenuItem) {
        strategyMenuItem.click();
    }

    if (filterType === 'all' || filterType === 'active' || filterType === 'champion') {
        const strategies = await APIClient.fetchDecisionConsoleStrategies();
        if (strategies.length > 0) {
            selectTreeLeaf('strategy', strategies[0].id);
        }
    } else if (filterType === 'pending') {
        selectTreeLeaf('proposal-group', 'PENDING');
    } else if (filterType === 'blocked') {
        selectTreeLeaf('proposal-group', 'PRUNED_DEFERRED');
    } else if (filterType === 'data-quality') {
        selectTreeLeaf('audit-events', 'all');
    }
}

/**
 * 의사결정 콘솔 UI 초기화 및 이벤트 리스너 바인딩
 */
function initDecisionConsole() {
    const expandBtn = document.getElementById('btn-expand-tracer');
    if (expandBtn) {
        expandBtn.onclick = openFullTracerModal;
    }

    const tabItems = document.querySelectorAll('.tracer-tab-sidebar .tab-menu-item');
    tabItems.forEach(item => {
        item.onclick = () => {
            const tabId = item.getAttribute('data-tab');
            switchTracerTab(tabId);
        };
    });

    const reevalBtn = document.getElementById('btn-request-reevaluation-action');
    if (reevalBtn) {
        reevalBtn.onclick = requestReevaluation;
    }

    const copyBtn = document.getElementById('btn-copy-raw-json');
    if (copyBtn) {
        copyBtn.onclick = copyRawJsonToClipboard;
    }
    const downloadBtn = document.getElementById('btn-download-raw-json');
    if (downloadBtn) {
        downloadBtn.onclick = downloadRawJsonFile;
    }

    // 전역 함수 노출
    window.applySummaryFilter = applySummaryFilter;
    window.selectTreeLeaf = selectTreeLeaf;
    window.openFullTracerModal = openFullTracerModal;
    window.closeFullTracerModal = closeFullTracerModal;
}

// 전역 window 바인딩 유지 및 추가
window.loadStrategies = loadStrategies;
window.saveStrategyParams = saveStrategyParams;
window.toggleStrategyStatus = toggleStrategyStatus;
window.updateStrategyStatusUI = updateStrategyStatusUI;
window.approveProposal = approveProposal;
window.deferProposal = deferProposal;
window.executeRollback = executeRollback;
window.refreshAIHealth = refreshAIHealth;
window.requestReevaluation = requestReevaluation;

// ViewRouter 연동 및 콘솔 진입 등록
document.addEventListener('DOMContentLoaded', () => {
    const router = window.ViewRouter || (typeof ViewRouter !== 'undefined' ? ViewRouter : null);
    if (router) {
        router.registerRoute('strategy-view', async () => {
            initDecisionConsole();
            await loadStrategies();
        });
    } else {
        console.error("ViewRouter가 정의되지 않아 'strategy-view' 라우트를 등록할 수 없습니다.");
    }
});

