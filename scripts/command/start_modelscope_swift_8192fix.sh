#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="/home/oem/llmDeploy/llm-benchmark-kit"
RESULT_DIR="${PROJECT_DIR}/results/modelscope_swift_nohup_8192fix"
LOG_DIR="${RESULT_DIR}/logs"
MODEL_ID="/home/oem/llmDeploy/models/huggingface/DeepSeek-R1-Distill-Qwen-14B"
SERVED_NAME="deepseek-r1-distill-qwen-14b"
PORT="8103"

mkdir -p "${LOG_DIR}"

source "${HOME}/miniconda3/etc/profile.d/conda.sh"
conda activate llm-modelscope
cd "${PROJECT_DIR}"

if ss -ltnp 2>/dev/null | grep -q ":${PORT} "; then
  echo "端口 ${PORT} 已被占用，请先停止旧 ModelScope / ms-swift 服务后再启动。"
  ss -ltnp 2>/dev/null | grep ":${PORT} " || true
  exit 1
fi

echo "启动 ModelScope ms-swift transformers 后端服务，日志写入：${LOG_DIR}/modelscope.log"

nohup bash -lc "
source ~/miniconda3/etc/profile.d/conda.sh
conda activate llm-modelscope
cd '${PROJECT_DIR}'

export MODEL_ID='${MODEL_ID}'
export SERVED_NAME='${SERVED_NAME}'
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True

CUDA_VISIBLE_DEVICES=0,1 swift deploy \
  --model \"\$MODEL_ID\" \
  --served-model-name \"\$SERVED_NAME\" \
  --host 0.0.0.0 \
  --port ${PORT} \
  --infer-backend transformers \
  --torch-dtype bfloat16 \
  --device-map auto \
  --max-model-len 8704 \
  --attn-impl sdpa
" > "${LOG_DIR}/modelscope.log" 2>&1 &

echo "$!" > "${RESULT_DIR}/modelscope.pid"
echo "ModelScope ms-swift 已提交后台启动，PID: $(cat "${RESULT_DIR}/modelscope.pid")"
echo "查看日志：tail -f ${LOG_DIR}/modelscope.log"
