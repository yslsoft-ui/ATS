/**
 * ViewRouter - UI 라우터 및 뷰 제어 모듈
 * 
 * 각 화면(뷰)의 노출 제어, 메뉴 아이템 활성화 상태 처리, 
 * 그리고 탭 전환 시의 라이프사이클(초기화 함수) 실행을 전담합니다.
 */
const ViewRouter = (() => {
    let currentActiveViewId = 'monitoring-view'; // 기본 활성 뷰
    let routes = {};

    /**
     * 라우터 초기화
     * @param {Object} config - 라우팅 정보 객체 (예: { routes: { 'view-id': callback } })
     */
    function initialize(config = {}) {
        if (config.routes) {
            routes = { ...routes, ...config.routes };
        }
        const menuItems = document.querySelectorAll('.menu-item');

        // 사이드바 메뉴 클릭 이벤트 리스너 등록
        menuItems.forEach((item) => {
            item.addEventListener('click', () => {
                const viewId = item.getAttribute('data-view');
                if (!viewId) return;
                navigateTo(viewId);
            });
        });

        // 초기 실행 시점에 활성화된 탭의 display 동기화
        const activeItem = document.querySelector('.menu-item.active');
        if (activeItem) {
            const initialViewId = activeItem.getAttribute('data-view');
            if (initialViewId) {
                currentActiveViewId = initialViewId;
            }
        }
    }

    /**
     * 특정 뷰에 대한 진입 콜백 등록
     * @param {string} viewId - 대상 뷰 컨테이너 DOM ID
     * @param {function} callback - 진입 시 실행할 함수
     */
    function registerRoute(viewId, callback) {
        routes[viewId] = callback;
    }

    /**
     * 특정 뷰로 화면 전환
     * @param {string} viewId - 대상 뷰 컨테이너 DOM ID
     */
    function navigateTo(viewId) {
        const menuItems = document.querySelectorAll('.menu-item');
        const viewIds = Object.keys(routes);

        // 1. 메뉴 활성화 active 클래스 토글
        menuItems.forEach((item) => {
            if (item.getAttribute('data-view') === viewId) {
                item.classList.add('active');
            } else {
                item.classList.remove('active');
            }
        });

        // 2. 각 뷰 컨테이너의 display 상태 제어 (block / none)
        viewIds.forEach((id) => {
            const viewEl = document.getElementById(id);
            if (viewEl) {
                viewEl.style.display = (id === viewId) ? 'block' : 'none';
            }
        });

        currentActiveViewId = viewId;

        // 3. 뷰 진입 콜백(초기화 함수)이 존재한다면 실행
        if (routes[viewId] && typeof routes[viewId] === 'function') {
            routes[viewId]();
        }
    }

    /**
     * 현재 노출 중인 활성 뷰 ID 반환
     * @returns {string}
     */
    function getActiveView() {
        return currentActiveViewId;
    }

    return {
        initialize,
        navigateTo,
        getActiveView,
        registerRoute
    };
})();

// 전역 노출 설정
window.ViewRouter = ViewRouter;
