#!/bin/bash
# -*- coding: utf-8 -*-

TMUX_SESSION="ats_rehearsal"
VENV_PATH="./venv"
export PYTHONPATH=.

# 가상환경 활성화
if [ -d "$VENV_PATH" ]; then
    PYTHON_EXEC="$VENV_PATH/bin/python"
else
    PYTHON_EXEC="python"
fi

check_safety() {
    echo "========================================="
    echo "안전 Preflight 체크를 수행 중..."
    echo "========================================="
    
    $PYTHON_EXEC -c "
import yaml
try:
    with open('config/settings.yaml', 'r', encoding='utf-8') as f:
        cfg = yaml.safe_load(f).get('system', {})
except Exception as e:
    print(f'[FAIL] 설정 파일을 읽을 수 없습니다: {e}')
    exit(1)

errors = []
if cfg.get('live_trading_enabled') is not False:
    errors.append('live_trading_enabled가 false가 아닙니다 (현재: ' + str(cfg.get('live_trading_enabled')) + ')')
if cfg.get('auto_strategy_promotion_enabled') is not False:
    errors.append('auto_strategy_promotion_enabled가 false가 아닙니다 (현재: ' + str(cfg.get('auto_strategy_promotion_enabled')) + ')')
if cfg.get('operation_mode') != 'shadow':
    errors.append('operation_mode가 shadow가 아닙니다 (현재: ' + str(cfg.get('operation_mode')) + ')')
if cfg.get('girs_shadow_mode') is not True:
    errors.append('girs_shadow_mode가 true가 아닙니다 (현재: ' + str(cfg.get('girs_shadow_mode')) + ')')

if errors:
    for err in errors:
        print('[FAIL]', err)
    exit(1)
else:
    print('[PASS] 모든 안전 필수 조건 충족완료!')
"
    if [ $? -ne 0 ]; then
        echo "========================================="
        echo "오류: 안전 Preflight 체크 실패! 데몬을 기동하지 않습니다."
        echo "========================================="
        exit 1
    fi
}

start_demons() {
    check_safety
    
    echo "========================================="
    echo "tmux 세션 [$TMUX_SESSION] 을 시작합니다..."
    echo "========================================="
    
    # 기존 세션 정리
    tmux kill-session -t "$TMUX_SESSION" 2>/dev/null
    
    # IPC stale 소켓 정리
    rm -rf data/ipc/*.ipc 2>/dev/null
    
    # 1. Web API 데몬 기동
    tmux new-session -d -s "$TMUX_SESSION" -n "web" "PYTHONPATH=. $PYTHON_EXEC src/server/main.py"
    
    # 2. Collector 데몬 기동
    tmux new-window -t "$TMUX_SESSION":1 -n "collector" "PYTHONPATH=. $PYTHON_EXEC src/collector_daemon.py"
    
    # 3. Strategy 데몬 기동
    tmux new-window -t "$TMUX_SESSION":2 -n "strategy" "PYTHONPATH=. $PYTHON_EXEC src/strategy_daemon.py"
    
    # 4. Evaluation 헬퍼 워커 기동
    tmux new-window -t "$TMUX_SESSION":3 -n "evaluation" "PYTHONPATH=. $PYTHON_EXEC scratch/evaluation_worker.py"
    
    echo "데몬 기동 완료. tmux session: $TMUX_SESSION"
    echo "모니터링을 하려면: tmux attach-session -t $TMUX_SESSION"
}

stop_demons() {
    echo "========================================="
    echo "tmux 세션 [$TMUX_SESSION] 을 종료합니다..."
    echo "========================================="
    
    tmux kill-session -t "$TMUX_SESSION" 2>/dev/null
    
    # IPC stale 소켓 정리
    rm -rf data/ipc/*.ipc 2>/dev/null
    
    echo "종료 완료."
}

case "$1" in
    start)
        start_demons
        ;;
    stop)
        stop_demons
        ;;
    *)
        echo "사용법: $0 {start|stop}"
        exit 1
        ;;
esac
