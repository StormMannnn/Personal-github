# Server Launch Commands

Run one service at a time unless you intentionally benchmark multi-service colocation.

## Shared Setup

```bash
export HF_HOME=/home/oem/llmDeploy/models/huggingface
export MODELSCOPE_CACHE=/home/oem/llmDeploy/models/modelscope
export OLLAMA_MODELS=/home/oem/llmDeploy/models/ollama
export MODEL_ID=deepseek-ai/DeepSeek-R1-Distill-Qwen-14B
export SERVED_NAME=deepseek-r1-distill-qwen-14b
```

## Transformers BF16

```bash
CUDA_VISIBLE_DEVICES=0,1 python server_examples/transformers_openai_server.py \
  --model "$MODEL_ID" \
  --served-name "$SERVED_NAME" \
  --port 8101
```

## Transformers BNB 4bit

```bash
CUDA_VISIBLE_DEVICES=1 python server_examples/transformers_openai_server.py \
  --model "$MODEL_ID" \
  --served-name "$SERVED_NAME" \
  --quantization bnb4 \
  --port 8102
```

## ModelScope ms-swift

```bash
CUDA_VISIBLE_DEVICES=0,1 swift deploy \
  --model "$MODEL_ID" \
  --infer_backend transformers \
  --served_model_name "$SERVED_NAME" \
  --host 0.0.0.0 \
  --port 8103 \
  --max_model_len 8192
```

## vLLM TP1

```bash
CUDA_VISIBLE_DEVICES=1 vllm serve "$MODEL_ID" \
  --served-model-name "$SERVED_NAME" \
  --host 0.0.0.0 \
  --port 8104 \
  --dtype bfloat16 \
  --max-model-len 8192 \
  --gpu-memory-utilization 0.92 \
  --enable-prefix-caching
```

If BF16 does not fit on one 24GB card, skip `vllm_tp1` or use a compatible AWQ/GPTQ 14B model and add the matching vLLM quantization flag.

## vLLM TP2

```bash
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
```

## LMDeploy TP1

```bash
CUDA_VISIBLE_DEVICES=1 lmdeploy serve api_server "$MODEL_ID" \
  --server-name 0.0.0.0 \
  --server-port 8106 \
  --model-name "$SERVED_NAME" \
  --backend turbomind \
  --tp 1 \
  --session-len 8192
```

If BF16 does not fit on one 24GB card, skip `lmdeploy_tp1` or test a 4-bit converted model.

## LMDeploy TP2

```bash
CUDA_VISIBLE_DEVICES=0,1 lmdeploy serve api_server "$MODEL_ID" \
  --server-name 0.0.0.0 \
  --server-port 8107 \
  --model-name "$SERVED_NAME" \
  --backend turbomind \
  --tp 2 \
  --session-len 8192
```

## Ollama

```bash
ollama pull deepseek-r1:7b
OLLAMA_HOST=0.0.0.0:11434 ollama serve
```

## SGLang TP1

```bash
CUDA_VISIBLE_DEVICES=1 python -m sglang.launch_server \
  --model-path "$MODEL_ID" \
  --served-model-name "$SERVED_NAME" \
  --host 0.0.0.0 \
  --port 8108 \
  --context-length 8192 \
  --mem-fraction-static 0.88 \
  --enable-torch-compile
```

## SGLang TP2

```bash
CUDA_VISIBLE_DEVICES=0,1 python -m sglang.launch_server \
  --model-path "$MODEL_ID" \
  --served-model-name "$SERVED_NAME" \
  --host 0.0.0.0 \
  --port 8109 \
  --context-length 8192 \
  --tp 2 \
  --mem-fraction-static 0.88 \
  --enable-torch-compile
```

## DeepSpeed TP2

```bash
CUDA_VISIBLE_DEVICES=0,1 deepspeed --num_gpus 2 server_examples/deepspeed_openai_server.py \
  --model "$MODEL_ID" \
  --served-name "$SERVED_NAME" \
  --port 8110 \
  --tp-size 2
```
