# 实验执行 Runbook

## 每个框架的固定流程

1. 停掉上一个服务，确认显存释放：

```bash
nvidia-smi
```

2. 按 `server_examples/launch_commands.md` 启动当前框架。

3. 跑 smoke test：

```bash
python scripts/smoke_test.py --framework <framework_key>
```

4. 开启 GPU 监控：

```bash
python scripts/gpu_monitor.py --output results/<framework_key>/gpu_metrics.csv --label <framework_key>
```

5. 正式压测：

```bash
python scripts/benchmark.py --framework <framework_key> --output-dir results/<framework_key>
```

6. 停止 GPU 监控，然后生成汇总：

```bash
python scripts/summarize.py --results-dir results/<framework_key>
```

7. 保存服务日志、启动命令和版本：

```bash
python -V
pip freeze > results/<framework_key>/pip_freeze.txt
nvidia-smi > results/<framework_key>/nvidia_smi.txt
```

## 推荐测试批次

第一批，14B 生产候选：

```text
vllm_tp2
sglang_tp2
lmdeploy_tp2
```

第二批，基线与易用性：

```text
transformers_bf16
transformers_bnb4
modelscope_swift
ollama_deepseek_r1_7b
```

第三批，双卡对照：

```text
deepspeed_tp2
vllm_tp1
sglang_tp1
lmdeploy_tp1
```

## 二测规则

进入二测的条件：

- 在 `chat_512_256` 目标并发下失败率不超过 1%。
- `ttft_p95_ms` 不超过 3000。
- 吞吐在所有候选中排名前 2，或结构化 JSON 场景明显领先。

二测模型：

```text
DeepSeek-R1-Distill-Qwen-14B 的 AWQ/GPTQ/INT4 版本，或同级 32B 量化模型
```

如果 14B BF16 显存压力过大，优先测试 AWQ/GPTQ/INT4 版本，并在结论中单独标注量化口径。

## nohup 后台跑 vLLM + GPU 监控 + 压测

适合下班前启动，断开 SSH 后服务器继续运行。以下命令默认测试 `vllm_tp2`，结果写到 `results/vllm_tp2_nohup/`。

### 1. 准备目录

```bash
cd /home/oem/llmDeploy/llm-benchmark-kit
mkdir -p results/vllm_tp2_nohup/logs
```

### 2. 后台启动 vLLM 服务

```bash
cd /home/oem/llmDeploy/llm-benchmark-kit
mkdir -p results/vllm_tp2_nohup/logs

nohup bash -lc '
source ~/miniconda3/etc/profile.d/conda.sh
conda activate llm-vllm
cd /home/oem/llmDeploy/llm-benchmark-kit
export MODEL_ID=/home/oem/llmDeploy/models/huggingface/DeepSeek-R1-Distill-Qwen-14B
export SERVED_NAME=deepseek-r1-distill-qwen-14b
VLLM_USE_FLASHINFER_SAMPLER=0 CUDA_VISIBLE_DEVICES=0,1 vllm serve "$MODEL_ID" \
  --served-model-name "$SERVED_NAME" \
  --host 0.0.0.0 \
  --port 8105 \
  --dtype bfloat16 \
  --max-model-len 8192 \
  --gpu-memory-utilization 0.88 \
  --tensor-parallel-size 2 \
  --enable-prefix-caching \
  --disable-custom-all-reduce
' > results/vllm_tp2_nohup/logs/vllm.log 2>&1 &

echo $! > results/vllm_tp2_nohup/vllm.pid
```

确认服务启动成功：

```bash
tail -f results/vllm_tp2_nohup/logs/vllm.log
```

看到下面这类日志后再启动压测：

```text
Starting vLLM server on http://0.0.0.0:8105
Application startup complete
```

也可以用健康检查确认：

```bash
curl http://127.0.0.1:8105/health
```

### 3. 后台启动 GPU 监控

```bash
cd /home/oem/llmDeploy/llm-benchmark-kit

nohup bash -lc '
source ~/miniconda3/etc/profile.d/conda.sh
conda activate llm-vllm
cd /home/oem/llmDeploy/llm-benchmark-kit
python scripts/gpu_monitor.py \
  --output results/vllm_tp2_nohup/gpu_metrics.csv \
  --label vllm_tp2 \
  --interval 1
' > results/vllm_tp2_nohup/logs/gpu_monitor.log 2>&1 &

echo $! > results/vllm_tp2_nohup/gpu_monitor.pid
```

### 4. 后台启动完整压测

```bash
cd /home/oem/llmDeploy/llm-benchmark-kit

nohup bash -lc '
source ~/miniconda3/etc/profile.d/conda.sh
conda activate llm-vllm
cd /home/oem/llmDeploy/llm-benchmark-kit
python scripts/benchmark.py \
  --framework vllm_tp2 \
  --output-dir results/vllm_tp2_nohup
' > results/vllm_tp2_nohup/logs/benchmark.log 2>&1 &

echo $! > results/vllm_tp2_nohup/benchmark.pid
```

### 5. 查看后台任务和日志

```bash
ps -p $(cat results/vllm_tp2_nohup/vllm.pid) -f
ps -p $(cat results/vllm_tp2_nohup/gpu_monitor.pid) -f
ps -p $(cat results/vllm_tp2_nohup/benchmark.pid) -f

tail -f results/vllm_tp2_nohup/logs/benchmark.log
```

### 6. 压测完成后停止 GPU 监控并汇总

如果 `benchmark.log` 里出现 `[done] wrote ... summary.csv`，说明压测完成。然后执行：

```bash
cd /home/oem/llmDeploy/llm-benchmark-kit
kill $(cat results/vllm_tp2_nohup/gpu_monitor.pid)

source ~/miniconda3/etc/profile.d/conda.sh
conda activate llm-vllm
python scripts/summarize.py --results-dir results/vllm_tp2_nohup \
  > results/vllm_tp2_nohup/logs/summarize.log 2>&1
```

如果还想停止 vLLM 服务：

```bash
kill $(cat results/vllm_tp2_nohup/vllm.pid)
```

主要结果文件：

```text
results/vllm_tp2_nohup/summary.csv
results/vllm_tp2_nohup/summary.jsonl
results/vllm_tp2_nohup/request_details.jsonl
results/vllm_tp2_nohup/gpu_metrics.csv
results/vllm_tp2_nohup/aggregate_summary.csv
results/vllm_tp2_nohup/gpu_summary.csv
results/vllm_tp2_nohup/charts/
```
