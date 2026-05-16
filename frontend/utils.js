/**
 * Upbit Terminal 유틸리티 함수 모음
 */

/**
 * 숫자를 가격 포맷에 맞춰 변환 (천 단위 콤마 등)
 * @param {number} price - 가격
 * @returns {string} 포맷팅된 가격 문자열
 */
function formatPrice(price) {
    if (price === undefined || price === null) return '0';
    if (price >= 1000000) return (price / 1000000).toFixed(2) + 'M';
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
 * API 호출을 위한 공통 헬퍼 함수
 * @param {string} url - API 엔드포인트
 * @param {object} options - Fetch 옵션
 * @returns {Promise<any>} API 응답 데이터
 */
async function fetchAPI(url, options = {}) {
    try {
        const response = await fetch(url, options);
        if (!response.ok) {
            throw new Error(`API 오류: ${response.status} ${response.statusText}`);
        }
        return await response.json();
    } catch (error) {
        console.error(`[API Error] ${url}:`, error);
        throw error;
    }
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
