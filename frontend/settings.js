/**
 * 설정 화면 제어 및 수집기 상태 관리 모듈
 */
let lastSeenErrors = {};
let lastSeenSuspended = { upbit: false, bithumb: false, kis: false };
const exchangeState = {
    upbit: { isRunning: false },
    bithumb: { isRunning: false },
    kis: { isRunning: false }
};

/**
 * API를 통해 현재 데이터 수집기들의 실시간 구동 상태를 조회하고 Store를 업데이트합니다.
 */
async function updateCollectorStatus() {
    try {
        const data = await APIClient.fetchCollectorStatus();
        Store.set('collectorStatuses', data);
    } catch (e) {
        console.error("Status check failed", e);
        const emergencyBanner = document.getElementById('global-emergency-banner');
        if (emergencyBanner) {
            emergencyBanner.style.display = 'flex';
            emergencyBanner.querySelector('.warning-text').innerText = '[비상 경고] 백엔드 거래 서버와의 실시간 API 통신이 차단되었습니다! 네트워크 연결을 확인하십시오.';
        }
    }
}

/**
 * 수집기들의 최신 상태( statuses )에 맞춰 설정 화면 및 사이드바 인디케이터를 렌더링합니다.
 */
function renderCollectorStatuses(statuses) {
    if (!statuses) return;

    let hasEmergency = false;

    // 1. 거래소 수집기 상태 렌더링
    const exchanges = ['upbit', 'bithumb', 'kis'];
    exchanges.forEach(exch => {
        const status = statuses[exch] || { is_running: false, error: null, status: 'STOPPED', status_reason: null };
        const isRunning = status.is_running;
        const error = status.error;

        // 사이드바 콤팩트 표시등 및 툴팁 업데이트
        const sidebarStatusEl = document.getElementById(`sidebar-${exch}-status`);
        const cardEl = sidebarStatusEl ? sidebarStatusEl.closest('.compact-status-card') : null;
        if (sidebarStatusEl) {
            if (status.status === 'SUSPENDED') {
                sidebarStatusEl.style.color = '#F59E0B'; // SUSPENDED: Amber 주황
            } else if (isRunning && !error) {
                sidebarStatusEl.style.color = '#4caf50'; // RUNNING: 초록
            } else if (error) {
                sidebarStatusEl.style.color = '#FF4B4B'; // ERROR: 빨강
            } else {
                sidebarStatusEl.style.color = '#64748B'; // STOPPED: Slate 회색
            }
        }
        if (cardEl) {
            let statusStr = isRunning ? (error ? 'ERROR' : 'RUNNING') : 'STOPPED';
            if (status.status === 'SUSPENDED') statusStr = 'SUSPENDED';
            const reasonStr = status.status_reason ? ` (${status.status_reason})` : (error ? ` (${error})` : '');
            cardEl.title = `${exch.toUpperCase()} Collector: ${statusStr}${reasonStr}`;
        }

        // 설정 화면의 거래소 수집기 위젯 상태 업데이트
        const statusEl = document.getElementById(`${exch}-status`);
        const btnEl = document.getElementById(`btn-toggle-${exch}`);
        const errorEl = document.getElementById(`${exch}-error-msg`);

        if (statusEl && btnEl) {
            exchangeState[exch].isRunning = isRunning;
            btnEl.disabled = false;

            if (status.status === 'SUSPENDED') {
                statusEl.innerText = 'SUSPENDED';
                statusEl.className = 'status-badge status-suspended';
                btnEl.innerText = '⏹️ 중단';
                btnEl.className = 'btn sm danger';
            } else if (isRunning && !error) {
                statusEl.innerText = 'RUNNING';
                statusEl.className = 'status-badge status-on';
                btnEl.innerText = '⏹️ 중단';
                btnEl.className = 'btn sm danger';
            } else {
                statusEl.innerText = error ? 'ERROR' : 'STOPPED';
                statusEl.className = error ? 'status-badge status-warn' : 'status-badge status-off';
                btnEl.innerText = '▶️ 시작';
                btnEl.className = 'btn sm primary';
            }
        }

        if (errorEl) {
            if (status.status === 'SUSPENDED' && status.status_reason) {
                errorEl.innerText = `⚠️ 정지 사유: ${status.status_reason}`;
                errorEl.style.color = '#F59E0B';
                errorEl.style.display = 'block';
                
                // SUSPENDED 상태로 최초 진입 시 토스트 알림 고지 (중복 노출 방지)
                if (!lastSeenSuspended[exch]) {
                    const reason = status.status_reason ? `: ${status.status_reason}` : '';
                    if (typeof showToast === 'function') {
                        showToast(`⚠️ [${exch.toUpperCase()}] 변동성 완화장치(VI) 등으로 인해 수집이 일시 정지(SUSPENDED)되었습니다${reason}`, 'warning');
                    } else {
                        showAlert({ msg: `⚠️ [${exch.toUpperCase()}] 수집 일시 정지${reason}`, alert_type: 'warning' });
                    }
                    lastSeenSuspended[exch] = true;
                }
            } else if (error) {
                errorEl.innerText = error;
                errorEl.style.color = '#FF4B4B';
                errorEl.style.display = 'block';
                if (lastSeenErrors[exch] !== error) {
                    showAlert({ msg: `⚠️ ${exch.toUpperCase()} 에러: ${error}`, alert_type: 'error' });
                    lastSeenErrors[exch] = error;
                }
                lastSeenSuspended[exch] = false;
            } else {
                errorEl.style.display = 'none';
                lastSeenErrors[exch] = null;
                lastSeenSuspended[exch] = false;
            }
        }

        // SUSPENDED 상태는 시장 메커니즘 정지(VI 등)이므로 비상 경고 배너 대상에서 제외
        if (status.status !== 'SUSPENDED' && (!isRunning || error)) {
            hasEmergency = true;
        }
    });

    // 2. 전략 엔진 상태 렌더링
    const strategy = statuses.strategy || { is_running: false, active_engines: 0, error: null };
    const stratRunning = strategy.is_running;
    const activeEngines = strategy.active_engines || 0;
    const stratError = strategy.error;

    const sidebarStratEl = document.getElementById('sidebar-strategy-status');
    const stratCardEl = sidebarStratEl ? sidebarStratEl.closest('.compact-status-card') : null;
    if (sidebarStratEl) {
        if (stratRunning && !stratError) {
            sidebarStratEl.style.color = '#4caf50'; // RUNNING: 초록
        } else if (stratError) {
            sidebarStratEl.style.color = '#FF4B4B'; // ERROR: 빨강
        } else {
            sidebarStratEl.style.color = '#64748B'; // STOPPED: Slate 회색
        }
    }
    if (stratCardEl) {
        const stratStatusStr = stratRunning ? (stratError ? 'ERROR' : `RUNNING (${activeEngines} 종목)`) : 'STOPPED';
        stratCardEl.title = `Strategy Engine: ${stratStatusStr}${stratError ? ` (${stratError})` : ''}`;
    }

    // 3. 글로벌 비상 경고 배너 업데이트
    const emergencyBanner = document.getElementById('global-emergency-banner');
    if (emergencyBanner) {
        if (hasEmergency) {
            emergencyBanner.style.display = 'flex';
            emergencyBanner.querySelector('.warning-text').innerText = '[비상 경고] 일부 데이터 수집기가 중단되었거나 에러 상태입니다! 상시 시세 모니터링 수급이 어렵습니다.';
        } else {
            emergencyBanner.style.display = 'none';
        }
    }
}

/**
 * 설정 화면 수집기 온/오프 단추 이벤트 바인딩 및 2초 간격 갱신 주기를 활성화합니다.
 */
function initCollectorControls() {
    ['upbit', 'bithumb', 'kis'].forEach(exch => {
        const btn = document.getElementById(`btn-toggle-${exch}`);
        if (btn) {
            btn.addEventListener('click', async () => {
                btn.disabled = true;
                const action = exchangeState[exch].isRunning ? 'stop' : 'start';
                try {
                    await APIClient.controlCollector(exch, action);
                    showAlert({ msg: `Collector ${exch} ${action}ed` });
                    if (action === 'start') {
                        const statusEl = document.querySelector(`#${exch}-status`);
                        const errorEl = document.querySelector(`#${exch}-error-msg`);
                        if (statusEl) {
                            statusEl.innerText = 'STARTING...';
                            statusEl.className = 'status-badge status-on';
                        }
                        if (errorEl) {
                            errorEl.style.display = 'none';
                        }
                    }
                    updateCollectorStatus();
                } catch (e) {
                    showAlert({ msg: `${exch} 제어 실패`, alert_type: 'error' });
                    btn.disabled = false;
                }
            });
        }
    });

    // [데몬 자가 재기동 제어]
    const btnRestartCollector = document.getElementById('btn-restart-collector-daemon');
    if (btnRestartCollector) {
        btnRestartCollector.addEventListener('click', async () => {
            if (!confirm("⚠️ 정말로 실시간 데이터 수집기(Collector Daemon) 프로세스 자체를 재시작하시겠습니까?\n\n이 작업은 실행 중인 큐의 안전 플러시를 수행한 뒤 프로세스를 리로드합니다. 약 1~2초간 데이터 수집이 끊어질 수 있습니다.")) {
                return;
            }
            btnRestartCollector.disabled = true;
            try {
                await APIClient.restartCollectorDaemon();
                showAlert({ msg: "🚀 수집 데몬 프로세스 재시작 신호가 전송되었습니다.", alert_type: 'success' });
            } catch (e) {
                showAlert({ msg: `❌ 수집 데몬 재시작 실패: ${e.message}`, alert_type: 'error' });
            } finally {
                setTimeout(() => { btnRestartCollector.disabled = false; }, 3000);
            }
        });
    }

    const btnRestartStrategy = document.getElementById('btn-restart-strategy-daemon');
    if (btnRestartStrategy) {
        btnRestartStrategy.addEventListener('click', async () => {
            if (!confirm("⚠️ 정말로 실시간 전략 엔진(Strategy Daemon) 프로세스 자체를 재시작하시겠습니까?\n\n이 작업은 실시간 모의투자를 안전히 정지한 후 프로세스를 리로드합니다. 기동 시 모의투자 상태 및 설정에 따라 재구동됩니다.")) {
                return;
            }
            btnRestartStrategy.disabled = true;
            try {
                await APIClient.restartStrategyDaemon();
                showAlert({ msg: "🚀 전략 데몬 프로세스 재시작 신호가 전송되었습니다.", alert_type: 'success' });
            } catch (e) {
                showAlert({ msg: `❌ 전략 데몬 재시작 실패: ${e.message}`, alert_type: 'error' });
            } finally {
                setTimeout(() => { btnRestartStrategy.disabled = false; }, 3000);
            }
        });
    }

    // 최초 1회 즉시 호출
    if (ViewRouter.getActiveView() === 'settings-view') {
        updateSystemEvents();
    }

    setInterval(() => {
        if (ViewRouter.getActiveView() === 'settings-view') {
            updateCollectorStatus();
            updateSystemEvents();
        }
    }, 2000);
}

// --- 데이터베이스 관리 로직 ---
const btnCleanup = document.getElementById('btn-cleanup');
const cleanupDateInput = document.getElementById('cleanup-date');
const previewPanel = document.getElementById('cleanup-preview-panel');
const previewTrades = document.getElementById('cleanup-preview-trades');
const previewCandles = document.getElementById('cleanup-preview-candles');
const previewTotal = document.getElementById('cleanup-preview-total');

/**
 * 선택한 소거 날짜 이전의 DB 테이블 용량 예측 통계를 조회합니다.
 */
async function updateCleanupPreview() {
    const btnCleanup = document.getElementById('btn-cleanup');
    const cleanupDateInput = document.getElementById('cleanup-date');
    const previewPanel = document.getElementById('cleanup-preview-panel');
    const previewTrades = document.getElementById('cleanup-preview-trades');
    const previewCandles = document.getElementById('cleanup-preview-candles');
    const previewTotal = document.getElementById('cleanup-preview-total');

    if (!cleanupDateInput || !previewPanel) return;
    const selectedDate = cleanupDateInput.value;
    if (!selectedDate) {
        previewPanel.style.display = 'none';
        return;
    }

    try {
        const data = await APIClient.fetchCleanupPreview(selectedDate);
        if (previewTrades) previewTrades.innerText = `${data.trades_count.toLocaleString()}건`;
        if (previewCandles) previewCandles.innerText = `${data.candles_count.toLocaleString()}건`;
        if (previewTotal) previewTotal.innerText = `${data.total_count.toLocaleString()}건`;
        previewPanel.style.display = 'block';
        
        // 삭제 실행을 위한 임시 속성 보관
        if (btnCleanup) {
            btnCleanup.dataset.trades = data.trades_count;
            btnCleanup.dataset.candles = data.candles_count;
            btnCleanup.dataset.total = data.total_count;
        }
    } catch (e) {
        console.error("Cleanup preview check failed", e);
    }
}

/**
 * DB 데이터 삭제 관련 단추 이벤트 리스너를 설정합니다.
 */
function initDatabaseControls() {
    const btnCleanup = document.getElementById('btn-cleanup');
    const cleanupDateInput = document.getElementById('cleanup-date');
    const progressPanel = document.getElementById('cleanup-progress-panel');
    const progressStatus = document.getElementById('cleanup-progress-status');
    const progressPercent = document.getElementById('cleanup-progress-percent');
    const progressBar = document.getElementById('cleanup-progress-bar');

    if (cleanupDateInput) {
        cleanupDateInput.addEventListener('change', updateCleanupPreview);
    }

    if (btnCleanup && cleanupDateInput) {
        btnCleanup.addEventListener('click', async () => {
            const selectedDate = cleanupDateInput.value;
            if (!selectedDate) {
                alert("삭제할 기준 날짜를 선택해주세요.");
                return;
            }

            const tradesCount = parseInt(btnCleanup.dataset.trades || "0");
            const candlesCount = parseInt(btnCleanup.dataset.candles || "0");
            const totalCount = parseInt(btnCleanup.dataset.total || "0");

            if (totalCount === 0) {
                alert("삭제할 대상 데이터가 없습니다.");
                return;
            }

            const warnMessage = `⚠️ [데이터베이스 영구 삭제 경고]\n\n` +
                `선택하신 날짜 (${selectedDate}) 이전의 과거 데이터를 데이터베이스에서 영구히 삭제합니다.\n\n` +
                `[삭제 정리 대상]\n` +
                `- 체결 데이터 (Trades): ${tradesCount.toLocaleString()}건\n` +
                `- 캔들 데이터 (Candles): ${candlesCount.toLocaleString()}건\n` +
                `- 총 소거 대상: ${totalCount.toLocaleString()}건\n\n` +
                `이 작업은 데이터베이스를 물리적으로 축소시키며 되돌릴 수 없습니다.\n` +
                `정말로 영구 삭제를 진행하시겠습니까?`;

            if (!confirm(warnMessage)) {
                return;
            }

            // UI 상태 초기화
            btnCleanup.disabled = true;
            btnCleanup.innerText = "삭제 진행 중...";
            if (progressPanel) progressPanel.style.display = 'block';
            if (progressStatus) progressStatus.innerText = "삭제 준비 중...";
            if (progressPercent) progressPercent.innerText = "0%";
            if (progressBar) progressBar.style.width = "0%";

            try {
                let deletedCount = 0;
                const chunkSize = 100000;

                while (deletedCount < totalCount) {
                    // 백엔드 API를 청크 사이즈(100000)만큼 호출
                    const response = await APIClient.runCleanup(selectedDate, chunkSize);
                    const stepDeleted = response.total_deleted || 0;
                    
                    if (stepDeleted === 0) {
                        break; // 더 이상 삭제할 건수가 없으면 정지
                    }

                    deletedCount += stepDeleted;
                    
                    // 진행률(%) 계산 및 UI 반영
                    const percent = Math.min(100, Math.floor((deletedCount / totalCount) * 100));
                    if (progressStatus) progressStatus.innerText = `데이터 삭제 중... (${deletedCount.toLocaleString()} / ${totalCount.toLocaleString()})`;
                    if (progressPercent) progressPercent.innerText = `${percent}%`;
                    if (progressBar) progressBar.style.width = `${percent}%`;

                    // 브라우저 렌더링 양보 및 사용자가 진행률을 시각적으로 인지할 수 있도록 지연시간(20ms) 부여
                    await new Promise(resolve => setTimeout(resolve, 20));
                }

                if (progressStatus) progressStatus.innerText = "삭제 정리 완료!";
                alert(`🧹 정리 완료!\n\n총 ${deletedCount.toLocaleString()}건의 데이터가 안전하게 삭제 정리되었습니다.`);
                await updateCleanupPreview();
            } catch (e) {
                console.error("Cleanup failed", e);
                alert(`정리 작업 도중 오류가 발생했습니다: ${e.message || e}`);
            } finally {
                btnCleanup.disabled = false;
                btnCleanup.innerText = "선택 날짜 이전 삭제";
                setTimeout(() => {
                    if (progressPanel) progressPanel.style.display = 'none';
                }, 3000);
            }
        });
    }
}

// 전역 window 바인딩
window.updateCollectorStatus = updateCollectorStatus;
window.renderCollectorStatuses = renderCollectorStatuses;
window.initCollectorControls = initCollectorControls;
window.updateCleanupPreview = updateCleanupPreview;
window.initDatabaseControls = initDatabaseControls;

/**
 * 거래소 종목 동기화 관련 단추 이벤트 리스너를 설정합니다.
 */
function initAssetSyncControls() {
    const btnSync = document.getElementById('btn-sync-assets');
    const resultPanel = document.getElementById('sync-result-panel');
    const resultText = document.getElementById('sync-result-text');

    if (btnSync) {
        btnSync.addEventListener('click', async () => {
            const confirmMsg = "🌐 거래소 API를 호출하여 종목 정보를 DB와 실시간 동기화하시겠습니까?\n\n" +
                "이 작업은 각 거래소(업비트/빗썸/KIS)의 최신 종목 목록을 다운로드하여:\n" +
                "- 신규 상장 종목을 마스터에 등록합니다.\n" +
                "- 상장폐지된 종목은 수집 비활성화(is_active=0) 및 사용안함(is_delisted=1) 처리합니다.";
            
            if (!confirm(confirmMsg)) {
                return;
            }

            btnSync.disabled = true;
            btnSync.innerText = "🔄 동기화 진행 중...";
            if (resultPanel) resultPanel.style.display = 'none';

            try {
                const response = await fetch('/market/sync-assets', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' }
                });

                const data = await response.json();
                if (response.ok && data.success) {
                    const results = data.results || {};
                    let htmlContent = `<span style="color: #4caf50; font-weight: bold; font-size: 0.95rem;">✔️ 동기화 성공</span><br><br>`;
                    
                    // 1. 거래소별 활성 종목 수 요약
                    htmlContent += `
                        <div style="background: rgba(15, 23, 42, 0.25); padding: 12px; border-radius: 6px; border: 1px solid rgba(148,163,184,0.1); margin-bottom: 15px; font-size: 0.85rem; line-height: 1.6;">
                            <strong>📌 거래소별 현재 활성 종목 현황:</strong><br>
                            - <strong style="color: #FF4B4B;">업비트 (Upbit):</strong> ${results.upbit?.total_active || 0}개 수집 중 / 전체 ${results.upbit?.total_registered || 0}개<br>
                            - <strong style="color: #F59E0B;">빗썸 (Bithumb):</strong> ${results.bithumb?.total_active || 0}개 수집 중 / 전체 ${results.bithumb?.total_registered || 0}개<br>
                            - <strong style="color: #0072FF;">국내주식 (KIS):</strong> ${results.kis?.total_active || 0}개 수집 중 / 전체 ${results.kis?.total_registered || 0}개
                        </div>
                    `;

                    // 2. 추가/삭제된 상세 리스트 추출
                    let details = [];
                    const exchanges = { upbit: "업비트", bithumb: "빗썸", kis: "국내주식" };
                    
                    for (const [exchKey, exchName] of Object.entries(exchanges)) {
                        const exchRes = results[exchKey] || { added: [], delisted: [] };
                        
                        // 추가된 종목
                        if (exchRes.added && exchRes.added.length > 0) {
                            details.push(`
                                <div style="margin-bottom: 8px;">
                                    <span style="color: #4caf50; font-weight: bold; font-size: 0.82rem;">[${exchName} 신규 추가 종목]</span><br>
                                    <span style="font-size: 0.8rem; color: #94A3B8; font-family: 'Pretendard', sans-serif;">${exchRes.added.join(', ')}</span>
                                </div>
                            `);
                        }
                        // 상폐/유실된 종목
                        if (exchRes.delisted && exchRes.delisted.length > 0) {
                            details.push(`
                                <div style="margin-bottom: 8px;">
                                    <span style="color: #FF4B4B; font-weight: bold; font-size: 0.82rem;">[${exchName} 상장폐지/비활성 종목]</span><br>
                                    <span style="font-size: 0.8rem; color: #64748B; font-family: 'Pretendard', sans-serif;">${exchRes.delisted.join(', ')}</span>
                                </div>
                            `);
                        }
                    }

                    if (details.length > 0) {
                        htmlContent += `
                            <div style="margin-top: 10px; border-top: 1px dashed rgba(148,163,184,0.15); padding-top: 10px;">
                                <strong style="color: #F8FAFC; font-size: 0.88rem; display: inline-block; margin-bottom: 6px;">🔍 신규 상장 / 상장 폐지 내역:</strong>
                                ${details.join('')}
                            </div>
                        `;
                    } else {
                        htmlContent += `
                            <div style="margin-top: 10px; color: #94A3B8; font-size: 0.82rem; border-top: 1px dashed rgba(148,163,184,0.15); padding-top: 10px;">
                                📢 이번 동기화에서 새로 추가되거나 삭제된 종목이 없습니다 (이미 최신 정보입니다).
                            </div>
                        `;
                    }

                    htmlContent += `
                        <span style="color: #64748B; font-size: 11px; display: inline-block; margin-top: 15px; border-top: 1px solid rgba(148,163,184,0.1); padding-top: 8px; width: 100%;">
                            * 동기화된 신규 종목은 수집기 데몬 기동 시에 자동 반영됩니다.<br>
                            * 변경된 정보(KIS 등)는 데몬에 실시간 신호(ZMQ)로 전달되었습니다.
                        </span>
                    `;

                    if (resultText) {
                        resultText.innerHTML = htmlContent;
                    }
                    if (resultPanel) resultPanel.style.display = 'block';
                    showAlert({ msg: "🌐 종목 동기화가 완료되었습니다." });
                } else {
                    throw new Error(data.detail || "서버 응답 오류");
                }
            } catch (e) {
                console.error("Asset sync failed", e);
                if (resultText) {
                    resultText.innerHTML = `
                        <span style="color: #FF4B4B; font-weight: bold; font-size: 0.95rem;">❌ 동기화 실패</span><br><br>
                        <span style="color: #EF4444;">오류 상세: ${e.message || '네트워크 통신 중 에러가 발생했습니다.'}</span>
                    `;
                }
                if (resultPanel) resultPanel.style.display = 'block';
                showAlert({ msg: "❌ 종목 동기화에 실패했습니다.", alert_type: 'error' });
            } finally {
                btnSync.disabled = false;
                btnSync.innerText = "🔄 관리종목 변경 확인 및 동기화";
            }
        });
    }
}

window.initAssetSyncControls = initAssetSyncControls;

/**
 * 최근 시스템 운영 이력을 조회하여 테이블에 렌더링합니다.
 */
async function updateSystemEvents() {
    const tbody = document.getElementById('system-events-tbody');
    if (!tbody) return;

    try {
        const events = await APIClient.fetchSystemEvents(20);
        if (!events || events.length === 0) {
            tbody.innerHTML = '<tr><td colspan="4" style="text-align: center; color: #64748B; padding: 20px;">기록된 시스템 이력이 없습니다.</td></tr>';
            return;
        }

        tbody.innerHTML = events.map(event => {
            const timeStr = formatTimestamp(event.timestamp);
            let badgeClass = 'status-neutral';
            let customStyle = '';

            switch (event.event_type) {
                case 'DAEMON_START':
                case 'COLLECTOR_START':
                case 'EXCHANGE_RESUMED':
                    badgeClass = 'status-on';
                    break;
                case 'DAEMON_STOP':
                case 'COLLECTOR_STOP':
                    badgeClass = 'status-neutral';
                    break;
                case 'EXCHANGE_SUSPENDED':
                    badgeClass = 'status-suspended';
                    break;
                case 'EXCHANGE_ERROR':
                    badgeClass = 'status-warn';
                    break;
                case 'STRATEGY_SESSION_LOAD':
                    badgeClass = 'status-info';
                    break;
            }

            const targetText = event.target ? event.target.toUpperCase() : '-';

            return `
                <tr style="border-bottom: 1px solid rgba(148, 163, 184, 0.08);">
                    <td style="padding: 10px 8px; font-family: 'Roboto Mono', monospace; font-size: 0.8rem; color: #94A3B8;">${timeStr}</td>
                    <td style="padding: 10px 8px;">
                        <span class="status-badge ${badgeClass}" style="font-size: 0.75rem; padding: 2px 6px; ${customStyle}">${event.event_type}</span>
                    </td>
                    <td style="padding: 10px 8px; font-weight: bold; color: #F8FAFC; font-size: 0.82rem;">${targetText}</td>
                    <td style="padding: 10px 8px; color: #E2E8F0; font-size: 0.85rem;">${event.message || '-'}</td>
                </tr>
            `;
        }).join('');
    } catch (e) {
        console.error("Failed to update system events", e);
        tbody.innerHTML = '<tr><td colspan="4" style="text-align: center; color: #FF4B4B; padding: 20px;">이력을 불러오지 못했습니다.</td></tr>';
    }
}

window.updateSystemEvents = updateSystemEvents;

if (typeof ViewRouter !== 'undefined') {
    ViewRouter.registerRoute('settings-view', () => {
        updateCollectorStatus();
    });
}

document.addEventListener('DOMContentLoaded', () => {
    if (typeof initCollectorControls === 'function') initCollectorControls();
    if (typeof initDatabaseControls === 'function') initDatabaseControls();
    if (typeof initAssetSyncControls === 'function') initAssetSyncControls();
});



