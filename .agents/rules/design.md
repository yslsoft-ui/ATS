---
trigger: always_on
---

## design

이 프로젝트의 모든 UI/UX 및 시각적 요소는 다음 가이드를 엄격히 준수해야 합니다.

### 1. 디자인 철학 (Design Philosophy)
- **Aesthetic Excellence**: 전문가용 터미널 느낌의 다크 테마와 세련된 가독성을 지향합니다.
- **Professional & Clean**: 불필요한 장식을 배제하고 데이터 시인성을 최우선으로 합니다.
- **Real-time Focused**: 실시간 데이터 변화가 즉각적으로 눈에 띄도록 대비가 명확한 배색을 사용합니다.

### 2. 색상 체계 (Color Palette - Recommended)
#### 2.1. 메인 테마
- **Background**: `#0F172A` (Deep Navy) - 전체 배경
- **Surface**: `#1E293B` (Slate Gray) - 카드 및 위젯 배경
- **Text**: `#F8FAFC` (Slate 50), `#94A3B8` (Slate 400)

#### 2.2. 트레이딩 상태 색상
- **Bull (상승/매수)**: `#FF4B4B` (Vibrant Red) - 양봉, 가격 상승, BUY 신호
- **Bear (하락/매도)**: `#0072FF` (Vibrant Blue) - 음봉, 가격 하락, SELL 신호
- **Neutral (보합)**: `#64748B`

#### 2.3. 기술 지표 색상
- **SMA (20)**: `#F59E0B` (Amber)
- **Bollinger Bands**: `rgba(148, 163, 184, 0.2)` (Slate)
- **RSI Line**: `#D946EF` (Fuchsia)
- **Volume Bar**: 상승 시 Bull Color, 하락 시 Bear Color 적용

### 3. 타이포그래피 (Typography)
- **수치 데이터**: `Roboto Mono`, `Source Code Pro` (고정폭)
- **일반 텍스트**: `Pretendard`, `Inter`

### 4. UI 컴포넌트 가이드
- **Chart**: `plotly_dark` 템플릿 사용, 여백 최소화. 그리드 라인 `linestyle='--'`, `alpha=0.1`.
- **Metrics**: 카드 형태의 레이아웃에 그림자 효과(`box-shadow`)를 최소화하고 보더(`border`)로 구분.
- **Icons**: 🚀 (시작), ⏹️ (중단), 📊 (분석), ⚙️ (설정) 사용.

이 규칙은 모든 프론트엔드 작업 시 저의 기본 지침으로 작동합니다.