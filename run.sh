#!/bin/bash

# 설정 변수
TMUX_SESSION="ats"
VENV_PATH="./venv"
export PYTHONPATH=.
export ATS_CONFIG="${ATS_CONFIG:-config/settings.yaml}"

# 1. 가상환경 검증 및 활성화 함수
setup_venv() {
    PYTHON_EXEC="python"
    if [ -d "$VENV_PATH" ]; then
        echo "가상환경 활성화: $VENV_PATH"
        source "$VENV_PATH"/bin/activate
        PYTHON_EXEC="$VENV_PATH/bin/python"
    else
        echo "경고: 가상환경($VENV_PATH)이 감지되지 않았습니다. 현재 환경에서 스크립트를 계속합니다."
    fi
}

# 2. 데이터베이스 사용 준비 및 마이그레이션 단독 실행 함수 (동시성 락 방지)
init_db_single() {
    echo "========================================="
    echo "데이터베이스 사용 준비 및 마이그레이션을 진행합니다..."
    echo "========================================="
    PYTHONPATH=. $PYTHON_EXEC -c "
import asyncio
import sys
from src.config.manager import ConfigManager
from src.database.schema import init_db
async def run():
    config = ConfigManager('${ATS_CONFIG}')
    db_path = config.get('system.db_path', 'data/backtest.db')
    await init_db(db_path)
asyncio.run(run())
"
    if [ $? -ne 0 ]; then
        echo "오류: 데이터베이스 사용 준비에 실패했습니다. 기동을 중단합니다."
        return 1
    fi
    echo "데이터베이스 사용 준비 완료."
    return 0
}

# 3. start 서브커맨드 구현 (tmux 세션 기동)
start_ats() {
    setup_venv

    # 3.1. 중복 세션 기동 차단
    if tmux has-session -t "$TMUX_SESSION" 2>/dev/null; then
        echo "========================================="
        echo "오류: '$TMUX_SESSION' tmux 세션이 이미 실행 중입니다."
        echo "기동을 중단합니다. 재시작하려면 './run.sh restart'를 실행하거나,"
        echo "종료하려면 './run.sh stop'을 실행하세요."
        echo "========================================="
        exit 1
    fi

    # 3.2. IPC 디렉토리 보장
    mkdir -p data/ipc

    # 3.3. DB 단독 마이그레이션 선행 기동
    init_db_single
    if [ $? -ne 0 ]; then
        exit 1
    fi

    # 3.4. 웹 서버 실행 커맨드 조립
    if [ "$USE_RELOAD" = true ]; then
        echo "웹 서버 핫 리로드(--reload) 모드 활성화"
        WEB_CMD="PYTHONPATH=. $PYTHON_EXEC -m uvicorn src.server.main:app --host 0.0.0.0 --port 8000 --reload"
    else
        WEB_CMD="PYTHONPATH=. $PYTHON_EXEC src/server/main.py"
    fi

    # 3.5. tmux 세션 시작
    echo "========================================="
    echo "tmux 세션 [$TMUX_SESSION] 을 시작합니다..."
    echo "========================================="

    # Web API 윈도우 실행
    tmux new-session -d -s "$TMUX_SESSION" -n "web" "$WEB_CMD"

    # Collector 윈도우 생성 및 실행
    tmux new-window -t "$TMUX_SESSION":1 -n "collector" "PYTHONPATH=. $PYTHON_EXEC src/collector_daemon.py"

    # Strategy 윈도우 생성 및 실행
    tmux new-window -t "$TMUX_SESSION":2 -n "strategy" "PYTHONPATH=. $PYTHON_EXEC src/strategy_daemon.py"

    # Evaluation 윈도우 생성 및 실행
    tmux new-window -t "$TMUX_SESSION":3 -n "evaluation" "PYTHONPATH=. $PYTHON_EXEC src/shadow_eval_daemon.py"

    # Cleanup 윈도우 생성 및 실행
    tmux new-window -t "$TMUX_SESSION":4 -n "cleanup" "PYTHONPATH=. $PYTHON_EXEC src/market_cleanup_daemon.py"

    # 첫 번째 윈도우(web)로 포커스 지정
    tmux select-window -t "$TMUX_SESSION":0

    # 세션 연결 (대화형 TTY 환경인 경우에만 세션 부착 진행)
    if [ -t 0 ]; then
        tmux attach-session -t "$TMUX_SESSION"
    else
        echo "비대화형(Non-interactive) 환경이므로 tmux 세션 부착을 건너뜁니다."
    fi
}

# 4. stop 서브커맨드 구현 (Graceful Shutdown)
stop_ats() {
    # tmux 세션 존재 여부 1차 체크
    if ! tmux has-session -t "$TMUX_SESSION" 2>/dev/null; then
        echo "알림: '$TMUX_SESSION' tmux 세션이 실행 중이 아닙니다."
        rm -rf data/ipc/*.ipc 2>/dev/null
        return 0
    fi

    echo "========================================="
    echo "ats 세션의 모든 프로세스에 종료 신호(SIGINT/Ctrl+C)를 전송합니다..."
    echo "========================================="

    # 4.1. 모든 pane에 Ctrl+C 전송하여 Graceful Shutdown 콜백 트리거 유도
    tmux list-panes -a -t "$TMUX_SESSION" -F '#{session_name}:#{window_index}.#{pane_index}' | xargs -I {} tmux send-keys -t {} C-c

    # 4.2. 파이썬 데몬 및 uvicorn 프로세스 잔존 여부 확인
    echo "프로세스가 안전하게 종료될 때까지 대기합니다 (최대 10초)..."
    local pids_alive=true
    for i in {1..10}; do
        if ! pgrep -f "src/collector_daemon.py|src/strategy_daemon.py|src/shadow_eval_daemon.py|src/market_cleanup_daemon.py|src/server/main.py|uvicorn" > /dev/null; then
            pids_alive=false
            echo "모든 파이썬 데몬 및 웹 서버 프로세스가 그래이스풀하게 종료되었습니다."
            break
        fi
        sleep 1
    done

    # 4.3. 시간 내에 종료되지 않은 프로세스가 있다면 pkill 강제 종료
    if [ "$pids_alive" = true ]; then
        echo "경고: 일부 프로세스가 응답하지 않습니다. 강제 종료(SIGKILL)를 수행합니다..."
        pkill -9 -f "src/collector_daemon.py|src/strategy_daemon.py|src/shadow_eval_daemon.py|src/market_cleanup_daemon.py|src/server/main.py|uvicorn" 2>/dev/null
    fi

    # 4.4. 마지막 단계로 남은 tmux 껍데기 세션 최종 정리
    if tmux has-session -t "$TMUX_SESSION" 2>/dev/null; then
        echo "tmux 껍데기 세션 [$TMUX_SESSION] 을 정리합니다."
        tmux kill-session -t "$TMUX_SESSION" 2>/dev/null
    fi

    # IPC stale 소켓 파일 정리
    rm -rf data/ipc/*.ipc 2>/dev/null
    echo "정리 완료."
}

# 5. restart 서브커맨드 구현
restart_ats() {
    echo "========================================="
    echo "시스템 재시작을 진행합니다..."
    echo "========================================="
    stop_ats
    sleep 1.5  # 소켓 바인딩 완전 해제 및 포트 정리 대기
    start_ats
}

# 6. 메인 처리 흐름 및 인자 파싱
COMMAND="${1:-start}"
USE_RELOAD=false

# 서브커맨드 인자 검사 및 시프트
if [[ "$COMMAND" == "start" || "$COMMAND" == "stop" || "$COMMAND" == "restart" ]]; then
    shift 1
else
    # 서브커맨드 없이 실행된 경우 기본값 start로 동작
    COMMAND="start"
fi

# 추가 옵션 파싱 (예: --reload)
for arg in "$@"; do
    case "$arg" in
        --reload)
            USE_RELOAD=true
            ;;
    esac
done

case "$COMMAND" in
    start)
        start_ats
        ;;
    stop)
        stop_ats
        ;;
    restart)
        restart_ats
        ;;
    *)
        echo "사용법: ./run.sh {start|stop|restart} [--reload]"
        exit 1
        ;;
esac
