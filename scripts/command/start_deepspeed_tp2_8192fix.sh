#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="/home/oem/llmDeploy/llm-benchmark-kit"
RESULT_DIR="${PROJECT_DIR}/results/deepspeed_tp2_nohup_8192fix"
LOG_DIR="${RESULT_DIR}/logs"
MODEL_ID="/home/oem/llmDeploy/models/huggingface/DeepSeek-R1-Distill-Qwen-14B"
SERVED_NAME="deepseek-r1-distill-qwen-14b"
PORT="8110"

mkdir -p "${LOG_DIR}"

source "${HOME}/miniconda3/etc/profile.d/conda.sh"
conda activate llm-deepspeed
cd "${PROJECT_DIR}"

if ss -ltnp 2>/dev/null | grep -q ":${PORT} "; then
  echo "端口 ${PORT} 已被占用，请先停止旧 DeepSpeed 服务后再启动。"
  ss -ltnp 2>/dev/null | grep ":${PORT} " || true
  exit 1
fi

echo "启动 DeepSpeed TP=2 服务，日志写入：${LOG_DIR}/deepspeed.log"

nohup bash -lc "
source ~/miniconda3/etc/profile.d/conda.sh
conda activate llm-deepspeed
cd '${PROJECT_DIR}'

export MODEL_ID='${MODEL_ID}'
export SERVED_NAME='${SERVED_NAME}'
export NCCL_WIN_ENABLE=0
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True

CUDA_VISIBLE_DEVICES=0,1 python server_examples/deepspeed_openai_server.py \
  --model \"\$MODEL_ID\" \
  --served-name \"\$SERVED_NAME\" \
  --host 0.0.0.0 \
  --port ${PORT} \
  --tp-size 1
" > "${LOG_DIR}/deepspeed.log" 2>&1 &

echo "$!" > "${RESULT_DIR}/deepspeed.pid"
echo "DeepSpeed 已提交后台启动，PID: $(cat "${RESULT_DIR}/deepspeed.pid")"
echo "查看日志：tail -f ${LOG_DIR}/deepspeed.log"
