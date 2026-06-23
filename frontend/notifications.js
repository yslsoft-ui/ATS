/**
 * Upbit Terminal 알림(Notifications) 시스템 모듈
 */

/**
 * 실시간으로 감지된 알림을 우측 하단 푸시 팝업으로 사용자에게 보여줍니다.
 * @param {object} notification - 알림 데이터
 */
function showNotification(notification, typeFallback) {
    if (!state.isAlertEnabled) return;

    const container = document.getElementById('alert-container');
    if (!container) return;

    let payload = {};
    if (typeof notification === 'string') {
        payload = {
            msg: notification,
            notification_type: typeFallback || 'info'
        };
    } else {
        payload = notification || {};
    }

    const type = payload.notification_type;
    
    // 감지 알림(detect)인 경우 실시간 팝업 거부
    if (type === 'detect') {
        return;
    }

    let cardClass = 'notification-card';
    let title = '🚀 알림';
    
    if (type === 'trade') {
        cardClass += ' success';
        title = '🤖 전략매매 체결';
    } else if (type === 'skip') {
        cardClass += ' warning';
        title = '⚠️ 매매 보류';
    } else if (type === 'warning' || type === 'error' || type === 'success' || type === 'system') {
        cardClass += ` ${type}`;
        title = type === 'error' ? '❌ 시스템 에러' : (type === 'warning' ? '⚠️ 시스템 경고' : 'ℹ️ 시스템 알림');
    } else {
        cardClass += ' info';
    }

    const card = document.createElement('div');
    card.className = cardClass;
    
    const symbol = payload.code || payload.symbol || '';
    const msg = payload.msg || '';

    card.innerHTML = `
        <div class="notification-header">
            <span class="notification-title">${title}</span>
            <span class="notification-time">${new Date().toLocaleTimeString()}</span>
        </div>
        <div class="notification-body">
            ${msg}
        </div>
    `;

    card.onclick = () => {
        // 상태 변경 전 탐색 변수 초기화
        state.alertMarkerTs = null;

        if (symbol) {
            // 스토어 상태 변경 -> 반응형 구독에 의해 자동 리로드 실행됨
            Store.update({
                currentExchange: payload.exchange_id || 'upbit',
                currentSymbol: symbol
            });

            ViewRouter.navigateTo('monitoring-view');
        }

        card.remove();
    };

    container.appendChild(card);

    setTimeout(() => {
        if (card.parentNode) {
            card.style.opacity = '0';
            setTimeout(() => card.remove(), 500);
        }
    }, 8000);
}

// 전역 window 바인딩
window.showNotification = showNotification;

/**
 * 상장 및 상장폐지 예정 이벤트를 확인하여 대시보드 상단에 고정형 배너를 노출합니다.
 */
async function checkUpcomingAssetEvents() {
    try {
        const events = await APIClient.fetchPlannedEvents('PLANNED');
        if (!events || events.length === 0) {
            const existing = document.getElementById('planned-events-banner');
            if (existing) existing.remove();
            return;
        }

        // 백엔드 DB에서 사용자가 명시적으로 닫은 이벤트 ID 목록 조회
        let dismissedIds = [];
        try {
            const res = await APIClient.fetchSystemSetting('dismissed_planned_events');
            if (res && res.value) {
                dismissedIds = JSON.parse(res.value);
            }
        } catch (e) {
            dismissedIds = [];
        }

        // 아직 닫히지 않은 이벤트만 필터링
        const activeEvents = events.filter(ev => !dismissedIds.includes(ev.id));
        if (activeEvents.length === 0) {
            const existing = document.getElementById('planned-events-banner');
            if (existing) existing.remove();
            return;
        }

        const existing = document.getElementById('planned-events-banner');
        if (existing) existing.remove();

        const eventTexts = activeEvents.map(ev => {
            const typeKo = ev.event_type === 'listing' ? '상장' : '상장폐지';
            const exchKo = ev.exchange_id === 'bithumb' ? '빗썸' : (ev.exchange_id === 'upbit' ? '업비트' : '한국투자증권');
            return `[${exchKo}] ${ev.symbol}(${ev.korean_name}) ${typeKo} 예정 (${ev.scheduled_at})`;
        });

        const banner = document.createElement('div');
        banner.id = 'planned-events-banner';
        banner.className = 'planned-events-banner';
        banner.innerHTML = `
            <div class="banner-content">
                <span class="banner-icon">🔔</span>
                <span class="banner-text"><strong>상장/상장폐지 일정 안내:</strong> ${eventTexts.join(' | ')}</span>
            </div>
            <button class="banner-close-btn" id="btn-close-planned-events">&times;</button>
        `;

        document.body.prepend(banner);

        document.getElementById('btn-close-planned-events').addEventListener('click', async () => {
            try {
                let currentDismissed = [];
                try {
                    const res = await APIClient.fetchSystemSetting('dismissed_planned_events');
                    if (res && res.value) {
                        currentDismissed = JSON.parse(res.value);
                    }
                } catch (e) {
                    currentDismissed = [];
                }
                activeEvents.forEach(ev => {
                    if (!currentDismissed.includes(ev.id)) {
                        currentDismissed.push(ev.id);
                    }
                });
                await APIClient.saveSystemSetting('dismissed_planned_events', JSON.stringify(currentDismissed));
            } catch (e) {
                console.error("[checkUpcomingAssetEvents] Failed to save dismissed events to backend:", e);
            }
            banner.remove();
        });
    } catch (e) {
        console.error("[checkUpcomingAssetEvents] Failed to fetch or render planned events:", e);
    }
}

window.checkUpcomingAssetEvents = checkUpcomingAssetEvents;

/**
 * 마지막 사이트 접속 이후 발생한 상장/상장폐지 시스템 이벤트를 감지하여 화면에 토스트로 노출합니다.
 */
async function checkMissedAssetEvents() {
    try {
        let lastSeenTs = 0;
        try {
            const res = await APIClient.fetchSystemSetting('last_seen_asset_event_ts');
            if (res && res.value) {
                lastSeenTs = parseInt(res.value, 10);
            }
        } catch (e) {
            lastSeenTs = 0;
        }
        
        const now = Date.now();
        
        // 백엔드 설정 키가 전혀 존재하지 않는 최초 접속일 경우, 과거의 로그가 대량으로 뜨는 것을 방지하기 위해 
        // 현재 시각으로 초기화하고 바로 리턴합니다.
        if (lastSeenTs === 0) {
            try {
                await APIClient.saveSystemSetting('last_seen_asset_event_ts', now.toString());
            } catch (e) {
                console.error("[checkMissedAssetEvents] Failed to initialize last_seen_asset_event_ts on backend:", e);
            }
            return;
        }

        const logs = await APIClient.fetchSystemEventLogs('all', '', 100);
        if (!logs || logs.length === 0) return;

        // ASSET_LISTED, ASSET_DELISTED 계열 이벤트만 필터링하며,
        // 마지막 확인 시각(lastSeenTs)보다 최근에 생성된 로그만 추립니다.
        const missedEvents = logs.filter(log => 
            (log.event_type === 'ASSET_LISTED' || log.event_type === 'ASSET_DELISTED') && 
            log.timestamp > lastSeenTs
        );

        if (missedEvents.length === 0) return;

        // 시간 오름차순(오래된 것부터)으로 정렬하여 차례대로 토스트 노출
        missedEvents.sort((a, b) => a.timestamp - b.timestamp);

        missedEvents.forEach(event => {
            const toastType = event.event_type === 'ASSET_LISTED' ? 'success' : 'warning';
            // 사용자가 명시적으로 확인하고 닫을 때 비로소 last_seen_asset_event_ts를 업데이트합니다.
            if (typeof showToast === 'function') {
                showToast(event.message, toastType, false, async () => {
                    try {
                        let currentLastSeen = 0;
                        const res = await APIClient.fetchSystemSetting('last_seen_asset_event_ts');
                        if (res && res.value) {
                            currentLastSeen = parseInt(res.value, 10);
                        }
                        if (event.timestamp > currentLastSeen) {
                            await APIClient.saveSystemSetting('last_seen_asset_event_ts', event.timestamp.toString());
                        }
                    } catch (e) {
                        console.error("[checkMissedAssetEvents] Failed to update last_seen_asset_event_ts on backend:", e);
                    }
                });
            }
        });
    } catch (e) {
        console.error("[checkMissedAssetEvents] Failed to check missed asset events:", e);
    }
}

window.checkMissedAssetEvents = checkMissedAssetEvents;
