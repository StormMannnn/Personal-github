#!/bin/bash
# vLLM 流式代理 — 启动脚本
# 用法: bash start_proxy.sh [start|stop|restart|status]

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONDA_ENV="llm-vllm"
PORT=8001
LOG_FILE="$SCRIPT_DIR/proxy.log"
PID_FILE="$SCRIPT_DIR/proxy.pid"

# 激活 conda 环境
source "$HOME/miniconda3/etc/profile.d/conda.sh" 2>/dev/null || true
conda activate "$CONDA_ENV"

start() {
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        echo "代理已在运行，PID: $(cat "$PID_FILE")"
        return 1
    fi
    echo "启动流式代理 (端口: $PORT, 环境: $CONDA_ENV)..."
    nohup python3 "$SCRIPT_DIR/stream_proxy.py" > "$LOG_FILE" 2>&1 &
    echo $! > "$PID_FILE"
    sleep 2
    if kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        echo "✅ 代理已启动 — PID: $(cat "$PID_FILE")"
        echo "   curl 调用: curl -N http://$(hostname -I | awk '{print $1}'):$PORT/ask -H 'Content-Type: application/json' -d '{\"question\":\"你好\"}'"
    else
        echo "❌ 代理启动失败，查看日志: tail -f $LOG_FILE"
        rm -f "$PID_FILE"
        return 1
    fi
}

stop() {
    if [ ! -f "$PID_FILE" ]; then
        echo "代理未运行"
        return 0
    fi
    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        echo "停止代理 (PID: $PID)..."
        kill "$PID"
        sleep 1
        if kill -0 "$PID" 2>/dev/null; then
            kill -9 "$PID" 2>/dev/null
        fi
        echo "✅ 代理已停止"
    fi
    rm -f "$PID_FILE"
}

restart() {
    stop
    sleep 1
    start
}

status() {
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        echo "✅ 代理运行中 — PID: $(cat "$PID_FILE")"
        echo "   端口: $PORT"
        echo "   日志: $LOG_FILE"
        echo "   健康: $(curl -s http://127.0.0.1:$PORT/health 2>/dev/null || echo '无响应')"
    else
        echo "❌ 代理未运行"
    fi
}

case "${1:-start}" in
    start)   start;;
    stop)    stop;;
    restart) restart;;
    status)  status;;
    *)       echo "用法: $0 {start|stop|restart|status}"; exit 1;;
esac
