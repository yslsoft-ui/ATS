#!/bin/bash

# 설정 변수
TMUX_SESSION="ats"
VENV_PATH="./venv"
export PYTHONPATH=.

# 1. 종료 처리용 trap 설정 (일반 실행 모드 시)
PIDS=()
cleanup() {
    echo "========================================="
    echo "정리 작업을 시작합니다. 백그라운드 프로세스를 모두 종료합니다..."
    echo "========================================="
    for pid in "${PIDS[@]}"; do
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid"
            wait "$pid" 2>/dev/null
        fi
    done
    # IPC stale 소켓 정리
    rm -rf data/ipc/*.ipc 2>/dev/null
    echo "정리 완료."
    exit 0
}

# 2. 가상환경 검증 및 활성화
PYTHON_EXEC="python"
if [ -d "$VENV_PATH" ]; then
    echo "가상환경 활성화: $VENV_PATH"
    source "$VENV_PATH"/bin/activate
    PYTHON_EXEC="$VENV_PATH/bin/python"
else
    echo "경고: 가상환경($VENV_PATH)이 감지되지 않았습니다. 현재 환경에서 스크립트를 계속합니다."
fi

# 3. IPC 디렉토리 보장
mkdir -p data/ipc

# 4. 실행 인자 파싱
USE_TMUX=false
USE_RELOAD=false

for arg in "$@"; do
    case "$arg" in
        --tmux)
            if command -v tmux &> /dev/null; then
                USE_TMUX=true
            else
                echo "경고: tmux가 시스템에 설치되어 있지 않습니다. 일반 멀티 프로세스 모드로 기동합니다."
            fi
            ;;
        --reload)
            USE_RELOAD=true
            ;;
    esac
done

# 웹 서버 실행 커맨드 조립
if [ "$USE_RELOAD" = true ]; then
    echo "웹 서버 핫 리로드(--reload) 모드 활성화"
    WEB_CMD="PYTHONPATH=. $PYTHON_EXEC -m uvicorn src.server.main:app --host 0.0.0.0 --port 8000 --reload"
else
    WEB_CMD="PYTHONPATH=. $PYTHON_EXEC src/server/main.py"
fi

# 5. 실행
if [ "$USE_TMUX" = true ]; then
    echo "========================================="
    echo "tmux 세션 [$TMUX_SESSION] 을 시작합니다..."
    echo "========================================="
    
    # 기존 세션이 있다면 종료
    tmux kill-session -t "$TMUX_SESSION" 2>/dev/null
    
    # 1. 세션 생성 및 첫 번째 윈도우(Web API) 실행
    tmux new-session -d -s "$TMUX_SESSION" -n "web" "$WEB_CMD"
    
    # 2. 두 번째 윈도우(Collector) 생성 및 실행
    tmux new-window -t "$TMUX_SESSION":1 -n "collector" "PYTHONPATH=. $PYTHON_EXEC src/collector_daemon.py; exec bash"
    
    # 3. 세 번째 윈도우(Strategy) 생성 및 실행
    tmux new-window -t "$TMUX_SESSION":2 -n "strategy" "PYTHONPATH=. $PYTHON_EXEC src/strategy_daemon.py; exec bash"
    
    # 첫 번째 윈도우로 포커스
    tmux select-window -t "$TMUX_SESSION":0
    
    # 세션 연결
    tmux attach-session -t "$TMUX_SESSION"
else
    # 일반 백그라운드 프로세스 기동 모드
    trap cleanup SIGINT SIGTERM EXIT
    
    echo "========================================="
    echo "1. 실시간 데이터 수집기(Collector Daemon) 기동..."
    $PYTHON_EXEC src/collector_daemon.py &
    PIDS+=($!)
    sleep 1 # 소켓 바인딩 시간을 고려해 약간의 딜레이
    
    echo "2. 전략 엔진 데몬(Strategy Daemon) 기동..."
    $PYTHON_EXEC src/strategy_daemon.py &
    PIDS+=($!)
    sleep 1
    
    echo "3. 웹/API 서버 기동..."
    if [ "$USE_RELOAD" = true ]; then
        PYTHONPATH=. $PYTHON_EXEC -m uvicorn src.server.main:app --host 0.0.0.0 --port 8000 --reload &
    else
        $PYTHON_EXEC src/server/main.py &
    fi
    PIDS+=($!)
    
    echo "========================================="
    echo "모든 프로세스가 기동되었습니다. 종료하려면 Ctrl+C를 누르세요."
    echo "========================================="
    
    # 프로세스들이 정상 가동되는 동안 대기
    wait
fi
