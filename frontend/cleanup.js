/**
 * CleanupView - 시장 데이터 클린업 데몬 모니터링 및 원격 제어 전담 컨트롤러
 */
const CleanupView = (() => {
    // 제어 명령 펜딩 맵 (commandId -> { type, timeoutId, timerStart })
    const pendingCommands = new Map();
    
    // 로딩 상태 플래그
    let isActionPending = false;

    // stale 감지용 상태 변수
    let staleCheckInterval = null;
    let lastDetailHeartbeat = 0;
    let currentStatus = null;

    const monitoringConfig = {
        daemon_detail_stale_ms: 15000
    };

    // 타임아웃 설정 (승인 기준)
    const TIMEOUTS = {
        LIFECYCLE: 3000,    // 시작, 중지, 재기동: 3초
        PREVIEW: 5000,      // 조회(미리보기): 5초
        RUN: 30000          // 즉시 삭제 실행: 30초
    };

    /**
     * 고유한 command_id 생성 헬퍼
     */
    function generateCommandId() {
        return 'cmd-clean-' + Math.random().toString(36).substr(2, 9) + '-' + Date.now();
    }

    /**
     * 숫자 천단위 콤마 포맷터
     */
    function formatNumber(num) {
        if (num === null || num === undefined || isNaN(num)) return '-';
        return Number(num).toLocaleString();
    }

    /**
     * 타임스탬프 포맷터 (YYYY-MM-DD HH:mm:ss)
     */
    function formatTimestamp(tsSec) {
        if (!tsSec || tsSec <= 0) return '-';
        const date = new Date(tsSec * 1000);
        return date.getFullYear() + '-' +
            String(date.getMonth() + 1).padStart(2, '0') + '-' +
            String(date.getDate()).padStart(2, '0') + ' ' +
            String(date.getHours()).padStart(2, '0') + ':' +
            String(date.getMinutes()).padStart(2, '0') + ':' +
            String(date.getSeconds()).padStart(2, '0');
    }

    /**
     * 연결 지연 체크 루프
     */
    function checkStaleStatus() {
        const now = Date.now();
        const isDaemonStale = lastDetailHeartbeat === 0 || (now - lastDetailHeartbeat > monitoringConfig.daemon_detail_stale_ms);

        if (typeof DaemonMonitoringView !== 'undefined' && currentStatus) {
            DaemonMonitoringView.updateSharedHeader('cleanup', {
                pid: currentStatus.pid || null,
                startedAtFormatted: formatTimestamp(currentStatus.start_time),
                heartbeatFormatted: currentStatus.timestamp ? new Date(currentStatus.timestamp * 1000).toLocaleTimeString() : '-',
                rssMb: currentStatus.rss_mb || 0,
                cpuUsagePct: null,
                isStale: isDaemonStale,
                staleReason: isDaemonStale ? "연결 끊김" : null,
                state: isDaemonStale ? 'ERROR' : currentStatus.cleanup_state
            });
        }
    }

    /**
     * 화면 진입 시 초기화 함수
     */
    async function init() {
        console.log("[CleanupView] Initializing view...");

        lastDetailHeartbeat = Date.now();
        if (staleCheckInterval) {
            clearInterval(staleCheckInterval);
        }
        staleCheckInterval = setInterval(checkStaleStatus, 3000);
        checkStaleStatus();
        
        // 1. 기본 오늘 날짜를 Date Picker에 세팅 (Ad-hoc 기본값: 30일 전 추천)
        const dateInput = document.getElementById('input-cleanup-date');
        if (dateInput) {
            if (!dateInput.value) {
                const defaultDate = new Date();
                defaultDate.setDate(defaultDate.getDate() - 3); // 기본 3일 전
                dateInput.value = defaultDate.toISOString().split('T')[0];
            }
            // change 이벤트 바인딩 (최초 1회 보장 처리를 위해 제거 후 재등록)
            const newDateInput = dateInput.cloneNode(true);
            dateInput.replaceWith(newDateInput);
            newDateInput.addEventListener('change', previewCleanup);
        }

        // 2. 초기 1회 REST API로 현재 상태 및 감사 이력 로드
        await loadCleanupStatus();
        await loadEvents();

        // 3. 버튼 이벤트 리스너 바인딩 (최초 1회 보장 처리를 위해 제거 후 재등록)
        const restartBtn = document.getElementById('btn-cleanup-restart-daemon');
        const runBtn = document.getElementById('btn-cleanup-run');

        if (restartBtn) {
            const newRestartBtn = restartBtn.cloneNode(true);
            restartBtn.replaceWith(newRestartBtn);
            newRestartBtn.addEventListener('click', restartCleanupDaemon);
        }
        if (runBtn) {
            const newRunBtn = runBtn.cloneNode(true);
            runBtn.replaceWith(newRunBtn);
            newRunBtn.addEventListener('click', runCleanup);
        }

        // 4. 초기 구동 시 해당 기본 날짜 기준 틱 예상량 자동 1회 조회
        await previewCleanup();
    }

    /**
     * 특정 날짜 기준 삭제 대상 틱(trades)의 건수를 비동기적으로 조회합니다.
     */
    async function previewCleanup() {
        const dateInput = document.getElementById('input-cleanup-date');
        if (!dateInput || !dateInput.value) return;

        const dateStr = dateInput.value;
        const commandId = generateCommandId();

        const previewText = document.getElementById('cleanup-manual-preview-text');
        if (previewText) {
            previewText.innerText = "삭제 대상 틱 수: 조회 중...";
            previewText.style.color = "#F59E0B"; // Amber
        }

        try {
            registerPendingCommand(commandId, 'cleanup_preview', TIMEOUTS.PREVIEW);
            const res = await APIClient.fetchCleanupPreview(dateStr, commandId);
            if (!res || res.status !== 'pending') {
                throw new Error("Invalid response status");
            }
        } catch (err) {
            clearPendingImmediate(commandId);
            if (previewText) {
                previewText.innerText = "삭제 대상 틱 수: 조회 실패";
                previewText.style.color = "#FF4B4B"; // Red
            }
        }
    }

    /**
     * REST API로부터 실시간 클린업 정보 강제 갱신
     */
    async function loadCleanupStatus() {
        try {
            const data = await APIClient.fetchCleanupStatus();
            if (data) {
                renderStatus(data);
            }
        } catch (error) {
            console.error("[CleanupView] Failed to fetch cleanup status:", error);
            showToast("클린업 상태 조회 실패", "error");
        }
    }

    /**
     * 감사 이력 데이터 로딩 및 렌더링
     */
    async function loadEvents() {
        const tbody = document.getElementById('cleanup-history-tbody');
        if (!tbody) return;

        try {
            // 시스템 이벤트 목록 조회 (넉넉히 50개 로드 후 필터링)
            const events = await APIClient.fetchSystemEvents(50);
            tbody.innerHTML = '';

            // 클린업 관련 이벤트 필터링 (MARKET_DATA_CLEANUP_SUMMARY)
            const cleanupEvents = events.filter(e => e.event_type === 'MARKET_DATA_CLEANUP_SUMMARY');

            if (cleanupEvents.length === 0) {
                tbody.innerHTML = '<tr><td colspan="6" style="text-align: center; color: #64748B; padding: 20px;">최근 정리 감사 이력이 없습니다.</td></tr>';
                return;
            }

            // 최근 최대 10건만 노출
            const top10 = cleanupEvents.slice(0, 10);
            top10.forEach(event => {
                const tr = document.createElement('tr');
                tr.style.borderBottom = '1px solid rgba(148, 163, 184, 0.05)';
                tr.style.height = '36px';

                const timeStr = new Date(event.timestamp).toLocaleString();
                
                let tradesDeleted = '-';
                let candlesDeleted = '-';
                let candlesDownsampled = '-';
                let typeStr = '자동';
                let typeColor = '#10B981'; // Green
                let detailText = '스케줄러에 의한 자동 보존 정책 청소';

                try {
                    const meta = JSON.parse(event.message);
                    tradesDeleted = formatNumber(meta.trades_deleted) + ' 건';
                    candlesDeleted = formatNumber(meta.candles_deleted) + ' 건';
                    candlesDownsampled = formatNumber(meta.candles_downsampled) + ' 건';
                    
                    if (meta.type === 'manual') {
                        typeStr = '수동';
                        typeColor = '#FF4B4B'; // Red
                        detailText = `Ad-hoc 수동 정리 실행 (기준일: ${meta.trades_cutoff ? new Date(meta.trades_cutoff).toISOString().split('T')[0] : '지정일'})`;
                    } else {
                        detailText = `자동 정리 완료 (TTL cutoff: trades ${meta.trades_hours || 72}h, candles ${meta.candles_days || 30}d)`;
                    }
                } catch (jsonErr) {
                    // JSON 파싱 실패 시 원본 메시지 노출 fallback
                    detailText = event.message;
                }

                tr.innerHTML = `
                    <td style="color: #F8FAFC; text-align: left; font-family: 'Roboto Mono', monospace;">${timeStr}</td>
                    <td style="text-align: left;"><span style="color: ${typeColor}; font-weight: bold;">${typeStr}</span></td>
                    <td style="text-align: right; color: #94A3B8;">${tradesDeleted}</td>
                    <td style="text-align: right; color: #94A3B8;">${candlesDeleted}</td>
                    <td style="text-align: right; color: #94A3B8;">${candlesDownsampled}</td>
                    <td style="text-align: left; padding-left: 20px; color: #64748B; font-size: 0.8rem;" title="${detailText}">${detailText}</td>
                `;
                tbody.appendChild(tr);
            });
        } catch (error) {
            console.error("[CleanupView] Failed to fetch audit events:", error);
            tbody.innerHTML = '<tr><td colspan="6" style="text-align: center; color: #EF4444; padding: 20px;">감사 이력을 불러오는 중 에러가 발생했습니다.</td></tr>';
        }
    }

    /**
     * ZMQ `market_cleanup_status` 실시간 텔레메트리 렌더링
     */
    function renderStatus(status) {
        if (!status) return;

        currentStatus = status;
        lastDetailHeartbeat = Date.now();

        const isDaemonStale = lastDetailHeartbeat === 0 || (Date.now() - lastDetailHeartbeat > monitoringConfig.daemon_detail_stale_ms);

        if (typeof DaemonMonitoringView !== 'undefined') {
            DaemonMonitoringView.updateSharedHeader('cleanup', {
                pid: status.pid || null,
                startedAtFormatted: formatTimestamp(status.start_time),
                heartbeatFormatted: status.timestamp ? new Date(status.timestamp * 1000).toLocaleTimeString() : '-',
                rssMb: status.rss_mb || 0,
                cpuUsagePct: null,
                isStale: isDaemonStale,
                staleReason: isDaemonStale ? "연결 끊김" : null,
                state: isDaemonStale ? 'ERROR' : status.cleanup_state
            });
        }

        const cleanupMemory = document.getElementById('res-val-cleanup-memory');
        if (cleanupMemory) {
            cleanupMemory.innerText = (status.rss_mb !== undefined && status.rss_mb !== null && status.rss_mb > 0)
                ? `${status.rss_mb.toFixed(2)} MB`
                : '- MB';
        }

        // 3. 글로벌 설정 정보
        const intervalText = document.getElementById('cleanup-setting-interval');
        if (intervalText) {
            const min = Math.floor(status.cleanup_interval / 60);
            intervalText.innerText = min >= 60 ? `${Math.floor(min/60)}시간` : `${min}분`;
        }

        const tradesTtlText = document.getElementById('cleanup-setting-trades-ttl');
        if (tradesTtlText) {
            tradesTtlText.innerText = `${status.trades_hours}시간 (${Math.floor(status.trades_hours / 24)}일)`;
        }

        const candlesTtlText = document.getElementById('cleanup-setting-candles-ttl');
        if (candlesTtlText) {
            candlesTtlText.innerText = `${status.candles_days}일`;
        }

        // 4. 마지막 실행 통계
        const lastTimeText = document.getElementById('cleanup-stat-last-time');
        if (lastTimeText) {
            lastTimeText.innerText = formatTimestamp(status.last_cleanup_time);
        }

        const nextTimeText = document.getElementById('cleanup-stat-next-time');
        if (nextTimeText) {
            if (status.cleanup_state === 'PAUSED') {
                nextTimeText.innerText = '일시중지됨';
                nextTimeText.style.color = '#94A3B8';
            } else {
                nextTimeText.innerText = formatTimestamp(status.next_cleanup_time);
                nextTimeText.style.color = '#F8FAFC';
            }
        }

        const durationText = document.getElementById('cleanup-stat-duration');
        if (durationText) {
            durationText.innerText = status.last_cleanup_duration_ms > 0 
                ? `${formatNumber(status.last_cleanup_duration_ms)} ms` 
                : '-';
        }

        // 5. 마지막 삭제량 요약 상세 카드 (수량 및 삭제선 시각 통합 렌더링)
        const delTradesDetail = document.getElementById('cleanup-stat-deleted-trades-detail');
        if (delTradesDetail) {
            const count = formatNumber(status.last_cleanup_summary?.trades_deleted);
            const cutoff = formatTimestamp(status.last_trades_cutoff);
            delTradesDetail.innerText = `${count} 건 (${cutoff} 이전)`;
        }

        const delCandlesDetail = document.getElementById('cleanup-stat-deleted-candles-detail');
        if (delCandlesDetail) {
            const count = formatNumber(status.last_cleanup_summary?.candles_deleted);
            const cutoff = formatTimestamp(status.last_candles_cutoff);
            delCandlesDetail.innerText = `${count} 건 (${cutoff} 이전)`;
        }

        const dwnCandlesDetail = document.getElementById('cleanup-stat-downsampled-detail');
        if (dwnCandlesDetail) {
            const count = formatNumber(status.last_cleanup_summary?.candles_downsampled);
            const cutoff = formatTimestamp(status.last_candles_cutoff);
            dwnCandlesDetail.innerText = `${count} 건 (${cutoff} 이전)`;
        }

        // 8. 다음 자동 정리 대상 예상 상세 카드 (수량 및 삭제선 시각 통합 렌더링)
        const targetTradesDetail = document.getElementById('next-target-trades-detail');
        if (targetTradesDetail) {
            const count = formatNumber(status.next_cleanup_target_trades);
            const cutoff = formatTimestamp(status.next_cleanup_target_trades_cutoff);
            targetTradesDetail.innerText = `${count} 건 (${cutoff} 이전)`;
        }

        const targetCandlesDetail = document.getElementById('next-target-candles-detail');
        if (targetCandlesDetail) {
            const count = formatNumber(status.next_cleanup_target_candles);
            const cutoff = formatTimestamp(status.next_cleanup_target_candles_cutoff);
            targetCandlesDetail.innerText = `${count} 건 (${cutoff} 이전)`;
        }

        const targetDownsampleDetail = document.getElementById('next-target-downsample-detail');
        if (targetDownsampleDetail) {
            const count = formatNumber(status.next_cleanup_target_downsample);
            const cutoff = formatTimestamp(status.next_cleanup_target_candles_cutoff);
            targetDownsampleDetail.innerText = `${count} 건 (${cutoff} 이전)`;
        }

        // 6. 에러 정보 표시
        const errorContainer = document.getElementById('cleanup-error-container');
        const errorText = document.getElementById('cleanup-error-text');
        if (status.last_error && status.cleanup_state === 'ERROR') {
            if (errorText) errorText.innerText = status.last_error;
            if (errorContainer) errorContainer.style.display = 'block';
        } else {
            if (errorContainer) errorContainer.style.display = 'none';
        }

        // 7. [CRITICAL] RUNNING_ONCE 이거나 락이 걸린 중복 실행 불가 상황일 때, 수동 제어 버튼 비활성화 (Mutex Protection)
        const isBusy = status.cleanup_state === 'RUNNING_ONCE' || isActionPending;
        updateButtonLockState(isBusy);
    }

    /**
     * 수동 삭제 및 미리보기 버튼의 비활성화(disabled) 상태 통제
     */
    function updateButtonLockState(isBusy) {
        const runBtn = document.getElementById('btn-cleanup-run');
        const restartBtn = document.getElementById('btn-cleanup-restart-daemon');

        if (runBtn) runBtn.disabled = isBusy;

        // 라이프사이클 버튼 등도 펜딩 중인 상황에 비활성화
        if (restartBtn) restartBtn.disabled = isActionPending;
    }

    /**
     * 펜딩 명령 등록 및 비동기 타임아웃 타이머 스케줄링
     */
    function registerPendingCommand(commandId, type, timeoutDuration) {
        isActionPending = true;
        updateButtonLockState(true);

        const timeoutId = setTimeout(() => {
            handleCommandTimeout(commandId);
        }, timeoutDuration);

        pendingCommands.set(commandId, {
            type,
            timeoutId,
            timerStart: Date.now()
        });
    }

    /**
     * 비동기 command_id 타임아웃 처리
     */
    function handleCommandTimeout(commandId) {
        const cmd = pendingCommands.get(commandId);
        if (!cmd) return;

        pendingCommands.delete(commandId);
        isActionPending = false;
        
        // 버튼 락 상태 복원
        loadCleanupStatus().then(() => {
            updateButtonLockState(false);
        });

        const cmdNames = {
            'restart_daemon': '데몬 재기동',
            'cleanup_run_once': '즉시 삭제 실행',
            'cleanup_preview': '삭제 대상 조회'
        };
        const friendlyName = cmdNames[cmd.type] || cmd.type;

        if (cmd.type === 'cleanup_preview') {
            const previewText = document.getElementById('cleanup-manual-preview-text');
            if (previewText) {
                previewText.innerText = "삭제 대상 틱 수: 조회 실패";
                previewText.style.color = "#FF4B4B";
            }
        } else {
            showToast(`${friendlyName} 명령 응답 시간(timeout)을 초과했습니다. 상태를 다시 확인해보세요.`, "error");
        }
    }

    // --- 원격 제어 비동기 REST API 핸들러들 ---



    async function restartCleanupDaemon() {
        if (isActionPending) return;
        
        if (!confirm("주의: 클린업 데몬 프로세스를 정말 재기동하시겠습니까?\n스케줄러 작업 중이었던 경우 작업이 유실될 수 있습니다.")) {
            return;
        }

        const commandId = generateCommandId();
        try {
            registerPendingCommand(commandId, 'restart_daemon', TIMEOUTS.LIFECYCLE);
            const res = await APIClient.restartCleanupDaemon(commandId);
            if (res && res.status === 'pending') {
                showToast("데몬 재기동 신호를 송신했습니다.", "info");
            } else {
                throw new Error("Invalid response status");
            }
        } catch (err) {
            clearPendingImmediate(commandId);
            showToast("데몬 재기동 신호 송신 실패", "error");
        }
    }



    async function runCleanup() {
        if (isActionPending) return;

        const dateInput = document.getElementById('input-cleanup-date');
        const limitInput = document.getElementById('input-cleanup-limit');

        if (!dateInput || !dateInput.value) {
            showToast("정리 기준 날짜를 선택해주십시오.", "warning");
            return;
        }

        const dateStr = dateInput.value;
        const limitVal = parseInt(limitInput?.value || '20000', 10);

        if (!confirm(`[경고] 수동 즉시 삭제 경고\n\n기준 날짜: ${dateStr} 이전\n삭제 한도: 최대 ${formatNumber(limitVal)} 행\n\n이 조건에 부합하는 모든 과거 시장 데이터를 영구적으로 삭제합니다.\n정말로 실행하시겠습니까?`)) {
            return;
        }

        const commandId = generateCommandId();
        try {
            registerPendingCommand(commandId, 'cleanup_run_once', TIMEOUTS.RUN);
            const res = await APIClient.runCleanup(dateStr, limitVal, commandId);
            if (res && res.status === 'pending') {
                showToast("수동 즉시 삭제 정리를 요청했습니다. 최대 30초 대기합니다.", "info");
            } else {
                throw new Error("Invalid response status");
            }
        } catch (err) {
            clearPendingImmediate(commandId);
            showToast("수동 즉시 삭제 요청 실패", "error");
        }
    }

    /**
     * API 호출 에러 등으로 펜딩 상태를 긴급 해제해야 할 때 사용
     */
    function clearPendingImmediate(commandId) {
        const cmd = pendingCommands.get(commandId);
        if (cmd) {
            clearTimeout(cmd.timeoutId);
            pendingCommands.delete(commandId);
        }
        isActionPending = false;
        loadCleanupStatus().then(() => {
            updateButtonLockState(false);
        });
    }

    // --- 웹소켓 메시지 수신 분기 지점 ---

    /**
     * ZMQ `market_cleanup_status` 하트비트/주기적 수신 시 라우팅
     */
    function handleStatusUpdate(tick) {
        renderStatus(tick);
    }

    /**
     * ZMQ `cleanup_command_result` 커맨드 ACK 결과 수신 시 라우팅
     */
    function handleCommandResult(tick) {
        const commandId = tick.command_id;
        const cmd = pendingCommands.get(commandId);
        if (!cmd) return;

        // 1. 타임아웃 해제 및 삭제
        clearTimeout(cmd.timeoutId);
        pendingCommands.delete(commandId);
        isActionPending = false;

        // 2. 성공 여부 메시지 판단
        if (tick.success) {
            // 명령 타입별 분기 처리
            if (cmd.type === 'cleanup_run_once') {
                showToast(tick.message || "성공적으로 삭제 처리를 완료했습니다.", "success");
                
                loadCleanupStatus();
                loadEvents();
                // 삭제 완료 후 피커 날짜 기준 틱 수량 조회 재실행
                previewCleanup();
            } else if (cmd.type === 'cleanup_preview') {
                const previewText = document.getElementById('cleanup-manual-preview-text');
                if (previewText && tick.data) {
                    previewText.innerText = `삭제 대상 틱 수: ${formatNumber(tick.data.trades_count)} 건`;
                    previewText.style.color = "#FF4B4B"; // Red
                }
            } else {
                showToast(tick.message || "요청이 정상 완료되었습니다.", "success");
                loadCleanupStatus();
                loadEvents();
            }
        } else {
            // 실패 결과 처리
            const errorMsg = tick.error || "원격 명령 실행에 실패했습니다.";
            if (cmd.type === 'cleanup_preview') {
                const previewText = document.getElementById('cleanup-manual-preview-text');
                if (previewText) {
                    previewText.innerText = "삭제 대상 틱 수: 조회 실패";
                    previewText.style.color = "#FF4B4B";
                }
            } else {
                showToast(`실패: ${errorMsg}`, "error");
            }
        }

        // 3. 버튼 락 해제
        loadCleanupStatus().then(() => {
            updateButtonLockState(false);
        });
    }

    /**
     * 뷰 퇴장 시 타이머 정리
     */
    function destroy() {
        if (staleCheckInterval) {
            clearInterval(staleCheckInterval);
            staleCheckInterval = null;
        }
    }

    return {
        init,
        destroy,
        loadEvents,
        restartCleanupDaemon,
        handleStatusUpdate,
        handleCommandResult
    };
})();

// 전역 노출 설정
window.CleanupView = CleanupView;
