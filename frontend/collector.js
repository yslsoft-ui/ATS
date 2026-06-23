/**
 * CollectorView - 수집기 데몬 모니터링 및 제어 전담 컨트롤러
 */
const CollectorView = (() => {
    // HTML 이스케이프 헬퍼 (Raw JSON 출력 등 특수문자 구조 깨짐 방지용)
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
    
    // 제어 명령 펜딩 맵 (commandId -> { type, exchange, timeoutId, previousPid, previousStartedAt })
    const pendingCommands = new Map();
    
    // 수집 데몬 프로세스 메타데이터 백업 (재기동 확인용)
    let currentPid = null;
    let currentDaemonStartedAt = 0;
    let currentRssMb = 0;
    
    // 로컬 종목 데이터 및 메타데이터 캐시
    let activeSymbols = {};
    let activeSymbolsMetadata = {};
    let daemonSymbolsVersions = {}; // 데몬의 실제 종목 버전 캐시
    let isStaleState = false;
    let lastRawDetailData = null; // 마지막 수신된 상세 데이터 스냅샷 캐시

    // 모니터링 관련 임계값 설정 (백엔드와 동기화되며, 연결 전에는 안전한 기본값으로 fallback)
    let monitoringConfig = {
        daemon_detail_stale_ms: 15000,
        active_symbols_stale_ms: 75000,
        request_symbols_sync_cooldown_ms: 10000,
        control_ack_timeout_ms: 5000
    };

    /**
     * 고유한 command_id 생성 헬퍼
     */
    function generateCommandId() {
        return 'cmd-' + Math.random().toString(36).substr(2, 9) + '-' + Date.now();
    }

    /**
     * 화면 진입 시 초기 데이터 조회 및 주기적 타이머 기동
     */
    async function init() {
        console.log("[CollectorView] Initializing view...");
        
        // 1. 초기 1회 REST API로 상세 정보 및 이벤트 로그 로드
        await loadDaemonDetail();
        await loadEvents();

        // 2. 3초 주기 정밀 Stale/Heartbeat 감시 타이머 기동
        if (staleCheckInterval) {
            clearInterval(staleCheckInterval);
        }
        staleCheckInterval = setInterval(checkStaleStatus, 3000);
        
        // 3. 버튼 이벤트 바인딩 등록
        document.getElementById('btn-collector-start-all')?.addEventListener('click', startAllCollectors);
        document.getElementById('btn-collector-stop-all')?.addEventListener('click', stopAllCollectors);
        document.getElementById('btn-collector-restart-daemon')?.addEventListener('click', restartCollectorDaemon);

        // 툴팁 활성화
        initTooltips();
    }

    /**
     * HTML에 설정된 ❓ 아이콘 마우스 오버 툴팁 동작 초기화
     */
    function initTooltips() {
        // 이벤트 위임 방식을 사용하여 동적으로 생성된 카드 내 ❓ 아이콘도 완벽히 지원합니다.
        document.addEventListener('mouseover', (e) => {
            const icon = e.target.closest('.tooltip-icon');
            if (!icon) return;
            
            const title = icon.getAttribute('title');
            const backup = icon.getAttribute('data-tooltip-backup');
            
            // 이미 툴팁 백업이 적용 중이라면 중복 동작을 방지합니다.
            if (!title && backup) return;
            if (!title) return;
            
            // 브라우저 기본 툴팁 방지 가드: 임시 속성에 백업하고 title 속성을 비웁니다.
            icon.setAttribute('data-tooltip-backup', title);
            icon.removeAttribute('title');
            
            let tooltipDiv = document.getElementById('global-tooltip');
            if (!tooltipDiv) {
                tooltipDiv = document.createElement('div');
                tooltipDiv.id = 'global-tooltip';
                tooltipDiv.style.position = 'absolute';
                tooltipDiv.style.background = '#1E293B';
                tooltipDiv.style.color = '#F8FAFC';
                tooltipDiv.style.border = '1px solid rgba(148, 163, 184, 0.2)';
                tooltipDiv.style.padding = '8px 12px';
                tooltipDiv.style.borderRadius = '4px';
                tooltipDiv.style.fontSize = '0.78rem';
                tooltipDiv.style.zIndex = '1000';
                tooltipDiv.style.maxWidth = '250px';
                tooltipDiv.style.pointerEvents = 'none';
                tooltipDiv.style.boxShadow = '0 4px 12px rgba(0,0,0,0.5)';
                document.body.appendChild(tooltipDiv);
            }
            
            tooltipDiv.innerText = title;
            tooltipDiv.style.display = 'block';
            
            const rect = icon.getBoundingClientRect();
            tooltipDiv.style.left = `${rect.left + window.scrollX}px`;
            tooltipDiv.style.top = `${rect.bottom + 6 + window.scrollY}px`;
        });
        
        document.addEventListener('mouseout', (e) => {
            const icon = e.target.closest('.tooltip-icon');
            if (!icon) return;
            
            // 마우스가 떠날 때 백업해 두었던 title 속성을 복원합니다.
            const backup = icon.getAttribute('data-tooltip-backup');
            if (backup) {
                icon.setAttribute('title', backup);
                icon.removeAttribute('data-tooltip-backup');
            }
            
            const tooltipDiv = document.getElementById('global-tooltip');
            if (tooltipDiv) {
                tooltipDiv.style.display = 'none';
            }
        });
    }

    /**
     * REST API 호출을 통한 전체 상태 갱신
     */
    async function loadDaemonDetail() {
        try {
            const data = await APIClient.fetchCollectorDaemonDetail();
            if (!data) return;

            // 모니터링 임계값 동기화
            if (data.monitoring_config) {
                monitoringConfig = { ...monitoringConfig, ...data.monitoring_config };
            }

            // 로컬 캐시 업데이트
            activeSymbols = data.active_symbols || {};
            activeSymbolsMetadata = data.active_symbols_metadata || {};
            
            // 데몬 메타데이터 백업
            const detail = data.daemon_detail || {};
            currentPid = detail.source_pid || null;
            currentDaemonStartedAt = detail.daemon_started_at || 0;
            daemonSymbolsVersions = detail.symbols_version || {};
            
            // 하트비트 갱신
            const detailSyncedAt = detail.synced_at || 0;
            if (detailSyncedAt > 0) {
                lastDetailHeartbeat = detailSyncedAt;
            }

            // UI 바인딩 실행
            updateUI(data);
        } catch (error) {
            console.error("[CollectorView] Failed to fetch daemon details:", error);
            showToast("수집기 데몬 정보를 가져오는데 실패했습니다.", "error");
        }
    }

    /**
     * 하단 감사 이벤트 로그 로드
     */
    async function loadEvents() {
        const tbody = document.getElementById('collector-events-tbody');
        if (!tbody) return;

        try {
            const events = await APIClient.fetchSystemEvents(20);
            tbody.innerHTML = '';

            if (!events || events.length === 0) {
                tbody.innerHTML = '<tr><td colspan="4" style="text-align: center; color: #64748B; padding: 20px;">기록된 시스템 감사 이벤트가 없습니다.</td></tr>';
                return;
            }

            events.forEach(event => {
                const tr = document.createElement('tr');
                const timeStr = new Date(event.timestamp).toLocaleString();
                
                // 구분별 컬러 지정
                let typeStyle = 'color: #94A3B8; font-weight: bold;';
                if (event.event_type.includes('ERROR') || event.event_type === 'SYSTEM_WARNING') {
                    typeStyle = 'color: #EF4444; font-weight: bold;';
                } else if (event.event_type.includes('START') || event.event_type === 'EXCHANGE_RESUMED') {
                    typeStyle = 'color: #10B981; font-weight: bold;';
                } else if (event.event_type.includes('STOP') || event.event_type === 'EXCHANGE_SUSPENDED') {
                    typeStyle = 'color: #F59E0B; font-weight: bold;';
                }

                // 무효 거래소 유입 등으로 인한 경고등 표시 지원을 위한 스타일 클래스 적용
                if (event.event_type === 'SYSTEM_WARNING') {
                    tr.style.background = 'rgba(239, 68, 68, 0.05)';
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
            console.error("[CollectorView] Failed to load system events:", error);
        }
    }

    /**
     * Stale & Heartbeat 감지 스케줄러 (3초 주기)
     */
    function checkStaleStatus() {
        const now = Date.now();
        
        // 1. 데몬 디테일 하트비트 지연 검증 (설정된 stale_ms 이상 무반응 시 STALE 판정)
        const isDaemonStale = lastDetailHeartbeat === 0 || (now - lastDetailHeartbeat > monitoringConfig.daemon_detail_stale_ms);
        
        if (isDaemonStale) {
            if (!isStaleState) {
                isStaleState = true;
                console.warn("[CollectorView] Collector daemon seems to be offline (Heartbeat stale).");
                // 화면 전체 요소 비시각화 흐름 지원을 위해 투명도나 오프라인 효과 적용
                document.querySelectorAll('.diag-card, .collector-exch-card').forEach(el => {
                    el.style.opacity = '0.5';
                });
                // 즉시 UI 리렌더링 유도 (거래소 뱃지 OFFLINE 전환)
                if (lastRawDetailData) {
                    updateUI(lastRawDetailData);
                }
            }
        } else {
            if (isStaleState) {
                isStaleState = false;
                console.log("[CollectorView] Collector daemon is back online.");
                document.querySelectorAll('.diag-card, .collector-exch-card').forEach(el => {
                    el.style.opacity = '1';
                });
                // 즉시 UI 리렌더링 유도 (거래소 뱃지 정상 복구)
                if (lastRawDetailData) {
                    updateUI(lastRawDetailData);
                }
            }
        }

        // 상단 공통 UI에 동기화
        if (typeof DaemonMonitoringView !== 'undefined') {
            DaemonMonitoringView.updateSharedHeader('collector', {
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

        // 2. 거래소별 종목 동기화 만료 및 버전 불일치 배너 노출 체크 제거 (DB 직접 조회 방식 전환)
        const banner = document.getElementById('collector-sync-warning-banner');
        if (banner) {
            banner.style.display = 'none';
        }
    }

    /**
     * 실시간 수신 데이터 UI 바인딩 핵심 로직
     */
    function updateUI(data) {
        lastRawDetailData = data; // 실시간 상세 데이터 캐시 최신화
        const detail = data.daemon_detail || {};
        const queues = detail.queues || {};
        
        // 1. 헤더 메타데이터 정보 바인딩 - 상단 공통 UI 위임
        if (detail.memory) {
            currentRssMb = detail.memory.rss_mb || 0;
        }

        const isDaemonStale = lastDetailHeartbeat === 0 || (Date.now() - lastDetailHeartbeat > monitoringConfig.daemon_detail_stale_ms);
        if (typeof DaemonMonitoringView !== 'undefined') {
            DaemonMonitoringView.updateSharedHeader('collector', {
                pid: detail.source_pid || null,
                startedAtFormatted: detail.daemon_started_at ? new Date(detail.daemon_started_at).toLocaleString() : '-',
                heartbeatFormatted: detail.synced_at ? new Date(detail.synced_at).toLocaleTimeString() : '-',
                rssMb: currentRssMb,
                cpuUsagePct: null,
                isStale: isDaemonStale,
                staleReason: isDaemonStale ? "연결 끊김" : null,
                state: isDaemonStale ? 'ERROR' : 'ACTIVE'
            });
        }

        // 2. 큐 리소스 상태 카드 갱신 (사용률 및 경고 스타일 클래스 바인딩)
        updateQueueCard('processing', queues.processing);
        updateQueueCard('database', queues.database);
        updateQueueCard('candle', queues.candle);

        // 3. 메모리 및 매퍼 캐시 렌더링
        const memEl = document.getElementById('res-val-memory');
        const mapperEl = document.getElementById('res-val-mapper');
        
        if (memEl && detail.memory) {
            memEl.innerText = `${detail.memory.rss_mb.toFixed(2)} MB`;
        }
        if (mapperEl && detail.memory) {
            mapperEl.innerText = `${detail.memory.stock_mapper_cache_count.toLocaleString()} 개`;
        }

        // 3.5 글로벌 설정 데이터 렌더링
        if (data.collector_config) {
            const cfg = data.collector_config;
            const warmupEl = document.getElementById('cfg-warmup');
            const workersEl = document.getElementById('cfg-workers');
            const dbPathEl = document.getElementById('cfg-db-path');
            const backfillEl = document.getElementById('cfg-backfill');
            const delaysEl = document.getElementById('cfg-delays');

            if (warmupEl) {
                warmupEl.innerText = cfg.warmup_enabled ? "활성화 (True)" : "비활성화 (False)";
                warmupEl.style.color = cfg.warmup_enabled ? "#10B981" : "#EF4444";
            }
            if (workersEl) workersEl.innerText = `${cfg.worker_count || 0} 워커`;
            if (dbPathEl) {
                dbPathEl.innerText = cfg.db_path || '-';
                dbPathEl.setAttribute('title', cfg.db_path || '-');
            }
            
            if (backfillEl && cfg.backfill) {
                const bfEnabled = cfg.backfill.enabled;
                const bfHours = cfg.backfill.max_hours || 0;
                backfillEl.innerText = `${bfEnabled ? '활성화' : '비활성화'} / 최근 ${bfHours}시간`;
                backfillEl.style.color = bfEnabled ? "#10B981" : "#EF4444";
            }
            
            if (delaysEl && cfg.backfill && cfg.backfill.delays) {
                const d = cfg.backfill.delays;
                delaysEl.innerText = `Upbit: ${d.upbit || 0}s | Bithumb: ${d.bithumb || 0}s | KIS: ${d.kis || 0}s`;
            }
        }

        // 4. 거래소별 상세 모니터링 카드 렌더링
        const grid = document.getElementById('exchange-collector-grid');
        if (grid && detail.exchanges) {
            // 업비트, 빗썸, 한국투자증권(KIS) 순서로 카드 배치 고정 정렬
            const orderedExchanges = ['upbit', 'bithumb', 'kis'];
            let htmlContent = "";
            
            // 1) 고정 정의된 순서대로 카드를 먼저 렌더링
            orderedExchanges.forEach(exch => {
                const exchInfo = detail.exchanges[exch];
                if (exchInfo) {
                    const adjustedInfo = { ...exchInfo };
                    if (isDaemonStale) {
                        adjustedInfo.is_running = false;
                        adjustedInfo.status = 'OFFLINE';
                    }
                    htmlContent += buildExchangeCardHtml(exch, adjustedInfo);
                }
            });
            
            // 2) 혹시 나중에 추가될 수 있는 미정의 거래소들을 뒤에 안전하게 순차 병합
            for (const [exch, exchInfo] of Object.entries(detail.exchanges)) {
                if (!orderedExchanges.includes(exch)) {
                    const adjustedInfo = { ...exchInfo };
                    if (isDaemonStale) {
                        adjustedInfo.is_running = false;
                        adjustedInfo.status = 'OFFLINE';
                    }
                    htmlContent += buildExchangeCardHtml(exch, adjustedInfo);
                }
            }
            
            grid.innerHTML = htmlContent;
        }
    }

    /**
     * 개별 큐 리소스 카드 렌더링 및 심각도 컬러 바인딩
     */
    function updateQueueCard(queueId, queueData) {
        if (!queueData) return;
        
        const valEl = document.getElementById(`q-val-${queueId}`);
        const barEl = document.getElementById(`q-bar-${queueId}`);
        const subEl = document.getElementById(`q-sub-${queueId}`);
        const cardEl = document.getElementById(`q-card-${queueId}`);

        const qsize = queueData.qsize || 0;
        const max_size = queueData.max_size || 1000;
        const usage_pct = queueData.usage_pct || 0.0;
        const level = queueData.level || 'NORMAL';

        if (valEl) valEl.innerText = `${qsize.toLocaleString()} / ${max_size.toLocaleString()}`;
        if (barEl) {
            barEl.style.width = `${Math.min(usage_pct, 100)}%`;
            
            // Level별 프로그레스바 컬러 바인딩
            if (level === 'CRITICAL') {
                barEl.style.background = '#EF4444'; // Red
            } else if (level === 'WARNING') {
                barEl.style.background = '#F59E0B'; // Amber
            } else {
                barEl.style.background = '#10B981'; // Green
            }
        }
        
        if (subEl) {
            subEl.innerText = `사용률: ${usage_pct.toFixed(2)}% (${level})`;
            subEl.className = `sub-label ${level.toLowerCase()}`;
        }

        // 카드 테두리에 깜빡임 애니메이션 등 경고등 바인딩
        if (cardEl) {
            cardEl.className = `diag-card ${level.toLowerCase()}`;
        }
    }

    /**
     * 거래소 카드 HTML 동적 구성
     */
    function buildExchangeCardHtml(exch, exchInfo) {
        const isRunning = exchInfo.is_running;
        const status = exchInfo.status || 'STOPPED';
        const symbolsCount = exchInfo.symbols_count || 0;
        const processedCount = exchInfo.processed_count || 0;
        const droppedCount = exchInfo.dropped_count || 0;
        const lastTick = exchInfo.last_tick;
        const lastError = exchInfo.last_error;

        // 1. 마지막 틱 수신 시각 변환
        let tickTimeStr = "-";
        let coinName = "-";
        let lastPriceStr = "-";
        if (lastTick && lastTick.trade_timestamp) {
            tickTimeStr = new Date(lastTick.trade_timestamp).toLocaleTimeString();
            const nameKey = `${exch}:${lastTick.code}`;
            coinName = (window.state.symbolNames && window.state.symbolNames[nameKey])
                ? window.state.symbolNames[nameKey]
                : lastTick.code;
            lastPriceStr = formatPrice(lastTick.trade_price);
        }

        // [NEW] Raw 및 Pretty Formatted 메시지 데이터 가공
        let rawText = "-";
        let formattedText = "";
        if (exchInfo.last_raw) {
            rawText = exchInfo.last_raw;
        } else if (lastTick) {
            rawText = JSON.stringify(lastTick);
        }

        if (rawText !== "-") {
            try {
                const parsed = JSON.parse(rawText);
                formattedText = JSON.stringify(parsed, null, 2);
            } catch (e) {
                formattedText = "";
            }
        }

        // 2. 동기화 대기 플래그 확인 제거 (DB 직접 조회 방식)

        // 3. 제어 펜딩 여부 확인 (스피너 로딩 스켈레톤 적용용)
        const isPending = isCommandPending(exch);

        // 4. 종목 칩 리스트 빌드
        const symbolsList = activeSymbols[exch] || [];
        let symbolsChips = '<span style="color: #64748B; font-size: 0.75rem;">등록된 수집 종목이 없습니다.</span>';
        if (symbolsList.length > 0) {
            symbolsChips = symbolsList.map(sym => {
                const key = `${exch}:${sym}`;
                const name = (window.state.symbolNames && window.state.symbolNames[key]) 
                    ? window.state.symbolNames[key] 
                    : sym;
                return `<span class="symbol-chip" title="${sym}">${name}</span>`;
            }).join('');
        }

        // 5. 거래소 테마 아이콘 바인딩
        const badgeClass = isRunning ? 'success' : 'danger';
        const exchLogo = exch === 'upbit' ? '🔵' : (exch === 'bithumb' ? '🟡' : '🔴');

        return `
            <div class="card collector-exch-card" id="exch-card-${exch}">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 15px; border-bottom: 1px solid rgba(148, 163, 184, 0.1); padding-bottom: 10px;">
                    <div style="display: flex; align-items: center; gap: 8px;">
                        <span class="exch-icon">${exchLogo}</span>
                        <h3 style="margin: 0; text-transform: uppercase; font-size: 1.1rem; color: #F8FAFC;">${exch.toUpperCase()}</h3>
                    </div>
                    <span class="badge ${badgeClass}" style="font-size: 0.75rem;">${status}</span>
                </div>
                
                <!-- 거래소 연결 명세 -->
                <div style="background: rgba(30, 41, 59, 0.3); border: 1px solid rgba(148, 163, 184, 0.05); padding: 8px 10px; border-radius: 6px; margin-bottom: 12px; font-size: 0.72rem; line-height: 1.5;">
                    <div style="display: flex; justify-content: space-between; margin-bottom: 3px;"><span style="color: #64748B;">운영 시간</span><span style="color: #F8FAFC; font-weight: bold;">${exchInfo.operating_hours || '-'}</span></div>
                    <div style="display: flex; justify-content: space-between; gap: 8px; margin-bottom: 3px;"><span style="color: #64748B; flex-shrink: 0;">WS URL</span><span style="color: #94A3B8; font-family: monospace; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;" title="${exchInfo.websocket_url || '-'}">${exchInfo.websocket_url || '-'}</span></div>
                    <div style="display: flex; justify-content: space-between; gap: 8px;"><span style="color: #64748B; flex-shrink: 0;">API URL</span><span style="color: #94A3B8; font-family: monospace; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;" title="${exchInfo.api_url || '-'}">${exchInfo.api_url || '-'}</span></div>
                </div>
                
                <!-- 실시간 수신 메트릭 -->
                <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 15px; font-size: 0.82rem;">
                    <div style="background: rgba(30, 41, 59, 0.5); padding: 8px; border-radius: 4px; border: 1px solid rgba(148, 163, 184, 0.05);">
                        <span style="color: #94A3B8; display: block; margin-bottom: 4px; font-size: 0.75rem;">수집 종목 수 <span class="tooltip-icon" title="데이터베이스 exchange_assets 테이블에서 활성화되어 현재 수집기 데몬이 소켓을 통해 거래소 측에 구독 요청을 보낸 종목 수입니다.">❓</span></span>
                        <span style="font-weight: bold; font-family: monospace; font-size: 1rem; color: #F8FAFC;">${symbolsCount} 개</span>
                    </div>
                    <div style="background: rgba(30, 41, 59, 0.5); padding: 8px; border-radius: 4px; border: 1px solid rgba(148, 163, 184, 0.05);">
                        <span style="color: #94A3B8; display: block; margin-bottom: 4px; font-size: 0.75rem;">누적 처리 / 드롭 <span class="tooltip-icon" title="누적 처리: 웹소켓을 통해 정상 수집되어 내부 처리 큐에 적재된 누적 틱 갯수 / 누적 드롭: 큐 포화 시 시스템 폭주를 막기 위해 유실 처리된 틱 갯수입니다.">❓</span></span>
                        <span style="font-weight: bold; font-family: monospace; font-size: 1rem; color: ${droppedCount > 0 ? '#FF4B4B' : '#F8FAFC'}">
                            ${processedCount.toLocaleString()} / <span style="color: #FF4B4B">${droppedCount.toLocaleString()}</span>
                        </span>
                    </div>
                </div>

                <!-- 마지막 틱 정보 -->
                <div style="background: rgba(15, 23, 42, 0.4); border: 1px solid rgba(148, 163, 184, 0.05); padding: 10px; border-radius: 6px; margin-bottom: 15px; font-size: 0.78rem;">
                    <div style="color: #64748B; font-weight: bold; margin-bottom: 6px; display: flex; justify-content: space-between;">
                        <span>⚡ 마지막 수신 틱</span>
                        <span style="font-family: monospace;" id="tick-time-${exch}">${tickTimeStr}</span>
                    </div>
                    ${lastTick ? `
                        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
                            <span style="color: #F8FAFC; font-weight: bold;">${coinName} <span style="font-size: 0.7rem; color: #64748B; font-family: monospace;">(${lastTick.code})</span></span>
                            <span style="font-family: 'Roboto Mono', monospace; font-weight: bold; color: #10B981;">${lastPriceStr} 원</span>
                        </div>
                        <details open class="raw-tick-details" style="margin-top: 8px; border-top: 1px dashed rgba(148, 163, 184, 0.1); padding-top: 6px;">
                            <summary style="font-size: 0.72rem; color: #94A3B8; cursor: pointer; outline: none; user-select: none; font-weight: bold;">🔍 Raw Message</summary>
                            <pre style="margin: 6px 0 0 0; background: #0F172A; border: 1px solid rgba(148, 163, 184, 0.1); border-radius: 4px; padding: 8px; font-family: 'Roboto Mono', monospace; font-size: 0.68rem; color: #38BDF8; text-align: left; white-space: pre-wrap; word-break: break-all;">${escapeHtml(rawText)}</pre>
                            ${formattedText ? `
                                <div style="font-size: 0.72rem; color: #94A3B8; font-weight: bold; margin-top: 8px;">✨ Formatted JSON</div>
                                <pre style="margin: 6px 0 0 0; background: #0F172A; border: 1px solid rgba(148, 163, 184, 0.1); border-radius: 4px; padding: 8px; font-family: 'Roboto Mono', monospace; font-size: 0.68rem; color: #10B981; text-align: left; white-space: pre-wrap; word-break: break-all;">${escapeHtml(formattedText)}</pre>
                            ` : ''}
                        </details>
                    ` : `<div style="color: #64748B; text-align: center; padding: 4px 0;">수신 데이터 없음</div>`}
                </div>

                <!-- 마지막 에러 메시지 (있는 경우만 노출) -->
                ${lastError ? `
                    <div style="background: rgba(239, 68, 68, 0.1); border: 1px solid rgba(239, 68, 68, 0.2); color: #EF4444; padding: 8px 10px; border-radius: 6px; margin-bottom: 15px; font-size: 0.75rem; word-break: break-all;">
                        <strong>⚠️ 에러 발생:</strong> ${lastError}
                    </div>
                ` : ''}

                <!-- 제어 버튼 영역 -->
                <div style="display: flex; gap: 8px; margin-top: auto;">
                    <button class="btn sm success ${isPending ? 'loading' : ''}" onclick="CollectorView.startCollector('${exch}')" ${isPending || isRunning ? 'disabled' : ''} style="flex: 1; font-size: 0.75rem; padding: 6px 0; font-weight: bold;">▶️ 시작</button>
                    <button class="btn sm danger ${isPending ? 'loading' : ''}" onclick="CollectorView.stopCollector('${exch}')" ${isPending || !isRunning ? 'disabled' : ''} style="flex: 1; font-size: 0.75rem; padding: 6px 0; font-weight: bold;">⏹️ 중지</button>
                </div>

                <!-- 활성 종목 (칩 영역) -->
                <div style="margin-top: 15px; border-top: 1px dashed rgba(148, 163, 184, 0.1); padding-top: 12px;">
                    <span style="font-size: 0.75rem; color: #64748B; font-weight: bold; display: block; margin-bottom: 8px;">📋 수집 중인 종목 리스트 <span class="tooltip-icon" title="사용자가 수집 요청을 보낸 활성 종목들의 전체 목록입니다. 실제 실시간 틱 수신 여부와 상관없이 고정 표시됩니다.">❓</span></span>
                    <div style="display: flex; flex-wrap: wrap; gap: 6px; max-height: 100px; overflow-y: auto; padding: 4px;" class="symbols-chips-container">
                        ${symbolsChips}
                    </div>
                </div>
            </div>
        `;
    }

    /**
     * 특정 거래소에 펜딩중인 명령이 있는지 감시
     */
    function isCommandPending(exch) {
        for (const [cmdId, cmd] of pendingCommands.entries()) {
            if (cmd.exchange === exch && (cmd.type === 'collector_start' || cmd.type === 'collector_stop')) {
                return true;
            }
        }
        return false;
    }

    /**
     * 1. 개별 수집기 시작 명령 송신
     */
    async function startCollector(exch) {
        const cmdId = generateCommandId();
        console.log(`[CollectorView] Request starting collector: ${exch} (id: ${cmdId})`);
        
        // 펜딩 등록
        registerPendingCommand(cmdId, 'collector_start', exch);
        
        try {
            await APIClient.controlCollector(exch, 'start', cmdId);
            showToast(`${exch.toUpperCase()} 수집기 가동 요청이 전송되었습니다.`, "success");
        } catch (error) {
            pendingCommands.delete(cmdId);
            refreshControlsState();
            showToast(`${exch.toUpperCase()} 수집기 기동 명령 전송 실패`, "error");
        }
    }

    /**
     * 2. 개별 수집기 중지 명령 송신
     */
    async function stopCollector(exch) {
        const cmdId = generateCommandId();
        console.log(`[CollectorView] Request stopping collector: ${exch} (id: ${cmdId})`);
        
        // 펜딩 등록
        registerPendingCommand(cmdId, 'collector_stop', exch);
        
        try {
            await APIClient.controlCollector(exch, 'stop', cmdId);
            showToast(`${exch.toUpperCase()} 수집기 정지 요청이 전송되었습니다.`, "success");
        } catch (error) {
            pendingCommands.delete(cmdId);
            refreshControlsState();
            showToast(`${exch.toUpperCase()} 수집기 정지 명령 전송 실패`, "error");
        }
    }

    /**
     * 3. 전체 시작 명령 송신
     */
    async function startAllCollectors() {
        const cmdId = generateCommandId();
        console.log(`[CollectorView] Request starting all collectors (id: ${cmdId})`);
        
        // 전체 거래소 펜딩 적용을 위해 루프 돌며 등록
        const exchanges = Object.keys(window.state.collectorStatuses || { upbit: {}, bithumb: {}, kis: {} });
        exchanges.forEach(exch => {
            if (exch !== 'strategy') {
                registerPendingCommand(cmdId, 'collector_start', exch);
            }
        });

        try {
            await APIClient.controlCollector('all', 'start', cmdId);
            showToast("전체 수집기 기동 명령이 전송되었습니다.", "success");
        } catch (error) {
            clearPendingById(cmdId);
            showToast("전체 수집기 기동 명령 전송 실패", "error");
        }
    }

    /**
     * 4. 전체 중지 명령 송신
     */
    async function stopAllCollectors() {
        const cmdId = generateCommandId();
        console.log(`[CollectorView] Request stopping all collectors (id: ${cmdId})`);
        
        const exchanges = Object.keys(window.state.collectorStatuses || { upbit: {}, bithumb: {}, kis: {} });
        exchanges.forEach(exch => {
            if (exch !== 'strategy') {
                registerPendingCommand(cmdId, 'collector_stop', exch);
            }
        });

        try {
            await APIClient.controlCollector('all', 'stop', cmdId);
            showToast("전체 수집기 정지 명령이 전송되었습니다.", "success");
        } catch (error) {
            clearPendingById(cmdId);
            showToast("전체 수집기 정지 명령 전송 실패", "error");
        }
    }

    /**
     * 5. 수집기 데몬 프로세스 자체 재기동
     */
    async function restartCollectorDaemon() {
        const cmdId = generateCommandId();
        console.log(`[CollectorView] Request restarting collector daemon (id: ${cmdId})`);
        
        // 재기동 전 이전 PID 및 기동시각 백업
        const backupPid = currentPid;
        const backupStartedAt = currentDaemonStartedAt;

        // UI 락 설정 및 버튼에 loading 클래스 부여
        const btn = document.getElementById('btn-collector-restart-daemon');
        if (btn) btn.classList.add('loading');

        pendingCommands.set(cmdId, {
            type: 'restart_daemon',
            exchange: 'all',
            previousPid: backupPid,
            previousStartedAt: backupStartedAt,
            timeoutId: setTimeout(() => {
                handleTimeout(cmdId);
                if (btn) btn.classList.remove('loading');
            }, 12000) // 프로세스가 완전히 소멸했다가 다시 뜨는 것을 고려해 넉넉히 12초 설정
        });

        // 즉시 하트비트를 만료시켜 UI에 연결 끊김 및 거래소 OFFLINE 강제 반영
        lastDetailHeartbeat = 0;
        checkStaleStatus();

        try {
            await APIClient.restartCollectorDaemon(cmdId);
            showToast("수집기 데몬 자가 재기동 신호가 전송되었습니다.", "success");
        } catch (error) {
            pendingCommands.delete(cmdId);
            if (btn) btn.classList.remove('loading');
            showToast("수집기 데몬 재기동 신호 전송 실패", "error");
            
            // 에러 발생 시 하트비트 복구 복원
            lastDetailHeartbeat = Date.now();
            checkStaleStatus();
        }
    }

    /**
     * 제어 명령 펜딩 등록 및 설정된 타임아웃 타이머 스케줄링
     */
    function registerPendingCommand(cmdId, type, exchange) {
        pendingCommands.set(cmdId, {
            type,
            exchange,
            timeoutId: setTimeout(() => {
                handleTimeout(cmdId);
            }, monitoringConfig.control_ack_timeout_ms) // 일반 시작/중지 커맨드는 설정값(ms) 타임아웃
        });
        refreshControlsState();
    }

    /**
     * 타임아웃 발생 시 에러 알림 및 펜딩 롤백
     */
    function handleTimeout(cmdId) {
        const cmd = pendingCommands.get(cmdId);
        if (!cmd) return;

        console.error(`[CollectorView] Command timeout: ${cmd.type} (id: ${cmdId})`);
        pendingCommands.delete(cmdId);
        refreshControlsState();

        if (cmd.type === 'restart_daemon') {
            const btn = document.getElementById('btn-collector-restart-daemon');
            if (btn) btn.classList.remove('loading');
            showToast("데몬 재기동 응답 타임아웃. 수동 복구 여부를 확인해 주십시오.", "error");
        } else {
            showToast(`${cmd.exchange.toUpperCase()} 수집기 제어 응답 시간 초과.`, "error");
        }
    }

    /**
     * 펜딩 해제 후 UI 컨트롤들 상태 업데이트 적용
     */
    function refreshControlsState() {
        // 이미 업데이트된 데이터 기반 카드 재생성이 유도되므로 즉시 상세정보를 1회 PULL하여 UI 정합성 강제 맞춤
        loadDaemonDetail();
    }

    /**
     * 동일 ID에 해당하는 모든 펜딩 항목 삭제
     */
    function clearPendingById(cmdId) {
        const cmd = pendingCommands.get(cmdId);
        if (cmd) {
            if (cmd.timeoutId) clearTimeout(cmd.timeoutId);
            pendingCommands.delete(cmdId);
        }
        // 다중 삭제 지원을 위해 남아있는 맵 키 순회 검사
        for (const [key, item] of pendingCommands.entries()) {
            if (key === cmdId) {
                if (item.timeoutId) clearTimeout(item.timeoutId);
                pendingCommands.delete(key);
            }
        }
        refreshControlsState();
    }

    /**
     * ZMQ -> 웹소켓을 타고 흘러들어온 실시간 collector_daemon_detail 처리
     */
    function handleDaemonDetail(data) {
        // 하트비트 시각 기록
        lastDetailHeartbeat = Date.now();

        // 실시간 브로드캐스트의 수신 시각 보완 안전 장치
        if (!data.synced_at) {
            data.synced_at = Date.now();
        }

        // 데몬 메타데이터 백업 및 갱신
        currentPid = data.source_pid || null;
        currentDaemonStartedAt = data.daemon_started_at || 0;
        daemonSymbolsVersions = data.symbols_version || {};

        // [재기동 검증] restart_daemon 명령 펜딩이 있는 경우, 새 PID/기동시각 대조
        for (const [cmdId, cmd] of pendingCommands.entries()) {
            if (cmd.type === 'restart_daemon') {
                const newPid = data.source_pid;
                const newStarted = data.daemon_started_at;
                
                // OR 조건 검증: PID가 변경되었거나, 최초 기동 시각이 더 증가한 경우 재기동 완료
                const isNewProcess = (cmd.previousPid !== null && newPid !== cmd.previousPid);
                const isNewStartTime = (newStarted > cmd.previousStartedAt);

                if (isNewProcess || isNewStartTime) {
                    console.log(`[CollectorView] Daemon restart verified. New PID: ${newPid}, StartedAt: ${newStarted}`);
                    
                    if (cmd.timeoutId) clearTimeout(cmd.timeoutId);
                    pendingCommands.delete(cmdId);
                    
                    const btn = document.getElementById('btn-collector-restart-daemon');
                    if (btn) btn.classList.remove('loading');
                    
                    showToast("수집기 데몬이 성공적으로 재기동되었습니다.", "success");
                    loadEvents(); // 감사 로그 리로드
                }
            }
        }

        // UI 갱신 유도 (REST API와 동일한 래핑 구조 전달)
        const wrappedData = {
            daemon_detail: data,
            active_symbols: activeSymbols,
            active_symbols_metadata: activeSymbolsMetadata
        };
        updateUI(wrappedData);
    }

    /**
     * ZMQ -> 웹소켓을 타고 들어온 실시간 종목 목록 동기화 처리
     */
    function handleSymbolsSync(data) {
        const exch = data.exchange;
        if (!exch) return;

        // 캐시 업데이트
        activeSymbols[exch] = data.symbols || [];
        activeSymbolsMetadata[exch] = {
            synced_at: Date.now(),
            symbols_version: data.symbols_version || 1,
            source_pid: data.source_pid,
            daemon_started_at: data.daemon_started_at
        };

        console.log(`[CollectorView] Received realtime symbols sync for ${exch.toUpperCase()} (Ver: ${data.symbols_version})`);
        
        // stale check 트리거
        checkStaleStatus();
        
        // 해당 거래소 부분 UI 업데이트 유도
        loadDaemonDetail();
    }

    /**
     * ZMQ -> 웹소켓을 통해 실시간 수신된 제어 응답 결과(ACK) 핸들러
     */
    function handleCommandResult(data) {
        const cmdId = data.command_id;
        if (!cmdId) return;

        const cmd = pendingCommands.get(cmdId);
        if (!cmd) return; // 내가 쏜 명령이 아니거나 이미 타임아웃 종결된 경우 패스

        console.log(`[CollectorView] Command ACK received: ${cmd.type} -> ${data.status} (id: ${cmdId})`);

        if (cmd.timeoutId) clearTimeout(cmd.timeoutId);
        pendingCommands.delete(cmdId);

        if (data.status === 'SUCCESS') {
            if (cmd.type === 'collector_start') {
                showToast(`${cmd.exchange.toUpperCase()} 수집기가 정상 시작되었습니다.`, "success");
            } else if (cmd.type === 'collector_stop') {
                showToast(`${cmd.exchange.toUpperCase()} 수집기가 안전하게 정지되었습니다.`, "success");
            }
        } else {
            const errorReason = data.error || '알 수 없는 이유';
            showToast(`${cmd.exchange.toUpperCase()} 제어 실패: ${errorReason}`, "error");
        }

        refreshControlsState();
        loadEvents(); // 감사 로그 리로드
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
        startCollector,
        stopCollector,
        startAllCollectors,
        stopAllCollectors,
        restartCollectorDaemon,
        handleDaemonDetail,
        handleSymbolsSync,
        handleCommandResult,
        loadEvents
    };
})();

// 전역 window 바인딩
window.CollectorView = CollectorView;
