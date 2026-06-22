/**
 * StrategyDaemonView - 전략 데몬 모니터링 및 제어 전담 컨트롤러
 */
const StrategyDaemonView = (() => {
    // HTML 이스케이프 헬퍼
    function escapeHtml(text) {
        if (!text) return '';
        return text
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
    }

    // 하트비트 감시용 변수
    let lastDetailHeartbeat = 0; // ms
    let staleCheckInterval = null;

    // 제어 명령 펜딩 맵 (commandId -> { type, timeoutId, previousPid, previousStartedAt })
    const pendingCommands = new Map();

    // 전략 데몬 프로세스 메타데이터 백업 (재기동 확인용)
    let currentPid = null;
    let currentDaemonStartedAt = 0;
    let isStaleState = false;

    // 모니터링 관련 임계값 설정
    let monitoringConfig = {
        daemon_detail_stale_ms: 15000,
        control_ack_timeout_ms: 5000
    };

    /**
     * 고유한 command_id 생성 헬퍼
     */
    function generateCommandId() {
        return 'cmd-str-' + Math.random().toString(36).substr(2, 9) + '-' + Date.now();
    }

    /**
     * 화면 진입 시 초기 데이터 조회 및 주기적 타이머 기동
     */
    async function init() {
        console.log("[StrategyDaemonView] Initializing view...");

        // 1. 초기 1회 REST API로 상세 정보 및 이벤트 로그 로드
        await loadDaemonDetail();
        await loadEvents();

        // 2. 3초 주기 정밀 Stale/Heartbeat 감시 타이머 기동
        if (staleCheckInterval) {
            clearInterval(staleCheckInterval);
        }
        staleCheckInterval = setInterval(checkStaleStatus, 3000);

        // 3. 버튼 이벤트 바인딩 등록
        const restartBtn = document.getElementById('btn-strategy-restart-daemon');
        if (restartBtn) {
            // 중복 리스너 등록 방지를 위해 새로 복제하거나 정리
            const newRestartBtn = restartBtn.cloneNode(true);
            restartBtn.parentNode.replaceChild(newRestartBtn, restartBtn);
            newRestartBtn.addEventListener('click', restartStrategyDaemon);
        }
    }

    /**
     * REST API 호출을 통한 전체 상태 갱신
     */
    async function loadDaemonDetail() {
        try {
            const res = await APIClient.fetchStrategyDaemonDetail();
            if (!res || !res.daemon_detail) return;

            const detail = res.daemon_detail;

            // 로컬 캐시 및 하트비트 업데이트
            currentPid = detail.lifecycle ? detail.lifecycle.pid : null;
            currentDaemonStartedAt = detail.lifecycle ? detail.lifecycle.started_at : 0;
            
            // API가 정상 응답한 시점을 마지막 하트비트로 기록
            lastDetailHeartbeat = Date.now();

            // UI 바인딩 실행
            updateUI(detail);
        } catch (error) {
            console.error("[StrategyDaemonView] Failed to fetch strategy daemon details:", error);
            showToast("전략 데몬 정보를 가져오는데 실패했습니다.", "error");
        }
    }

    /**
     * 하단 감사 이벤트 로그 로드
     */
    async function loadEvents() {
        const tbody = document.getElementById('strategy-events-tbody');
        if (!tbody) return;

        try {
            // 시스템 전체 로그 중 strategy 대상 또는 STRATEGY_ 접두사를 필터링
            const events = await APIClient.fetchSystemEvents(50);
            tbody.innerHTML = '';

            const filteredEvents = (events || []).filter(event => 
                event.target === 'strategy' || 
                (event.event_type && event.event_type.startsWith('STRATEGY_'))
            ).slice(0, 20); // 최근 20건만 표시

            if (filteredEvents.length === 0) {
                tbody.innerHTML = '<tr><td colspan="4" style="text-align: center; color: #64748B; padding: 20px;">기록된 전략 감사 이벤트가 없습니다.</td></tr>';
                return;
            }

            filteredEvents.forEach(event => {
                const tr = document.createElement('tr');
                const timeStr = new Date(event.timestamp).toLocaleString();

                // 구분별 컬러 지정 (RULE[design.md] 상승: Red, 하락: Blue)
                let typeStyle = 'color: #94A3B8; font-weight: bold;';
                if (event.event_type.includes('ERROR') || event.event_type.includes('FAIL') || event.event_type.includes('DEMOTION')) {
                    typeStyle = 'color: #0072FF; font-weight: bold;'; // Bear/하락/오류
                } else if (event.event_type.includes('START') || event.event_type.includes('PROMOTION') || event.event_type.includes('RESTART')) {
                    typeStyle = 'color: #FF4B4B; font-weight: bold;'; // Bull/상승/기동
                }

                tr.innerHTML = `
                    <td style="color: #64748B; font-family: monospace;">${timeStr}</td>
                    <td style="${typeStyle}">${event.event_type}</td>
                    <td style="text-transform: uppercase; font-weight: bold; color: #F8FAFC;">${event.target}</td>
                    <td style="color: #94A3B8; max-width: 400px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;" title="${event.message}">${event.message}</td>
                `;
                tbody.appendChild(tr);
            });
        } catch (error) {
            console.error("[StrategyDaemonView] Failed to load strategy system events:", error);
        }
    }

    /**
     * Stale & Heartbeat 감지 스케줄러 (3초 주기)
     */
    function checkStaleStatus() {
        const now = Date.now();
        const badge = document.getElementById('strategy-stale-badge');

        const isDaemonStale = lastDetailHeartbeat === 0 || (now - lastDetailHeartbeat > monitoringConfig.daemon_detail_stale_ms);

        if (isDaemonStale) {
            if (badge) {
                badge.innerText = "연결 끊김";
                badge.style.display = "inline-block";
            }
            if (!isStaleState) {
                isStaleState = true;
                console.warn("[StrategyDaemonView] Strategy daemon seems to be offline (Heartbeat stale).");
                document.querySelectorAll('#strategy-daemon-view .diag-card, #strategy-daemon-view .card').forEach(el => {
                    el.style.opacity = '0.5';
                });
            }
        } else {
            if (badge) {
                badge.style.display = "none";
            }
            if (isStaleState) {
                isStaleState = false;
                console.log("[StrategyDaemonView] Strategy daemon is back online.");
                document.querySelectorAll('#strategy-daemon-view .diag-card, #strategy-daemon-view .card').forEach(el => {
                    el.style.opacity = '1';
                });
            }
        }
    }

    /**
     * 실시간 수신 데이터 UI 바인딩 핵심 로직
     */
    function updateUI(data) {
        if (!data) return;

        const lifecycle = data.lifecycle || {};
        const engines = data.engines || {};
        const decisionStatus = data.decision_status || {};
        const girsStatus = data.girs_status || {};
        const guardrailStats = data.guardrail_stats || {};
        const promotionStatus = data.promotion_status || {};

        // 1. 헤더 메타데이터 정보 바인딩
        const pidEl = document.getElementById('strategy-pid');
        const startedEl = document.getElementById('strategy-started-at');
        const heartbeatEl = document.getElementById('strategy-last-heartbeat');

        if (pidEl) pidEl.innerText = lifecycle.pid || '-';
        if (startedEl) {
            startedEl.innerText = lifecycle.started_at 
                ? new Date(lifecycle.started_at).toLocaleString() 
                : '-';
        }
        if (heartbeatEl) {
            // REST API 호출 시점 또는 웹소켓 수신 시점
            const updateTime = data.last_updated_at || lifecycle.heartbeat || Date.now();
            heartbeatEl.innerText = new Date(updateTime).toLocaleTimeString();
        }

        // 2. 5단 리소스 및 실시간 판단 메트릭 카드 갱신
        const memEl = document.getElementById('res-val-strategy-memory');
        const cpuEl = document.getElementById('res-val-strategy-cpu');
        const decTimeEl = document.getElementById('res-val-strategy-last-decision');
        const decLatEl = document.getElementById('res-val-strategy-decision-latency');
        const girsVerEl = document.getElementById('res-val-strategy-girs-version');
        const proposalsEl = document.getElementById('res-val-strategy-proposals');
        const signalsEl = document.getElementById('res-val-strategy-signals');
        const intentsEl = document.getElementById('res-val-strategy-intents');
        const promStatusEl = document.getElementById('res-val-strategy-promotion-status');
        const promCountsEl = document.getElementById('res-val-strategy-prom-dem-counts');

        if (memEl) memEl.innerText = lifecycle.rss_mb ? `${lifecycle.rss_mb.toFixed(2)} MB` : '- MB';
        if (cpuEl) cpuEl.innerText = lifecycle.cpu_usage_pct !== undefined ? `CPU: ${lifecycle.cpu_usage_pct.toFixed(1)}%` : 'CPU: -%';
        
        if (decTimeEl) {
            decTimeEl.innerText = decisionStatus.last_decision_at 
                ? new Date(decisionStatus.last_decision_at).toLocaleTimeString() 
                : '-';
        }
        if (decLatEl) {
            decLatEl.innerText = decisionStatus.decision_latency_ms !== undefined 
                ? `${decisionStatus.decision_latency_ms.toFixed(1)} ms` 
                : '- ms';
        }

        if (girsVerEl) girsVerEl.innerText = girsStatus.girs_model_version || '-';
        if (proposalsEl) proposalsEl.innerText = girsStatus.proposal_count_today !== undefined ? `오늘: ${girsStatus.proposal_count_today}건` : '오늘: -건';

        if (signalsEl) signalsEl.innerText = decisionStatus.signal_count_today !== undefined ? `${decisionStatus.signal_count_today} 건` : '- 건';
        if (intentsEl) intentsEl.innerText = decisionStatus.order_intent_count_today !== undefined ? `의도: ${decisionStatus.order_intent_count_today}건` : '의도: -건';

        if (promStatusEl) {
            const enabled = promotionStatus.auto_promotion_enabled;
            promStatusEl.innerText = enabled ? '자동화 활성' : '자동화 비활성';
            promStatusEl.style.color = enabled ? '#10B981' : '#FF4B4B'; // 녹색 / 적색
        }
        if (promCountsEl) {
            const prom = promotionStatus.promotion_count_today || 0;
            const dem = promotionStatus.demotion_count_today || 0;
            promCountsEl.innerText = `승: ${prom} / 강: ${dem} 회`;
        }

        // 3. 가드레일 및 롤백 통계 바인딩
        const gc = document.getElementById('res-val-guardrail-cooldown');
        const gq = document.getElementById('res-val-guardrail-quota');
        const gd = document.getElementById('res-val-guardrail-daily-limit');
        const gls = document.getElementById('res-val-guardrail-low-stability');
        const gdq = document.getElementById('res-val-guardrail-data-quality');
        const glr = document.getElementById('res-val-guardrail-lazy-replay');
        const gcc = document.getElementById('res-val-guardrail-champion-cooldown');
        const rb = document.getElementById('res-val-strategy-rollback');
        const lastReason = document.getElementById('res-val-strategy-last-block-reason');

        if (gc) gc.innerText = guardrailStats.cooldown || 0;
        if (gq) gq.innerText = guardrailStats.quota || 0;
        if (gd) gd.innerText = guardrailStats.daily_limit || 0;
        if (gls) gls.innerText = guardrailStats.low_stability || 0;
        if (gdq) gdq.innerText = guardrailStats.data_quality || 0;
        if (glr) glr.innerText = guardrailStats.lazy_replay || 0;
        if (gcc) gcc.innerText = guardrailStats.champion_cooldown || 0;
        if (rb) rb.innerText = promotionStatus.rollback_count_today || 0;
        if (lastReason) lastReason.innerText = guardrailStats.last_block_reason || '차단 이력 없음';

        // 4. 엔진 상태 요약 바인딩
        const engTotal = document.getElementById('res-val-engines-total');
        const engActive = document.getElementById('res-val-engines-active');
        const engStale = document.getElementById('res-val-engines-stale');

        if (engTotal) engTotal.innerText = `${engines.total_engines || 0} 개`;
        if (engActive) engActive.innerText = `${engines.active_engines || 0} 개`;
        if (engStale) engStale.innerText = `${engines.stale_engines || 0} 개`;

        // 5. 전략/거래소 분류 통계 렌더링
        const statsContainer = document.getElementById('strategy-engine-stats-container');
        if (statsContainer) {
            let leftColHtml = '<div style="display: flex; flex-direction: column; gap: 8px;"><strong>🎯 전략별 엔진 기동 (활성/전체)</strong>';
            const stratStats = engines.strategy_stats || {};
            if (Object.keys(stratStats).length === 0) {
                leftColHtml += '<span style="color: #64748B;">통계 정보가 없습니다.</span>';
            } else {
                for (const [sid, stat] of Object.entries(stratStats)) {
                    leftColHtml += `<div style="display: flex; justify-content: space-between; padding-right: 15px;"><span>${sid}</span><span style="font-family: monospace; font-weight: bold; color: #F8FAFC;">${stat.active} / ${stat.total}</span></div>`;
                }
            }
            leftColHtml += '</div>';

            let rightColHtml = '<div style="display: flex; flex-direction: column; gap: 8px;"><strong>🌐 거래소별 엔진 기동 (활성/전체)</strong>';
            const exchStats = engines.exchange_stats || {};
            if (Object.keys(exchStats).length === 0) {
                rightColHtml += '<span style="color: #64748B;">통계 정보가 없습니다.</span>';
            } else {
                for (const [exch, stat] of Object.entries(exchStats)) {
                    const exchLogo = exch === 'upbit' ? '🔵' : (exch === 'bithumb' ? '🟡' : '🔴');
                    rightColHtml += `<div style="display: flex; justify-content: space-between; padding-right: 15px;"><span>${exchLogo} ${exch.toUpperCase()}</span><span style="font-family: monospace; font-weight: bold; color: #F8FAFC;">${stat.active} / ${stat.total}</span></div>`;
                }
            }
            rightColHtml += '</div>';

            statsContainer.innerHTML = leftColHtml + rightColHtml;
        }

        // 6. 개별 엔진 테이블 렌더링
        const enginesTbody = document.getElementById('strategy-engines-tbody');
        if (enginesTbody) {
            enginesTbody.innerHTML = '';
            const enginesList = engines.engines || [];

            if (enginesList.length === 0) {
                enginesTbody.innerHTML = '<tr><td colspan="6" style="text-align: center; color: #64748B; padding: 20px;">적재된 개별 전략 엔진이 없습니다.</td></tr>';
                return;
            }

            enginesList.forEach(eng => {
                const tr = document.createElement('tr');
                const lastTickStr = eng.last_tick_received_at 
                    ? new Date(eng.last_tick_received_at).toLocaleTimeString() 
                    : '수신 없음';

                const activeBadge = eng.is_active 
                    ? '<span class="badge success" style="font-size: 0.72rem; padding: 2px 6px;">활성</span>'
                    : '<span class="badge danger" style="font-size: 0.72rem; padding: 2px 6px;">정지</span>';

                const staleBadge = eng.is_stale
                    ? '<span class="badge warning" style="font-size: 0.72rem; padding: 2px 6px;">지연</span>'
                    : '<span class="badge success" style="font-size: 0.72rem; padding: 2px 6px;">정상</span>';

                const latencyStr = (eng.decision_latency_ms !== undefined && eng.decision_latency_ms !== null) 
                    ? `${eng.decision_latency_ms.toFixed(1)} ms`
                    : '-';

                tr.innerHTML = `
                    <td style="font-weight: bold; color: #F8FAFC;">${eng.symbol}</td>
                    <td style="font-family: monospace; color: #cbd5e1;">${eng.strategy_id}</td>
                    <td style="text-align: center;">${activeBadge}</td>
                    <td style="text-align: center;">${staleBadge}</td>
                    <td style="color: #94A3B8; font-family: monospace;">${lastTickStr}</td>
                    <td style="text-align: right; padding-right: 15px; font-family: 'Roboto Mono', monospace; font-weight: bold; color: #10B981;">${latencyStr}</td>
                `;
                enginesTbody.appendChild(tr);
            });
        }
    }

    /**
     * 전략 데몬 프로세스 자체 재기동
     */
    async function restartStrategyDaemon() {
        const cmdId = generateCommandId();
        console.log(`[StrategyDaemonView] Request restarting strategy daemon (id: ${cmdId})`);

        const backupPid = currentPid;
        const backupStartedAt = currentDaemonStartedAt;

        // UI 락 및 로딩 상태 변경
        const btn = document.getElementById('btn-strategy-restart-daemon');
        if (btn) btn.classList.add('loading');

        pendingCommands.set(cmdId, {
            type: 'restart_daemon',
            previousPid: backupPid,
            previousStartedAt: backupStartedAt,
            timeoutId: setTimeout(() => {
                handleTimeout(cmdId);
                if (btn) btn.classList.remove('loading');
            }, 12000) // 넉넉히 12초 타임아웃
        });

        try {
            await APIClient.restartStrategyDaemon(cmdId);
            showToast("전략 데몬 자가 재기동 신호가 전송되었습니다.", "success");
        } catch (error) {
            pendingCommands.delete(cmdId);
            if (btn) btn.classList.remove('loading');
            showToast("전략 데몬 재기동 신호 전송 실패", "error");
        }
    }

    /**
     * 타임아웃 발생 처리
     */
    function handleTimeout(cmdId) {
        const cmd = pendingCommands.get(cmdId);
        if (!cmd) return;

        console.error(`[StrategyDaemonView] Command timeout: ${cmd.type} (id: ${cmdId})`);
        pendingCommands.delete(cmdId);

        const btn = document.getElementById('btn-strategy-restart-daemon');
        if (btn) btn.classList.remove('loading');

        showToast("전략 데몬 재기동 응답 타임아웃. 수동 복구 여부를 확인해 주십시오.", "error");
        loadDaemonDetail();
    }

    /**
     * ZMQ -> 웹소켓을 타고 들어온 실시간 strategy_daemon_detail 처리
     */
    function handleDaemonDetail(data) {
        lastDetailHeartbeat = Date.now();

        if (!data.last_updated_at) {
            data.last_updated_at = Date.now();
        }

        // 만약 data가 API 응답처럼 daemon_detail 래퍼를 가지고 있다면 풀어서 사용
        const detail = data.daemon_detail ? data.daemon_detail : data;

        // 재기동 펜딩이 있는 경우, PID 및 기동시각 대조를 통한 자가 복구 검증
        for (const [cmdId, cmd] of pendingCommands.entries()) {
            if (cmd.type === 'restart_daemon') {
                const newPid = detail.lifecycle ? detail.lifecycle.pid : null;
                const newStarted = detail.lifecycle ? detail.lifecycle.started_at : 0;

                const isNewProcess = (cmd.previousPid !== null && newPid !== cmd.previousPid);
                const isNewStartTime = (newStarted > cmd.previousStartedAt);

                if (isNewProcess || isNewStartTime) {
                    console.log(`[StrategyDaemonView] Strategy daemon restart verified. New PID: ${newPid}, StartedAt: ${newStarted}`);
                    
                    if (cmd.timeoutId) clearTimeout(cmd.timeoutId);
                    pendingCommands.delete(cmdId);

                    const btn = document.getElementById('btn-strategy-restart-daemon');
                    if (btn) btn.classList.remove('loading');

                    showToast("전략 데몬이 성공적으로 재기동되었습니다.", "success");
                    loadEvents(); // 감사 로그 리로드
                }
            }
        }

        updateUI(detail);
    }

    /**
     * ZMQ -> 웹소켓을 통해 실시간 수신된 제어 응답 결과(ACK) 핸들러
     */
    function handleCommandResult(data) {
        const cmdId = data.command_id;
        if (!cmdId) return;

        const cmd = pendingCommands.get(cmdId);
        if (!cmd) return;

        console.log(`[StrategyDaemonView] Strategy Command ACK received: ${cmd.type} -> ${data.status} (id: ${cmdId})`);

        if (cmd.timeoutId) clearTimeout(cmd.timeoutId);
        pendingCommands.delete(cmdId);

        const btn = document.getElementById('btn-strategy-restart-daemon');
        if (btn) btn.classList.remove('loading');

        if (data.status === 'SUCCESS') {
            showToast("전략 데몬 재기동이 정상 확인되었습니다.", "success");
        } else {
            const errorReason = data.error || '알 수 없는 이유';
            showToast(`전략 데몬 재기동 실패: ${errorReason}`, "error");
        }

        loadDaemonDetail();
        loadEvents();
    }

    /**
     * 뷰 퇴장 시 리소스 정리
     */
    function destroy() {
        if (staleCheckInterval) {
            clearInterval(staleCheckInterval);
            staleCheckInterval = null;
        }
        pendingCommands.forEach(cmd => {
            if (cmd.timeoutId) clearTimeout(cmd.timeoutId);
        });
        pendingCommands.clear();
    }

    return {
        init,
        destroy,
        loadEvents,
        handleDaemonDetail,
        handleCommandResult
    };
})();

// 전역 window 바인딩
window.StrategyDaemonView = StrategyDaemonView;

// 라우터 등록
if (typeof ViewRouter !== 'undefined') {
    ViewRouter.registerRoute('strategy-daemon-view', () => {
        if (typeof exitExplorerMode === 'function') exitExplorerMode();
        StrategyDaemonView.init();
    });
}
