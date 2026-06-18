/**
 * KIS 종목 상세 정보 화면 제어 모듈
 */

const KisDetailView = (() => {

    /**
     * KIS 종목 상세 정보를 서버로부터 로드하여 화면에 렌더링합니다.
     */
    async function loadKisDetail() {
        const symbol = state.currentSymbol;
        const exchange = state.currentExchange || 'upbit';
        
        const kisWrapper = document.getElementById('kis-detail-fields-wrapper');
        const cryptoWrapper = document.getElementById('crypto-detail-info-wrapper');
        
        if (!symbol) {
            showToast("선택된 종목이 없습니다.", "error");
            return;
        }

        // 거래소에 따라 영역 가시성 분기 제어
        if (exchange !== 'kis') {
            if (kisWrapper) kisWrapper.style.display = 'none';
            if (cryptoWrapper) {
                cryptoWrapper.style.display = 'block';
                // 텍스트 동적 변경 (선택된 심볼 및 거래소 표시)
                const descP = cryptoWrapper.querySelector('p');
                if (descP) {
                    descP.innerHTML = `
                        선택하신 종목은 가상자산입니다.<br>
                        <strong>${symbol} (${exchange.toUpperCase()})</strong>은 가상자산이므로 기업 제원 및 대체거래소(Nextrade) 거래 정보가 제공되지 않습니다.
                    `;
                }
            }
            return;
        }

        if (kisWrapper) kisWrapper.style.display = 'block';
        if (cryptoWrapper) cryptoWrapper.style.display = 'none';

        // 제목 및 기본 코드 설정 (존재 시)
        const nameEl = document.getElementById('kis-detail-title-name');
        const codeEl = document.getElementById('kis-detail-title-code');
        if (nameEl) nameEl.innerText = '로딩 중...';
        if (codeEl) codeEl.innerText = `(${symbol})`;

        // 로딩 표시 처리
        setLoadingState(true);

        try {
            const data = await APIClient.fetchKisSymbolDetail(symbol);
            if (!data) {
                throw new Error("상세 정보를 가져올 수 없습니다.");
            }

            renderDetailData(data);
            updateActionButtons(data);
        } catch (e) {
            showToast(`상세 정보 로드 실패: ${e.message}`, "error");
            setLoadingState(false);
        }
    }

    /**
     * 로딩 상태를 UI에 표시합니다.
     */
    function setLoadingState(isLoading) {
        const placeholders = [
            'kis-detail-nxt-tr-psbl', 'kis-detail-nxt-tr-stop', 'kis-detail-sub-tr-id',
            'kis-detail-tr-stop', 'kis-detail-admn-item', 'kis-detail-thdt-clpr', 'kis-detail-bfdy-clpr',
            'kis-detail-market-id', 'kis-detail-scty-grp', 'kis-detail-lstg-stqt', 'kis-detail-lstg-cptl',
            'kis-detail-cpta', 'kis-detail-papr', 'kis-detail-issu-pric', 'kis-detail-lstg-dt',
            'kis-detail-k200-yn', 'kis-detail-std-idst-name', 'kis-detail-lcls-name', 'kis-detail-mcls-name'
        ];

        placeholders.forEach(id => {
            const el = document.getElementById(id);
            if (el) {
                if (isLoading) {
                    el.innerHTML = '<span style="color: #64748B; font-size: 0.85rem;">⏳ 로딩 중...</span>';
                }
            }
        });
    }

    /**
     * 서버로부터 수신한 상세 데이터를 엘리먼트에 바인딩합니다.
     */
    function renderDetailData(data) {
        // 제목 바인딩 (존재 시)
        const titleNameEl = document.getElementById('kis-detail-title-name');
        if (titleNameEl) {
            titleNameEl.innerText = data.prdt_abrv_name || data.prdt_name || '-';
        }
        
        // Nextrade 가능 여부 (cptt_trad_tr_psbl_yn)
        const nxtPsbl = data.cptt_trad_tr_psbl_yn === 'Y';
        document.getElementById('kis-detail-nxt-tr-psbl').innerHTML = nxtPsbl 
            ? `<span style="background: #10B981; color: #FFFFFF; padding: 4px 10px; border-radius: 4px; font-size: 0.85rem; font-weight: bold;">지원 (Y)</span>` 
            : `<span style="background: #64748B; color: #FFFFFF; padding: 4px 10px; border-radius: 4px; font-size: 0.85rem; font-weight: bold;">미지원 (N)</span>`;

        // Nextrade 거래정지 여부 (nxt_tr_stop_yn)
        const nxtStop = data.nxt_tr_stop_yn === 'Y';
        document.getElementById('kis-detail-nxt-tr-stop').innerHTML = nxtStop 
            ? `<span style="background: #EF4444; color: #FFFFFF; padding: 4px 10px; border-radius: 4px; font-size: 0.85rem; font-weight: bold;">거래정지 (Y)</span>` 
            : `<span style="background: #475569; color: #E2E8F0; padding: 4px 10px; border-radius: 4px; font-size: 0.85rem; font-weight: bold;">정상 (N)</span>`;

        // 실시간 체결 구독 예상 채널 (tr_id)
        const trIdCnt = nxtPsbl && !nxtStop ? 'H0UNCNT0 (통합체결)' : 'H0STCNT0 (KRX 체결)';
        const trIdMko = nxtPsbl && !nxtStop ? 'H0UNMKO0 (통합장운영)' : 'H0STMKO0 (KRX 장운영)';
        document.getElementById('kis-detail-sub-tr-id').innerHTML = `
            <div>체결: <span style="color: #F59E0B; font-weight: bold;">${trIdCnt}</span></div>
            <div style="margin-top: 4px;">장운영: <span style="color: #F59E0B; font-weight: bold;">${trIdMko}</span></div>
        `;

        // 거래정지 여부 (tr_stop_yn)
        const trStop = data.tr_stop_yn === 'Y';
        document.getElementById('kis-detail-tr-stop').innerHTML = trStop 
            ? `<span style="background: #EF4444; color: #FFFFFF; padding: 4px 10px; border-radius: 4px; font-size: 0.85rem; font-weight: bold;">거래정지 (Y)</span>` 
            : `<span style="background: #475569; color: #E2E8F0; padding: 4px 10px; border-radius: 4px; font-size: 0.85rem; font-weight: bold;">정상 (N)</span>`;

        // 관리종목 여부 (admn_item_yn)
        const admnYn = data.admn_item_yn === 'Y';
        document.getElementById('kis-detail-admn-item').innerHTML = admnYn 
            ? `<span style="background: #F59E0B; color: #FFFFFF; padding: 4px 10px; border-radius: 4px; font-size: 0.85rem; font-weight: bold;">관리종목 (Y)</span>` 
            : `<span style="background: #475569; color: #E2E8F0; padding: 4px 10px; border-radius: 4px; font-size: 0.85rem; font-weight: bold;">일반 (N)</span>`;

        // 가격 정보 포맷터
        const formatPriceKRW = (val) => {
            if (val === undefined || val === null) return '-';
            const num = parseFloat(val);
            return num.toLocaleString('ko-KR') + ' 원';
        };

        // 당일종가 / 전일종가
        document.getElementById('kis-detail-thdt-clpr').innerText = formatPriceKRW(data.thdt_clpr);
        document.getElementById('kis-detail-bfdy-clpr').innerText = formatPriceKRW(data.bfdy_clpr);

        // 시장ID코드 (STK: 코스피, KSQ: 코스닥, KNX: 코넥스 등)
        const marketMap = { 'STK': '유가증권시장 (KOSPI)', 'KSQ': '코스닥시장 (KOSDAQ)', 'KNX': '코넥스시장 (KONEX)' };
        document.getElementById('kis-detail-market-id').innerText = `${data.mket_id_cd || '-'} (${marketMap[data.mket_id_cd] || '기타'})`;

        // 증권그룹ID코드 (ST: 주권, EF: ETF, EN: ETN 등)
        const grpMap = { 'ST': '주권', 'DR': '주식예탁증서', 'EF': 'ETF (상장지수펀드)', 'EN': 'ETN (상장지수증권)', 'EW': 'ELW (주식워런트증권)' };
        document.getElementById('kis-detail-scty-grp').innerText = `${data.scty_grp_id_cd || '-'} (${grpMap[data.scty_grp_id_cd] || '기타'})`;

        // 상장주식수 / 자본금 / 상장자본금액
        const formatQty = (val) => val ? parseInt(val).toLocaleString('ko-KR') + ' 주' : '-';
        const formatAmt = (val) => val ? parseInt(val).toLocaleString('ko-KR') + ' 원' : '-';
        document.getElementById('kis-detail-lstg-stqt').innerText = formatQty(data.lstg_stqt);
        document.getElementById('kis-detail-lstg-cptl').innerText = formatAmt(data.lstg_cptl_amt);
        document.getElementById('kis-detail-cpta').innerText = formatAmt(data.cpta);

        // 액면가 / 발행가
        document.getElementById('kis-detail-papr').innerText = formatPriceKRW(data.papr);
        document.getElementById('kis-detail-issu-pric').innerText = formatPriceKRW(data.issu_pric);

        // 상장일자
        const formatDt = (val) => {
            if (!val || val.length !== 8) return '-';
            return `${val.slice(0, 4)}-${val.slice(4, 6)}-${val.slice(6, 8)}`;
        };
        const lstgDt = data.scts_mket_lstg_dt || data.kosdaq_mket_lstg_dt || '-';
        document.getElementById('kis-detail-lstg-dt').innerText = formatDt(lstgDt);

        // 코스피200 여부
        document.getElementById('kis-detail-k200-yn').innerText = data.kospi200_item_yn === 'Y' ? '예 (Y)' : '아니오 (N)';

        // 산업/업종 분류명
        document.getElementById('kis-detail-std-idst-name').innerText = data.std_idst_clsf_cd_name || '-';
        document.getElementById('kis-detail-lcls-name').innerText = data.idx_bztp_lcls_cd_name || '-';
        document.getElementById('kis-detail-mcls-name').innerText = data.idx_bztp_mcls_cd_name || '-';
    }

    /**
     * 종목 수집 활성화 여부에 따라 상단 액션 버튼을 갱신합니다.
     */
    function updateActionButtons(data) {
        const symbol = state.currentSymbol;
        // 전역 마켓 목록에서 수집 중인지 매칭하여 검사
        const activeItem = (window.marketData || []).find(item => item.exchange === 'kis' && item.market === symbol);
        // DB의 exchange_assets 기준 수집 가능 목록 및 수집 중 표시
        const isCollected = activeItem ? (activeItem.is_collected !== false) : false;

        const collectBtn = document.getElementById('btn-kis-detail-collect');

        if (isCollected) {
            // 수집 중일 때: 해제 버튼
            if (collectBtn) {
                collectBtn.innerText = '수집 해제';
                collectBtn.style.background = '#EF4444'; // Red
                collectBtn.onclick = () => handleToggleCollection(symbol, data.prdt_abrv_name || data.prdt_name || symbol, false);
            }
        } else {
            // 수집 중이 아닐 때: 수집 등록 버튼
            if (collectBtn) {
                collectBtn.innerText = '수집 시작';
                collectBtn.style.background = '#3B82F6'; // Blue
                collectBtn.onclick = () => handleToggleCollection(symbol, data.prdt_abrv_name || data.prdt_name || symbol, true);
            }
        }
    }

    /**
     * 수집 시작/해제 이벤트를 처리합니다.
     */
    async function handleToggleCollection(code, name, isChecked) {
        const actionText = isChecked ? '수집 시작' : '수집 해제';
        const confirmMsg = `${name} (${code}) ${actionText}을 진행하시겠습니까?`;

        if (!confirm(confirmMsg)) {
            return;
        }

        try {
            const result = await APIClient.toggleKisSymbol(code, name, isChecked);
            const statusMsg = result.is_collected ? '수집 등록 완료' : '수집 해제 완료';
            showToast(`${name} (${code}) ${statusMsg}`, result.is_collected ? 'success' : 'info');

            // 마켓 데이터 리로드 및 상세페이지 정보 강제 재갱신
            setTimeout(async () => {
                if (typeof loadMarket === 'function') {
                    await loadMarket(true);
                }
                loadKisDetail();
            }, 500);

            if (window.updateCollectorStatus) {
                window.updateCollectorStatus();
            }
        } catch (err) {
            showToast(`수집 변경 실패: ${err.message}`, 'error');
        }
    }

    return {
        loadKisDetail
    };
})();
