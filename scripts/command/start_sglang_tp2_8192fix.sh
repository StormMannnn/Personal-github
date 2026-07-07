#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="/home/oem/llmDeploy/llm-benchmark-kit"
RESULT_DIR="${PROJECT_DIR}/results/sglang_tp2_detailed_8192fix"
LOG_DIR="${RESULT_DIR}/logs"
MODEL_ID="/home/oem/llmDeploy/models/huggingface/DeepSeek-R1-Distill-Qwen-14B"
SERVED_NAME="deepseek-r1-distill-qwen-14b"
PORT="8109"

mkdir -p "${LOG_DIR}"

source "${HOME}/miniconda3/etc/profile.d/conda.sh"
conda activate llm-sglang
cd "${PROJECT_DIR}"

if ss -ltnp 2>/dev/null | grep -q ":${PORT} "; then
  echo "端口 ${PORT} 已被占用，请先停止旧 SGLang 服务后再启动。"
  ss -ltnp 2>/dev/null | grep ":${PORT} " || true
  exit 1
fi

echo "启动 SGLang TP=2 服务，日志写入：${LOG_DIR}/sglang.log"

nohup bash -lc "
source ~/miniconda3/etc/profile.d/conda.sh
conda activate llm-sglang
cd '${PROJECT_DIR}'

export MODEL_ID='${MODEL_ID}'
export SERVED_NAME='${SERVED_NAME}'
export CUDA_HOME=\"\$CONDA_PREFIX/lib/python3.10/site-packages/nvidia/cu13\"
export CUDA_PATH=\"\$CUDA_HOME\"
export PATH=\"\$CUDA_HOME/bin:\$PATH\"
export CUDA_INCLUDE=\"\$CUDA_HOME/include\"
export CCCL_INCLUDE=\"\$CONDA_PREFIX/lib/python3.10/site-packages/flashinfer/data/cccl/libcudacxx/include\"
mkdir -p \"\$CUDA_HOME/lib64\"
if [ ! -e \"\$CUDA_HOME/lib64/libcudart.so\" ]; then
  ln -s \"\$CUDA_HOME/lib/libcudart.so.13\" \"\$CUDA_HOME/lib64/libcudart.so\"
fi
export CPATH=\"\$CCCL_INCLUDE:\$CUDA_INCLUDE:\${CPATH:-}\"
export CPLUS_INCLUDE_PATH=\"\$CCCL_INCLUDE:\$CUDA_INCLUDE:\${CPLUS_INCLUDE_PATH:-}\"
export LD_LIBRARY_PATH=\"\$CUDA_HOME/lib:\$CUDA_HOME/lib64:\${LD_LIBRARY_PATH:-}\"
export LIBRARY_PATH=\"\$CUDA_HOME/lib:\$CUDA_HOME/lib64:\${LIBRARY_PATH:-}\"
export MASTER_ADDR=127.0.0.1
export GLOO_SOCKET_IFNAME=lo
export NCCL_SOCKET_IFNAME=lo
export NCCL_P2P_DISABLE=1

CUDA_VISIBLE_DEVICES=0,1 python -m sglang.launch_server \
  --model-path \"\$MODEL_ID\" \
  --served-model-name \"\$SERVED_NAME\" \
  --host 0.0.0.0 \
  --port ${PORT} \
  --dtype bfloat16 \
  --context-length 9216 \
  --tensor-parallel-size 2 \
  --mem-fraction-static 0.84 \
  --attention-backend triton \
  --sampling-backend pytorch \
  --disable-custom-all-reduce \
  --trust-remote-code
" > "${LOG_DIR}/sglang.log" 2>&1 &

echo "$!" > "${RESULT_DIR}/sglang.pid"
echo "SGLang 已提交后台启动，PID: $(cat "${RESULT_DIR}/sglang.pid")"
echo "查看日志：tail -f ${LOG_DIR}/sglang.log"
