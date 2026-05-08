# Design Guidelines - Upbit Scalping System

본 문서는 시스템의 UI/UX 일관성을 유지하기 위한 디자인 가이드라인을 정의합니다.

## 1. 디자인 철학 (Design Philosophy)
- **Professional & Clean**: 트레이딩 툴 특성에 맞게 불필요한 장식을 배제하고 데이터 시인성을 최우선으로 합니다.
- **Real-time Focused**: 실시간 데이터 변화가 즉각적으로 눈에 띄도록 대비가 명확한 배색을 사용합니다.
- **Dark Mode First**: 장시간 모니터링 시 피로도를 줄이기 위해 다크 테마를 기본으로 합니다.

## 2. 색상 체계 (Color Palette)

### 2.1. 메인 테마
- **Background**: `#0E1117` (Deep Dark) - 대시보드 배경
- **Surface**: `#262730` (Medium Dark) - 카드 및 위젯 배경
- **Text**: `#FAFAFA` (Primary), `#AFAFAF` (Secondary)

### 2.2. 트레이딩 상태 색상
- **Bull (상승/매수)**: `#FF4B4B` (Vibrant Red) - 양봉, 가격 상승, BUY 신호
- **Bear (하락/매도)**: `#0072FF` (Vibrant Blue) - 음봉, 가격 하락, SELL 신호
- **Neutral (보합)**: `#7A7A7A` (Gray)

### 2.3. 기술 지표 색상
- **SMA (20)**: `#FFA500` (Orange)
- **Bollinger Bands**: `rgba(173, 216, 230, 0.4)` (Light Blue)
- **RSI Line**: `#FF00FF` (Magenta)
- **Volume Bar**: 상승 시 Bull Color, 하락 시 Bear Color 적용

## 3. 타이포그래피 (Typography)
- **폰트**: 고정폭(Monospace)과 고딕(Sans-serif)의 조화
  - 수치 데이터: `Roboto Mono`, `Source Code Pro` 등 가독성 좋은 고정폭 폰트 권장
  - 일반 텍스트: `Pretendard`, `Noto Sans KR`

## 4. UI 컴포넌트 가이드

### 4.1. 차트 (Plotly)
- `template="plotly_dark"`를 기본으로 사용합니다.
- 그리드 라인은 최소화하되, `linestyle='--'`, `alpha=0.2`로 흐리게 표시합니다.
- 차트 내 여백(Margin)을 최적화하여 좁은 화면에서도 데이터가 최대한 많이 보이도록 합니다.

### 4.2. 위젯 (Streamlit)
- **Metrics**: 상승/하락 변화량을 반드시 포함하여 현재 추세를 알 수 있게 합니다.
- **Sidebar**: 설정 및 필터 요소는 사이드바로 분리하여 메인 영역의 데이터 공간을 확보합니다.

## 5. 아이콘 활용
- **상태 표시**: 
  - 🚀 (시스템 시작), ⏹️ (시스템 중단), 📊 (분석), ⚙️ (설정)
  - 매수/매도 타점: `^` (Triangle Up), `v` (Triangle Down)
