# 双 RTX 5090 大模型部署与性能对比工具包

这个目录把 7 种部署方式落成可执行的对比实验：

- Transformers
- ModelScope ms-swift
- vLLM
- LMDeploy
- Ollama
- SGLang
- DeepSpeed

主测模型：`deepseek-ai/DeepSeek-R1-Distill-Qwen-14B`。

## 1. 建议目录与环境

在 5090 服务器上复制本目录后执行：

```bash
cd /home/oem/llmDeploy/llm-benchmark-kit
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
```

再按要测试的框架安装额外依赖：

```bash
pip install vllm
pip install lmdeploy
pip install sglang
pip install ms-swift
pip install deepspeed
```

Ollama 使用系统安装：

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

统一模型缓存目录：

```bash
export HF_HOME=/home/oem/llmDeploy/models/huggingface
export MODELSCOPE_CACHE=/home/oem/llmDeploy/models/modelscope
export OLLAMA_MODELS=/home/oem/llmDeploy/models/ollama
```

## 2. 启动一个服务

启动命令见 [server_examples/launch_commands.md](server_examples/launch_commands.md)。

推荐顺序：

1. `vllm_tp1`
2. `sglang_tp1`
3. `lmdeploy_tp1`
4. `transformers_bf16`
5. `transformers_bnb4`
6. `modelscope_swift`
7. `ollama_deepseek_r1_7b`
8. `vllm_tp2`
9. `sglang_tp2`
10. `lmdeploy_tp2`
11. `deepspeed_tp2`

14B BF16 建议优先用双卡 `CUDA_VISIBLE_DEVICES=0,1`。单卡 `CUDA_VISIBLE_DEVICES=1` 更适合 4bit/量化版本，或者用来测试是否能在较短上下文下勉强装下。

## 3. Smoke Test

服务启动后先跑 10 条中文问题：

```bash
python scripts/smoke_test.py --framework vllm_tp1
```

如果返回正常，再开始正式压测。

## 4. GPU 监控

另开一个终端：

```bash
python scripts/gpu_monitor.py \
  --output results/gpu_metrics.csv \
  --label vllm_tp1 \
  --interval 1
```

压测结束后按 `Ctrl-C` 停止监控。

## 5. 正式压测

单个框架、单个场景：

```bash
python scripts/benchmark.py \
  --framework vllm_tp1 \
  --scenario chat_512_256 \
  --output-dir results/vllm_tp1_chat
```

单个框架跑全部场景：

```bash
python scripts/benchmark.py \
  --framework vllm_tp1 \
  --output-dir results/vllm_tp1_all
```

不建议所有框架一起跑，因为每次只能启动一个服务，且不同端口对应不同框架。

## 后台长时间压测

如果要断开 SSH 后继续运行 vLLM 服务、GPU 监控和完整压测，请使用 [RUNBOOK.md](RUNBOOK.md) 中的 `nohup 后台跑 vLLM + GPU 监控 + 压测` 章节。

## 6. 汇总图表

```bash
python scripts/summarize.py --results-dir results/vllm_tp1_all
```

输出：

- `summary.csv`：每次 repetition 的结果。
- `aggregate_summary.csv`：按框架、场景、并发聚合后的均值。
- `request_details.jsonl`：逐请求明细。
- `gpu_summary.csv`：GPU 采样汇总。
- `charts/*.png`：吞吐、TTFT、延迟、失败率曲线。

## 7. 指标解释

核心排序指标：

- `output_tokens_per_s`：输出 token 吞吐。
- `total_tokens_per_s`：输入加输出 token 总吞吐。
- `requests_per_s`：请求吞吐。
- `ttft_p95_ms`：95 分位首 token 延迟。
- `latency_p95_ms`：95 分位端到端延迟。
- `error_rate`：失败率。
- `json_valid_rate`：结构化 JSON 场景的合法 JSON 比例。

生产候选门槛建议：

- `error_rate <= 0.01`
- 目标并发下 `ttft_p95_ms <= 3000`
- GPU 显存峰值低于 90% 且无 OOM
- 温度与功耗稳定，无持续降频

## 8. 场景说明

场景配置在 [configs/scenarios.yaml](configs/scenarios.yaml)。

- `latency_256_128`：单用户延迟基线。
- `chat_512_256`：典型客服/RAG 问答。
- `saturation_512_256`：并发递增，直到失败率或 TTFT 超限。
- `long_context_4096_256`：4K 输入长上下文。
- `long_context_8192_256`：8K 输入长上下文。
- `prefix_cache_4096_256`：固定前缀缓存收益。
- `structured_json_512_256`：结构化 JSON 输出场景。

## 9. 结论产出模板

每个框架记录：

```text
框架：
版本：
启动命令：
GPU 策略：
模型：
量化：
最佳并发：
最大稳定 output tokens/s：
p95 TTFT：
p95 latency：
峰值显存：
平均功耗：
错误率：
是否进入二测：
备注：
```

最终建议按 Pareto frontier 选择，不只看吞吐。对双 RTX 5090 来说，14B 的合理路线是：BF16 用双卡 TP 做质量基线，单卡用 4bit/量化版本做吞吐和成本基线。
