# Issue tracker: Local Markdown

이 프로젝트의 작업 내역(Issues)과 요구사항 정의서(PRDs)는 `scratch/` 디렉토리 내의 마크다운 파일로 관리됩니다.

## 규칙 (Conventions)

- **기능별 디렉토리**: 각 기능은 `.scratch/<feature-slug>/` 폴더에 위치합니다.
- **PRD**: 기능의 상세 기획은 `.scratch/<feature-slug>/PRD.md` 파일에 작성합니다.
- **이슈(작업 단위)**: 실제 구현 작업은 `.scratch/<feature-slug>/issues/<NN>-<slug>.md` 형식으로 작성하며, `01`부터 번호를 부여합니다.
- **심사 상태 (Triage State)**: 각 이슈 파일 상단에 `Status:` 라인을 두어 현재 상태를 기록합니다. (사용 가능한 라벨은 `triage-labels.md` 참고)
- **코멘트 및 히스토리**: 대화 내용이나 피드백은 파일 하단의 `## Comments` 섹션에 추가합니다.

## "이슈 트래커에 게시" 하라는 요청을 받았을 때

`scratch/<feature-slug>/` 아래에 새 파일을 생성합니다 (필요한 경우 디렉토리 생성).

## "관련 티켓을 가져오라"는 요청을 받았을 때

지정된 경로의 파일을 읽습니다. 사용자가 직접 경로를 알려주거나 이슈 번호를 전달할 것입니다.
