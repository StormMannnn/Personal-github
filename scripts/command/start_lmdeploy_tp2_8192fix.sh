#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="/home/oem/llmDeploy/llm-benchmark-kit"
RESULT_DIR="${PROJECT_DIR}/results/lmdeploy_tp2_nohup_8192fix"
LOG_DIR="${RESULT_DIR}/logs"
MODEL_ID="/home/oem/llmDeploy/models/huggingface/DeepSeek-R1-Distill-Qwen-14B"
SERVED_NAME="deepseek-r1-distill-qwen-14b"
PORT="8107"

mkdir -p "${LOG_DIR}"

source "${HOME}/miniconda3/etc/profile.d/conda.sh"
conda activate llm-lmdeploy
cd "${PROJECT_DIR}"

if ss -ltnp 2>/dev/null | grep -q ":${PORT} "; then
  echo "端口 ${PORT} 已被占用，请先停止旧 LMDeploy 服务后再启动。"
  ss -ltnp 2>/dev/null | grep ":${PORT} " || true
  exit 1
fi

echo "启动 LMDeploy TP=2 服务，日志写入：${LOG_DIR}/lmdeploy.log"

nohup bash -lc "
source ~/miniconda3/etc/profile.d/conda.sh
conda activate llm-lmdeploy
cd '${PROJECT_DIR}'

export MODEL_ID='${MODEL_ID}'
export SERVED_NAME='${SERVED_NAME}'
export NCCL_WIN_ENABLE=0

CUDA_VISIBLE_DEVICES=0,1 lmdeploy serve api_server \"\$MODEL_ID\" \
  --server-name 0.0.0.0 \
  --server-port ${PORT} \
  --model-name \"\$SERVED_NAME\" \
  --backend turbomind \
  --dtype bfloat16 \
  --tp 2 \
  --session-len 8704 \
  --cache-max-entry-count 0.80 \
  --max-prefill-token-num 8192 \
  --enable-prefix-caching \
  --trust-remote-code
" > "${LOG_DIR}/lmdeploy.log" 2>&1 &

echo "$!" > "${RESULT_DIR}/lmdeploy.pid"
echo "LMDeploy 已提交后台启动，PID: $(cat "${RESULT_DIR}/lmdeploy.pid")"
echo "查看日志：tail -f ${LOG_DIR}/lmdeploy.log"
