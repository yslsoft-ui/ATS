/**
 * EvaluationDaemonView - 평가 데몬 모니터링 및 제어 전담 프론트엔드 컨트롤러
 */
const EvaluationDaemonView = (() => {
    // HTML 이스케이프 헬퍼
    function escapeHtml(text) {
        if (!text) return '';
        return String(text)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
    }

    // 시간 포맷팅 헬퍼 (ms단위 age -> 읽기 쉬운 문자열)
    function formatDuration(ms) {
        if (!ms || ms <= 0) return '-';
        const seconds = Math.floor(ms / 1000);
        if (seconds < 60) return `${seconds}초`;
        const minutes = Math.floor(seconds / 60);
        const remainingSeconds = seconds % 60;
        if (minutes < 60) return `${minutes}분 ${remainingSeconds}초`;
        const hours = Math.floor(minutes / 60);
        const remainingMinutes = minutes % 60;
        return `${hours}시간 ${remainingMinutes}분`;
    }

    // 하트비트 감시용 변수
    let lastDetailHeartbeat = 0; // ms
    let staleCheckInterval = null;

    // 제어 명령 펜딩 맵 (commandId -> { type, timeoutId, previousPid, previousStartedAt })
    const pendingCommands = new Map();

    // 평가 데몬 프로세스 메타데이터 백업 (재기동 확인용)
    let currentPid = null;
    let currentDaemonStartedAt = 0;
    let isStaleState = false;
    let currentRssMb = 0;

    // 모니터링 관련 임계값 설정
    let monitoringConfig = {
        daemon_detail_stale_ms: 15000,
        control_ack_timeout_ms: 5000
    };

    /**
     * 고유한 command_id 생성 헬퍼
     */
    function generateCommandId() {
        return 'cmd-eval-' + Math.random().toString(36).substr(2, 9) + '-' + Date.now();
    }

    /**
     * 화면 진입 시 초기 데이터 조회 및 주기적 타이머 기동
     */
    async function init() {
        console.log("[EvaluationDaemonView] Initializing view...");

        // 1. 초기 1회 REST API로 상세 정보, 평가 리스트, 수동 Job, 감사 로그 로드
        await loadDaemonDetail();
        await loadEvaluationsTable();
        await loadJobsTable();
        await loadEvents();

        // 2. 3초 주기 Stale/Heartbeat 감시 타이머 기동
        if (staleCheckInterval) {
            clearInterval(staleCheckInterval);
        }
        staleCheckInterval = setInterval(checkStaleStatus, 3000);

        // 3. 버튼 이벤트 바인딩 등록
        const restartBtn = document.getElementById('btn-evaluation-restart-daemon');
        if (restartBtn) {
            const newRestartBtn = restartBtn.cloneNode(true);
            restartBtn.parentNode.replaceChild(newRestartBtn, restartBtn);
            newRestartBtn.addEventListener('click', restartEvaluationDaemon);
        }
    }

    /**
     * REST API 호출을 통한 전체 상태 갱신
     */
    async function loadDaemonDetail() {
        try {
            const res = await APIClient.fetchEvaluationDaemonDetail();
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
            console.error("[EvaluationDaemonView] Failed to fetch evaluation daemon details:", error);
            showToast("평가 데몬 정보를 가져오는데 실패했습니다.", "error");
        }
    }

    /**
     * 사후 평가 현황 테이블 로드
     */
    async function loadEvaluationsTable() {
        const tbody = document.getElementById('evaluation-results-tbody');
        if (!tbody) return;

        try {
            const evals = await APIClient.fetchEvaluations(50);
            tbody.innerHTML = '';

            if (!evals || evals.length === 0) {
                tbody.innerHTML = '<tr><td colspan="11" style="text-align: center; color: #64748B; padding: 20px;">기록된 사후 평가 내역이 없습니다.</td></tr>';
                return;
            }

            evals.forEach(pe => {
                const tr = document.createElement('tr');
                
                const dueTimeStr = pe.due_at ? new Date(pe.due_at).toLocaleString() : '-';
                const evalTimeStr = pe.evaluated_at ? new Date(pe.evaluated_at).toLocaleString() : '-';
                
                const predRoi = pe.predicted_roi_7d !== null ? `${pe.predicted_roi_7d.toFixed(2)}%` : '-';
                const actRoi = pe.actual_roi_7d !== null ? `${pe.actual_roi_7d.toFixed(2)}%` : '-';
                
                // ROI Gap의 부호에 따른 색상 정의 (RULE[design.md] 상승: Vibrant Red, 하락: Vibrant Blue)
                let gapColor = '#64748B'; // 보합
                let gapText = '-';
                if (pe.roi_gap !== null && pe.roi_gap !== undefined) {
                    gapText = `${pe.roi_gap.toFixed(2)}%`;
                    if (pe.roi_gap > 0) {
                        gapColor = '#FF4B4B'; // 상승
                        gapText = `+${gapText}`;
                    } else if (pe.roi_gap < 0) {
                        gapColor = '#0072FF'; // 하락
                    }
                }

                // 상태 배지 렌더링
                let statusBadge = `<span class="badge" style="background: #475569; color: #F8FAFC;">${pe.evaluation_status}</span>`;
                if (pe.evaluation_status === 'COMPLETED') {
                    statusBadge = '<span class="badge success" style="font-size: 0.72rem; padding: 2px 6px;">완료</span>';
                } else if (pe.evaluation_status === 'PENDING') {
                    statusBadge = '<span class="badge" style="background: rgba(245, 158, 11, 0.2); color: #F59E0B; font-size: 0.72rem; padding: 2px 6px;">대기</span>';
                } else if (pe.evaluation_status === 'EVALUATING') {
                    statusBadge = '<span class="badge" style="background: rgba(16, 185, 129, 0.2); color: #10B981; font-size: 0.72rem; padding: 2px 6px;">평가중</span>';
                } else if (pe.evaluation_status === 'ERROR') {
                    statusBadge = '<span class="badge danger" style="font-size: 0.72rem; padding: 2px 6px;">에러</span>';
                }

                tr.innerHTML = `
                    <td style="color: #64748B; font-family: monospace;">#${pe.proposal_id}</td>
                    <td style="font-family: monospace; font-weight: bold; color: #cbd5e1;">${pe.strategy_id}</td>
                    <td style="font-weight: bold; color: #F8FAFC;">${pe.symbol || '-'}</td>
                    <td style="text-align: center; color: #94A3B8;">${pe.horizon_name}</td>
                    <td style="text-align: right; font-family: 'Roboto Mono', monospace; color: #94A3B8;">${predRoi}</td>
                    <td style="text-align: right; font-family: 'Roboto Mono', monospace; color: #F8FAFC;">${actRoi}</td>
                    <td style="text-align: right; font-family: 'Roboto Mono', monospace; font-weight: bold; color: ${gapColor};">${gapText}</td>
                    <td style="text-align: center;">${statusBadge}</td>
                    <td style="color: #94A3B8; font-family: monospace; font-size: 0.75rem;">${dueTimeStr}</td>
                    <td style="color: #94A3B8; font-family: monospace; font-size: 0.75rem;">${evalTimeStr}</td>
                    <td style="color: #94A3B8; font-size: 0.75rem; max-width: 150px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;" title="${escapeHtml(pe.last_error)}">${escapeHtml(pe.last_error) || '-'}</td>
                `;
                tbody.appendChild(tr);
            });
        } catch (error) {
            console.error("[EvaluationDaemonView] Failed to load evaluations table:", error);
        }
    }

    /**
     * 수동 재평가 Job 내역 테이블 로드
     */
    async function loadJobsTable() {
        const tbody = document.getElementById('evaluation-jobs-tbody');
        if (!tbody) return;

        try {
            const jobs = await APIClient.fetchReevaluationJobsList(50);
            tbody.innerHTML = '';

            if (!jobs || jobs.length === 0) {
                tbody.innerHTML = '<tr><td colspan="8" style="text-align: center; color: #64748B; padding: 20px;">수동 재평가 실행 내역이 없습니다.</td></tr>';
                return;
            }

            jobs.forEach(job => {
                const tr = document.createElement('tr');
                
                const reqTime = job.requested_at ? new Date(job.requested_at).toLocaleString() : '-';
                const startTime = job.started_at ? new Date(job.started_at).toLocaleString() : '-';
                const finishTime = job.finished_at ? new Date(job.finished_at).toLocaleString() : '-';

                let statusBadge = `<span class="badge" style="background: #475569; color: #F8FAFC;">${job.status}</span>`;
                if (job.status === 'COMPLETED') {
                    statusBadge = '<span class="badge success" style="font-size: 0.72rem; padding: 2px 6px;">성공</span>';
                } else if (job.status === 'QUEUED') {
                    statusBadge = '<span class="badge" style="background: rgba(245, 158, 11, 0.2); color: #F59E0B; font-size: 0.72rem; padding: 2px 6px;">대기</span>';
                } else if (job.status === 'RUNNING') {
                    statusBadge = '<span class="badge" style="background: rgba(16, 185, 129, 0.2); color: #10B981; font-size: 0.72rem; padding: 2px 6px;">실행중</span>';
                } else if (job.status === 'FAILED') {
                    statusBadge = '<span class="badge danger" style="font-size: 0.72rem; padding: 2px 6px;">실패</span>';
                }

                tr.innerHTML = `
                    <td style="color: #64748B; font-family: monospace;">Job #${job.job_id}</td>
                    <td style="color: #cbd5e1; font-family: monospace;">#${job.proposal_id}</td>
                    <td style="text-align: center;">${statusBadge}</td>
                    <td style="color: #94A3B8; font-family: monospace; font-size: 0.75rem;">${reqTime}</td>
                    <td style="color: #94A3B8; font-family: monospace; font-size: 0.75rem;">${startTime}</td>
                    <td style="color: #94A3B8; font-family: monospace; font-size: 0.75rem;">${finishTime}</td>
                    <td style="text-align: center; color: #F8FAFC; font-family: monospace;">${job.attempt_count || 1}</td>
                    <td style="color: #94A3B8; font-size: 0.75rem; max-width: 200px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;" title="${escapeHtml(job.error_message)}">${escapeHtml(job.error_message) || '-'}</td>
                `;
                tbody.appendChild(tr);
            });
        } catch (error) {
            console.error("[EvaluationDaemonView] Failed to load jobs table:", error);
        }
    }

    /**
     * 하단 감사 이벤트 로그 로드
     */
    async function loadEvents() {
        const tbody = document.getElementById('evaluation-events-tbody');
        if (!tbody) return;

        try {
            const events = await APIClient.fetchSystemEvents(50);
            tbody.innerHTML = '';

            const filteredEvents = (events || []).filter(event => 
                event.target === 'shadow_eval_daemon' || 
                (event.event_type && (
                    event.event_type.startsWith('EVAL_') || 
                    event.event_type.startsWith('PROPOSAL_REEVALUATION_')
                ))
            ).slice(0, 20); // 최근 20건만 표시

            if (filteredEvents.length === 0) {
                tbody.innerHTML = '<tr><td colspan="4" style="text-align: center; color: #64748B; padding: 20px;">기록된 평가 감사 이벤트가 없습니다.</td></tr>';
                return;
            }

            filteredEvents.forEach(event => {
                const tr = document.createElement('tr');
                const timeStr = new Date(event.timestamp).toLocaleString();

                let typeStyle = 'color: #94A3B8; font-weight: bold;';
                if (event.event_type.includes('ERROR') || event.event_type.includes('FAIL') || event.event_type.includes('FAILED')) {
                    typeStyle = 'color: #0072FF; font-weight: bold;'; // 하락/Bear/오류 방향
                } else if (event.event_type.includes('START') || event.event_type.includes('COMPLETED') || event.event_type.includes('SUCCESS')) {
                    typeStyle = 'color: #FF4B4B; font-weight: bold;'; // 상승/Bull/기동 방향
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
            console.error("[EvaluationDaemonView] Failed to load evaluation events:", error);
        }
    }

    /**
     * Stale & Heartbeat 감지 스케줄러 (3초 주기)
     */
    function checkStaleStatus() {
        const now = Date.now();

        const isDaemonStale = lastDetailHeartbeat === 0 || (now - lastDetailHeartbeat > monitoringConfig.daemon_detail_stale_ms);

        if (isDaemonStale) {
            if (!isStaleState) {
                isStaleState = true;
                console.warn("[EvaluationDaemonView] Evaluation daemon offline.");
                document.querySelectorAll('#evaluation-daemon-view .diag-card, #evaluation-daemon-view .card').forEach(el => {
                    el.style.opacity = '0.5';
                });
            }
        } else {
            if (isStaleState) {
                isStaleState = false;
                console.log("[EvaluationDaemonView] Evaluation daemon online.");
                document.querySelectorAll('#evaluation-daemon-view .diag-card, #evaluation-daemon-view .card').forEach(el => {
                    el.style.opacity = '1';
                });
            }
        }

        // 상단 공통 UI에 동기화
        if (typeof DaemonMonitoringView !== 'undefined') {
            DaemonMonitoringView.updateSharedHeader('evaluation', {
                pid: currentPid,
                startedAtFormatted: currentDaemonStartedAt ? new Date(currentDaemonStartedAt).toLocaleString() : '-',
                heartbeatFormatted: lastDetailHeartbeat ? new Date(lastDetailHeartbeat).toLocaleTimeString() : '-',
                rssMb: currentRssMb,
                cpuUsagePct: null,
                isStale: isDaemonStale,
                staleReason: isDaemonStale ? "연결 끊김" : null,
                state: isDaemonStale ? 'ERROR' : 'ACTIVE'
            });
        }
    }

    /**
     * 실시간 수신 데이터 UI 바인딩 핵심 로직
     */
    function updateUI(data) {
        if (!data) return;

        const lifecycle = data.lifecycle || {};
        const stats = data.telemetry || {};

        // 1. 헤더 메타데이터 정보 바인딩 - 상단 공통 UI 위임
        if (lifecycle.pid) currentPid = lifecycle.pid;
        if (lifecycle.started_at) currentDaemonStartedAt = lifecycle.started_at;
        if (lifecycle.rss_mb) currentRssMb = lifecycle.rss_mb;

        const updateTime = data.last_updated_at || lifecycle.heartbeat || Date.now();
        const isDaemonStale = lastDetailHeartbeat === 0 || (Date.now() - lastDetailHeartbeat > monitoringConfig.daemon_detail_stale_ms);
        if (typeof DaemonMonitoringView !== 'undefined') {
            DaemonMonitoringView.updateSharedHeader('evaluation', {
                pid: currentPid,
                startedAtFormatted: currentDaemonStartedAt ? new Date(currentDaemonStartedAt).toLocaleString() : '-',
                heartbeatFormatted: new Date(updateTime).toLocaleTimeString(),
                rssMb: currentRssMb,
                cpuUsagePct: null,
                isStale: isDaemonStale,
                staleReason: isDaemonStale ? "연결 끊김" : null,
                state: isDaemonStale ? 'ERROR' : 'ACTIVE'
            });
        }

        // 2. 5단 진단 카드 메트릭 갱신
        const memEl = document.getElementById('res-val-evaluation-memory');
        const futPendingEl = document.getElementById('res-val-evaluation-future-pending');
        const duePendingEl = document.getElementById('res-val-evaluation-due-pending');
        const compTodayEl = document.getElementById('res-val-evaluation-completed-today');
        const failTodayEl = document.getElementById('res-val-evaluation-failed-today');
        const jobsRunningEl = document.getElementById('res-val-evaluation-jobs-running');
        const jobsTodayEl = document.getElementById('res-val-evaluation-jobs-today');
        const oldestDueEl = document.getElementById('res-val-evaluation-oldest-due');
        const maxLockEl = document.getElementById('res-val-evaluation-max-lock');

        if (memEl) memEl.innerText = lifecycle.rss_mb ? `${lifecycle.rss_mb.toFixed(2)} MB` : '- MB';
        
        if (futPendingEl) futPendingEl.innerText = stats.future_pending !== undefined ? `${stats.future_pending} 건` : '- 건';
        if (duePendingEl) duePendingEl.innerText = stats.due_pending !== undefined ? `Due: ${stats.due_pending}건` : 'Due: -건';

        if (compTodayEl) compTodayEl.innerText = stats.completed_today !== undefined ? `${stats.completed_today} 건` : '- 건';
        if (failTodayEl) failTodayEl.innerText = stats.failed_today !== undefined ? `실패: ${stats.failed_today}건` : '실패: -건';

        if (jobsRunningEl) jobsRunningEl.innerText = stats.manual_jobs_queued_running !== undefined ? `${stats.manual_jobs_queued_running} 건` : '- 건';
        if (jobsTodayEl) {
            const compJ = stats.manual_jobs_completed_today || 0;
            const failJ = stats.manual_jobs_failed_today || 0;
            jobsTodayEl.innerText = `오늘: 완료 ${compJ} / 실패 ${failJ}`;
        }

        if (oldestDueEl) oldestDueEl.innerText = formatDuration(stats.oldest_due_age_ms);
        if (maxLockEl) maxLockEl.innerText = formatDuration(stats.max_lock_age_ms);
    }

    /**
     * 평가 데몬 프로세스 자체 재기동
     */
    async function restartEvaluationDaemon() {
        const cmdId = generateCommandId();
        console.log(`[EvaluationDaemonView] Request restarting evaluation daemon (id: ${cmdId})`);

        const backupPid = currentPid;
        const backupStartedAt = currentDaemonStartedAt;

        const btn = document.getElementById('btn-evaluation-restart-daemon');
        if (btn) btn.classList.add('loading');

        pendingCommands.set(cmdId, {
            type: 'restart_daemon',
            previousPid: backupPid,
            previousStartedAt: backupStartedAt,
            timeoutId: setTimeout(() => {
                handleTimeout(cmdId);
                if (btn) btn.classList.remove('loading');
            }, 12000)
        });

        try {
            await APIClient.restartEvaluationDaemon(cmdId);
            showToast("평가 데몬 자가 재기동 신호가 전송되었습니다.", "success");
        } catch (error) {
            pendingCommands.delete(cmdId);
            if (btn) btn.classList.remove('loading');
            showToast("평가 데몬 재기동 신호 전송 실패", "error");
        }
    }

    /**
     * 타임아웃 발생 처리
     */
    function handleTimeout(cmdId) {
        const cmd = pendingCommands.get(cmdId);
        if (!cmd) return;

        console.error(`[EvaluationDaemonView] Command timeout: ${cmd.type} (id: ${cmdId})`);
        pendingCommands.delete(cmdId);

        const btn = document.getElementById('btn-evaluation-restart-daemon');
        if (btn) btn.classList.remove('loading');

        showToast("평가 데몬 재기동 응답 타임아웃. 수동 상태를 확인해 주십시오.", "error");
        loadDaemonDetail();
    }

    /**
     * ZMQ -> 웹소켓 실시간 evaluation_daemon_detail 수신 처리
     */
    function handleDaemonDetail(data) {
        lastDetailHeartbeat = Date.now();

        if (!data.last_updated_at) {
            data.last_updated_at = Date.now();
        }

        const detail = data.daemon_detail ? data.daemon_detail : data;

        // 재기동 펜딩이 있는 경우 복구 검증
        for (const [cmdId, cmd] of pendingCommands.entries()) {
            if (cmd.type === 'restart_daemon') {
                const newPid = detail.lifecycle ? detail.lifecycle.pid : null;
                const newStarted = detail.lifecycle ? detail.lifecycle.started_at : 0;

                const isNewProcess = (cmd.previousPid !== null && newPid !== cmd.previousPid);
                const isNewStartTime = (newStarted > cmd.previousStartedAt);

                if (isNewProcess || isNewStartTime) {
                    console.log(`[EvaluationDaemonView] Evaluation daemon restart verified. New PID: ${newPid}, StartedAt: ${newStarted}`);
                    
                    if (cmd.timeoutId) clearTimeout(cmd.timeoutId);
                    pendingCommands.delete(cmdId);

                    const btn = document.getElementById('btn-evaluation-restart-daemon');
                    if (btn) btn.classList.remove('loading');

                    showToast("평가 데몬이 성공적으로 재기동되었습니다.", "success");
                    loadEvents(); // 감사 로그 리로드
                    loadEvaluationsTable(); // 테이블 리로드
                    loadJobsTable();
                }
            }
        }

        updateUI(detail);
    }

    /**
     * ZMQ -> 웹소켓 제어 응답 결과(ACK) 핸들러
     */
    function handleCommandResult(data) {
        const cmdId = data.command_id;
        if (!cmdId) return;

        const cmd = pendingCommands.get(cmdId);
        if (!cmd) return;

        console.log(`[EvaluationDaemonView] Evaluation Command ACK received: ${cmd.type} -> ${data.status} (id: ${cmdId})`);

        if (cmd.timeoutId) clearTimeout(cmd.timeoutId);
        pendingCommands.delete(cmdId);

        const btn = document.getElementById('btn-evaluation-restart-daemon');
        if (btn) btn.classList.remove('loading');

        if (data.status === 'SUCCESS') {
            showToast("평가 데몬 재기동이 정상 확인되었습니다.", "success");
        } else {
            const errorReason = data.error || '알 수 없는 이유';
            showToast(`평가 데몬 재기동 실패: ${errorReason}`, "error");
        }

        loadDaemonDetail();
        loadEvaluationsTable();
        loadJobsTable();
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
        loadEvaluationsTable,
        loadJobsTable,
        handleDaemonDetail,
        handleCommandResult
    };
})();

// 전역 window 바인딩
window.EvaluationDaemonView = EvaluationDaemonView;
