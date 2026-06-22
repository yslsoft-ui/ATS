/**
 * DaemonMonitoringView - 통합 데몬 상태 모니터링 조율 컨트롤러
 */
const DaemonMonitoringView = (() => {
    let activeTab = 'collector'; // 'collector', 'strategy', 'evaluation', 'cleanup'
    const daemonDataCache = {
        collector: null,
        strategy: null,
        evaluation: null,
        cleanup: null
    };

    /**
     * 뷰 초기화
     */
    function init() {
        console.log("[DaemonMonitoringView] Initializing integrated view...");
        
        // 1. 탭 버튼 이벤트 바인딩
        const tabs = document.querySelectorAll('.daemon-tab');
        tabs.forEach(tabBtn => {
            // 기존 이벤트 핸들러 중복 등록 방지를 위한 복제 교체
            tabBtn.replaceWith(tabBtn.cloneNode(true));
        });

        document.querySelectorAll('.daemon-tab').forEach(tabBtn => {
            tabBtn.addEventListener('click', () => {
                const targetTab = tabBtn.getAttribute('data-tab');
                if (targetTab) {
                    switchTab(targetTab);
                }
            });
        });

        // 2. 상단 공통 데몬 재기동 버튼 바인딩
        const restartBtn = document.getElementById('btn-daemon-restart');
        if (restartBtn) {
            const newRestartBtn = restartBtn.cloneNode(true);
            restartBtn.replaceWith(newRestartBtn);
            newRestartBtn.addEventListener('click', triggerActiveDaemonRestart);
        }

        // 3. 기본 활성 탭 전환 및 가동
        switchTab(activeTab);
    }

    /**
     * 현재 활성화된 탭 리턴
     * @returns {string}
     */
    function getActiveTab() {
        return activeTab;
    }

    /**
     * 탭 전환 처리
     */
    function switchTab(tabName) {
        console.log(`[DaemonMonitoringView] Switching tab to: ${tabName}`);
        
        // 1. 이전 활성 탭 리소스 파괴
        deactivateCurrentTab();

        activeTab = tabName;

        // 2. 탭 UI 클래스 토글
        document.querySelectorAll('.daemon-tab').forEach(btn => {
            if (btn.getAttribute('data-tab') === tabName) {
                btn.classList.add('active');
            } else {
                btn.classList.remove('active');
            }
        });

        // 3. 서브 패널 컨테이너 display 토글
        const panelIds = {
            collector: 'daemon-panel-collector',
            strategy: 'daemon-panel-strategy',
            evaluation: 'daemon-panel-evaluation',
            cleanup: 'daemon-panel-cleanup'
        };

        Object.entries(panelIds).forEach(([name, id]) => {
            const panel = document.getElementById(id);
            if (panel) {
                panel.style.display = (name === tabName) ? 'block' : 'none';
            }
        });

        // 4. 상단 공통 영역에 해당 캐시값 복원 렌더링
        renderSharedHeaderFromCache(tabName);

        // 5. 새로 선택된 탭 뷰 초기화 구동
        initActiveTab(tabName);
    }

    /**
     * 이전 활성 탭 모듈 파괴 호출
     */
    function deactivateCurrentTab() {
        if (activeTab === 'collector' && typeof CollectorView !== 'undefined' && typeof CollectorView.destroy === 'function') {
            CollectorView.destroy();
        } else if (activeTab === 'strategy' && typeof StrategyDaemonView !== 'undefined' && typeof StrategyDaemonView.destroy === 'function') {
            StrategyDaemonView.destroy();
        } else if (activeTab === 'evaluation' && typeof EvaluationDaemonView !== 'undefined' && typeof EvaluationDaemonView.destroy === 'function') {
            EvaluationDaemonView.destroy();
        } else if (activeTab === 'cleanup' && typeof CleanupView !== 'undefined' && typeof CleanupView.destroy === 'function') {
            CleanupView.destroy();
        }
    }

    /**
     * 활성 탭 모듈 초기화 호출
     */
    function initActiveTab(tabName) {
        if (tabName === 'collector' && typeof CollectorView !== 'undefined') {
            CollectorView.init();
        } else if (tabName === 'strategy' && typeof StrategyDaemonView !== 'undefined') {
            StrategyDaemonView.init();
        } else if (tabName === 'evaluation' && typeof EvaluationDaemonView !== 'undefined') {
            EvaluationDaemonView.init();
        } else if (tabName === 'cleanup' && typeof CleanupView !== 'undefined') {
            CleanupView.init();
        }
    }

    /**
     * 캐시 데이터를 상단 공통 UI에 동기화 적용
     */
    function renderSharedHeaderFromCache(tabName) {
        const cached = daemonDataCache[tabName];
        
        const pidEl = document.getElementById('daemon-pid');
        const startedEl = document.getElementById('daemon-started-at');
        const heartbeatEl = document.getElementById('daemon-last-heartbeat');
        const memoryEl = document.getElementById('daemon-memory');
        const cpuEl = document.getElementById('daemon-cpu');
        const staleBadge = document.getElementById('daemon-stale-badge');
        const stateBadge = document.getElementById('daemon-state-badge');

        if (!cached) {
            // 캐시 데이터가 없을 때의 초기화 상태
            if (pidEl) pidEl.innerText = '-';
            if (startedEl) startedEl.innerText = '-';
            if (heartbeatEl) heartbeatEl.innerText = '-';
            if (memoryEl) memoryEl.innerText = '- MB';
            if (cpuEl) cpuEl.style.display = 'none';
            if (staleBadge) staleBadge.style.display = 'none';
            if (stateBadge) stateBadge.style.display = 'none';
            return;
        }

        // 1. PID
        if (pidEl) pidEl.innerText = cached.pid || '-';

        // 2. 기동 시각
        if (startedEl) {
            startedEl.innerText = cached.startedAtFormatted || '-';
        }

        // 3. 상태 갱신
        if (heartbeatEl) {
            heartbeatEl.innerText = cached.heartbeatFormatted || '-';
        }

        // 4. 메모리 RSS
        if (memoryEl) {
            memoryEl.innerText = cached.rssMb !== undefined && cached.rssMb !== null && cached.rssMb > 0
                ? `${cached.rssMb.toFixed(2)} MB`
                : '- MB';
        }

        // 5. CPU 사용량
        if (cpuEl) {
            if (cached.cpuUsagePct !== undefined && cached.cpuUsagePct !== null) {
                cpuEl.innerText = `CPU: ${cached.cpuUsagePct.toFixed(1)}%`;
                cpuEl.style.display = 'inline-block';
            } else {
                cpuEl.style.display = 'none';
            }
        }

        // 6. 연결 지연 뱃지
        if (staleBadge) {
            staleBadge.style.display = cached.isStale ? 'inline-block' : 'none';
            if (cached.isStale) {
                staleBadge.innerText = cached.staleReason || "연결 끊김";
            }
        }

        // 7. 데몬 상태 뱃지 (ACTIVE, PAUSED 등)
        if (stateBadge) {
            if (cached.state) {
                stateBadge.innerText = cached.state;
                stateBadge.style.display = 'inline-block';
                
                // 상태별 Harmonious Color 클래스 스타일 지정
                stateBadge.style.borderRadius = '6px';
                stateBadge.style.padding = '4px 10px';
                stateBadge.style.fontWeight = 'bold';
                
                const s = cached.state.toUpperCase();
                if (s === 'ACTIVE' || s === 'RUNNING') {
                    stateBadge.style.background = 'rgba(16, 185, 129, 0.2)';
                    stateBadge.style.color = '#10B981';
                } else if (s === 'PAUSED' || s === 'STOPPED') {
                    stateBadge.style.background = 'rgba(100, 116, 139, 0.2)';
                    stateBadge.style.color = '#94A3B8';
                } else if (s === 'RUNNING_ONCE') {
                    stateBadge.style.background = 'rgba(0, 114, 255, 0.2)';
                    stateBadge.style.color = '#38BDF8';
                } else if (s === 'ERROR') {
                    stateBadge.style.background = 'rgba(255, 75, 75, 0.2)';
                    stateBadge.style.color = '#FF4B4B';
                } else {
                    stateBadge.style.background = 'rgba(30, 41, 59, 0.5)';
                    stateBadge.style.color = '#F8FAFC';
                }
            } else {
                stateBadge.style.display = 'none';
            }
        }
    }

    /**
     * 개별 데몬 모듈에서 공통 상태 변경 시 호출하여 값을 업데이트하고 즉시 반영합니다.
     */
    function updateSharedHeader(daemonType, data) {
        daemonDataCache[daemonType] = {
            pid: data.pid,
            startedAtFormatted: data.startedAtFormatted,
            heartbeatFormatted: data.heartbeatFormatted,
            rssMb: data.rssMb,
            cpuUsagePct: data.cpuUsagePct,
            isStale: data.isStale,
            staleReason: data.staleReason,
            state: data.state
        };

        // 업데이트 유입된 데몬이 현재 활성화된 탭일 때만 렌더링 동기화
        if (daemonType === activeTab) {
            renderSharedHeaderFromCache(activeTab);
        }
    }

    /**
     * 현재 활성화된 데몬에 해당하는 재기동 프로세스 호출
     */
    async function triggerActiveDaemonRestart() {
        const restartBtn = document.getElementById('btn-daemon-restart');
        const backupText = restartBtn ? restartBtn.innerText : '';
        
        try {
            if (activeTab === 'collector' && typeof CollectorView !== 'undefined' && typeof CollectorView.restartCollectorDaemon === 'function') {
                await CollectorView.restartCollectorDaemon();
            } else if (activeTab === 'strategy' && typeof StrategyDaemonView !== 'undefined' && typeof StrategyDaemonView.restartStrategyDaemon === 'function') {
                await StrategyDaemonView.restartStrategyDaemon();
            } else if (activeTab === 'evaluation' && typeof EvaluationDaemonView !== 'undefined' && typeof EvaluationDaemonView.restartEvaluationDaemon === 'function') {
                await EvaluationDaemonView.restartEvaluationDaemon();
            } else if (activeTab === 'cleanup' && typeof CleanupView !== 'undefined' && typeof CleanupView.restartCleanupDaemon === 'function') {
                await CleanupView.restartCleanupDaemon();
            }
        } catch (error) {
            console.error("[DaemonMonitoringView] Failed to restart daemon:", error);
        }
    }

    /**
     * 뷰 퇴장 시 소멸 처리
     */
    function destroy() {
        deactivateCurrentTab();
    }

    return {
        init,
        destroy,
        getActiveTab,
        updateSharedHeader
    };
})();

// 전역 노출
window.DaemonMonitoringView = DaemonMonitoringView;
