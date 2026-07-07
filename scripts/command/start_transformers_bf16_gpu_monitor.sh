#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="/home/oem/llmDeploy/llm-benchmark-kit"
RESULT_DIR="${PROJECT_DIR}/results/transformers_bf16_nohup_8192fix"
LOG_DIR="${RESULT_DIR}/logs"

mkdir -p "${LOG_DIR}"

source "${HOME}/miniconda3/etc/profile.d/conda.sh"
conda activate llm-transformers
cd "${PROJECT_DIR}"

echo "启动 Transformers BF16 GPU 监控，数据写入：${RESULT_DIR}/gpu_metrics.csv"

nohup bash -lc "
source ~/miniconda3/etc/profile.d/conda.sh
conda activate llm-transformers
cd '${PROJECT_DIR}'

python scripts/gpu_monitor.py \
  --output results/transformers_bf16_nohup_8192fix/gpu_metrics.csv \
  --interval 1 \
  --label transformers_bf16_8192fix
" > "${LOG_DIR}/gpu_monitor.log" 2>&1 &

echo "$!" > "${RESULT_DIR}/gpu_monitor.pid"
echo "GPU 监控已启动，PID: $(cat "${RESULT_DIR}/gpu_monitor.pid")"
echo "查看实时数据：tail -f ${RESULT_DIR}/gpu_metrics.csv"
