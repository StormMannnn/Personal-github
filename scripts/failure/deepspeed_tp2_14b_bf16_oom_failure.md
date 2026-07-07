# DeepSpeed TP2 14B BF16 启动失败案例

## 基本信息

- 测试日期：2026-07-02
- 服务器：双 NVIDIA GeForce RTX 5090 24G
- 项目路径：`/home/oem/llmDeploy/llm-benchmark-kit`
- 模型路径：`/home/oem/llmDeploy/models/huggingface/DeepSeek-R1-Distill-Qwen-14B`
- 模型：`DeepSeek-R1-Distill-Qwen-14B`
- 精度：BF16
- 框架配置名：`deepspeed_tp2`
- 服务端口：`8110`
- 启动日志：`/home/oem/llmDeploy/llm-benchmark-kit/results/deepspeed_tp2_nohup_8192fix/logs/deepspeed.log`

## 失败结论

DeepSpeed 这轮没有进入正式压测阶段，失败发生在服务启动和模型初始化阶段。

本次失败应记录为：

```text
DeepSpeed TP2 / DeepSeek-R1-Distill-Qwen-14B / BF16：启动失败，CUDA OOM。
```

该失败不代表双 RTX 5090 24G 服务器无法部署 14B BF16。vLLM、SGLang、LMDeploy 已经可以在同一台服务器上运行该模型。DeepSpeed 失败的主要原因是当前项目里的 DeepSpeed 服务封装比较简化，模型加载路径与 DeepSpeed inference 接管方式冲突。

## 失败前的启动方式

当前项目使用内置的 OpenAI-compatible FastAPI 封装：

```text
server_examples/deepspeed_openai_server.py
```

修正 `--local_rank` 报错后，启动脚本采用单 HTTP 进程方式：

```bash
CUDA_VISIBLE_DEVICES=0,1 python server_examples/deepspeed_openai_server.py \
  --model "$MODEL_ID" \
  --served-name "$SERVED_NAME" \
  --host 0.0.0.0 \
  --port 8110 \
  --tp-size 1
```

使用单 HTTP 进程是为了避免 `deepspeed --num_gpus 2` 启动两个 Python 进程后，两个 rank 同时尝试监听 `8110` 端口。

## 关键日志

启动时模型权重可以加载到 100%：

```text
Loading weights: 100%|██████████| 579/579 [00:11<00:00, 49.00it/s]
```

随后 DeepSpeed 接管模型时出现警告：

```text
Config parameter replace_method is deprecated.
Config parameter mp_size is deprecated use tensor_parallel.tp_size instead
You shouldn't move a model that is dispatched using accelerate hooks.
```

最终报 CUDA OOM：

```text
torch.OutOfMemoryError: CUDA out of memory.
Tried to allocate 136.00 MiB.
GPU 0 has a total capacity of 23.41 GiB of which 103.25 MiB is free.
Including non-PyTorch memory, this process has 23.09 GiB memory in use.
Of the allocated memory 22.59 GiB is allocated by PyTorch.
```

## 根因分析

当前 DeepSpeed 服务封装的加载链路如下：

1. `transformers_openai_server.py` 使用 `AutoModelForCausalLM.from_pretrained(...)` 加载模型。
2. 加载参数里使用了 `device_map="auto"`。
3. `device_map="auto"` 会通过 Accelerate hooks 把 14B BF16 模型自动切分到双卡。
4. 模型加载完成后，`deepspeed_openai_server.py` 调用 `deepspeed.init_inference(...)`。
5. DeepSpeed InferenceEngine 初始化时又尝试执行 `self.module.to(device)`。
6. 这会试图移动一个已经由 Accelerate hooks 分片管理的模型。
7. GPU0 在模型加载后只剩约 `103 MiB` 空闲，DeepSpeed 再申请 `136 MiB` 时触发 OOM。

因此，本次 OOM 的核心不是模型权重一开始完全放不下，而是：

```text
Transformers device_map=auto 与 deepspeed.init_inference 的二次模型搬运发生冲突。
```

日志中的这句是最直接证据：

```text
You shouldn't move a model that is dispatched using accelerate hooks.
```

## 为什么不是服务器硬件问题

同一台双 RTX 5090 24G 服务器已经完成过以下框架的 14B BF16 测试：

- vLLM TP=2：完整压测成功。
- SGLang TP=2：完整压测成功。
- LMDeploy TP=2：完整压测成功。

这些框架有成熟的推理引擎、KV cache 管理和张量并行能力，因此可以稳定运行 14B BF16。

DeepSpeed 当前失败，是因为本项目中的 DeepSpeed wrapper 是最小基线封装，不是完整生产级 DeepSpeed 推理服务。

## 已排除的问题

### 1. `torch_dtype` 提示不是失败原因

日志中的：

```text
`torch_dtype` is deprecated! Use `dtype` instead!
```

只是 Transformers 新版本的弃用提示，不会导致启动失败。

### 2. `--local_rank` 报错已修复

最初使用 `deepspeed --num_gpus 2` 时，DeepSpeed launcher 会自动追加：

```text
--local_rank=0
--local_rank=1
```

旧版 `deepspeed_openai_server.py` 不接收该参数，因此启动失败。后续已给脚本增加：

```python
parser.add_argument("--local_rank", "--local-rank", ...)
```

该问题已经不是当前 OOM 的原因。

### 3. 端口冲突风险已规避

如果直接使用：

```bash
deepspeed --num_gpus 2 server_examples/deepspeed_openai_server.py
```

会启动两个 rank。当前最小 FastAPI 封装没有做 rank0-only HTTP 服务控制，两个进程可能同时监听 `8110`，导致端口冲突。

因此后续改为单 HTTP 进程启动，以规避该问题。

## 记录建议

在最终框架对比表中，DeepSpeed 建议记录为：

```text
DeepSpeed：14B BF16 启动失败，未进入压测。
失败原因：当前最小 wrapper 使用 Transformers device_map=auto 后再调用 deepspeed.init_inference，
导致 Accelerate 分片模型被 DeepSpeed 二次搬运，GPU0 OOM。
```

不要把该结果解释为“双 5090 不能部署 14B”。更准确的结论是：

```text
该项目当前 DeepSpeed 最小服务封装不适合部署 DeepSeek-R1-Distill-Qwen-14B BF16。
```

## 后续可选方案

### 方案 1：保留失败案例，不继续投入

推荐。DeepSpeed 在本项目中主要是基线项，不是当前双 5090 14B 生产部署首选。

生产候选仍建议优先看：

1. vLLM
2. LMDeploy
3. SGLang

### 方案 2：把 DeepSpeed 改测 7B BF16

如果必须保留 DeepSpeed 成功数据，可以用 7B BF16 重新测试。7B 对显存压力小很多，更可能启动成功。

但它不能和 14B BF16 的 vLLM / SGLang / LMDeploy 结果直接公平对比。

### 方案 3：改成 14B 4bit

可以尝试使用 4bit 量化方式降低显存压力，但这会改变模型精度和推理路径，也不再是和其它 BF16 框架的同口径对比。

### 方案 4：重写 DeepSpeed 多进程推理服务

如果必须做真正 DeepSpeed TP=2，需要重写服务架构：

- HTTP 服务只在 rank0 启动。
- rank0 接收请求后，把输入广播给 rank1。
- 所有 rank 同步执行 `generate`。
- rank0 收集输出并返回 HTTP 响应。

这个改造成本明显高于当前基线测试价值，不建议作为第一轮框架选型的必要工作。
