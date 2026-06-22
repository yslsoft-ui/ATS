/**
 * Upbit Terminal 유틸리티 함수 모음
 */

/**
 * 숫자를 가격 포맷에 맞춰 변환 (천 단위 콤마 등)
 * @param {number} price - 가격
 * @returns {string} 포맷팅된 가격 문자열
 */
function formatPrice(price, decimals) {
    if (price === undefined || price === null) return '0';
    if (decimals !== undefined) {
        return price.toLocaleString(undefined, { 
            minimumFractionDigits: decimals, 
            maximumFractionDigits: decimals 
        });
    }
    if (price >= 1000) return price.toLocaleString();
    return price.toFixed(price < 1 ? 4 : 2);
}

/**
 * 거래량을 한국식 단위(조, 억, 만)로 변환
 * @param {number} vol - 거래량
 * @returns {string} 포맷팅된 거래량 문자열
 */
function formatVolume(vol) {
    if (vol === undefined || vol === null) return '0';
    if (vol >= 1e12) return (vol / 1e12).toFixed(1) + '조';
    if (vol >= 1e8) return (vol / 1e8).toFixed(1) + '억';
    return (vol / 1e4).toFixed(0) + '만';
}

/**
 * 거래량을 소수점 4째자리에서 반올림(최대 소수점 3자리)하여 천 단위 콤마를 찍고 불필요한 소수점 이하 0 제거
 * @param {number} vol - 거래량
 * @returns {string} 포맷팅된 거래량 문자열
 */
function formatTooltipVolume(vol) {
    if (vol === undefined || vol === null) return '0';
    const num = parseFloat(parseFloat(vol).toFixed(3));
    return num.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 3 });
}




/**
 * 퍼센트 값을 포맷팅하고 상승/하락 클래스를 반환
 * @param {number} rate - 변화율
 * @returns {object} { text, className }
 */
function formatRate(rate) {
    const value = parseFloat(rate) || 0;
    return {
        text: (value >= 0 ? '+' : '') + value.toFixed(2) + '%',
        className: value >= 0 ? 'bull' : 'bear'
    };
}

/**
 * 세련된 토스트 알림 메시지를 화면 우측 상단에 표시합니다.
 * @param {string} message - 메시지 내용
 * @param {string} type - 알림 타입 ('success', 'error', 'info')
 */
function showToast(message, type = 'success', autoClose = true, onClose = null) {
    let container = document.getElementById('toast-container');
    if (!container) {
        container = document.createElement('div');
        container.id = 'toast-container';
        container.style.cssText = 'position: fixed; top: 20px; right: 20px; z-index: 9999; display: flex; flex-direction: column; gap: 10px; pointer-events: none;';
        document.body.appendChild(container);
    }
    
    const toast = document.createElement('div');
    toast.className = `toast-message ${type}`;
    
    const borderLeftColor = type === 'success' ? '#FF4B4B' : (type === 'error' ? '#EF4444' : '#0072FF');
    
    toast.style.cssText = `
        background: #1E293B;
        border-left: 4px solid ${borderLeftColor};
        color: #F8FAFC;
        padding: 12px 20px;
        border-radius: 8px;
        box-shadow: 0 10px 25px rgba(0,0,0,0.4);
        font-size: 0.88rem;
        font-weight: 600;
        pointer-events: auto;
        min-width: 280px;
        max-width: 400px;
        opacity: 0;
        transform: translateY(-20px);
        transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
        display: flex;
        justify-content: space-between;
        align-items: center;
        gap: 15px;
    `;
    
    const textSpan = document.createElement('span');
    textSpan.innerText = message;
    textSpan.style.flex = '1';
    toast.appendChild(textSpan);

    const closeToast = () => {
        toast.style.opacity = '0';
        toast.style.transform = 'translateY(-20px)';
        if (typeof onClose === 'function') {
            try {
                onClose();
            } catch (e) {
                console.error("[showToast] onClose callback error:", e);
            }
        }
        setTimeout(() => toast.remove(), 300);
    };

    if (!autoClose) {
        const closeBtn = document.createElement('span');
        closeBtn.innerHTML = '&times;';
        closeBtn.style.cssText = 'cursor: pointer; font-size: 1.2rem; color: #94A3B8; font-weight: bold; line-height: 1; padding: 2px 5px; transition: color 0.2s;';
        closeBtn.addEventListener('mouseover', () => closeBtn.style.color = '#F8FAFC');
        closeBtn.addEventListener('mouseout', () => closeBtn.style.color = '#94A3B8');
        closeBtn.onclick = closeToast;
        toast.appendChild(closeBtn);
    }
    
    container.appendChild(toast);
    
    toast.offsetHeight; // 리플로우 트리거
    
    toast.style.opacity = '1';
    toast.style.transform = 'translateY(0)';
    
    if (autoClose) {
        setTimeout(closeToast, 3000);
    }
}

/**
 * KIS 순위분석 데이터를 타입에 맞게 포맷팅합니다.
 */
function formatValueByType(val, colSpec, item) {
    if (val === undefined || val === null || (typeof val === 'string' && val.trim() === '')) {
        return '-';
    }
    
    switch (colSpec.type) {
        case 'price':
            const price = Math.round(parseFloat(val));
            if (isNaN(price)) return '-';
            
            let formattedPrice = price.toLocaleString();
            if (colSpec.signKey) {
                const signVal = String(item[colSpec.signKey] || '');
                let signText = '';
                let colorClass = '';
                if (signVal === '1' || signVal === '2') {
                    signText = '▲';
                    colorClass = 'bull';
                } else if (signVal === '4' || signVal === '5') {
                    signText = '▼';
                    colorClass = 'bear';
                } else if (signVal === '3') {
                    signText = '';
                    colorClass = '';
                }
                
                if (colSpec.key.includes('vrss') || colSpec.key.includes('diff')) {
                    return `<span class="${colorClass}" style="font-weight: bold;">${signText}${formattedPrice}</span>`;
                }
            }
            return formattedPrice;
            
        case 'integer':
            const intVal = Math.round(parseFloat(val));
            return isNaN(intVal) ? '-' : intVal.toLocaleString();
            
        case 'percent':
            const pct = parseFloat(val);
            return isNaN(pct) ? '-' : pct.toFixed(2) + '%';
            
        case 'date':
            const s = String(val).trim();
            if (s.length === 8) {
                return `${s.substring(0, 4)}-${s.substring(4, 6)}-${s.substring(6, 8)}`;
            }
            return s;
            
        case 'marketDiv':
            const div = String(val).trim();
            if (div === 'J') return '코스피';
            if (div === 'Q') return '코스닥';
            return div;
            
        case 'rate':
            const rate = parseFloat(val);
            if (isNaN(rate)) return '-';
            
            let sign = '';
            const signVal = colSpec.signKey ? String(item[colSpec.signKey]) : '';
            if (signVal === '1' || signVal === '2') {
                sign = '+';
            } else if (signVal === '4' || signVal === '5') {
                if (rate > 0) {
                    sign = '-';
                }
            } else {
                if (rate > 0) sign = '+';
            }
            
            let colorClass = '';
            if (rate > 0 || sign === '+') {
                colorClass = 'bull';
            } else if (rate < 0 || sign === '-') {
                colorClass = 'bear';
            }
            
            const formattedVal = Math.abs(rate).toFixed(2) + '%';
            return `<span class="${colorClass}" style="font-weight: bold;">${sign}${formattedVal}</span>`;
            
        case 'text':
        default:
            return String(val);
    }
}

/**
 * 타임스탬프 값을 읽기 쉬운 날짜 시간 포맷으로 변환 (밀리초 단위 포함)
 * @param {number} ts - 타임스탬프 (초 혹은 밀리초)
 * @returns {string} 포맷팅된 일시 문자열 (YYYY-MM-DD HH:mm:ss.SSS)
 */
function formatTimestamp(ts) {
    if (!ts) return '-';
    let ms;
    
    if (typeof ts === 'string') {
        const trimmed = ts.trim();
        if (/^\d+$/.test(trimmed)) {
            const num = parseInt(trimmed, 10);
            ms = num < 10000000000 ? num * 1000 : num;
        } else {
            const isoStr = trimmed.includes(' ') ? trimmed.replace(' ', 'T') : trimmed;
            const parsed = Date.parse(isoStr);
            if (!isNaN(parsed)) {
                ms = parsed;
            } else {
                return ts;
            }
        }
    } else if (typeof ts === 'number') {
        ms = ts < 10000000000 ? ts * 1000 : ts;
    } else {
        return '-';
    }
    
    const d = new Date(ms);
    if (isNaN(d.getTime())) return ts;

    const pad = (n) => String(n).padStart(2, '0');
    const padMs = (n) => String(n).padStart(3, '0');
    
    const yyyy = d.getFullYear();
    const mm = pad(d.getMonth() + 1);
    const dd = pad(d.getDate());
    const hh = pad(d.getHours());
    const mi = pad(d.getMinutes());
    const ss = pad(d.getSeconds());
    const msec = padMs(d.getMilliseconds());
    
    return `${yyyy}-${mm}-${dd} ${hh}:${mi}:${ss}`;
}

// 전역 바인딩
window.showToast = showToast;
window.formatValueByType = formatValueByType;
window.formatPrice = formatPrice;
window.formatVolume = formatVolume;
window.formatTooltipVolume = formatTooltipVolume;
window.formatRate = formatRate;
window.formatTimestamp = formatTimestamp;

/**
 * 트레이딩 테마 색상 상수
 */
const BULL_COLOR = '#FF4B4B';
const BEAR_COLOR = '#0072FF';
const NEUTRAL_COLOR = '#64748B';
const SUCCESS_COLOR = '#4caf50';

/**
 * 값의 부호(양수/음수/보합)에 따른 색상을 반환
 * @param {number} value - 등락률 또는 변화량
 * @returns {string} 색상 코드
 */
function getTrendColor(value) {
    const num = parseFloat(value) || 0;
    if (num > 0) return BULL_COLOR;
    if (num < 0) return BEAR_COLOR;
    return NEUTRAL_COLOR;
}

/**
 * 현재가와 이전가의 비교를 통해 색상을 반환
 * @param {number} current - 현재 가격
 * @param {number} prev - 이전 가격
 * @returns {string} 색상 코드
 */
function getPriceColor(current, prev) {
    const currNum = parseFloat(current) || 0;
    const prevNum = parseFloat(prev) || 0;
    if (currNum > prevNum) return BULL_COLOR;
    if (currNum < prevNum) return BEAR_COLOR;
    return NEUTRAL_COLOR;
}

window.BULL_COLOR = BULL_COLOR;
window.BEAR_COLOR = BEAR_COLOR;
window.NEUTRAL_COLOR = NEUTRAL_COLOR;
window.SUCCESS_COLOR = SUCCESS_COLOR;
window.getTrendColor = getTrendColor;
window.getPriceColor = getPriceColor;



