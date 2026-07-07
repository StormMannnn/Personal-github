#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="/home/oem/llmDeploy/llm-benchmark-kit"
RESULT_DIR="${PROJECT_DIR}/results/transformers_bf16_nohup_8192fix"
LOG_DIR="${RESULT_DIR}/logs"

mkdir -p "${LOG_DIR}"

source "${HOME}/miniconda3/etc/profile.d/conda.sh"
conda activate llm-transformers
cd "${PROJECT_DIR}"

echo "启动 Transformers BF16 压测，结果写入：${RESULT_DIR}"

nohup bash -lc "
source ~/miniconda3/etc/profile.d/conda.sh
conda activate llm-transformers
cd '${PROJECT_DIR}'

python scripts/benchmark.py \
  --framework transformers_bf16 \
  --output-dir results/transformers_bf16_nohup_8192fix
" > "${LOG_DIR}/benchmark.log" 2>&1 &

echo "$!" > "${RESULT_DIR}/benchmark.pid"
echo "压测已启动，PID: $(cat "${RESULT_DIR}/benchmark.pid")"
echo "查看压测日志：tail -f ${LOG_DIR}/benchmark.log"
