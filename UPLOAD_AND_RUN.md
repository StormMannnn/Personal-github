# 上传到双 RTX 5090 服务器并运行

可以。这个目录就是一个独立测试项目，可以整体上传到 5090 服务器。

## 1. 上传项目

在本机执行，替换服务器地址和目标目录：

```bash
rsync -av --exclude '__pycache__' --exclude '.DS_Store' \
  ./llm-benchmark-kit/ oem@10.17.101.201:/home/oem/llmDeploy/llm-benchmark-kit/
```

如果你在当前 Codex 输出目录中执行，源路径是：

```bash
rsync -av --exclude '__pycache__' --exclude '.DS_Store' \
  /home/oem/llmDeploy/llm-benchmark-kit/ \
  oem@10.17.101.201:/home/oem/llmDeploy/llm-benchmark-kit/
```

## 2. 服务器端准备环境

```bash
cd /home/oem/llmDeploy/llm-benchmark-kit
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
pip install vllm sglang lmdeploy ms-swift deepspeed
```

建议统一模型缓存：

```bash
export HF_HOME=/home/oem/llmDeploy/models/huggingface
export MODELSCOPE_CACHE=/home/oem/llmDeploy/models/modelscope
export OLLAMA_MODELS=/home/oem/llmDeploy/models/ollama
```

## 3. 推荐首测：vLLM 双卡 14B BF16

终端 1，启动服务：

```bash
cd /home/oem/llmDeploy/llm-benchmark-kit
source .venv/bin/activate
export HF_HOME=/home/oem/llmDeploy/models/huggingface
export MODEL_ID=/home/oem/llmDeploy/models/huggingface/DeepSeek-R1-Distill-Qwen-14B
export SERVED_NAME=deepseek-r1-distill-qwen-14b

CUDA_VISIBLE_DEVICES=0,1 vllm serve "$MODEL_ID" \
  --served-model-name "$SERVED_NAME" \
  --host 0.0.0.0 \
  --port 8105 \
  --dtype bfloat16 \
  --max-model-len 8192 \
  --gpu-memory-utilization 0.92 \
  --tensor-parallel-size 2 \
  --enable-prefix-caching
```

终端 2，跑 smoke test：

```bash
cd /home/oem/llmDeploy/llm-benchmark-kit
source .venv/bin/activate
python scripts/smoke_test.py --framework vllm_tp2
```

终端 3，开 GPU 监控：

```bash
cd /home/oem/llmDeploy/llm-benchmark-kit
source .venv/bin/activate
python scripts/gpu_monitor.py --output results/vllm_tp2/gpu_metrics.csv --label vllm_tp2
```

终端 2，正式压测：

```bash
python scripts/benchmark.py --framework vllm_tp2 --output-dir results/vllm_tp2
python scripts/summarize.py --results-dir results/vllm_tp2
```

## 4. 如果 vLLM 14B BF16 OOM

优先降低上下文或显存占比：

```bash
--max-model-len 4096
--gpu-memory-utilization 0.88
```

如果仍然 OOM，改测 AWQ/GPTQ/INT4 量化版本，并在 `configs/frameworks.yaml` 和启动命令里把模型 ID 改成对应量化模型。
